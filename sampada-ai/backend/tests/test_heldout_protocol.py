"""Tests for the held-out evaluation protocol and factor evidence registry.

Synthetic only: outcomes are fabricated in a throwaway DB and the protocol's
filesystem operations run in tmp_path. The point is to prove the guard-rails
work - leakage raises, one-shot consumption holds, pre-registration precedes
judgement - not to validate any real weight.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.evaluation import factor_evidence, heldout
from app.evaluation.factor_evidence import MEASURED, PARTIAL, UNMEASURED
from tests.conftest import API
from app.evaluation.heldout import (
    LeakageError,
    Proposal,
    Window,
    consume_heldout,
    guard_no_tuning,
    pre_register_proposal,
)


# ---------------------------------------------------------------------------
# Factor evidence registry
# ---------------------------------------------------------------------------
class TestFactorEvidence:
    def test_exactly_one_measured_factor(self):
        reg = factor_evidence.registry()
        measured = [f for f, e in reg.items() if e.status == MEASURED]
        assert measured == ["market_price"]

    def test_the_other_five_cannot_change(self):
        # farmer_history is PARTIAL (onboarding history exists) but its link to
        # recorded outcomes is unmeasured, so it must not be weight-changeable.
        reg = factor_evidence.registry()
        changeable = [f for f, e in reg.items() if e.status == MEASURED]
        assert changeable == ["market_price"]
        assert factor_evidence.status_for("farmer_history").status == PARTIAL
        assert set(factor_evidence.unmeasured_factors()) == {
            "soil_suitability",
            "weather_match",
            "water_availability",
            "community_saturation",
        }

    def test_every_factor_states_its_evidence_bar(self):
        for factor, entry in factor_evidence.registry().items():
            assert entry.minimum_evidence, factor
            assert entry.outcome_needed, factor
            assert entry.evidence_source, factor

    def test_summary_labels_unvalidated_weights(self):
        summary = factor_evidence.summary()
        for factor, entry in summary["factors"].items():
            if factor == "market_price":
                assert "evidence-informed" in entry["weight_validation"]
            else:
                assert "UNVALIDATED DEFAULT" in entry["weight_validation"]


# ---------------------------------------------------------------------------
# Window segmentation
# ---------------------------------------------------------------------------
class TestWindow:
    def test_default_split_is_training_before_cutoff(self):
        window = Window(cutoff_date=date(2026, 6, 1))
        assert window.segment_for(date(2026, 5, 31)) == "training"
        assert window.segment_for(date(2026, 6, 1)) == "heldout"
        assert window.segment_for(date(2026, 7, 15)) == "heldout"

    def test_labels_match_semantics(self):
        window = Window(cutoff_date=date(2026, 6, 1))
        assert window.training_label == "outcomes < 2026-06-01"
        assert window.heldout_label == "outcomes >= 2026-06-01"

    def test_end_inclusive_variant(self):
        window = Window(cutoff_date=date(2026, 6, 1), training_end_inclusive=True)
        assert window.segment_for(date(2026, 6, 1)) == "training"
        assert window.segment_for(date(2026, 6, 2)) == "heldout"
        assert window.training_label == "outcomes <= 2026-06-01"


# ---------------------------------------------------------------------------
# Leakage guard
# ---------------------------------------------------------------------------
class TestLeakageGuard:
    def test_fitting_fn_receives_training_rows_only(self):
        window = Window(cutoff_date=date(2026, 6, 1))
        seen = {}

        def fit(view, rows):
            seen["label"] = view.label
            seen["rows"] = rows
            return sum(rows)

        guarded = guard_no_tuning(window, [1, 2, 3], fit)
        assert guarded() == 6
        assert seen["label"] == "outcomes < 2026-06-01"
        assert seen["rows"] == [1, 2, 3]

    def test_extra_arguments_are_refused(self):
        """There is no call path for held-out data to reach the fitting fn."""
        window = Window(cutoff_date=date(2026, 6, 1))

        def fit(view, rows):
            return sum(rows)

        guarded = guard_no_tuning(window, [1, 2, 3], fit)
        with pytest.raises(LeakageError):
            guarded([4, 5, 6])  # an attempt to smuggle in other rows


# ---------------------------------------------------------------------------
# Pre-registration and one-shot consumption
# ---------------------------------------------------------------------------
@pytest.fixture
def proposal() -> Proposal:
    return Proposal(
        factor="weather_match",
        old_weight=0.20,
        new_weight=0.25,
        rationale="hypothetical: rainfed outcome contrast",
        evidence_source="outcomes table",
        training_window="outcomes < 2026-06-01",
        proposed_at="2026-09-23T10:00:00",
    )


class TestPreRegistration:
    def test_proposal_is_written_before_judging(self, tmp_path, proposal):
        path = pre_register_proposal(proposal, tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["factor"] == "weather_match"
        assert payload["new_weight"] == 0.25
        assert "not yet consumed" in payload["status"]

    def test_fingerprint_is_deterministic(self, proposal):
        assert proposal.fingerprint() == Proposal(
            factor=proposal.factor,
            old_weight=proposal.old_weight,
            new_weight=proposal.new_weight,
            rationale=proposal.rationale,
            evidence_source=proposal.evidence_source,
            training_window=proposal.training_window,
            proposed_at=proposal.proposed_at,
        ).fingerprint()


class TestOneShotConsumption:
    def test_first_judgement_records(self, tmp_path, proposal):
        window = Window(cutoff_date=date(2026, 6, 1))
        result = consume_heldout(
            window, proposal, {"mae": 0.12}, proposals_dir=tmp_path
        )
        assert result["metrics"] == {"mae": 0.12}
        ledger = tmp_path / "consumed_windows.jsonl"
        assert len(ledger.read_text(encoding="utf-8").strip().splitlines()) == 1

    def test_second_judgement_on_same_window_raises(self, tmp_path, proposal):
        window = Window(cutoff_date=date(2026, 6, 1))
        consume_heldout(window, proposal, {"mae": 0.12}, proposals_dir=tmp_path)
        challenger = Proposal(
            factor="weather_match",
            old_weight=0.20,
            new_weight=0.30,  # a different candidate for the same window
            rationale="second try - exactly what the protocol forbids",
            evidence_source="outcomes table",
            training_window="outcomes < 2026-06-01",
            proposed_at="2026-09-23T11:00:00",
        )
        with pytest.raises(LeakageError):
            consume_heldout(window, challenger, {"mae": 0.11}, proposals_dir=tmp_path)

    def test_new_cutoff_is_a_new_window(self, tmp_path, proposal):
        consume_heldout(
            Window(cutoff_date=date(2026, 6, 1)), proposal, {"mae": 0.12}, proposals_dir=tmp_path
        )
        # A later boundary means a later segment: legitimate, not repetition.
        result = consume_heldout(
            Window(cutoff_date=date(2026, 9, 1)), proposal, {"mae": 0.10}, proposals_dir=tmp_path
        )
        assert result["metrics"] == {"mae": 0.10}


class TestFactorStatusAPI:
    def test_status_labels_every_weight(self, client):
        response = client.get(f"{API}/evaluation/factor-status")
        assert response.status_code == 200, response.text
        body = response.json()
        by_factor = {f["factor"]: f for f in body["factors"]}
        assert len(by_factor) == 6
        assert by_factor["market_price"]["status"] == "measured"
        assert by_factor["market_price"]["configured_weight"] == 0.25
        assert by_factor["soil_suitability"]["status"] == "unmeasured"
        assert "UNVALIDATED" in by_factor["soil_suitability"]["weight_validation"]
        assert body["unmeasured_count"] == 4
        # The reliability table exists in this repo, so per-crop numbers are exposed.
        assert body["per_crop_reliability"]["soybean"] == 0.99
        assert body["default_reliability"] == 0.75
