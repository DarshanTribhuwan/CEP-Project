"""Retry, caching and offline behaviour.

Two properties are under test, and they pull in opposite directions, which is
why they need their own file:

    availability  a transient failure or a network outage should not blank the
                  screen, so requests are retried and real earlier responses are
                  kept on disk and reused;
    honesty       nothing served from that cache may be presented as current.
                  Every cached response states its age, and a response that has
                  aged out of its window is marked stale.

A test that only checked the first property would happily pass on a system that
showed a farmer week-old prices as today's.

No test here touches the network: ``httpx.MockTransport`` supplies the responses.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from app.core import httpcache
from app.core.retry import Attempt, backoff_delay, retry_call
from app.providers.base import DataResult
from app.providers.http import HttpOutcome, fetch_json, offline_mode
from tests.conftest import API

URL = "https://example.invalid/v1/forecast"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def ok_json(payload: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def failing_then_ok(failures: list[Exception], payload: dict) -> tuple[httpx.MockTransport, list[int]]:
    """Transport that raises ``failures`` in order, then succeeds."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) <= len(failures):
            raise failures[len(calls) - 1]
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler), calls


def always_failing(error: Exception) -> tuple[httpx.MockTransport, list[int]]:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise error

    return httpx.MockTransport(handler), calls


