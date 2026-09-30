"""Suitability scoring tests."""

from __future__ import annotations

import pytest

from app.services import suitability
from app.services.crops import load_crops


def test_water_capacity_reflects_irrigation_and_source():
    assert suitability.farm_water_capacity("RAINFED", None) == pytest.approx(0.30)
    assert suitability.farm_water_capacity("FULL", None) == pytest.approx(0.90)
    assert suitability.farm_water_capacity("PARTIAL", "BOREWELL") == pytest.approx(0.70)
    # A rainwater-only partial setup is worse than a bare partial one.
    assert suitability.farm_water_capacity("PARTIAL", "RAINWATER") < 0.60


def test_water_match_penalises_deficit_but_not_surplus():
    high_need = load_crops()["rice"]      # HIGH water need
    low_need = load_crops()["bajra"]      # LOW water need

    assert suitability.water_match(0.90, high_need) == 1.0
    assert suitability.water_match(0.90, low_need) == 1.0   # surplus is fine
    assert suitability.water_match(0.30, high_need) < 0.5   # deficit is not


def test_season_match_rejects_out_of_season_crops():
    summer = load_crops()["moong"]
    wheat = load_crops()["wheat"]
    assert suitability.season_match("KHARIF", summer) == 1.0
    assert suitability.season_match("KHARIF", wheat) == 0.0
    assert suitability.season_match("RABI", wheat) == 1.0


def test_perennial_crops_are_not_season_restricted():
    sugarcane = load_crops()["sugarcane"]
    for season in ("KHARIF", "RABI", "SUMMER"):
        assert suitability.season_match(season, sugarcane) == 1.0


def test_soil_match_uses_the_probability_distribution():
    # Lists come from FAO EcoCrop's optimal/absolute texture ranges, with the
    # Maharashtra package-of-practices override applied on top (soybean and
    # safflower on vertisols), so these expectations track the generated table
    # rather than a hand-written guess.
    soybean = load_crops()["soybean"]   # optimal: RED, ALLUVIAL + BLACK (PoP override)
    assert suitability.soil_match({"RED": 1.0}, soybean) == pytest.approx(1.0)
    assert suitability.soil_match({"ALLUVIAL": 1.0}, soybean) == pytest.approx(1.0)
    # Black cotton soil is a vertisol and NFSM records soybean's best performance
    # on vertisols - the local source promoted it to optimal (credit 1.0).
    assert suitability.soil_match({"BLACK": 1.0}, soybean) == pytest.approx(1.0)
    # A soil the source excludes outright is a genuine zero.
    bajra = load_crops()["bajra"]       # optimal: RED, ALLUVIAL, LATERITE, SANDY_LOAM
    assert suitability.soil_match({"BLACK": 1.0}, bajra) == pytest.approx(0.0)


def test_uncertain_soil_yields_a_between_value():
    # An uncertain farmer report still lands between the soils' credits - and
    # with soybean now optimal on RED and BLACK alike (PoP override), a 50/50
    # split is full credit on both, so use a soil the source excludes.
    soybean = load_crops()["soybean"]
    mixed = suitability.soil_match({"SALINE": 0.5, "RED": 0.5}, soybean)
    assert mixed is not None
    assert 0.0 < mixed < 1.0


def test_missing_soil_distribution_returns_none_not_zero():
    """Absent data must be distinguishable from bad data."""
    soybean = load_crops()["soybean"]
    assert suitability.soil_match(None, soybean) is None
    assert suitability.soil_match({}, soybean) is None
    assert suitability.soil_match({"BLACK": 0.0}, soybean) is None


def test_missing_forecast_returns_none():
    assert suitability.weather_match(None, load_crops()["soybean"]) is None


def test_soil_credit_follows_the_sourced_two_tier_lists():
    """Credit comes from EcoCrop's optimal/absolute split, not from a guess."""
    tur = load_crops()["tur"]  # optimal: RED, ALLUVIAL, LATERITE, SANDY_LOAM
    assert suitability.soil_compatibility("RED", tur) == 1.0
    assert suitability.soil_compatibility("BLACK", tur) == pytest.approx(
        suitability.TOLERATED_SOIL_CREDIT
    )
    # BLACK is in neither of bajra's lists, so it scores zero.
    assert suitability.soil_compatibility("BLACK", load_crops()["bajra"]) == 0.0


def test_a_crop_with_no_sourced_soil_preference_is_not_scored():
    """Nothing is claimed where the source publishes no optimal texture.

    Rice is the case: paddy is grown in standing water, so texture does not
    constrain it. Reporting the factor as unavailable is honest; scoring it 0.0
    would silently punish the crop for a gap in the source.
    """
    rice = load_crops()["rice"]
    assert rice.soil_types_optimal is None
    assert suitability.soil_compatibility("RED", rice) is None

    result = suitability.evaluate(
        rice,
        soil_distribution={"BLACK": 1.0},
        irrigation_type="FULL",
        water_source="CANAL",
        season="KHARIF",
        forecast=None,
    )
    assert result.soil is None
    assert any("not recorded" in note for note in result.notes)


def test_evaluate_aggregates_and_notes_gaps():
    # Rice is a HIGH water-need crop, so a rainfed farm is genuinely short.
    rice = load_crops()["rice"]
    result = suitability.evaluate(
        rice,
        soil_distribution=None,
        irrigation_type="RAINFED",
        water_source=None,
        season="KHARIF",
        forecast=None,
    )
    assert result.soil is None
    assert result.weather is None
    assert result.season == 1.0
    assert result.water is not None and result.water < 1.0
    assert any("soil" in note for note in result.notes)
    assert any("weather" in note for note in result.notes)
    assert any("water deficit" in note for note in result.notes)
    assert result.agronomic_mean is not None


def test_agronomic_mean_is_none_when_nothing_is_known():
    soybean = load_crops()["soybean"]
    empty = suitability.SuitabilityResult()
    assert empty.agronomic_mean is None
    _ = soybean
