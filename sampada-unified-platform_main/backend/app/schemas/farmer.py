"""Farmer, farm and onboarding schemas.

Enumerated fields use standardized codes documented as ``enum.<group>.<CODE>``
in the locale files. Clients send and receive codes, never translated text.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

IrrigationType = Literal["FULL", "PARTIAL", "RAINFED"]
WaterSource = Literal["WELL", "BOREWELL", "RIVER", "CANAL", "RAINWATER", "POND", "OTHER"]
LandUnit = Literal["ACRE", "HECTARE", "GUNTHA"]
Season = Literal["KHARIF", "RABI", "SUMMER", "PERENNIAL"]
SoilType = Literal["BLACK", "RED", "LATERITE", "ALLUVIAL", "SANDY_LOAM", "CLAY_LOAM", "SALINE"]
SoilRainBehaviour = Literal["DRAINS_QUICKLY", "HOLDS_SOME", "HOLDS_LONG", "DONT_KNOW"]

# Area conversion factors to hectares. Standard land measures; a guntha is
# 1/40 of an acre.
LAND_UNIT_TO_HA: dict[str, float] = {
    "ACRE": 0.404686,
    "HECTARE": 1.0,
    "GUNTHA": 0.0101171,
}


class FarmCreate(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    land_area: float = Field(gt=0, le=5000, description="area in the unit given by land_unit")
    land_unit: LandUnit = "ACRE"
    irrigation_type: IrrigationType
    water_source: WaterSource | None = None
    district: str | None = Field(default=None, max_length=80)
    taluka: str | None = Field(default=None, max_length=80)
    village: str | None = Field(default=None, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    def area_ha(self) -> float:
        return round(self.land_area * LAND_UNIT_TO_HA[self.land_unit], 4)


class FarmUpdate(BaseModel):
    label: str | None = None
    land_area: float | None = Field(default=None, gt=0, le=5000)
    land_unit: LandUnit | None = None
    irrigation_type: IrrigationType | None = None
    water_source: WaterSource | None = None
    district: str | None = None
    taluka: str | None = None
    village: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class FarmOut(BaseModel):
    id: int
    farmer_id: int
    label: str | None
    land_area_ha: float
    land_area_input: float | None
    land_unit: str
    irrigation_type: str
    water_source: str | None
    district: str | None
    taluka: str | None
    village: str | None
    latitude: float | None
    longitude: float | None
    created_at: datetime
    updated_at: datetime


class CropHistoryCreate(BaseModel):
    """Optional past-season record. Every field is optional by design.

    The design sessions identified the cold-start problem: a new farmer cannot
    be forced to enter years of history before the app will answer. So these are
    accepted whenever the farmer has them and skipped otherwise, and the
    confidence score reflects how much history actually exists.
    """

    crop_id: str
    season: Season | None = None
    year: int | None = Field(default=None, ge=1980, le=2100)
    area_ha: float | None = Field(default=None, gt=0)
    yield_quintal: float | None = Field(default=None, ge=0)
    cost_inr: float | None = Field(default=None, ge=0)
    sell_price_inr_per_quintal: float | None = Field(default=None, ge=0)
    market: str | None = None


class ResourcesPayload(BaseModel):
    """Non-land resources, used by the income-diversification module."""

    resources: list[Literal[
        "COW", "BUFFALO", "GOAT", "POULTRY", "FISH_POND",
        "TRACTOR", "MACHINERY", "STORAGE", "UNUSED_LAND",
    ]] = Field(default_factory=list)
    willing_crops: list[str] = Field(default_factory=list)
    unwilling_crops: list[str] = Field(default_factory=list)
    willing_to_try_new_crop: Literal["YES", "NO", "MAYBE"] | None = None
    biggest_problem: str | None = None


class OnboardingQuestionsOut(BaseModel):
    """The onboarding question set, localized.

    Returned by the API so the client never hardcodes farmer-facing text.
    Each entry carries a stable ``key``, the localized ``question``, a ``type``
    the client switches on, and any ``options`` for pickers.
    """

    language: str
    questions: list[dict[str, Any]]
