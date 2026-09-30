"""Shared test fixtures.

Two things every test in this suite depends on:

  1. **Isolation.** The backend is pointed at a throwaway config file in a temp
     directory with its own SQLite file, so tests never touch ``sampada.db``.
     The config path is set before ``app`` is imported, because
     ``app.core.config`` reads the environment once at import time.

  2. **No network.** Every external provider is replaced with a fake. A test
     suite for this project must not depend on Open-Meteo being up, and must
     never silently pass because a live call happened to succeed.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
from datetime import date, timedelta

# --- Point the app at an isolated config BEFORE importing it ----------------
_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
_TMP_DIR = pathlib.Path(tempfile.mkdtemp(prefix="sampada-tests-"))
_TMP_DB = (_TMP_DIR / "test.db").as_posix()

_config_text = (_BACKEND_DIR / "config.toml").read_text(encoding="utf-8")
_config_text = _config_text.replace(
    'db_url = "sqlite:///./sampada.db"', f'db_url = "sqlite:///{_TMP_DB}"'
)
# The suite asserts the *unconfigured* market-provider state, so it must see no
# resource id and no API key regardless of what the developer's machine has
# (an exported variable, or backend/.env - which is never read here anyway,
# because the app loads .env from beside SAMPADA_CONFIG, and this is a temp
# directory). If the resource-id literal below ever drifts from config.toml,
# the tests that depend on this fail loudly rather than quietly making live calls.
_config_text = _config_text.replace(
    'resource_id = "9ef84268-d588-465a-a308-a864a43d0070"', 'resource_id = ""'
)
os.environ.pop("SAMPADA_DATA_GOV_IN_KEY", None)
# Keep the outbound-response cache inside the temp directory too, so a test run
# never reads or writes the developer's real cache.
_config_text = _config_text.replace(
    'cache_dir = "var/http_cache"',
    f'cache_dir = "{(_TMP_DIR / "http_cache").as_posix()}"',
)
os.environ["SAMPADA_CONFIG"] = str(_TMP_DIR / "config.toml")
(_TMP_DIR / "config.toml").write_text(_config_text, encoding="utf-8")

import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app.core import db as db_module  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.providers.base import DataResult  # noqa: E402
from app.providers.market import MarketPriceRecord  # noqa: E402
from app.providers.weather import WeatherDay, WeatherForecast  # noqa: E402

API = str(settings.get("backend.api_prefix", "/api/v1"))


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def fresh_database():
    """Every test starts from an empty schema."""
    db_module.reset_db()
    yield
    db_module.reset_db()


@pytest.fixture
def client() -> TestClient:
    # Used as a context manager so lifespan handlers run, matching production.
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Offline provider fakes
# ---------------------------------------------------------------------------
def make_forecast(latitude: float = 18.5, longitude: float = 73.8) -> DataResult:
    """A live-looking forecast, no network involved."""
    start = date.today()
    days = [
        WeatherDay(
            day=start + timedelta(days=offset),
            temp_max_c=29.0 + offset * 0.1,
            temp_min_c=20.0,
            rainfall_mm=1.5,
        )
        for offset in range(7)
    ]
    forecast = WeatherForecast(
        latitude=latitude, longitude=longitude, timezone="Asia/Kolkata", days=days
    )
    return DataResult.live(forecast, source_name="FakeWeather", source_url="fake://weather")


def make_unavailable_forecast(reason: str = "provider disabled in test") -> DataResult:
    return DataResult.unavailable(reason, source_name="FakeWeather")


def _records(crop_id: str, prices: list[float]) -> list[MarketPriceRecord]:
    today = date.today()
    return [
        MarketPriceRecord(
            crop_id=crop_id,
            commodity=crop_id,
            market="Fake Mandi",
            district="Pune",
            state="Maharashtra",
            price_date=today - timedelta(days=30 * (len(prices) - index)),
            min_price=price - 100,
            max_price=price + 100,
            modal_price=price,
        )
        for index, price in enumerate(prices)
    ]


class FakeMarketProvider:
    """Live-labelled price data for tests, or explicitly unavailable."""

    name = "FakeMarket"

    def __init__(
        self,
        series: dict[str, list[float]] | None = None,
        unavailable_reason: str | None = None,
        demo: bool = False,
    ) -> None:
        self.series = series or {}
        self.unavailable_reason = unavailable_reason
        self.demo = demo

    def price_history(self, crop_id: str, district: str | None = None, days: int = 180) -> DataResult:
        if self.unavailable_reason:
            return DataResult.unavailable(self.unavailable_reason, source_name=self.name)
        prices = self.series.get(crop_id)
        if not prices:
            return DataResult.unavailable(
                f"no records for {crop_id!r}", source_name=self.name
            )
        records = _records(crop_id, prices)
        if self.demo:
            return DataResult.sample(records, source_name=self.name)
        return DataResult.live(records, source_name=self.name, source_url="fake://market")


DEFAULT_SERIES = {
    "soybean": [4300, 4350, 4400, 4450, 4500, 4550, 4600, 4650, 4700, 4750, 4800, 4850],
    "tur": [6600, 6700, 6800, 6900, 7000, 7100, 7200, 7300, 7400, 7500, 7600, 7700],
    "onion": [2500, 2100, 1300, 1450, 1700, 2200, 2350, 1950, 1700, 1875, 2100, 2300],
    "jowar": [2600, 2700, 2800, 2700, 2600, 2550, 2650, 2750, 2800, 2820, 2850, 2900],
}


@pytest.fixture
def offline_providers(monkeypatch):
    """Replace weather and market providers everywhere they are used."""

    def _apply(
        market: FakeMarketProvider | None = None,
        forecast: DataResult | None = None,
    ) -> None:
        market = market or FakeMarketProvider(DEFAULT_SERIES)
        forecast = forecast if forecast is not None else make_forecast()

        monkeypatch.setattr(
            "app.services.recommendation.get_forecast",
            lambda lat, lon, days=7: forecast,
        )
        monkeypatch.setattr(
            "app.services.recommendation.get_market_provider", lambda: market
        )
        monkeypatch.setattr(
            "app.api.weather.get_forecast", lambda lat, lon, days=7: forecast
        )
        monkeypatch.setattr("app.api.market.get_market_provider", lambda: market)

    _apply()
    return _apply


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------
@pytest.fixture
def farmer_client(client, offline_providers):
    """A registered farmer with a farm and a soil estimate already recorded.

    Returns a small helper object so tests can reach the token, headers and ids
    without repeating the setup.
    """

    class Farmer:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.farm_id: int = 0
            self.mobile: str = ""

        def get(self, path: str, **kwargs):
            return client.get(path, headers=self.headers, **kwargs)

        def post(self, path: str, **kwargs):
            return client.post(path, headers=self.headers, **kwargs)

        def patch(self, path: str, **kwargs):
            return client.patch(path, headers=self.headers, **kwargs)

        def put(self, path: str, **kwargs):
            return client.put(path, headers=self.headers, **kwargs)

    helper = Farmer()
    mobile = "9876543210"
    response = client.post(
        f"{API}/auth/register",
        json={"name": "Test Farmer", "mobile": mobile, "language": "mr", "pin": "1234"},
    )
    assert response.status_code == 201, response.text
    helper.headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    helper.mobile = mobile

    farm = client.post(
        f"{API}/farms",
        headers=helper.headers,
        json={
            "label": "Home farm",
            "land_area": 5,
            "land_unit": "ACRE",
            "irrigation_type": "PARTIAL",
            "water_source": "BOREWELL",
            "district": "Pune",
            "taluka": "Baramati",
            "village": "Testvillage",
            "latitude": 18.15,
            "longitude": 74.58,
        },
    )
    assert farm.status_code == 201, farm.text
    helper.farm_id = int(farm.json()["id"])

    soil = client.post(
        f"{API}/farms/{helper.farm_id}/soil/estimate",
        headers=helper.headers,
        json={
            "knows_soil_type": True,
            "soil_type": "BLACK",
            "after_rain_behaviour": "HOLDS_LONG",
        },
    )
    assert soil.status_code == 200, soil.text

    helper.client = client
    return helper
