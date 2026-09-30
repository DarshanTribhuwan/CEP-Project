"""Walk-forward backtest of the §5.3 price estimator.

Part 4's rule: a model may only be called good once it has been scored against
outcomes it could not have seen. This module runs
:func:`app.services.price.estimate_price` over a real price history exactly as
it would run in production - each prediction sees **only** records dated before
its cutoff - and scores the predicted range against the held-out modal price.

Metrics, defined once so they can be attacked:

    MAE / RMSE   on the range midpoint vs the held-out modal price
    coverage     fraction of actuals inside [low, high] - for a ~10th-90th pct
                 band the target is roughly 80%; a number that is *too* high
                 means the ranges are wider than they need to be
    sharpness    mean width of the predicted range, in % of mid; narrower is
                 more useful, and only meaningful alongside coverage

Honesty rules this module enforces:

    - no interpolation: a market-week with no observations contributes no
      target, and the gap is reported as ``missing_weeks``, never filled;
    - the first ``min_history_days`` of each series are burn-in; predictions
      are not made there because production would not have had enough
      records either;
    - a cutoff with too few usable prices is skipped and counted, never
      guessed through;
    - every report carries the exact data window so a run is reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import fmean

from ..providers.base import DataResult
from ..providers.market import MarketPriceRecord
from ..services.price import MIN_RECORDS, estimate_price

HORIZON_DAYS = 7
STEP_DAYS = 7
MIN_HISTORY_DAYS = 28


@dataclass(slots=True)
class Prediction:
    cutoff: date
    target_date: date
    low: float
    mid: float
    high: float
    confidence: float
    based_on_records: int


@dataclass(slots=True)
class Miss:
    cutoff: date
    target_date: date
    low: float
    high: float
    actual: float
    kind: str  # "below" (actual < low) or "above" (actual > high)


@dataclass(slots=True)
class SeriesReport:
    key: str
    first_date: date | None = None
    last_date: date | None = None
    predictions: int = 0
    evaluated: int = 0
    skipped_cold_start: int = 0
    missing_weeks: int = 0
    mae: float | None = None
    rmse: float | None = None
    mae_pct_of_mean: float | None = None
    mean_actual_price: float | None = None
    coverage: float | None = None
    sharpness_pct: float | None = None
    misses: list[Miss] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # Raw accumulators, kept so summarize() can pool across series exactly.
    _errors: list[float] = field(default_factory=list, repr=False)
    _sq_errors: list[float] = field(default_factory=list, repr=False)
    _widths: list[float] = field(default_factory=list, repr=False)
    _mids: list[float] = field(default_factory=list, repr=False)
    _actuals: list[float] = field(default_factory=list, repr=False)

    def finalize(self) -> None:
        if self._errors:
            abs_errors = [abs(e) for e in self._errors]
            self.mae = round(fmean(abs_errors), 2)
            self.rmse = round(fmean(self._sq_errors) ** 0.5, 2)
        if self._actuals:
            self.mean_actual_price = round(fmean(self._actuals), 2)
            if self.mae is not None and self.mean_actual_price > 0:
                self.mae_pct_of_mean = round(
                    100.0 * self.mae / self.mean_actual_price, 2
                )
        if self._widths and self._mids:
            hits = len(self._widths) - len(self.misses)
            self.coverage = round(hits / len(self._widths), 4)
            relative = [w / m for w, m in zip(self._widths, self._mids) if m > 0]
            self.sharpness_pct = round(100.0 * fmean(relative), 2) if relative else None


def usable_series(records: list[MarketPriceRecord]) -> list[tuple[date, float]]:
    """The exact transformation the production estimator applies.

    Reusing the production cleaner keeps the backtest honest: if production
    changes how it selects and cleans prices, the backtest follows
    automatically instead of drifting away from what would really run.
    """
    from ..services.price import _usable_prices

    return [
        (day, price)
        for day, price in _usable_prices(records)
        if day is not None
    ]


def _modal_by_day(records: list[MarketPriceRecord]) -> dict[date, float]:
    """One target price per calendar day: the mean of that day's modal prices.

    The estimator treats records as a bag, but scoring needs a per-day target.
    Averaging the day's observations is not interpolation - days with no
    observations stay absent and are counted in ``missing_weeks``.
    """
    by_day: dict[date, list[float]] = {}
    for record in records:
        price = record.modal_price
        if price is None or price <= 0:
            continue
        by_day.setdefault(record.price_date, []).append(float(price))
    return {day: fmean(prices) for day, prices in sorted(by_day.items())}


def run_backtest(
    key: str,
    records: list[MarketPriceRecord],
    *,
    horizon_days: int = HORIZON_DAYS,
    step_days: int = STEP_DAYS,
    min_history_days: int = MIN_HISTORY_DAYS,
    min_records: int = MIN_RECORDS,
) -> SeriesReport:
    """Walk one series forward. ``key`` names the series (crop / market)."""
    report = SeriesReport(key=key)
    if not records:
        report.notes.append("no records at all")
        return report

    dated = [r for r in records if r.price_date is not None]
    if not dated:
        report.notes.append("no records carry a parseable date")
        return report

    report.first_date = min(r.price_date for r in dated)
    report.last_date = max(r.price_date for r in dated)

    series = usable_series(records)
    modal = _modal_by_day(records)
    template = dated[0]
    burn_in_end = report.first_date + timedelta(days=min_history_days)

    cutoff = burn_in_end
    while cutoff + timedelta(days=horizon_days) <= report.last_date:
        target_date = cutoff + timedelta(days=horizon_days)
        history = [
            MarketPriceRecord(
                crop_id=template.crop_id,
                commodity=template.commodity,
                market=template.market,
                district=template.district,
                state=template.state,
                price_date=day,
                min_price=None,
                max_price=None,
                modal_price=price,
            )
            for day, price in series
            if day <= cutoff
        ]
        if len(history) < min_records:
            report.skipped_cold_start += 1
            cutoff += timedelta(days=step_days)
            continue

        result = DataResult.live(
            history,
            source_name="backtest-history",
            notes=["walk-forward slice: only records at or before the cutoff"],
        )
        estimate = estimate_price(result, horizon_days=horizon_days)
        if estimate is None:
            report.skipped_cold_start += 1
            cutoff += timedelta(days=step_days)
            continue

        report.predictions += 1
        actual = modal.get(target_date)
        if actual is None:
            report.missing_weeks += 1
            cutoff += timedelta(days=step_days)
            continue

        report.evaluated += 1
        error = estimate.mid - actual
        report._errors.append(error)
        report._sq_errors.append(error * error)
        report._widths.append(estimate.high - estimate.low)
        report._mids.append(estimate.mid)
        report._actuals.append(actual)
        if actual < estimate.low:
            report.misses.append(
                Miss(cutoff, target_date, estimate.low, estimate.high, actual, "below")
            )
        elif actual > estimate.high:
            report.misses.append(
                Miss(cutoff, target_date, estimate.low, estimate.high, actual, "above")
            )
        cutoff += timedelta(days=step_days)

    report.finalize()
    return report


def summarize(reports: list[SeriesReport]) -> dict[str, object]:
    """Portfolio-level numbers, pooling every evaluated week across series."""
    errors = [abs(e) for r in reports for e in r._errors]
    sq_errors = [e for r in reports for e in r._sq_errors]
    actuals = [a for r in reports for a in r._actuals]
    evaluated = sum(r.evaluated for r in reports)
    hits = evaluated - sum(len(r.misses) for r in reports)
    mean_actual = fmean(actuals) if actuals else None
    pooled_mae = round(fmean(errors), 2) if errors else None
    return {
        "series": len(reports),
        "predictions": sum(r.predictions for r in reports),
        "evaluated": evaluated,
        "missing_target_weeks": sum(r.missing_weeks for r in reports),
        "skipped_cold_start": sum(r.skipped_cold_start for r in reports),
        "mae": pooled_mae,
        "rmse": round(fmean(sq_errors) ** 0.5, 2) if sq_errors else None,
        "mae_pct_of_mean": (
            round(100.0 * pooled_mae / mean_actual, 2)
            if pooled_mae is not None and mean_actual and mean_actual > 0
            else None
        ),
        "mean_actual_price": round(mean_actual, 2) if mean_actual else None,
        "coverage": round(hits / evaluated, 4) if evaluated else None,
        "note": "missing weeks are reported, never filled; per-series detail lists the misses",
    }
