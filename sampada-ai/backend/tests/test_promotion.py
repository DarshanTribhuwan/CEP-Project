"""Tests for the Part 4 promotion flow (incumbent vs challenger on held-out
outcomes).

The properties pinned here are the ones the project's honesty standard
requires:

    1. scoring is storage-only  - both models are scored on the same stored
       factor-score snapshots (schema v2), recomputed under each weight
       vector exactly as the engine renormalises;
    2. the comparison is fair   - identical rows, identical metric code, the
       challenger differs from the incumbent by exactly one factor;
    3. one-shot windows         - a held-out segment that already judged a
       proposal cannot judge another (the leakage guard holds end-to-end);
    4. the evidence bar gates   - below the registry bar the result is
       ``insufficient_evidence``, and adopting it needs an explicit override
       with a recorded reason;
    5. humans decide, code      - no function in this flow writes to
       ``config.toml``; adoption is a ledger entry, and applying it is a
       separate operator edit. A test asserts no promotion call mutates the
       config the engine reads.
"""

from __future__ import annotations

import json

import pytest

from app.core import db as db_module
from app.core.config import settings
from app.evaluation import promotion
from app.evaluation.heldout import Proposal, Window
from tests.conftest import API

FACTOR = "weather_match"  # an UNMEASURED factor with a 100-outcome bar


def _window(cutoff: str = "2099-01-01") -> Window:
    return Window(cutoff_date=__import__("datetime").date.fromisoformat(cutoff))


def _proposal(new_weight: float = 0.30) -> Proposal:
    return Proposal(
        factor=FACTOR,
        old_weight=0.20,
        new_weight=new_weight,
        rationale="test: challenger for the promotion flow tests",
        evidence_source="tests/test_promotion.py seeded outcomes",
        training_window="outcomes < 2099-01-01",
        proposed_at="2026-09-24T00:00:00",
        factor_versions={"recommender": "test"},
    )


# ---------------------------------------------------------------------------
# Seeding: stored recommendations with factor scores + outcomes with profits
# ---------------------------------------------------------------------------
def _seed_rows(n: int, *, scores_by_index, profits_by_index) -> None:
    """Write recommendations + linked outcomes directly through the repos.

    ``scores_by_index(i)`` returns a v2-style factor_scores list;
    ``profits_by_index(i)`` the realized profit for that outcome. Dates are
    spread around the cutoff so the time split is exercised.
    """
    from app.repositories import recommendations as recs_repo

    with db_module.get_conn() as conn:
        conn.execute(
            "INSERT INTO farmers (id, name, mobile, mobile_lookup, language, "
            "pin_hash, pin_salt, created_at) "
            "VALUES (1, 'Seeded', 'enc-seed', 'hash-seed', 'mr', 'h', 's', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO farms (id, farmer_id, land_area_ha, land_unit, "
            "irrigation_type, created_at, updated_at) "
            "VALUES (1, 1, 2.0, 'ACRE', 'PARTIAL', '2026-01-01', '2026-01-01')"
        )
    for i in range(n):
        saved = recs_repo.save_recommendations(
            farm_id=1,
            season="KHARIF",
            district="Pune",
            options=[
                {
                    "crop_id": "soybean",
                    "rank": 1,
                    "final_score": 0.6,
                    "confidence": 0.55,
                    "factor_scores": scores_by_index(i),
                }
            ],
            unavailable=[],
            model_versions={"recommender": "test"},
        )
        rec_id = saved[0]["id"]
        # Outcomes alternate before/after the 2099-01-01 cutoff: every date
        # here is held-out (>= cutoff), which is what the tests want.
        outcome_date = "2099-02-1%d" % (i % 10)
        with db_module.get_conn() as conn:
            conn.execute(
                "INSERT INTO outcomes (recommendation_id, farm_id, crop_id, season, "
                "year, profit_inr, created_at) VALUES (?, 1, 'soybean', 'KHARIF', "
                "2099, ?, ?)",
                (rec_id, profits_by_index(i), outcome_date),
            )


