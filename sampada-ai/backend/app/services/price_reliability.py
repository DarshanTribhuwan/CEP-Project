"""Per-crop price-signal reliability, measured by the walk-forward backtest.

The design rule this implements: a factor should carry weight in proportion to
how well its signal has been *measured* to work. The backtest
(``app/evaluation/backtest.py``) measured, per crop, how often the price
estimator's range actually contained the real outcome a week later. This module
turns that measurement into a 0.5..1.0 scale factor for the ``market_price``
weight, via one stated rule:

    reliability = clamp(pooled_coverage / COVERAGE_TARGET, RELIABILITY_FLOOR, 1.0)

where ``pooled_coverage`` is hits / evaluated weeks for the whole crop across
its backtested series, and ``COVERAGE_TARGET`` is 0.80 - the expected hit rate
of the estimator's ~10th-90th percentile band. A crop whose bands hold up keeps
full weight; a crop whose bands miss half the time carries proportionally less.
``RELIABILITY_FLOOR`` keeps a measured signal from being discarded entirely.

Honesty rules:

    - the table in ``app/data/price_reliability.json`` is generated only by
      ``scripts/export_price_reliability.py`` from the backtest report - it is
      cited evidence, not hand-tuned numbers;
    - a crop that has not been backtested gets ``DEFAULT_RELIABILITY`` (a mild
      discount) and the API shows a ``price_unmeasured`` reason. "Not measured
      yet" must not look like "fully trusted";
    - a missing or unreadable table makes the feature **inert**: every weight
      is unscaled (1.0) and no unmeasured claims are made. The feature can
      degrade to the previous behaviour instead of misinforming.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.config import DATA_DIR

TABLE_PATH = DATA_DIR / "price_reliability.json"

COVERAGE_TARGET = 0.80
RELIABILITY_FLOOR = 0.5
DEFAULT_RELIABILITY = 0.75


def reliability_from_coverage(pooled_coverage: float) -> float:
    """The single stated rule. The export script imports this, so there is
    exactly one place where coverage becomes reliability."""
    clamped = max(RELIABILITY_FLOOR, min(1.0, pooled_coverage / COVERAGE_TARGET))
    return round(clamped, 2)


def _load_table() -> dict[str, dict] | None:
    """Read and shape-check the table. Any problem means 'feature inert'."""
    try:
        payload = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    crops = payload.get("crops") if isinstance(payload, dict) else None
    if not isinstance(crops, dict):
        return None
    cleaned: dict[str, dict] = {}
    for crop_id, entry in crops.items():
        if not isinstance(entry, dict):
            continue
        reliability = entry.get("reliability")
        if not isinstance(reliability, (int, float)) or not 0.0 < reliability <= 1.0:
            continue
        cleaned[str(crop_id)] = {"reliability": float(reliability)}
    return cleaned or None


_TABLE = _load_table()


def active() -> bool:
    """True when a valid reliability table is loaded."""
    return _TABLE is not None


def reliability_for(crop_id: str) -> float:
    """Weight scale for this crop's market factor: 0.5..1.0 (1.0 if inert)."""
    if _TABLE is None:
        return 1.0
    entry = _TABLE.get(crop_id)
    if entry is None:
        return DEFAULT_RELIABILITY
    return entry["reliability"]


def reliabilities() -> dict[str, float] | None:
    """Snapshot of the committed table (crop -> reliability), or None if inert.

    For status endpoints: shows exactly which crops the engine has measured
    evidence for. Unmeasured crops are absent from this map - their default is
    reported separately rather than disguised as a measurement.
    """
    if _TABLE is None:
        return None
    return {crop: entry["reliability"] for crop, entry in sorted(_TABLE.items())}


def measured(crop_id: str) -> bool:
    """True when this crop's own backtest evidence backs its reliability.

    False only when the table exists but the crop is absent from it - the one
    case where the API must say ``price_unmeasured``. When the whole feature is
    inert (no table) nothing is being scaled, so nothing needs disclaiming.
    """
    if _TABLE is None:
        return True
    return crop_id in _TABLE
