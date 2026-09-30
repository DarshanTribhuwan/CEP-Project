"""Build ``app/data/crops.json`` from cited sources.

Inputs (all on disk, all re-derivable):

  app/data/sources/ecocrop.json          FAO EcoCrop records, fetched by fetch_ecocrop.py
  app/data/crop_agronomy_curated.json    the fields that are judgement, not measurement
  app/data/soil_types.json               texture/drainage vocabulary the match is expressed in
  app/locales/<lang>.json                crop display names (crop.<id>)

The FAO-56 values (Tables 11 and 12) are transcribed below, because they are a
handful of numbers read off a published PDF table rather than a bulk dataset.
Each transcription records the exact table row so a reviewer can check it.

What this script replaced
-------------------------
The previous crops.json held hand-written "plausible magnitude" values marked
UNVERIFIED, including two fields that drove the recommendation - water_need_class
and suitable_soil_types. Both are now derived from sources, and the derivation is
recorded per crop.

Run: python scripts/fetch_ecocrop.py && python scripts/build_crop_table.py
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "app" / "data"
SOURCES_DIR = DATA_DIR / "sources"
LOCALES_DIR = BACKEND_DIR / "app" / "locales"

ECOCROP_SOURCE = SOURCES_DIR / "ecocrop.json"
CURATED_SOURCE = DATA_DIR / "crop_agronomy_curated.json"
SOIL_SOURCE = DATA_DIR / "soil_types.json"
POP_SOURCE = SOURCES_DIR / "pop_transcribed.json"
OUTPUT = DATA_DIR / "crops.json"

FAO56_URL = "https://www.fao.org/4/x0490e/x0490e0b.htm"
FAO56_CITATION = "FAO Irrigation and Drainage Paper 56, Chapter 6 (Tables 11 and 12)"

# ---------------------------------------------------------------------------
# FAO-56 Table 11 - length of the total growing period, in days.
#
# ``rows`` lists the table rows used, with the region/planting label exactly as
# printed. ``excluded`` lists rows deliberately not used and why - a choice like
# this has to be visible, otherwise it is just another silent edit.
# ---------------------------------------------------------------------------
FAO56_DURATIONS: dict[str, dict[str, Any]] = {
    "soybean": {
        "rows": [
            ("Tropics (Dec planting)", 85),
            ("Central USA (May)", 140),
            ("Japan (June)", 150),
        ],
        "excluded": [],
    },
    "tur": {"rows": [], "excluded": [], "fallback": "ecocrop_crop_cycle"},
    "cotton": {
        "rows": [
            ("Egypt/Pakistan/California (Mar-May)", 195),
            ("California Desert (Mar)", 225),
            ("Yemen (Sept)", 195),
            ("Texas (Apr)", 180),
        ],
        "excluded": [],
    },
    "maize": {
        "rows": [
            ("East Africa, alt. (Apr)", 180),
            ("Arid Climate (Dec/Jan)", 140),
            ("Nigeria, humid (June)", 125),
            ("India, dry and cool (Oct)", 125),
            ("Spain (Apr)", 150),
            ("Idaho USA (Apr)", 170),
        ],
        "excluded": [],
    },
    "jowar": {
        "rows": [
            ("Sorghum, USA/Pakistan/Mediterranean (May/June)", 130),
            ("Sorghum, Arid Region (Mar/Apr)", 140),
        ],
        "excluded": [],
    },
    "bajra": {
        "rows": [("Millet, Pakistan (June)", 105), ("Millet, Central USA (Apr)", 140)],
        "excluded": [],
    },
    "groundnut": {
        "rows": [
            ("Dry season, West Africa", 130),
            ("High Latitudes", 140),
            ("Mediterranean (May; May/June)", 140),
        ],
        "excluded": [],
    },
    "onion": {
        "rows": [("Onion (dry), Mediterranean (Apr)", 150), ("Onion (dry), Arid Region / California", 210)],
        "excluded": [("Onion (green)", "not the dry-bulb crop"), ("Onion (seed)", "not the dry-bulb crop")],
    },
    "sugarcane": {
        "rows": [
            ("Sugarcane, virgin, Low Latitudes", 405),
            ("Sugarcane, virgin, Tropics", 480),
        ],
        "excluded": [
            ("Sugarcane, virgin, Hawaii USA", "720-day Hawaiian cycle is not representative for Maharashtra"),
            ("Sugarcane, ratoon", "ratoon cycles are a different crop year and are not modelled in the MVP"),
        ],
    },
    "rice": {
        "rows": [("Tropics; Mediterranean (Dec; May)", 150), ("Tropics (May)", 180)],
        "excluded": [],
    },
    "wheat": {
        "rows": [
            ("Barley/Oats/Wheat, Central India (Nov)", 120),
            ("Barley/Oats/Wheat, 35-45 deg latitude (Mar/Apr)", 135),
            ("Barley/Oats/Wheat, East Africa (July)", 150),
            ("Barley/Oats/Wheat (Apr)", 130),
            ("Barley/Oats/Wheat (Nov)", 200),
            ("Barley/Oats/Wheat, California Desert (Dec)", 160),
        ],
        "excluded": [
            ("Winter Wheat, California USA (Dec)", "dormant-season row; Maharashtra wheat is not winter-dormant"),
            ("Winter Wheat, Mediterranean (Nov)", "dormant-season row; Maharashtra wheat is not winter-dormant"),
            ("Winter Wheat, Idaho USA (Oct)", "dormant-season row; Maharashtra wheat is not winter-dormant"),
        ],
    },
    "gram": {"rows": [], "excluded": [], "fallback": "ecocrop_crop_cycle"},
    "moong": {"rows": [("Green gram, cowpeas, Mediterranean (Mar)", 110)], "excluded": []},
    "urad": {"rows": [], "excluded": [], "fallback": "ecocrop_crop_cycle"},
    "sunflower": {"rows": [("Sunflower, Mediterranean/California (Apr/May)", 130)], "excluded": []},
    "safflower": {
        "rows": [
            ("Safflower, California USA (Apr)", 125),
            ("Safflower, High Latitudes (Mar)", 145),
            ("Safflower, Arid Region (Oct/Nov)", 190),
        ],
        "excluded": [],
    },
}

# ---------------------------------------------------------------------------
# FAO-56 Table 12 - single (time-averaged) crop coefficients and max crop height.
# Where the table prints a range, the range is kept; the scalar used for the
# water-need derivation is its midpoint.
# ---------------------------------------------------------------------------
FAO56_KC: dict[str, dict[str, Any]] = {
    "soybean": {"row": "Soybeans", "ini": [0.40, 0.40], "mid": [1.15, 1.15], "end": [0.50, 0.50], "height_m": [0.5, 1.0]},
    "tur": {
        "row": "Beans, dry and Pulses",
        "ini": [0.40, 0.40],
        "mid": [1.15, 1.15],
        "end": [0.35, 0.35],
        "height_m": [0.4, 0.4],
        "proxy": "FAO-56 Table 12 has no pigeon-pea row; the dry-beans/pulses row is used as a proxy.",
    },
    "cotton": {"row": "Cotton", "ini": [0.35, 0.35], "mid": [1.15, 1.20], "end": [0.50, 0.70], "height_m": [1.2, 1.5]},
    "maize": {
        "row": "Maize, Field (grain)",
        "ini": [0.30, 0.30],
        "mid": [1.20, 1.20],
        "end": [0.35, 0.60],
        "height_m": [2.0, 2.0],
    },
    "jowar": {"row": "Sorghum - grain", "ini": [0.30, 0.30], "mid": [1.00, 1.10], "end": [0.55, 0.55], "height_m": [1.0, 2.0]},
    "bajra": {"row": "Millet", "ini": [0.30, 0.30], "mid": [1.00, 1.00], "end": [0.30, 0.30], "height_m": [1.5, 1.5]},
    "groundnut": {"row": "Groundnut (Peanut)", "ini": [0.40, 0.40], "mid": [1.15, 1.15], "end": [0.60, 0.60], "height_m": [0.4, 0.4]},
    "onion": {"row": "Onions - dry", "ini": [0.70, 0.70], "mid": [1.05, 1.05], "end": [0.75, 0.75], "height_m": [0.4, 0.4]},
    "sugarcane": {"row": "Sugar Cane", "ini": [0.40, 0.40], "mid": [1.25, 1.25], "end": [0.75, 0.75], "height_m": [3.0, 3.0]},
    "rice": {"row": "Rice", "ini": [1.05, 1.05], "mid": [1.20, 1.20], "end": [0.60, 0.90], "height_m": [1.0, 1.0]},
    "wheat": {
        "row": "Spring Wheat",
        "ini": [0.30, 0.30],
        "mid": [1.15, 1.15],
        "end": [0.25, 0.40],
        "height_m": [1.0, 1.0],
        "proxy": "Kc ini is the Cereals group value (0.3). The Winter Wheat row is not used because Maharashtra wheat is not winter-dormant.",
    },
    "gram": {"row": "Chick pea", "ini": [0.40, 0.40], "mid": [1.00, 1.00], "end": [0.35, 0.35], "height_m": [0.4, 0.4]},
    "moong": {
        "row": "Green Gram and Cowpeas",
        "ini": [0.40, 0.40],
        "mid": [1.05, 1.05],
        "end": [0.35, 0.60],
        "height_m": [0.4, 0.4],
    },
    "urad": {
        "row": "Beans, dry and Pulses",
        "ini": [0.40, 0.40],
        "mid": [1.15, 1.15],
        "end": [0.35, 0.35],
        "height_m": [0.4, 0.4],
        "proxy": "FAO-56 Table 12 has no black-gram row; the dry-beans/pulses row is used as a proxy.",
    },
    "sunflower": {"row": "Sunflower", "ini": [0.35, 0.35], "mid": [1.00, 1.15], "end": [0.35, 0.35], "height_m": [2.0, 2.0]},
    "safflower": {"row": "Safflower", "ini": [0.35, 0.35], "mid": [1.00, 1.15], "end": [0.25, 0.25], "height_m": [0.8, 0.8]},
}

# Water-need class boundaries for seasonal_water_index = Kc mid x mid season days.
# These are the tercile midpoints of the 16-crop table produced by this script,
# pinned so that adding a crop does not silently reclassify the existing ones.
# The assertion in build() fails if the pinned values stop matching the terciles.
WATER_CLASS_BOUNDS = (137.4, 186.0)
WATER_BOUNDS_NOTE = (
    "LOW below 137.4, MEDIUM to 186.0, HIGH above. Boundaries are the tercile "
    "midpoints of this 16-crop table, pinned so that adding a crop does not "
    "reclassify existing ones."
)

CROP_ORDER = list(FAO56_DURATIONS)

TOLERATED_CREDIT = 0.7
OPTIMAL_CREDIT = 1.0

WIDE = "wide"

# Our soil table has one soil whose defining problem is a chemistry, not a
# texture: SALINE. EcoCrop records salinity separately from texture, so texture
# alone would happily match soybean to saline soil. These rules put the sourced
# salinity limit back in.
SALINE_SOIL_ID = "SALINE"


def salinity_is_low_only(levels: list[str]) -> bool:
    """True when EcoCrop's only acceptable salinity band is the low one."""
    return bool(levels) and all("low" in level.lower() for level in levels)


