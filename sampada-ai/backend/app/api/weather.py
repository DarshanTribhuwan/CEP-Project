"""Weather route.

Returns real forecast data from Open-Meteo, or HTTP 503 with a structured
``data_unavailable`` body. It never substitutes a plausible-looking forecast.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ..core.config import settings
from ..core.errors import DataUnavailable
from ..providers import get_forecast
from ..schemas.weather import WeatherDayOut, WeatherOut
from .deps import current_farmer, owned_farm

router = APIRouter(tags=["weather"])


@router.get("/weather", response_model=WeatherOut, summary="Daily forecast for a location")
def weather(
    latitude: float | None = Query(default=None, ge=-90, le=90),
    longitude: float | None = Query(default=None, ge=-180, le=180),
    farm_id: int | None = Query(default=None, description="use a farm's stored coordinates"),
    days: int = Query(default=7, ge=1, le=16),
    farmer: dict[str, Any] = Depends(current_farmer),
) -> WeatherOut:
    notes: list[str] = []

    if farm_id is not None:
        farm = owned_farm(farm_id, farmer)
        latitude = farm.get("latitude")
        longitude = farm.get("longitude")
        if latitude is None or longitude is None:
            raise DataUnavailable(
                "this farm has no coordinates stored, so no weather can be retrieved for it"
            )
        place_label = farm.get("village") or farm.get("taluka") or farm.get("district")
    elif latitude is None or longitude is None:
        # Fall back to the configured default location, and say so plainly rather
        # than implying it is the caller's own position.
        latitude = float(settings.get("weather.default_lat", 18.5204))
        longitude = float(settings.get("weather.default_lon", 73.8567))
        place_label = str(settings.get("weather.default_town", "Pune"))
        notes.append("no coordinates supplied; these are the configured default location")
    else:
        place_label = None

    result = get_forecast(latitude, longitude, days=days)
    if not result.available or result.value is None:
        raise DataUnavailable(result.provenance.unavailable_reason or "weather unavailable")

    forecast = result.value
    provenance = result.provenance.model_copy(update={"notes": result.provenance.notes + notes})

    return WeatherOut(
        latitude=forecast.latitude,
        longitude=forecast.longitude,
        timezone=forecast.timezone,
        place_label=place_label,
        days=[
            WeatherDayOut(
                day=day.day,
                temp_max_c=day.temp_max_c,
                temp_min_c=day.temp_min_c,
                rainfall_mm=day.rainfall_mm,
            )
            for day in forecast.days
        ],
        total_rainfall_mm=forecast.total_rainfall_mm,
        mean_temp_max_c=forecast.mean_temp_max_c,
        provenance=provenance,
    )
