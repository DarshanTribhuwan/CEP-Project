"""Geographic soil evidence provider.

There is no keyless national soil-map API that this project can rely on, so this
provider stays *unavailable* until an operator downloads a soil dataset (for
example an NBSS&LUP or Bhuvan export) and points ``providers.soil_geo.dataset_path``
at it. That is the honest default: the soil module then fuses only the evidence
it genuinely has, and says so in its confidence score.

Expected input: a GeoJSON ``FeatureCollection`` whose features carry a soil-type
attribute (configurable via ``soil_type_attribute``). Feature geometries may be
``Polygon`` or ``MultiPolygon``; coordinates are assumed to be [lon, lat].
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..core.config import BACKEND_DIR, settings
from .base import DataResult

SOURCE_NAME = "configured soil dataset (GeoJSON)"


def _dataset_path() -> Path | None:
    raw = str(settings.get("providers.soil_geo.dataset_path", "")).strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


@lru_cache(maxsize=4)
def _load_features(path_str: str, attribute: str) -> tuple[tuple[str, Any], ...]:
    """Load and index features as (soil_type, geometry) pairs."""
    with open(path_str, encoding="utf-8") as handle:
        collection = json.load(handle)
    features = collection.get("features", []) if isinstance(collection, dict) else []
    indexed: list[tuple[str, Any]] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        soil_type = props.get(attribute) if isinstance(props, dict) else None
        if soil_type and geometry:
            indexed.append((str(soil_type).strip().upper(), geometry))
    return tuple(indexed)


def lookup_soil_type(latitude: float, longitude: float) -> DataResult[str]:
    """Return the mapped soil type at a coordinate, or an unavailable result."""
    if not settings.get("providers.soil_geo.enabled", False):
        return DataResult.unavailable(
            "geographic soil lookup is disabled (providers.soil_geo.enabled = false)",
            source_name=SOURCE_NAME,
        )

    path = _dataset_path()
    if path is None:
        return DataResult.unavailable(
            "no soil dataset configured (providers.soil_geo.dataset_path is empty)",
            source_name=SOURCE_NAME,
        )
    if not path.exists():
        return DataResult.unavailable(
            f"configured soil dataset not found at {path}", source_name=SOURCE_NAME
        )

    attribute = str(settings.get("providers.soil_geo.soil_type_attribute", "soil_type"))
    try:
        features = _load_features(str(path), attribute)
    except (OSError, ValueError) as exc:
        return DataResult.unavailable(
            f"soil dataset could not be read ({type(exc).__name__})", source_name=SOURCE_NAME
        )

    if not features:
        return DataResult.unavailable(
            f"soil dataset has no features carrying the {attribute!r} attribute",
            source_name=SOURCE_NAME,
        )

    matches = [
        soil_type
        for soil_type, geometry in features
        if _geometry_contains(geometry, longitude, latitude)
    ]
    if not matches:
        return DataResult.unavailable(
            "no soil polygon in the dataset covers this location", source_name=SOURCE_NAME
        )

    # Overlapping polygons are possible in messy datasets; report the first and
    # flag the ambiguity rather than pretending there is a single answer.
    notes = ["geographic lookup is only as good as the supplied dataset"]
    if len(set(matches)) > 1:
        notes.append(f"location matched {len(set(matches))} different soil polygons")
    return DataResult.live(matches[0], source_name=SOURCE_NAME, source_url=str(path), notes=notes)


def _geometry_contains(geometry: dict[str, Any], lon: float, lat: float) -> bool:
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Polygon":
        return _polygon_contains(coords, lon, lat)
    if gtype == "MultiPolygon":
        return any(_polygon_contains(poly, lon, lat) for poly in (coords or []))
    return False


def _polygon_contains(rings: Any, lon: float, lat: float) -> bool:
    if not rings:
        return False
    # First ring is the outer boundary, the rest are holes.
    if not _ring_contains(rings[0], lon, lat):
        return False
    return not any(_ring_contains(hole, lon, lat) for hole in rings[1:])


def _ring_contains(ring: Any, lon: float, lat: float) -> bool:
    """Ray-casting point-in-polygon test."""
    if not isinstance(ring, (list, tuple)) or len(ring) < 3:
        return False
    inside = False
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index][0], ring[index][1]
        x2, y2 = ring[(index + 1) % count][0], ring[(index + 1) % count][1]
        if (y1 > lat) != (y2 > lat):
            slope = (x2 - x1) / (y2 - y1) if y2 != y1 else 0.0
            if lon < x1 + slope * (lat - y1):
                inside = not inside
    return inside