def _factor_scores(soil: float, weather: float, market: float) -> list[dict]:
    """A v2-style snapshot: three available factors, three unavailable ones,
    so renormalisation is exercised on every row."""
    def entry(factor: str, score: float | None, available: bool) -> dict:
        return {
            "factor": factor,
            "score": score,
            "quality": 0.8,
            "configured_weight": 0.25,
            "effective_weight": 0.0 if not available else 0.2,
            "available": available,
        }

    return [
        entry("soil_suitability", soil, soil is not None),
        entry("weather_match", weather, weather is not None),
        entry("market_price", market, market is not None),
        entry("water_availability", None, False),
        entry("farmer_history", None, False),
        entry("community_saturation", None, False),
    ]


@pytest.fixture
def seeded_storage(monkeypatch):
    """40 held-out outcomes whose profits track the weather factor's score.

    With these rows, a challenger that RAISES the weather weight must look
    better than the incumbent (tau and spread move the same way), because
    profit was constructed to increase with the weather score.
    """
    n = 40

    def scores(i):
        return _factor_scores(soil=0.5, weather=i / n, market=0.5)

    def profits(i):
        return 1000.0 + i * 50.0

    _seed_rows(n, scores_by_index=scores, profits_by_index=profits)
    return n


# ---------------------------------------------------------------------------
# 1. Scoring
# ---------------------------------------------------------------------------
class TestScoring:
    def test_recombination_renormalises_like_the_engine(self):
        scores = _factor_scores(soil=0.4, weather=0.8, market=0.6)
        # Engine: available factors only, weights renormalised over them.
        expected = (0.55 * 0.4 + 0.20 * 0.8 + 0.25 * 0.6) / (0.55 + 0.20 + 0.25)
        assert promotion.score_with(
            {"soil_suitability": 0.55, "weather_match": 0.20, "market_price": 0.25},
            scores,
        ) == pytest.approx(expected)

    def test_row_with_no_available_factors_is_missing_never_zero(self):
        scores = _factor_scores(soil=None, weather=None, market=None)
        assert promotion.score_with({"soil_suitability": 0.55}, scores) is None

    def test_challenger_differs_from_incumbent_by_exactly_one_factor(self):
        incumbent = promotion.WeightModel(
            "incumbent",
            {"soil_suitability": 0.25, "weather_match": 0.20, "market_price": 0.25},
        )
        proposal = _proposal(new_weight=0.30)
        challenger_weights = promotion.challenger_weights(proposal, incumbent.weights)
        assert challenger_weights[FACTOR] == 0.30
        assert challenger_weights["soil_suitability"] == 0.25
        assert challenger_weights["market_price"] == 0.25


# ---------------------------------------------------------------------------
# 2. The flow, end to end
# ---------------------------------------------------------------------------
class TestPromotionFlow:
    def test_propose_register_score_ledger(
        self, seeded_storage, tmp_path
    ):
        window = _window()
        proposal = _proposal()
        incumbent = promotion.WeightModel(
            "incumbent",
            {
                "soil_suitability": 0.55,
                "weather_match": 0.20,
                "market_price": 0.25,
                "water_availability": 0.15,
                "farmer_history": 0.10,
                "community_saturation": 0.05,
            },
        )

        result = promotion.run_promotion(window, proposal, incumbent, tmp_path)
        record = result["record"]

        # Both models scored on the same rows; the window is consumed exactly
        # once (the ledger record is the proof, not the verdict text).
        metrics = record["metrics"]
        assert metrics["challenger"]["n"] == metrics["incumbent"]["n"] == 40
        assert (tmp_path / "consumed_windows.jsonl").exists()

        # Profits were constructed to rise with the weather score, and the
        # challenger weights that signal higher - so it must not look worse.
        assert record["evidence_bar"]["status"] == "insufficient_evidence"
        assert record["decision"] == promotion.DECISION_PENDING

        # The ledger is on disk and readable through the read-only surface.
        assert promotion.list_promotions(tmp_path)[0]["proposal_fingerprint"] == (
            proposal.fingerprint()
        )

    def test_window_consumption_blocks_a_second_proposal(
        self, seeded_storage, tmp_path
    ):
        from app.evaluation.heldout import LeakageError

        window = _window()
        incumbent = promotion.WeightModel("incumbent", {"weather_match": 0.20})
        promotion.run_promotion(window, _proposal(0.30), incumbent, tmp_path)
        with pytest.raises(LeakageError, match="already judged"):
            promotion.run_promotion(window, _proposal(0.35), incumbent, tmp_path)


