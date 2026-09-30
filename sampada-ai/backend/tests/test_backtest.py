"""Tests for the walk-forward price backtest.

Synthetic series only - the suite never touches the network, and the backtest
module is deliberately network-free: it scores whatever records it is handed.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.evaluation.backtest import (
    HORIZON_DAYS,
    MIN_HISTORY_DAYS,
    Miss,
    SeriesReport,
    run_backtest,
    summarize,
)
from app.providers.market import MarketPriceRecord


def _record(day: date, price: float, crop_id: str = "onion") -> MarketPriceRecord:
    return MarketPriceRecord(
        crop_id=crop_id,
        commodity=crop_id,
        market="Test APMC",
        district="Pune",
        state="Maharashtra",
        price_date=day,
        min_price=price,
        max_price=price,
        modal_price=price,
    )


def _constant_series(
    days: int, price: float, start: date = date(2025, 1, 1)
) -> list[MarketPriceRecord]:
    return [_record(start + timedelta(days=i), price) for i in range(days)]


def _linear_series(
    days: int, start_price: float, slope: float, start: date = date(2025, 1, 1)
) -> list[MarketPriceRecord]:
    return [_record(start + timedelta(days=i), start_price + slope * i) for i in range(days)]


class TestConstantSeries:
    def test_every_target_is_hit(self):
        records = _constant_series(120, 2000.0)
        report = run_backtest("onion/test", records)
        assert report.predictions > 0
        assert report.evaluated == report.predictions  # daily records: no gaps
        assert report.mae == 0.0
        assert report.rmse == 0.0
        assert report.coverage == 1.0
        assert report.misses == []

    def test_burn_in_respected(self):
        records = _constant_series(120, 2000.0)
        report = run_backtest("onion/test", records)
        assert report.first_date is not None and report.last_date is not None
        span_days = (report.last_date - report.first_date).days
        # Predictions cover (span - horizon - burn-in) / step weeks, minus one
        # for the loop boundary - the point is the order of magnitude, and that
        # predictions start only after the burn-in window.
        expected_min = (span_days - HORIZON_DAYS - MIN_HISTORY_DAYS) // 7 - 2
        assert report.predictions >= expected_min


class TestLinearSeries:
    def test_momentum_captures_drift(self):
        # +5/quartal-ish slope: the damped trend term should keep MAE small
        # relative to the price level.
        records = _linear_series(180, 2000.0, 2.0)
        report = run_backtest("onion/test", records)
        assert report.evaluated > 0
        assert report.mae is not None
        assert report.mae < 100.0  # < 5% of the ~2000-2350 price band
        assert report.coverage == 1.0


class TestHonestyRules:
    def test_no_target_week_is_counted_not_filled(self):
        records = _constant_series(90, 2000.0)
        # Remove one full target week: burn-in end + horizon.
        gap_start = date(2025, 1, 1) + timedelta(days=MIN_HISTORY_DAYS + HORIZON_DAYS)
        gap_days = {gap_start + timedelta(days=i) for i in range(3)}
        records = [r for r in records if r.price_date not in gap_days]
        report = run_backtest("onion/test", records)
        assert report.missing_weeks == 1
        assert report.evaluated == report.predictions - 1

    def test_fewer_than_min_records_is_skipped_not_guessed(self):
        # A series of 20 daily records: after 28-day burn-in there is nothing
        # left to predict with.
        records = _constant_series(20, 2000.0)
        report = run_backtest("onion/test", records)
        assert report.predictions == 0
        assert report.evaluated == 0
        assert report.mae is None
        assert report.coverage is None

    def test_unusable_prices_are_excluded(self):
        records = _constant_series(60, 2000.0)
        records.append(_record(date(2025, 4, 30), 0.0))   # zero price
        records.append(_record(date(2025, 4, 30), -3.0))  # negative price
        report = run_backtest("onion/test", records)
        assert report.predictions > 0  # ran fine despite junk rows

    def test_no_date_records_reported(self):
        records = [_record(date(2025, 1, 1), 100.0)]
        for r in records:
            r.price_date = None
        report = run_backtest("onion/test", records)
        assert report.predictions == 0
        assert any("parseable date" in n for n in report.notes)

    def test_empty_input(self):
        report = run_backtest("onion/test", [])
        assert report.predictions == 0
        assert "no records at all" in report.notes


class TestSummarize:
    def test_pools_across_series(self):
        r1 = run_backtest("onion/a", _constant_series(90, 2000.0))
        r2 = run_backtest("onion/b", _linear_series(90, 2000.0, 1.0))
        summary = summarize([r1, r2])
        assert summary["series"] == 2
        assert summary["evaluated"] == r1.evaluated + r2.evaluated
        assert summary["coverage"] is not None
        assert 0.0 <= summary["coverage"] <= 1.0

    def test_empty_summary(self):
        summary = summarize([])
        assert summary["series"] == 0
        assert summary["mae"] is None
        assert summary["coverage"] is None