def load_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def midpoint(values: list[float]) -> float:
    return statistics.fmean(values)


def soils_by_texture(soil_types: list[dict[str, Any]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for soil in soil_types:
        grouped.setdefault(soil["texture_class"], []).append(soil["id"])
    return grouped


def expand_textures(textures: list[str], grouped: dict[str, list[str]], soil_types: list[dict[str, Any]]) -> list[str] | None:
    """Map EcoCrop texture classes onto our soil codes.

    Returns None for ``wide`` and for an empty list, both of which mean the
    source records no texture preference - which is not the same as "every soil
    is ideal", so the caller marks the soil factor unavailable instead of
    awarding a full score nobody justified.
    """
    if not textures:
        return None
    if any(texture.lower() == WIDE for texture in textures):
        return None
    selected: list[str] = []
    for texture in textures:
        for soil_id in grouped.get(texture.lower(), []):
            if soil_id not in selected:
                selected.append(soil_id)
    return selected or None


def all_soil_ids(soil_types: list[dict[str, Any]]) -> list[str]:
    return [soil["id"] for soil in soil_types]


def build_duration(crop_id: str, ecocrop: dict[str, Any]) -> dict[str, Any]:
    spec = FAO56_DURATIONS[crop_id]
    rows: list[tuple[str, int]] = list(spec["rows"])
    excluded = list(spec.get("excluded", []))

    if rows:
        totals = [days for _, days in rows]
        return {
            "duration_days": [min(totals), max(totals)],
            "basis": "fao56_table11",
            "citation": (
                "FAO-56 Table 11, row(s): "
                + "; ".join(f"{label} = {days} d" for label, days in rows)
            ),
            "excluded_rows": [f"{label} - {reason}" for label, reason in excluded],
            "caveat": (
                "FAO-56 Table 11 is a cross-region envelope; the table itself states the "
                "lengths are examples from the listed regions and that local observations "
                "should be used. Read this as a plausible span, not a local figure."
            ),
        }

    cycle = ecocrop.get("crop_cycle_days")
    if not cycle:
        raise SystemExit(f"{crop_id}: no FAO-56 duration row and no EcoCrop crop cycle")
    return {
        "duration_days": [int(cycle[0]), int(cycle[1])],
        "basis": "ecocrop_crop_cycle",
        "citation": f"EcoCrop record {ecocrop.get('ecocrop_id')} 'Crop cycle' = {int(cycle[0])}-{int(cycle[1])} days",
        "excluded_rows": [],
        "caveat": (
            "FAO-56 Table 11 has no row for this crop, so EcoCrop's global crop-cycle "
            "envelope is used instead. It is wider than a real local season."
        ),
    }


def build_water_need(
    crop_id: str, duration: list[int], kc: dict[str, Any]
) -> dict[str, Any]:
    kc_mid = midpoint([float(x) for x in kc["mid"]])
    mid_days = midpoint([float(x) for x in duration])
    index = round(kc_mid * mid_days, 2)
    low_max, medium_max = WATER_CLASS_BOUNDS
    if index < low_max:
        water_class = "LOW"
    elif index <= medium_max:
        water_class = "MEDIUM"
    else:
        water_class = "HIGH"

    entry: dict[str, Any] = {
        "water_need_class": water_class,
        "basis": "derived_from_fao56",
        "index": index,
        "method": (
            "seasonal_water_index = Kc mid (FAO-56 Table 12) x mid season days "
            "(FAO-56 Table 11). A relative consumptive-use indicator, not a millimetre figure."
        ),
        "bounds": WATER_BOUNDS_NOTE,
        "kc_mid_used": kc_mid,
        "mid_season_days_used": mid_days,
        "limitations": (
            "Kc x duration does not capture puddling and percolation losses (rice), "
            "residual-moisture rabi cropping on stored soil water (safflower, gram), or "
            "conveyance losses. Treat the class as a coarse ordering, not a water budget."
        ),
    }
    if kc.get("proxy"):
        entry["kc_proxy"] = kc["proxy"]
    return entry


def build_duration_basis_label(entry: dict[str, Any]) -> str:
    return entry["basis"]


def main() -> int:
    # This builder is entirely offline: it reads the fetched EcoCrop JSON and the
    # transcribed FAO-56 tables (below) and never opens a socket. If the fetched
    # source is absent, say what to do rather than raising a bare FileNotFoundError.
    if not ECOCROP_SOURCE.exists():
        raise SystemExit(
            f"missing {ECOCROP_SOURCE.relative_to(BACKEND_DIR)}\n"
            "Run:  python scripts/fetch_ecocrop.py   (once, on a machine with internet "
            "access; afterwards this builder works offline from the cached HTML)"
        )

    ecocrop_all = load_json(ECOCROP_SOURCE)["crops"]
    curated_all = load_json(CURATED_SOURCE)["crops"]
    soil_payload = load_json(SOIL_SOURCE)
    soil_types = soil_payload["soil_types"]
    grouped = soils_by_texture(soil_types)
    every_soil = all_soil_ids(soil_types)
    pop_all = load_json(POP_SOURCE)["crops"] if POP_SOURCE.exists() else {}

    locales: dict[str, dict[str, str]] = {}
    for lang in ("en", "mr", "hi"):
        locales[lang] = load_json(LOCALES_DIR / f"{lang}.json")

    missing_order = [crop_id for crop_id in CROP_ORDER if crop_id not in curated_all]
    if missing_order:
        raise SystemExit(f"crop_agronomy_curated.json is missing: {missing_order}")

    crops: list[dict[str, Any]] = []
    local_review: list[dict[str, str]] = []
    indices: list[float] = []

    for crop_id in CROP_ORDER:
        ecocrop = ecocrop_all.get(crop_id)
        if not ecocrop:
            raise SystemExit(f"{crop_id}: no EcoCrop record; run scripts/fetch_ecocrop.py")
        curated = curated_all[crop_id]
        kc = FAO56_KC[crop_id]

        # --- soil: texture drives the match, drainage is reported and checked ---
        texture_optimal = ecocrop.get("soil_texture_optimal") or []
        texture_absolute = ecocrop.get("soil_texture_absolute") or []
        texture_source = f"EcoCrop record {ecocrop['ecocrop_id']}"
        fallback = ecocrop.get("fallback") or {}
        if fallback and not texture_optimal:
            fallback_data = fallback.get("data", {})
            texture_optimal = fallback_data.get("soil_texture_optimal") or []
            texture_absolute = fallback_data.get("soil_texture_absolute") or []
            texture_source = (
                f"EcoCrop record {fallback['ecocrop_id']} "
                f"({fallback.get('scientific_name')}) - substituted because record "
                f"{ecocrop['ecocrop_id']} has no soil texture data"
            )
            local_review.append(
                {
                    "crop": crop_id,
                    "field": "soil_texture",
                    "issue": (
                        f"Texture came from a second EcoCrop record ({fallback.get('scientific_name')}). "
                        "A crop specialist should confirm the substitution."
                    ),
                }
            )

        salinity_optimal = ecocrop.get("soil_salinity_optimal") or []
        salinity_absolute = ecocrop.get("soil_salinity_absolute") or []

        optimal_soils = expand_textures(texture_optimal, grouped, soil_types)
        absolute_soils = expand_textures(texture_absolute, grouped, soil_types)
        if absolute_soils is None:
            absolute_soils = every_soil

        if salinity_is_low_only(salinity_optimal) and optimal_soils is not None:
            optimal_soils = [s for s in optimal_soils if s != SALINE_SOIL_ID]
            if not optimal_soils:
                optimal_soils = None
        # An unrecorded absolute band is not evidence that saline soil is fine, so
        # fall back to the optimal bands rather than granting the tolerance.
        salinity_absolute_bands = salinity_absolute or salinity_optimal
        if salinity_is_low_only(salinity_absolute_bands):
            absolute_soils = [s for s in absolute_soils if s != SALINE_SOIL_ID]

        tolerant_soils = [s for s in absolute_soils if optimal_soils is None or s not in optimal_soils]

        # A local package-of-practices source can override the global EcoCrop
        # envelope. Recorded per crop in the pop block, applied to the lists the
        # scorer actually reads, and provenance-stamped so the override is
        # visible in every API response that carries soil_types_optimal.
        pop = pop_all.get(crop_id) or {}
        local_adds = pop.get("soil_optimal_add") or []
        if local_adds and optimal_soils is not None:
            for soil_id in local_adds:
                if soil_id in every_soil and soil_id not in optimal_soils:
                    optimal_soils = list(optimal_soils) + [soil_id]
            tolerant_soils = [s for s in tolerant_soils if s not in optimal_soils]

        drainage_required = ecocrop.get("soil_drainage_optimal") or []
        if fallback and not drainage_required:
            drainage_required = (fallback.get("data") or {}).get("soil_drainage_optimal") or []
        if optimal_soils and drainage_required:
            matched = [
                soil["id"]
                for soil in soil_types
                if soil["id"] in optimal_soils and soil["ecocrop_drainage"] in drainage_required
            ]
            if not matched:
                local_review.append(
                    {
                        "crop": crop_id,
                        "field": "soil_drainage",
                        "issue": (
                            "EcoCrop requires "
                            + ", ".join(drainage_required)
                            + ", which no soil in this table records for this crop's texture class. "
                            "The MVP treats drainage as advisory only."
                        ),
                    }
                )
        if optimal_soils is None:
            local_review.append(
                {
                    "crop": crop_id,
                    "field": "soil_texture",
                    "issue": (
                        "EcoCrop records no texture preference ('wide' or blank), so soil "
                        "suitability is reported unavailable for this crop rather than scored "
                        "full marks for every soil."
                    ),
                }
            )

        # --- names come from the locale files, so they are covered by review ---
        for lang in locales:
            if f"crop.{crop_id}" not in locales[lang]:
                raise SystemExit(f"locale {lang}.json is missing crop.{crop_id}")

        duration = build_duration(crop_id, ecocrop)
        water = build_water_need(crop_id, duration["duration_days"], kc)
        indices.append(water["index"])

        entry: dict[str, Any] = {
            "id": crop_id,
            "scientific_name": ecocrop.get("scientific_name"),
            "seasons": curated["seasons"],
            "duration_days": duration["duration_days"],
            "water_need_class": water["water_need_class"],
            "temp_optimal_c": ecocrop.get("temp_optimal_c"),
            "temp_absolute_c": ecocrop.get("temp_absolute_c"),
            "rainfall_optimal_mm": ecocrop.get("rainfall_optimal_mm"),
            "rainfall_absolute_mm": ecocrop.get("rainfall_absolute_mm"),
            "ph_optimal": ecocrop.get("ph_optimal"),
            "ph_absolute": ecocrop.get("ph_absolute"),
            "soil_texture_optimal": texture_optimal,
            "soil_texture_absolute": texture_absolute,
            "soil_drainage_optimal": drainage_required,
            "soil_salinity_optimal": salinity_optimal,
            "soil_salinity_absolute": salinity_absolute,
            "soil_types_optimal": optimal_soils,
            "soil_types_tolerated": tolerant_soils,
            "kc": {"ini": kc["ini"], "mid": kc["mid"], "end": kc["end"], "max_height_m": kc["height_m"], "row": kc["row"]},
            "max_height_m": kc["height_m"],
            "investment_level": curated["investment_level"],
            "major_risks": curated["major_risks"],
            **({"pop": pop} if pop else {}),
            "provenance": {
                "scientific_name": {"status": "SOURCED", "citation": f"EcoCrop record {ecocrop['ecocrop_id']}"},
                "seasons": {"status": "CURATED_CONVENTION", "citation": "app/data/crop_agronomy_curated.json"},
                "duration_days": {"status": "SOURCED", "citation": duration["citation"], "caveat": duration["caveat"]},
                "water_need_class": {"status": "DERIVED", "method": water["method"], "limitations": water["limitations"]},
                "temp_optimal_c": {"status": "SOURCED", "citation": f"EcoCrop record {ecocrop['ecocrop_id']} 'Temperat. requir.' Optimal"},
                "temp_absolute_c": {"status": "SOURCED", "citation": f"EcoCrop record {ecocrop['ecocrop_id']} 'Temperat. requir.' Absolute"},
                "rainfall_optimal_mm": {"status": "SOURCED", "citation": f"EcoCrop record {ecocrop['ecocrop_id']} 'Rainfall (annual)' Optimal"},
                "rainfall_absolute_mm": {"status": "SOURCED", "citation": f"EcoCrop record {ecocrop['ecocrop_id']} 'Rainfall (annual)' Absolute"},
                "ph_optimal": {"status": "SOURCED", "citation": f"EcoCrop record {ecocrop['ecocrop_id']} 'Soil PH' Optimal"},
                "ph_absolute": {"status": "SOURCED", "citation": f"EcoCrop record {ecocrop['ecocrop_id']} 'Soil PH' Absolute"},
                "soil_texture": {"status": "SOURCED", "citation": texture_source},
                "soil_drainage_optimal": {"status": "SOURCED", "citation": "EcoCrop 'Soil drainage' Optimal - advisory only in the MVP"},
                "soil_salinity": {
                    "status": "SOURCED",
                    "citation": "EcoCrop 'Soil salinity' Optimal/Absolute",
                    "applied": (
                        "SALINE soil is excluded from soil_types_optimal whenever the crop's optimal "
                        "salinity band is low only, and excluded from soil_types_tolerated when the "
                        "absolute band is also low only."
                    ),
                },
                "soil_types_optimal": {
                    "status": "DERIVED",
                    "method": "Soils whose texture_class appears in EcoCrop's Optimal texture list. eco_credit " + str(OPTIMAL_CREDIT),
                },
                "soil_types_tolerated": {
                    "status": "DERIVED",
                    "method": "Soils whose texture_class appears only in EcoCrop's Absolute texture list. eco_credit " + str(TOLERATED_CREDIT),
                },
                "kc": {"status": "SOURCED", "citation": f"FAO-56 Table 12, row '{kc['row']}'"},
                "max_height_m": {"status": "SOURCED", "citation": f"FAO-56 Table 12, row '{kc['row']}'"},
                "investment_level": {"status": "CURATED_UNVALIDATED", "citation": "app/data/crop_agronomy_curated.json"},
                "major_risks": {"status": "CURATED_UNVALIDATED", "citation": "app/data/crop_agronomy_curated.json"},
                **(
                    {
                        "soil_types_optimal": {
                            "status": "SOURCED_LOCAL_OVERRIDE",
                            "citation": (
                                "app/data/sources/pop_transcribed.json ("
                                + crop_id
                                + ".soil_evidence) - Maharashtra package-of-practices; EcoCrop envelope overridden"
                            ),
                            "added": local_adds,
                        }
                    }
                    if local_adds
                    else {}
                ),
            },
            "kc_proxy_note": kc.get("proxy"),
            "sourcing_note": ecocrop.get("sourcing_note"),
        }

        # Known disagreements between the global source and Maharashtra practice.
        # RESOLVED by the PoP transcription (pop_transcribed.json, soybean:
        # NFSM 2018 records vertisols as where soybean performs best), so the
        # old flag is deliberately NOT re-emitted here.
        if crop_id == "soybean" and "soybean" not in pop_all:
            local_review.append(
                {
                    "crop": crop_id,
                    "field": "soil_types_optimal",
                    "issue": (
                        "EcoCrop's optimal texture for soybean is medium/organic, so black cotton "
                        "soil scores as tolerated (0.7) rather than optimal. Soybean is a major "
                        "kharif crop on black soil in Vidarbha, so this is a real conflict between "
                        "the global species envelope and local practice. Needs a Maharashtra "
                        "package-of-practices source to settle."
                    ),
                }
            )
        if kc.get("proxy"):
            local_review.append(
                {
                    "crop": crop_id,
                    "field": "kc",
                    "issue": kc["proxy"],
                }
            )

        crops.append(entry)

    # The pinned water-need bounds must still be the terciles of this table.
    ordered = sorted(indices)
    if len(ordered) >= 12:
        expected = (
            round((ordered[4] + ordered[5]) / 2, 1),
            round((ordered[10] + ordered[11]) / 2, 1),
        )
        if any(abs(a - b) > 0.6 for a, b in zip(expected, WATER_CLASS_BOUNDS)):
            raise SystemExit(
                f"WATER_CLASS_BOUNDS {WATER_CLASS_BOUNDS} no longer match the tercile "
                f"midpoints of this table {expected}. Re-derive them deliberately and "
                f"record the change, do not just bump the numbers."
            )

    payload = {
        "_meta": {
            "purpose": "Crop reference table read by app/services/crops.py and scored by app/services/suitability.py.",
            "schema_version": 2,
            "generated_by": "scripts/build_crop_table.py",
            "do_not_hand_edit": "Generated file. Edit the sources listed below and re-run the builder.",
            "sources": {
                "ecocrop": {
                    "citation": "FAO EcoCrop crop environmental requirements database, Land & Water Division",
                    "url": "https://ecocrop.apps.fao.org/ecocrop/srv/en/cropList",
                    "fetched_by": "scripts/fetch_ecocrop.py",
                    "cached_at": "app/data/sources/ecocrop.json and app/data/sources/ecocrop_html/",
                },
                "fao56": {
                    "citation": FAO56_CITATION,
                    "url": FAO56_URL,
                    "note": "Tables 11 and 12 are transcribed into scripts/build_crop_table.py with the exact row labels used.",
                },
                "curated": {
                    "citation": "app/data/crop_agronomy_curated.json - judgement fields, kept separate and labelled",
                },
                "soil_vocabulary": {
                    "citation": "app/data/soil_types.json - texture_class and ecocrop_drainage",
                },
                "package_of_practices": {
                    "citation": "app/data/sources/pop_transcribed.json - Maharashtra package-of-practices, transcribed verbatim from government/ICAR publications (NFSM 2018, DCD Cotton Ready Reckoner, ICAR-IISR 2023)",
                    "verification": "VERBATIM_QUOTE",
                    "note": "Adds the advisory 'pop' block (sowing window, seed rate, fertilizer dose) to transcribed crops only; also records local soil-preference overrides. Crops without a transcription carry no pop block.",
                },
            },
            "verification": "PARTIALLY_VERIFIED",
            "verification_summary": (
                "The fields that drive a recommendation now come from cited sources instead of "
                "hand-written placeholders: soil_texture and temperature ranges from EcoCrop, "
                "KC values from FAO-56 Table 12, durations from FAO-56 Table 11 (EcoCrop crop "
                "cycle where FAO-56 has no row), and water_need_class derived from Kc x duration. "
                "seasons, investment_level and major_risks remain curated judgement and are "
                "labelled as such per field."
            ),
            "scoring_semantics": {
                "soil_types_optimal": f"credit {OPTIMAL_CREDIT}",
                "soil_types_tolerated": f"credit {TOLERATED_CREDIT}",
                "unlisted_soil": "credit 0.0",
                "null_soil_types": "means the source records no texture preference; the soil factor is reported unavailable and its weight redistributed, not scored as a pass",
                "combined": "app/services/suitability.py:soil_compatibility reads these lists; it no longer infers credit from water-retention similarity",
            },
            "removed_fields": {
                "water_need_mm": (
                    "Removed in schema 2. It was a hand-written millimetre range with no source. "
                    "EcoCrop's rainfall_optimal_mm / rainfall_absolute_mm replace it, and they mean "
                    "something different: the annual rainfall of the environment the crop grows in, "
                    "not the crop's water requirement. A real per-farm requirement needs ETo and "
                    "effective rainfall, which is not implemented."
                ),
                "sowing_months": (
                    "Removed in schema 2. Hand-written and wrong to model as a fixed month list. "
                    "A real sowing window needs a local package-of-practices source; EcoCrop's "
                    "planting dates are for other regions. Partially reversed since: transcribed "
                    "crops carry a 'pop' block with a sourced sowing window (VERBATIM_QUOTE); the "
                    "scoring engine still does not read it - seasons drive seasonal fit."
                ),
                "harvest_months": "Removed in schema 2, same reason as sowing_months.",
            },
            "local_review_needed": local_review,
            "standing_caveat": (
                "EcoCrop publishes global species-level envelopes. They are a citable improvement "
                "over invented numbers; the Maharashtra package-of-practices transcription "
                "(pop_transcribed.json) now provides local practice values for the crops it covers. "
                "local_review_needed lists every place where this build still knows the source is "
                "weak or conflicts with local practice."
            ),
        },
        "crops": crops,
    }

    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"wrote {OUTPUT.relative_to(BACKEND_DIR)} ({len(crops)} crops)")
    print("\ncrop        water  duration      temp abs      soils(opt/tol)")
    for entry in crops:
        soils = entry["soil_types_optimal"]
        opt = len(soils) if soils else "n/a"
        print(
            f"{entry['id']:11s} {entry['water_need_class']:6s} "
            f"{str(entry['duration_days']):13s} "
            f"{str(entry['temp_absolute_c']):13s} {str(opt):>3s}/{len(entry['soil_types_tolerated'] or [])}"
        )
    print(f"\nlocal_review_needed: {len(local_review)} item(s)")
    for item in local_review:
        print(f"  - {item['crop']}/{item['field']}: {item['issue'][:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
