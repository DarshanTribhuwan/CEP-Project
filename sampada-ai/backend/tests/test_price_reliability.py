"""Tests for crop-aware price-factor reliability scaling.

Synthetic only: the coverage-to-reliability rule is checked against stated
values, the lookup table is the real committed one (generated from the
backtest report), and the recommendation integration uses the standard
offline fakes. No network anywhere.
"""

from __future__ import annotations

import pytest

from app.services import price_reliability as pr
from tests.conftest import API, DEFAULT_SERIES, FakeMarketProvider

# The committed table was generated from the 2026-09-23 backtest report. These
# values are pinned on purpose: if the backtest is re-run and the evidence
# changes, regenerating the file must be a conscious act that updates this
# test too - silent drift would defeat the audit trail.
PINNED = {"soybean": 0.99, "onion": 0.71, "tur": 0.65}


class TestRule:
    def test_target_coverage_maps_to_full_weight(self):
        assert pr.reliability_from_coverage(0.80) == 1.0

    def test_poor_coverage_scales_down(self):
        assert pr.reliability_from_coverage(0.40) == 0.5  # 0.40/0.80 = 0.5

    def test_floor_holds(self):
        assert pr.reliability_from_coverage(0.10) == 0.5

    def test_above_target_clamps(self):
        assert pr.reliability_from_coverage(0.95) == 1.0


class TestCommittedTable:
    def test_measured_crops_carry_their_evidence(self):
        for crop_id, expected in PINNED.items():
            assert pr.reliability_for(crop_id) == expected
            assert pr.measured(crop_id) is True

    def test_ordering_follows_coverage(self):
        assert pr.reliability_for("soybean") > pr.reliability_for("onion")

    def test_unmeasured_crop_gets_default_and_is_flagged(self):
        assert pr.reliability_for("cotton") == pr.DEFAULT_RELIABILITY
        assert pr.measured("cotton") is False

    def test_active_with_real_table(self):
        assert pr.active() is True


class TestInertDegradation:
    def test_no_table_means_no_scaling(self, monkeypatch):
        monkeypatch.setattr(pr, "_TABLE", None)
        assert pr.active() is False
        assert pr.reliability_for("onion") == 1.0
        assert pr.reliability_for("anything") == 1.0

    def test_no_table_means_nothing_needs_disclaiming(self, monkeypatch):
        monkeypatch.setattr(pr, "_TABLE", None)
        assert pr.measured("cotton") is True

    def test_corrupt_file_is_inert_not_fatal(self, monkeypatch, tmp_path):
        bad = tmp_path / "price_reliability.json"
        bad.write_text("{not json at all", encoding="utf-8")
        monkeypatch.setattr(pr, "TABLE_PATH", bad)
        assert pr._load_table() is None

    def test_malformed_entries_are_dropped(self, monkeypatch, tmp_path):
        import json

        bad = tmp_path / "price_reliability.json"
        bad.write_text(
            json.dumps(
                {
                    "crops": {
                        "onion": {"reliability": 0.71},
                        "bad1": {"reliability": "high"},
                        "bad2": {"reliability": 1.5},
                        "bad3": "not a dict",
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(pr, "TABLE_PATH", bad)
        table = pr._load_table()
        assert table == {"onion": {"reliability": 0.71}}


class TestRecommendationIntegration:
    def test_measured_and_unmeasured_crops_differ_visibly(self, farmer_client, offline_providers, monkeypatch):
        offline_providers(
            market=FakeMarketProvider(
                {
                    **DEFAULT_SERIES,
                    # cotton is deliberately absent from the backtest table
                    "cotton": [7000, 7050, 7100, 7150, 7200, 7250, 7300, 7350, 7400, 7450, 7500, 7550],
                }
            )
        )
        response = farmer_client.post(
            f"{API}/recommendations",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF", "include_crops": ["soybean", "cotton"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        by_crop = {option["crop"]["code"]: option for option in body["options"]}

        soybean_scores = {s["factor"]: s for s in by_crop["soybean"]["scores"]}
        cotton_scores = {s["factor"]: s for s in by_crop["cotton"]["scores"]}

        # The configured weight itself carries the reliability scaling, so the
        # score breakdown shows exactly what drove the answer.
        assert soybean_scores["market_price"]["configured_weight"] == pytest.approx(0.25 * PINNED["soybean"])
        assert cotton_scores["market_price"]["configured_weight"] == pytest.approx(
            0.25 * pr.DEFAULT_RELIABILITY
        )
        assert (
            cotton_scores["market_price"]["configured_weight"]
            < soybean_scores["market_price"]["configured_weight"]
        )

        # And the unmeasured crop says so in its reasons; the measured one does not.
        cotton_codes = {r["code"] for r in by_crop["cotton"]["reasons"]}
        soybean_codes = {r["code"] for r in by_crop["soybean"]["reasons"]}
        assert "price_unmeasured" in cotton_codes
        assert "price_unmeasured" not in soybean_codes

    def test_feature_inert_restores_previous_weights(self, farmer_client, offline_providers, monkeypatch):
        monkeypatch.setattr(pr, "_TABLE", None)
        offline_providers()
        response = farmer_client.post(
            f"{API}/recommendations",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        for option in body["options"]:
            market = next(s for s in option["scores"] if s["factor"] == "market_price")
            # Inert: every market weight equals the configured factor weight.
            assert market["configured_weight"] == pytest.approx(0.25)
            assert all(r["code"] != "price_unmeasured" for r in option["reasons"])
