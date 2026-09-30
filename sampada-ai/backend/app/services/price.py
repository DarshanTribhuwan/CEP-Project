"""Price estimation - the MVP's deliberately naive baseline model.

The design sessions settled on two rules this module obeys:

  1. Never output a single confident price. Output a *range* plus a confidence,
     because agricultural markets are driven by factors this project does not
     observe (policy, exports, weather shocks, speculation).
  2. Never claim accuracy. Nothing here has been backtested against held-out
     real mandi data, so no error figure is reported anywhere.

Method, stated so it can be attacked:

    baseline      = median(modal prices, most recent N records)
    monthly_trend = mean(recent third) / mean(previous third) - 1, damped by 0.5
    mid           = baseline · (1 + monthly_trend · months), clamped to ±MAX_DRIFT
    spread k      = max(MIN_K, 1.28 · stdev / baseline)      (≈10th-90th pct)
    low, high     = mid · (1 ∓ k)
    confidence    = clamp(0.85 · (0.5·n_factor + 0.5·1/(1+cv)), 0.10, 0.85)
      where n_factor = min(1, n / 12) and cv = stdev / baseline

Every constant is an unvalidated default. The trend damping is an assumption -
it says "recent momentum probably persists, but weakly" - and is not derived
from data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, median, pstdev

from ..core.config import settings
from ..providers.base import DataResult
from ..providers.market import MarketPriceRecord

MIN_RECORDS = 3
RECENT_WINDOW = 6
TREND_DAMPING = 0.5
MAX_DRIFT = 0.40
MIN_K = 0.06
CONFIDENCE_CEILING = 0.85
CONFIDENCE_FLOOR = 0.10


@dataclass(slots=True)
class PriceEstimate:
    low: float
    mid: float
    high: float
    confidence: float
    horizon_days: int
    method: str
    model_version: str
    based_on_records: int
    cv: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "low": round(self.low, 2),
            "mid": round(self.mid, 2),
            "high": round(self.high, 2),
            "unit": "INR per quintal",
            "horizon_days": self.horizon_days,
            "confidence": self.confidence,
            "method": self.method,
            "model_version": self.model_version,
            "based_on_records": self.based_on_records,
            "notes": self.notes,
        }


def _usable_prices(records: list[MarketPriceRecord]) -> list[tuple[object, float]]:
    """Extract (date, price) pairs, preferring modal price."""
    series: list[tuple[object, float]] = []
    for record in records:
        price = record.modal_price
        if price is None and record.min_price is not None and record.max_price is not None:
            price = (record.min_price + record.max_price) / 2
        if price is None or price <= 0:
            continue
        series.append((record.price_date, float(price)))
    series.sort(key=lambda item: (item[0] is None, item[0]))
    return series


def estimate_price(
    result: DataResult[list[MarketPriceRecord]], horizon_days: int = 30
) -> PriceEstimate | None:
    """Estimate a price range. Returns None when there is not enough data."""
    if not result.available or not result.value:
        return None

    series = _usable_prices(result.value)
    if len(series) < MIN_RECORDS:
        return None

    prices = [price for _, price in series]
    recent = prices[-RECENT_WINDOW:]
    baseline = median(recent)
    if baseline <= 0:
        return None

    # Relative momentum, damped. Assumes records are roughly evenly spaced.
    monthly_trend = 0.0
    if len(prices) >= 6:
        older = mean(prices[-6:-3])
        newer = mean(prices[-3:])
        if older > 0:
            monthly_trend = (newer - older) / older / 3.0 * TREND_DAMPING

    months = horizon_days / 30.44
    drift = max(-MAX_DRIFT, min(MAX_DRIFT, monthly_trend * months))
    mid = baseline * (1.0 + drift)

    dispersion = pstdev(recent) if len(recent) > 1 else 0.0
    cv = dispersion / baseline if baseline else 0.0
    k = max(MIN_K, 1.28 * cv)

    n_factor = min(1.0, len(prices) / 12.0)
    stability = 1.0 / (1.0 + cv)
    confidence = 0.85 * (0.5 * n_factor + 0.5 * stability)
    confidence = round(max(CONFIDENCE_FLOOR, min(CONFIDENCE_CEILING, confidence)), 4)

    notes = [
        "naive seasonal baseline; walk-forward backtested per series - results vary "
        "sharply by crop (see app/evaluation/backtest.py and the latest report under var/history/)",
        "no single accuracy figure is claimed: measured error depends on crop and mandi",
    ]
    if result.provenance.unavailable_reason:
        notes.append(result.provenance.unavailable_reason)
    if result.is_demo:
        notes.append("DEMO DATA - this price range is derived from sample data, not real prices")
    if len(prices) < 6:
        notes.append(f"only {len(prices)} usable price records, so the trend term was not computed")

    return PriceEstimate(
        low=max(0.0, mid * (1.0 - k)),
        mid=mid,
        high=mid * (1.0 + k),
        confidence=confidence,
        horizon_days=horizon_days,
        method="median of recent prices, damped momentum trend, stdev-derived range",
        model_version=str(settings.get("ml.price_model_version", "unknown")),
        based_on_records=len(prices),
        cv=round(cv, 4),
        notes=notes,
    )


def stability_score(estimate: PriceEstimate) -> float:
    """0..1: how steady this crop's price is. Used as the market-stability factor."""
    # A coefficient of variation of 0.10 or below is treated as steady, 0.60+
    # as erratic. Thresholds are display-grade choices, not fitted parameters.
    if estimate.cv <= 0.10:
        return 1.0
    if estimate.cv >= 0.60:
        return 0.0
    return round(1.0 - (estimate.cv - 0.10) / 0.50, 4)