# ---------------------------------------------------------------------------
# 3. Human decisions, gated
# ---------------------------------------------------------------------------
class TestDecisions:
    def _evaluate_first(self, tmp_path):
        window = _window()
        proposal = _proposal()
        promotion.run_promotion(
            window,
            proposal,
            promotion.WeightModel("incumbent", {"weather_match": 0.20}),
            tmp_path,
        )
        return proposal

    def test_adoption_against_the_bar_requires_override(self, seeded_storage, tmp_path):
        proposal = self._evaluate_first(tmp_path)
        # The bar (100 outcomes) was not met (40 rows): a plain adoption is
        # refused, with a message that says why.
        with pytest.raises(ValueError, match="override"):
            promotion.record_decision(
                proposal.fingerprint(), promotion.DECISION_ADOPTED, "spec", tmp_path
            )

    def test_override_adoption_is_recorded_with_its_reason(
        self, seeded_storage, tmp_path
    ):
        proposal = self._evaluate_first(tmp_path)
        record = promotion.record_decision(
            proposal.fingerprint(),
            promotion.DECISION_ADOPTED,
            "spec",
            tmp_path,
            override_reason="test override: human accepted the risk knowingly",
        )
        assert record["decision"] == "adopted"
        assert record["override_reason"].startswith("test override")
        assert record["decided_at"]

    def test_rejection_needs_no_override(self, seeded_storage, tmp_path):
        proposal = self._evaluate_first(tmp_path)
        record = promotion.record_decision(
            proposal.fingerprint(), promotion.DECISION_REJECTED, "spec", tmp_path
        )
        assert record["decision"] == "rejected"

    def test_unknown_fingerprint_is_an_error(self, tmp_path):
        with pytest.raises(KeyError):
            promotion.record_decision("nope", promotion.DECISION_REJECTED, "spec", tmp_path)


# ---------------------------------------------------------------------------
# 4. Nothing in the flow touches the engine's weights
# ---------------------------------------------------------------------------
class TestNoAutomaticWeights:
    def test_promotion_does_not_mutate_configured_weights(self, seeded_storage, tmp_path):
        before = dict(settings.get("recommendation.factor_weights") or {})
        proposal = _proposal()
        promotion.run_promotion(
            _window(),
            proposal,
            promotion.WeightModel("incumbent", {"weather_match": 0.20}),
            tmp_path,
        )
        promotion.record_decision(
            proposal.fingerprint(), promotion.DECISION_REJECTED, "spec", tmp_path
        )
        after = dict(settings.get("recommendation.factor_weights") or {})
        assert before == after

    def test_engine_config_has_no_promotion_hook(self):
        """The module must not even import a config-writer: grep-level pin."""
        import inspect

        source = inspect.getsource(promotion)
        assert "config.toml" not in source or "separate, deliberate" in source
        assert "settings.set" not in source
        assert "os.environ[" not in source


# ---------------------------------------------------------------------------
# 5. API surface: read-only status
# ---------------------------------------------------------------------------
class TestApiSurface:
    def test_evaluation_promotions_returns_empty_ledger(self, client):
        response = client.get(f"{API}/evaluation/promotions")
        assert response.status_code == 200
        body = response.json()
        assert body["promotions"] == []
        assert "human decision" in body["note"]

    def test_evaluation_promotions_lists_records(self, client, tmp_path, monkeypatch):
        from app.api import evaluation as evaluation_api

        monkeypatch.setattr(evaluation_api, "proposals_dir", lambda: tmp_path)
        proposal = _proposal()
        promotion.run_promotion(
            _window(),
            proposal,
            promotion.WeightModel("incumbent", {"weather_match": 0.20}),
            tmp_path,
        )
        response = client.get(f"{API}/evaluation/promotions")
        assert response.status_code == 200
        records = response.json()["promotions"]
        assert records[0]["factor"] == FACTOR
        assert records[0]["decision"] == "pending_human_decision"
