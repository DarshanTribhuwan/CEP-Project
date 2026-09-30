"""Weather provider.

Live source: Open-Meteo (https://open-meteo.com), which is free, keyless and was
verified reachable from the development machine. Values returned here are real
forecast data, tagged ``Provenance.LIVE``.

Resilience (see ``app/providers/http.py``)
------------------------------------------
Outbound calls are retried with backoff, and every successful response is cached
on disk. When Open-Meteo is unreachable - a flaky connection, a 502, a machine
with no route out - the last real forecast is served instead, tagged
``Provenance.CACHED`` with ``is_stale = true`` and an age in seconds. The farmer
is told the forecast is not current rather than being shown nothing, and is
never shown an invented forecast.

A stale forecast older than ``providers.weather.max_stale_minutes`` is refused
outright: past that point, yesterday's rain is worse than no answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ..core.config import settings
from .base import DataResult
from .http import fetch_json

SOURCE_NAME = "Open-Meteo"
PROVIDER_KEY = "providers.weather"


@dataclass(slots=True)
class WeatherDay:
    day: date
    temp_max_c: float | None
    temp_min_c: float | None
    rainfall_mm: float | None


@dataclass(slots=True)
class WeatherForecast:
    latitude: float
    longitude: float
    timezone: str | None
    days: list[WeatherDay] = field(default_factory=list)

    @property
    def total_rainfall_mm(self) -> float | None:
        known = [d.rainfall_mm for d in self.days if d.rainfall_mm is not None]
        return round(sum(known), 1) if known else None

    @property
    def mean_temp_max_c(self) -> float | None:
        known = [d.temp_max_c for d in self.days if d.temp_max_c is not None]
        return round(sum(known) / len(known), 1) if known else None


def get_forecast(latitude: float, longitude: float, days: int = 7) -> DataResult[WeatherForecast]:
    """Fetch a daily forecast. Never invents one; reports unavailability instead."""
    if not settings.get(f"{PROVIDER_KEY}.enabled", True):
        return DataResult.unavailable(
            "weather provider is disabled in config.toml", source_name=SOURCE_NAME
        )

    days = max(1, min(int(days), 16))
    # Round coordinates so that nearby farms share a cache entry, matching what
    # Open-Meteo itself does internally.
    base_url = settings.require(f"{PROVIDER_KEY}.base_url")
    params: dict[str, Any] = {
        "latitude": round(latitude, 3),
        "longitude": round(longitude, 3),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "forecast_days": days,
        "timezone": "Asia/Kolkata",
    }

    fresh_ttl = float(settings.get(f"{PROVIDER_KEY}.cache_ttl_minutes", 30)) * 60
    max_stale = float(settings.get(f"{PROVIDER_KEY}.max_stale_minutes", 720)) * 60

    outcome = fetch_json(
        base_url,
        kind="weather",
        params=params,
        timeout=float(settings.get(f"{PROVIDER_KEY}.timeout_seconds", 8)),
        fresh_ttl_seconds=fresh_ttl,
        max_stale_seconds=max_stale,
    )

    if not outcome.ok:
        return DataResult.unavailable(outcome.reason or "weather source unavailable", source_name=SOURCE_NAME)

    parsed = _parse(outcome.payload)
    if parsed is None:
        return DataResult.unavailable(
            "weather source returned an unexpected response shape", source_name=SOURCE_NAME
        )

    if not outcome.from_cache:
        return DataResult.live(
            parsed,
            source_name=SOURCE_NAME,
            source_url=base_url,
            notes=["daily max/min temperature and precipitation, timezone Asia/Kolkata"],
        )

    # A cached response is real data, but it is not *current* data, and the
    # distinction has to survive into the API response.
    return DataResult.cached(
        parsed,
        source_name=SOURCE_NAME,
        source_url=base_url,
        age_seconds=outcome.age_seconds,
        is_stale=outcome.stale,
        notes=outcome.provenance_note,
    )


def _parse(payload: Any) -> WeatherForecast | None:
    if not isinstance(payload, dict):
        return None
    try:
        daily = payload["daily"]
        times = daily["time"]
        tmax = daily.get("temperature_2m_max", [None] * len(times))
        tmin = daily.get("temperature_2m_min", [None] * len(times))
        rain = daily.get("precipitation_sum", [None] * len(times))
    except (KeyError, TypeError):
        return None

    days: list[WeatherDay] = []
    for index, stamp in enumerate(times):
        try:
            day = datetime.fromisoformat(str(stamp)).date()
        except ValueError:
            continue
        days.append(
            WeatherDay(
                day=day,
                temp_max_c=_as_float(tmax, index),
                temp_min_c=_as_float(tmin, index),
                rainfall_mm=_as_float(rain, index),
            )
        )
    if not days:
        return None
    return WeatherForecast(
        latitude=float(payload.get("latitude", 0.0)),
        longitude=float(payload.get("longitude", 0.0)),
        timezone=payload.get("timezone"),
        days=days,
    )


def _as_float(seq: Any, index: int) -> float | None:
    try:
        value = seq[index]
    except (IndexError, TypeError):
        return None
    return None if value is None else float(value)
