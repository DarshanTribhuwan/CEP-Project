"""External data providers.

Nothing else in the codebase may call an external HTTP API directly. Modules go
through this package so that availability and provenance stay auditable in one
place.

``providers.http`` is the single outbound transport: it retries transient
failures, caches every successful response on disk, and serves the last real
response (clearly labelled as cached and, if applicable, stale) when a source is
unreachable. ``SAMPADA_OFFLINE=1`` disables outbound calls entirely.
"""

from .base import DataResult
from .http import HttpOutcome, describe_offline_state, fetch_json, offline_mode
from .market import (
    DataGovAgmarknetProvider,
    MarketPriceRecord,
    MarketProvider,
    SampleMarketProvider,
    get_market_provider,
)
from .soil_geo import lookup_soil_type
from .weather import WeatherDay, WeatherForecast, get_forecast

__all__ = [
    "DataResult",
    "DataGovAgmarknetProvider",
    "HttpOutcome",
    "MarketPriceRecord",
    "MarketProvider",
    "SampleMarketProvider",
    "describe_offline_state",
    "fetch_json",
    "get_market_provider",
    "get_forecast",
    "offline_mode",
    "WeatherDay",
    "WeatherForecast",
    "lookup_soil_type",
]