def never_called() -> httpx.MockTransport:
    """A transport that fails the test if a request is made at all."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("a request was made when none should have been")

    return httpx.MockTransport(handler)


def seed_cache(
    kind: str,
    payload: dict,
    *,
    age_seconds: float,
    url: str = URL,
    params: dict | None = None,
) -> str:
    """Write a cache entry, then backdate it to simulate elapsed time."""
    key = httpcache.make_key(kind, url, params)
    assert httpcache.write(key, payload, url=url) is True
    path = httpcache.entry_path(key)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["stored_at"] = time.time() - age_seconds
    path.write_text(json.dumps(document), encoding="utf-8")
    return key


@pytest.fixture(autouse=True)
def clean_cache():
    """Each test starts with an empty response cache."""
    httpcache.clear()
    yield
    httpcache.clear()


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------
def test_backoff_grows_exponentially_and_is_capped():
    delays = [backoff_delay(n, base=1.0, maximum=8.0, jitter=0.0) for n in range(1, 6)]
    assert delays == [1.0, 2.0, 4.0, 8.0, 8.0]


def test_backoff_jitter_only_shortens_and_stays_bounded():
    import random

    rng = random.Random(1234)
    for _ in range(50):
        delay = backoff_delay(3, base=1.0, maximum=100.0, jitter=0.25, rng=rng)
        assert 0.75 * 4.0 <= delay <= 4.0


def test_retry_call_retries_transient_failures_then_succeeds():
    slept: list[float] = []
    calls: list[int] = []

    def work() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("connection reset")
        return "ok"

    outcome = retry_call(work, attempts=3, base_delay=0.5, jitter=0.0, sleep=slept.append)

    assert outcome.ok is True
    assert outcome.value == "ok"
    assert outcome.attempt_count == 3
    assert outcome.retried is True
    # Two waits, for the second and third attempts.
    assert slept == [0.5, 1.0]


def test_retry_call_gives_up_and_explains_why():
    def work() -> str:
        raise httpx.ConnectTimeout("timed out")

    outcome = retry_call(work, attempts=2, base_delay=0.1, jitter=0.0, sleep=lambda _: None)

    assert outcome.ok is False
    assert outcome.attempt_count == 2
    assert "ConnectTimeout" in outcome.failure_summary
    assert outcome.failure_summary.count("->") == 1


def test_a_permanent_failure_is_not_retried():
    """A rejected key or a 404 is final; retrying only delays the honest answer."""
    calls: list[int] = []

    def work() -> str:
        calls.append(1)
        request = httpx.Request("GET", URL)
        raise httpx.HTTPStatusError(
            "404", request=request, response=httpx.Response(404, request=request)
        )

    outcome = retry_call(work, attempts=4, base_delay=0.0, jitter=0.0, sleep=lambda _: None)

    assert outcome.ok is False
    assert outcome.attempt_count == 1
    assert calls == [1]


def test_retry_records_every_attempt_for_diagnostics():
    seen: list[Attempt] = []

    def work() -> None:
        raise httpx.ReadTimeout("slow")

    retry_call(work, attempts=3, base_delay=0.0, jitter=0.0, sleep=lambda _: None, on_attempt=seen.append)

    assert [item.number for item in seen] == [1, 2, 3]
    assert all("ReadTimeout" in (item.error or "") for item in seen)


# ---------------------------------------------------------------------------
# Cache storage
# ---------------------------------------------------------------------------
def test_cache_round_trips_the_payload_verbatim():
    payload = {"days": [1.5, None, 0], "nested": {"a": [1, 2]}, "text": "काही"}
    key = httpcache.make_key("weather", URL, {"lat": 18.5})

    assert httpcache.write(key, payload, url=URL) is True
    entry = httpcache.read(key)

    assert entry is not None
    assert entry.payload == payload          # byte-for-byte, nothing coerced
    assert entry.url == URL
    assert entry.age_seconds <= 1


def test_a_corrupt_entry_is_discarded_not_repaired():
    key = httpcache.make_key("weather", URL, None)
    path = httpcache.entry_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")

    assert httpcache.read(key) is None
    # And it is gone, so nothing downstream can try to interpret it.
    assert not path.exists()


def test_cache_keys_separate_different_requests():
    a = httpcache.make_key("weather", URL, {"lat": 18.5, "days": 7})
    b = httpcache.make_key("weather", URL, {"days": 7, "lat": 18.5})  # same, reordered
    c = httpcache.make_key("weather", URL, {"lat": 18.6, "days": 7})

    assert a == b          # stable regardless of key order
    assert a != c          # but a different location is a different entry


# ---------------------------------------------------------------------------
# fetch_json: the four paths
# ---------------------------------------------------------------------------
def test_a_current_cache_entry_answers_without_a_request():
    payload = {"value": "cached"}
    seed_cache("weather", payload, age_seconds=60)

    outcome = fetch_json(
        URL, kind="weather", fresh_ttl_seconds=1800, transport=never_called()
    )

    assert outcome.ok is True
    assert outcome.payload == payload
    assert outcome.from_cache is True
    assert outcome.stale is False
    assert outcome.attempts == 0
    assert any("cache" in note for note in outcome.notes)


def test_a_transient_failure_is_retried_then_cached():
    payload = {"value": "fresh"}
    transport, calls = failing_then_ok([httpx.ConnectError("reset")], payload)

    outcome = fetch_json(
        URL, kind="weather", attempts=3, base_delay=0.0, transport=transport
    )

    assert outcome.ok is True
    assert outcome.payload == payload
    assert outcome.from_cache is False
    assert outcome.attempts == 2          # failed once, succeeded on the second try
    assert outcome.retried is True
    assert len(calls) == 2
    # The successful response was stored, so the next call is free.
    assert httpcache.read(httpcache.make_key("weather", URL, None)) is not None


def test_a_permanent_http_error_is_not_retried_through_fetch_json():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404, request=request, json={"error": "nope"})

    outcome = fetch_json(
        URL, kind="weather", attempts=4, base_delay=0.0, transport=httpx.MockTransport(handler)
    )

    assert outcome.ok is False
    assert outcome.attempts == 1
    assert calls == [1]


def test_total_failure_with_a_stale_cache_serves_it_and_says_so():
    payload = {"value": "last known good"}
    seed_cache("weather", payload, age_seconds=3 * 3600)
    transport, _ = always_failing(httpx.ConnectError("no route to host"))

    outcome = fetch_json(
        URL,
        kind="weather",
        fresh_ttl_seconds=1800,        # the entry is well past this
        max_stale_seconds=12 * 3600,   # but inside this
        attempts=2,
        base_delay=0.0,
        transport=transport,
    )

    assert outcome.ok is True
    assert outcome.from_cache is True
    assert outcome.stale is True
    assert outcome.age_seconds is not None and outcome.age_seconds >= 3 * 3600 - 5
    assert outcome.attempts == 2
    assert outcome.failure_trace and "ConnectError" in outcome.failure_trace
    # The critical assertion: the staleness is stated, not hidden.
    joined = " ".join(outcome.notes)
    assert "NOT current" in joined
    assert "3 hours" in joined


def test_total_failure_with_no_cache_is_unavailable():
    transport, _ = always_failing(httpx.ConnectError("no route"))

    outcome = fetch_json(URL, kind="weather", attempts=3, base_delay=0.0, transport=transport)

    assert outcome.ok is False
    assert outcome.payload is None
    assert outcome.attempts == 3
    assert outcome.reason and "3 attempt" in outcome.reason


def test_a_cache_entry_past_the_staleness_limit_is_refused():
    """Past a point, an old value is worse than no value."""
    seed_cache("weather", {"value": "ancient"}, age_seconds=72 * 3600)
    transport, _ = always_failing(httpx.ConnectError("no route"))

    outcome = fetch_json(
        URL,
        kind="weather",
        fresh_ttl_seconds=1800,
        max_stale_seconds=12 * 3600,
        attempts=1,
        base_delay=0.0,
        transport=transport,
    )

    assert outcome.ok is False
    assert outcome.reason and "staleness limit" in outcome.reason


# ---------------------------------------------------------------------------
# Offline mode
# ---------------------------------------------------------------------------
def test_offline_mode_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SAMPADA_OFFLINE", raising=False)
    assert offline_mode() is False


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_offline_mode_is_switched_on_by_the_environment(monkeypatch, value):
    monkeypatch.setenv("SAMPADA_OFFLINE", value)
    assert offline_mode() is True


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_an_explicit_falsy_env_value_overrides_config(monkeypatch, value):
    monkeypatch.setenv("SAMPADA_OFFLINE", value)
    assert offline_mode() is False


def test_offline_uses_the_cache_without_touching_the_network(monkeypatch):
    payload = {"value": "from before the outage"}
    seed_cache("weather", payload, age_seconds=2 * 3600)
    monkeypatch.setenv("SAMPADA_OFFLINE", "1")

    outcome = fetch_json(
        URL,
        kind="weather",
        fresh_ttl_seconds=1800,
        max_stale_seconds=12 * 3600,
        transport=never_called(),
    )

    assert outcome.ok is True
    assert outcome.served_offline is True
    assert outcome.stale is True          # 2h > the 30min freshness window
    assert outcome.payload == payload
    assert "offline" in " ".join(outcome.notes).lower()


def test_offline_with_nothing_cached_reports_unavailable(monkeypatch):
    monkeypatch.setenv("SAMPADA_OFFLINE", "1")

    outcome = fetch_json(URL, kind="weather", transport=never_called())

    assert outcome.ok is False
    assert outcome.served_offline is True
    assert outcome.reason and "offline" in outcome.reason.lower()
    assert outcome.attempts == 0


def test_offline_still_refuses_an_entry_past_its_limit(monkeypatch):
    seed_cache("weather", {"value": "old"}, age_seconds=48 * 3600)
    monkeypatch.setenv("SAMPADA_OFFLINE", "1")

    outcome = fetch_json(
        URL,
        kind="weather",
        fresh_ttl_seconds=1800,
        max_stale_seconds=12 * 3600,
        transport=never_called(),
    )

    assert outcome.ok is False
    assert outcome.reason and "staleness limit" in outcome.reason


# ---------------------------------------------------------------------------
# Provider mapping: outcome -> provenance
# ---------------------------------------------------------------------------
def test_weather_provider_maps_a_stale_outcome_to_cached_provenance(monkeypatch):
    import app.providers.weather as weather
    from app.core.errors import Provenance

    payload = {
        "latitude": 18.5,
        "longitude": 73.8,
        "timezone": "Asia/Kolkata",
        "daily": {
            "time": ["2026-09-17", "2026-09-18"],
            "temperature_2m_max": [30.0, 31.0],
            "temperature_2m_min": [20.0, 21.0],
            "precipitation_sum": [1.0, 0.0],
        },
    }
    monkeypatch.setattr(
        weather,
        "fetch_json",
        lambda *a, **k: HttpOutcome(
            payload=payload,
            url="fake://weather",
            from_cache=True,
            stale=True,
            age_seconds=5 * 3600,
            notes=["source unreachable after 3 attempts; using a cached response"],
        ),
    )

    result = weather.get_forecast(18.5, 73.8)
    provenance = result.provenance

    assert result.available is True
    assert provenance.data_source is Provenance.CACHED
    assert provenance.is_stale is True
    assert provenance.cached_age_seconds == 5 * 3600
    assert provenance.is_demo is False
    # A cached value must never be reported as freshly retrieved.
    assert provenance.data_source is not Provenance.LIVE


def test_weather_provider_calls_a_fresh_cache_hit_current(monkeypatch):
    import app.providers.weather as weather
    from app.core.errors import Provenance

    payload = {
        "daily": {
            "time": ["2026-09-17"],
            "temperature_2m_max": [30.0],
            "temperature_2m_min": [20.0],
            "precipitation_sum": [0.0],
        }
    }
    monkeypatch.setattr(
        weather,
        "fetch_json",
        lambda *a, **k: HttpOutcome(
            payload=payload, url="fake://weather", from_cache=True, stale=False, age_seconds=120
        ),
    )

    provenance = weather.get_forecast(18.5, 73.8).provenance

    assert provenance.data_source is Provenance.CACHED
    assert provenance.is_stale is False     # within its TTL, so still current
    assert provenance.cached_age_seconds == 120


def test_weather_provider_reports_the_failure_reason(monkeypatch):
    import app.providers.weather as weather

    monkeypatch.setattr(
        weather,
        "fetch_json",
        lambda *a, **k: HttpOutcome(
            url="fake://weather",
            attempts=3,
            reason="the source could not be reached after 3 attempt(s) (ConnectError)",
            failure_trace="ConnectError: no route",
        ),
    )

    result = weather.get_forecast(18.5, 73.8)

    assert result.available is False
    assert "3 attempt" in (result.provenance.unavailable_reason or "")


def test_market_provider_does_not_serve_the_cache_when_unconfigured():
    """A missing API key is a configuration fault, not a transport hiccup.

    Serving yesterday's cached prices here would make a switched-off feed look
    like a working one, so the cache is deliberately not consulted.
    """
    from app.providers.market import DataGovAgmarknetProvider

    seed_cache("market", {"records": [{"modal_price": "9999"}]}, age_seconds=60)
    result = DataGovAgmarknetProvider().price_history("soybean", "Pune")

    assert result.available is False
    assert "resource id" in (result.provenance.unavailable_reason or "")


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------
def test_provider_status_reports_offline_and_cache_state(client):
    body = client.get(f"{API}/providers/status").json()
    transport = body["transport"]

    assert set(transport) >= {
        "offline",
        "retries",
        "backoff_seconds",
        "cache",
        "offline_env_var",
    }
    assert transport["offline_env_var"] == "SAMPADA_OFFLINE"
    assert transport["cache"]["directory"]
    assert isinstance(transport["cache"]["entries"], int)
    # Secrets must never appear in this payload.
    assert "api-key=" not in json.dumps(body)


def test_recommendation_flags_a_stale_forecast(offline_providers, farmer_client):
    from datetime import date, timedelta

    from app.providers.weather import WeatherDay, WeatherForecast

    stale = DataResult.cached(
        WeatherForecast(
            latitude=18.15,
            longitude=74.58,
            timezone="Asia/Kolkata",
            days=[
                WeatherDay(
                    day=date.today() + timedelta(days=offset),
                    temp_max_c=29.0,
                    temp_min_c=20.0,
                    rainfall_mm=1.0,
                )
                for offset in range(7)
            ],
        ),
        source_name="FakeWeather",
        age_seconds=16 * 3600,
        is_stale=True,
        notes=["source unreachable; using the last cached forecast"],
    )
    offline_providers(forecast=stale)

    body = farmer_client.post(
        f"{API}/recommendations?lang=en", json={"farm_id": farmer_client.farm_id}
    ).json()

    stale_components = {item["component"]: item for item in body["stale_inputs"]}
    assert "weather" in stale_components
    assert stale_components["weather"]["age_seconds"] == 16 * 3600
    assert "16 hours" in stale_components["weather"]["note"]

    codes = {reason["code"] for option in body["options"] for reason in option["reasons"]}
    assert "weather_stale" in codes
    assert any("not current" in item.lower() for item in body["disclaimers"])


def test_recommendation_reports_no_staleness_when_everything_is_current(
    offline_providers, farmer_client
):
    body = farmer_client.post(
        f"{API}/recommendations?lang=en", json={"farm_id": farmer_client.farm_id}
    ).json()

    assert body["stale_inputs"] == []
    codes = {reason["code"] for option in body["options"] for reason in option["reasons"]}
    assert "weather_stale" not in codes
    assert "price_stale" not in codes
