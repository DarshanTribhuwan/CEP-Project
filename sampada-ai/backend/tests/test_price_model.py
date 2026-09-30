"""Price model tests.

The contract under test: output a *range* with a confidence, never a single
confident number, and return nothing at all rather than inventing a price when
the data is not there.
"""

from __future__ import annotations

from app.providers.base import DataResult
from app.services import price as price_service
from tests.conftest import FakeMarketProvider


def _estimate(series: dict[str, list[float]], crop: str = "soybean"):
    provider = FakeMarketProvider(series)
    return price_service.estimate_price(provider.price_history(crop))


def test_unavailable_source_produces_no_estimate():
    provider = FakeMarketProvider(unavailable_reason="no source configured")
    assert price_service.estimate_price(provider.price_history("soybean")) is None


def test_too_few_records_produces_no_estimate():
    assert _estimate({"soybean": [4300, 4400]}) is None


def test_exactly_three_records_is_enough():
    estimate = _estimate({"soybean": [4300, 4400, 4500]})
    assert estimate is not None
    assert estimate.based_on_records == 3


def test_output_is_a_range_around_the_midpoint():
    estimate = _estimate({"soybean": [4300, 4350, 4400, 4450, 4500, 4550, 4600, 4650]})
    assert estimate is not None
    assert estimate.low < estimate.mid < estimate.high


def test_volatile_prices_get_a_wider_range_than_steady_ones():
    steady = _estimate({"soybean": [4400, 4400, 4400, 4400, 4400, 4400, 4400, 4400]})
    volatile = _estimate({"soybean": [2000, 5000, 2400, 5200, 2100, 4900, 2300, 5100]})
    assert steady is not None and volatile is not None
    assert (volatile.high - volatile.low) > (steady.high - steady.low)
    assert price_service.stability_score(steady) > price_service.stability_score(volatile)


def test_confidence_stays_within_bounds():
    for series in (
        [4300, 4400, 4500],
        [2000, 5000, 2400, 5200, 2100, 4900, 2300, 5100, 2200, 5100, 2400, 5300],
    ):
        estimate = _estimate({"soybean": series})
        assert estimate is not None
        assert 0.10 <= estimate.confidence <= 0.85


def test_no_accuracy_is_claimed_in_the_notes():
    """The estimator is backtested per series, never with one headline number."""
    estimate = _estimate({"soybean": [4300, 4400, 4500, 4600, 4700, 4800]})
    assert estimate is not None
    # Must point to the per-series backtest rather than assert a global figure.
    assert any("backtest" in note.lower() for note in estimate.notes)
    assert any("no single accuracy figure" in note for note in estimate.notes)
    # And it must not quietly claim blanket accuracy either.
    assert not any("accurate" in note.lower() for note in estimate.notes)


def test_demo_data_is_flagged_in_the_notes():
    provider = FakeMarketProvider({"soybean": [4300, 4400, 4500, 4600, 4700, 4800]}, demo=True)
    estimate = price_service.estimate_price(provider.price_history("soybean"))
    assert estimate is not None
    assert any("DEMO DATA" in note for note in estimate.notes)


def test_trend_does_not_explode():
    """Damped momentum plus a hard drift clamp keeps absurd extrapolation out."""
    series = [1000, 1200, 1500, 1900, 2400, 3000, 3800, 4800, 6000, 7500, 9500, 12000]
    estimate = _estimate({"soybean": series})
    assert estimate is not None
    baseline = sorted(series)[-1]
    assert estimate.mid <= baseline * 1.45


def test_price_estimate_dict_is_serializable_and_labelled():
    estimate = _estimate({"soybean": [4300, 4400, 4500]})
    assert estimate is not None
    payload = estimate.as_dict()
    assert payload["unit"] == "INR per quintal"
    assert payload["model_version"] == price_service.settings.get("ml.price_model_version")
    assert set(payload) >= {"low", "mid", "high", "confidence", "method", "notes"}


def test_unavailable_dataresult_has_no_value():
    result = DataResult.unavailable("nope")
    assert result.available is False
    assert price_service.estimate_price(result) is None
