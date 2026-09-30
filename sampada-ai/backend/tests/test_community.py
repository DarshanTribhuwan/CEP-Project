"""Community signal tests.

This module makes a deliberately narrow claim. The tests below assert both the
behaviour AND that the limitation stays attached to the output - because the
temptation in a project like this is to let an app-scoped number drift into
being described as district-level oversupply.
"""

from __future__ import annotations

import pytest

from app.services import community


def test_no_app_history_is_reported_as_unavailable():
    signal = community.compute_signal({}, ["soybean", "tur", "onion"])
    assert signal.available is False
    assert signal.reason is not None
    assert signal.score_for("soybean", 3) is None
    assert any("not available" in note for note in signal.notes)


def test_more_issued_area_means_a_lower_score():
    issued = {"soybean": 10.0, "tur": 2.0}
    signal = community.compute_signal(issued, ["soybean", "tur", "onion"])

    assert signal.available is True
    crowded = signal.score_for("soybean", 3)
    moderate = signal.score_for("tur", 3)
    untouched = signal.score_for("onion", 3)

    assert crowded is not None and moderate is not None and untouched is not None
    assert crowded < moderate < untouched
    assert untouched == 1.0


def test_oversupply_risk_is_the_inverse_of_the_score():
    signal = community.compute_signal({"soybean": 10.0, "tur": 2.0}, ["soybean", "tur"])
    risk = community.oversupply_risk(signal, "soybean", 2)
    score = signal.score_for("soybean", 2)
    assert risk is not None and score is not None
    assert risk == pytest.approx(1.0 - score)
    assert risk > 0.9  # dominant share of issued area ⇒ near-total crowding


def test_scope_limitation_is_always_attached():
    signal = community.compute_signal({"soybean": 1.0}, ["soybean"])
    joined = " ".join(signal.notes).lower()
    assert "only farms using this app" in joined
    assert "not the whole district" in joined
    assert "not verified market oversupply" in joined


def test_scores_stay_in_unit_range():
    issued = {crop: float(area) for crop, area in
              zip(["soybean", "tur", "onion", "jowar", "gram"], [50, 30, 5, 1, 0.5])}
    signal = community.compute_signal(issued, list(issued))
    for crop_id in issued:
        score = signal.score_for(crop_id, len(issued))
        assert score is not None
        assert 0.0 <= score <= 1.0


def test_shares_are_reported_for_auditability():
    signal = community.compute_signal({"soybean": 3.0, "tur": 1.0}, ["soybean", "tur"])
    assert signal.total_issued_area_ha == 4.0
    assert sum(signal.shares.values()) == 1.0
