"""Agronomic suitability scoring.

Four independent questions, each answered from data the system actually has:

    soil    - does the estimated soil distribution suit this crop?
    water   - can the farm's water situation meet this crop's need?
    season  - is this crop normally grown now?
    weather - does the current forecast fall inside the crop's tolerable range?

Each returns ``None`` when its input is missing, so the caller can redistribute
weight instead of inventing a neutral pass. Constants are unvalidated defaults.

Soil scoring reads the two lists that ``scripts/build_crop_table.py`` derives
from FAO EcoCrop's *optimal* and *absolute* soil-texture ranges. Their meaning is
recorded in the generated table's ``_meta.scoring_semantics`` and is deliberately
two-tier:

    soil_types_optimal    the crop performs well here        -> 1.0
    soil_types_tolerated  the crop can be grown here         -> 0.7
    unlisted                                              -> 0.0
    optimal is null       the source publishes no optimal texture for this
                          crop, so nothing is claimed: the soil factor is
                          reported unavailable and its weight redistributes.

That last case is rice, and it is the reason this module no longer awards credit
for ``water_retention`` similarity. That old rule was the project's own
invention; EcoCrop's optimal/absolute split replaces it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..providers.weather import WeatherForecast
from .crops import Crop

# Water capacity implied by the irrigation situation, on the same 0..1 scale as
# Crop.water_score. These are judgments, not measurements.
IRRIGATION_BASE = {"FULL": 0.90, "PARTIAL": 0.60, "RAINFED": 0.30}
WATER_SOURCE_ADJUST = {
    "BOREWELL": 0.10,
    "CANAL": 0.08,
    "RIVER": 0.05,
    "WELL": 0.05,
    "POND": -0.05,
    "RAINWATER": -0.10,
    "OTHER": 0.0,
}

# Credit for a soil in the crop's tolerated list, mirroring
# _meta.scoring_semantics in app/data/crops.json. UNVALIDATED DEFAULT.
OPTIMAL_SOIL_CREDIT = 1.0
TOLERATED_SOIL_CREDIT = 0.7


@dataclass(slots=True)
class SuitabilityResult:
    soil: float | None = None
    water: float | None = None
    season: float | None = None
    weather: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def agronomic_mean(self) -> float | None:
        known = [
            value
            for value in (self.soil, self.water, self.season, self.weather)
            if value is not None
        ]
        return round(sum(known) / len(known), 4) if known else None


def farm_water_capacity(irrigation_type: str, water_source: str | None) -> float:
    base = IRRIGATION_BASE.get(irrigation_type, 0.30)
    adjusted = base + WATER_SOURCE_ADJUST.get(water_source or "", 0.0)
    return round(max(0.05, min(1.0, adjusted)), 4)


def soil_compatibility(soil_id: str, crop: Crop) -> float | None:
    """Credit for one soil type, or ``None`` when nothing is claimed for the crop.

    ``None`` means the cited source records no *optimal* texture for this crop,
    which is not the same as "no soil suits it". Scoring it 0.0 would silently
    punish the crop for a gap in the source.
    """
    if crop.soil_types_optimal is None:
        return None
    if soil_id in crop.soil_types_optimal:
        return OPTIMAL_SOIL_CREDIT
    if soil_id in crop.soil_types_tolerated:
        return TOLERATED_SOIL_CREDIT
    return 0.0


def soil_match(distribution: dict[str, float] | None, crop: Crop) -> float | None:
    """Expected compatibility under the soil probability distribution.

    Returns ``None`` - not 0.0 - when the distribution is empty or zero, so the
    caller can distinguish "we do not know the soil" from "the soil is wrong".
    """
    if not distribution:
        return None
    if crop.soil_types_optimal is None:
        return None
    total = sum(value for value in distribution.values() if value > 0)
    if total <= 0:
        return None
    expected = sum(
        (value / total) * float(soil_compatibility(soil_id, crop) or 0.0)
        for soil_id, value in distribution.items()
        if value > 0
    )
    return round(expected, 4)


def water_match(capacity: float, crop: Crop) -> float:
    """Meet-the-need score. A surplus is not penalised; a deficit is."""
    need = crop.water_score
    if need <= 0:
        return 1.0
    if capacity >= need:
        return 1.0
    return round(capacity / need, 4)


def season_match(season: str, crop: Crop) -> float:
    if "PERENNIAL" in crop.seasons:
        return 1.0
    return 1.0 if season in crop.seasons else 0.0


def weather_match(forecast: WeatherForecast | None, crop: Crop) -> float | None:
    """Fraction of forecast days whose max temperature is inside the crop range.

    The optimal band is used when the source publishes one, falling back to the
    absolute (survival) band. Only temperature is scored here; the forecast's
    rainfall is not turned into a water-requirement figure, because doing so
    properly needs reference evapotranspiration, which this version does not
    compute.
    """
    if forecast is None or not forecast.days:
        return None
    bounds = crop.temp_bounds_optimal or crop.temp_bounds_absolute
    if bounds is None:
        return None
    low, high = bounds
    observed = [day.temp_max_c for day in forecast.days if day.temp_max_c is not None]
    if not observed:
        return None
    inside = sum(1 for temp in observed if low <= temp <= high)
    return round(inside / len(observed), 4)


def evaluate(
    crop: Crop,
    soil_distribution: dict[str, float] | None,
    irrigation_type: str,
    water_source: str | None,
    season: str,
    forecast: WeatherForecast | None,
) -> SuitabilityResult:
    capacity = farm_water_capacity(irrigation_type, water_source)
    result = SuitabilityResult(
        soil=soil_match(soil_distribution, crop),
        water=water_match(capacity, crop),
        season=season_match(season, crop),
        weather=weather_match(forecast, crop),
    )
    if result.soil is None:
        if soil_distribution and crop.soil_types_optimal is None:
            result.notes.append(
                "soil preference is not recorded for this crop in the cited source, "
                "so soil was not scored"
            )
        else:
            result.notes.append("soil suitability could not be scored (no soil estimate on file)")
    if result.weather is None:
        result.notes.append("weather suitability could not be scored (no forecast available)")
    if result.water is not None and result.water < 1.0:
        result.notes.append(
            f"water deficit: farm capacity {capacity:.2f} vs crop need {crop.water_score:.2f}"
        )
    if result.season == 0.0:
        result.notes.append(f"{crop.id} is not normally grown in {season}")
    return result
