"""Weather schemas."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from ..core.errors import DataProvenance


class WeatherDayOut(BaseModel):
    day: date
    temp_max_c: float | None
    temp_min_c: float | None
    rainfall_mm: float | None


class WeatherOut(BaseModel):
    latitude: float
    longitude: float
    timezone: str | None
    place_label: str | None = None
    days: list[WeatherDayOut] = Field(default_factory=list)
    total_rainfall_mm: float | None = None
    mean_temp_max_c: float | None = None
    provenance: DataProvenance
