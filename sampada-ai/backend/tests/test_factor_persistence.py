"""Tests for per-factor score persistence (schema v2).

Three concerns:

    1. migration      - a database written by the v1 schema keeps every row and
                        gains the factor_scores column in place;
    2. fidelity       - the stored snapshot is exactly what was used to
                        generate the recommendation, not a recomputation that
                        could drift if weights later change;
    3. regression     - the stored-recommendation API keeps its old shape; the
                        new field is purely additive, and rows written before
                        the migration report ``null`` rather than a fake
                        snapshot.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.core import db as db_module
from tests.conftest import API


# ---------------------------------------------------------------------------
# 1. Migration from a v1 database
# ---------------------------------------------------------------------------
def _create_v1_db(path) -> None:
    """Build the v1 recommendations table exactly as the old schema had it."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE recommendations (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            farm_id          INTEGER NOT NULL,
            season           TEXT    NOT NULL,
            district         TEXT,
            crop_id          TEXT    NOT NULL,
            rank             INTEGER NOT NULL,
            final_score      REAL    NOT NULL,
            confidence       REAL    NOT NULL,
            price_estimate   TEXT,
            risk             TEXT,
            reasons          TEXT,
            unavailable      TEXT,
            pilot_plot_ha    REAL,
            model_versions   TEXT    NOT NULL,
            status           TEXT    NOT NULL DEFAULT 'proposed',
            created_at       TEXT    NOT NULL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO recommendations (
            farm_id, season, district, crop_id, rank, final_score, confidence,
            price_estimate, risk, reasons, unavailable, pilot_plot_ha,
            model_versions, status, created_at
        ) VALUES (1, 'KHARIF', 'Pune', 'soybean', 1, 0.62, 0.55,
                  NULL, '{}', '[]', '[]', NULL, '{}', 'proposed', '2026-01-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()


def test_v1_database_migrates_in_place_and_keeps_rows(tmp_path):
    db_file = tmp_path / "old.db"
    _create_v1_db(db_file)

    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    # Run the app's migration against the old file.
    saved = db_module.db_path
    db_module.db_path = lambda: db_file  # type: ignore[method-assign]
    try:
        db_module.init_db()
    finally:
        db_module.db_path = saved  # type: ignore[method-assign]

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    columns = {row[1] for row in conn.execute("PRAGMA table_info(recommendations)")}
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    rows = conn.execute("SELECT * FROM recommendations").fetchall()
    conn.close()

    assert "factor_scores" in columns
    assert version == db_module.SCHEMA_VERSION
    assert len(rows) == 1  # the pre-existing row survived
    assert rows[0]["crop_id"] == "soybean"
    assert rows[0]["factor_scores"] is None


def test_fresh_database_has_the_column_and_is_idempotent():
    db_module.reset_db()  # fresh v2 schema
    conn = sqlite3.connect(db_module.db_path())
    columns = {row[1] for row in conn.execute("PRAGMA table_info(recommendations)")}
    conn.close()
    assert "factor_scores" in columns
    # Running init again must not fail or duplicate anything.
    db_module.init_db()


# ---------------------------------------------------------------------------
# 2. Snapshot fidelity
# ---------------------------------------------------------------------------
class TestSnapshotFidelity:
    def test_stored_snapshot_matches_the_response_that_generated_it(self, farmer_client):
        body = farmer_client.post(
            f"{API}/recommendations",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        ).json()
        option = body["options"][0]

        stored = farmer_client.get(
            f"{API}/recommendations/{_stored_id(farmer_client, option)}"
        ).json()

        live_scores = {s["factor"]: s for s in option["scores"]}
        stored_scores = {f["factor"]: f for f in stored["factor_scores"]}
        assert set(stored_scores) == set(live_scores)

        for factor, live in live_scores.items():
            snap = stored_scores[factor]
            assert snap["score"] == live["score"], factor
            assert snap["configured_weight"] == live["configured_weight"], factor
            assert snap["effective_weight"] == live["effective_weight"], factor
            assert snap["available"] == live["available"], factor
            # quality is stored too - it feeds the confidence maths
            assert "quality" in snap

    def test_effective_weights_in_snapshot_sum_to_one(self, farmer_client):
        farmer_client.post(
            f"{API}/recommendations",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        )
        stored = farmer_client.get(
            f"{API}/recommendations?farm_id={farmer_client.farm_id}"
        ).json()
        top = next(r for r in stored if r["rank"] == 1)
        available = [f for f in top["factor_scores"] if f["available"]]
        total = sum(f["effective_weight"] for f in available)
        assert total == pytest.approx(1.0, abs=0.01)

    def test_snapshot_records_the_reliability_scaled_weight(self, farmer_client):
        """The market factor's configured_weight in storage must be the scaled
        one that actually applied - not the unscaled config value."""
        from app.services import price_reliability as pr

        farmer_client.post(
            f"{API}/recommendations",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF", "include_crops": ["soybean"]},
        )
        stored = farmer_client.get(
            f"{API}/recommendations?farm_id={farmer_client.farm_id}"
        ).json()
        top = next(r for r in stored if r["rank"] == 1)
        market = next(f for f in top["factor_scores"] if f["factor"] == "market_price")
        assert market["configured_weight"] == pytest.approx(
            0.25 * pr.reliability_for("soybean")
        )


def _stored_id(farmer_client, option: dict) -> int:
    stored = farmer_client.get(
        f"{API}/recommendations?farm_id={farmer_client.farm_id}"
    ).json()
    row = next(
        r
        for r in stored
        if r["crop_id"] == option["crop"]["code"] and r["rank"] == option["rank"]
    )
    return int(row["id"])


# ---------------------------------------------------------------------------
# 3. API regression
# ---------------------------------------------------------------------------
class TestStoredRecommendationRegression:
    def test_old_fields_are_untouched(self, farmer_client):
        farmer_client.post(
            f"{API}/recommendations",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        )
        record = farmer_client.get(
            f"{API}/recommendations/{_first_stored_id(farmer_client)}"
        ).json()
        for field in (
            "id",
            "farm_id",
            "season",
            "crop_id",
            "rank",
            "final_score",
            "confidence",
            "price_estimate",
            "risk",
            "reasons",
            "unavailable",
            "model_versions",
            "status",
            "created_at",
        ):
            assert field in record, field

    def test_factor_scores_is_additive_and_populated(self, farmer_client):
        farmer_client.post(
            f"{API}/recommendations",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        )
        record = farmer_client.get(
            f"{API}/recommendations/{_first_stored_id(farmer_client)}"
        ).json()
        assert isinstance(record["factor_scores"], list) and record["factor_scores"]
        assert {f["factor"] for f in record["factor_scores"]} == {
            "soil_suitability",
            "weather_match",
            "market_price",
            "water_availability",
            "farmer_history",
            "community_saturation",
        }


def _first_stored_id(farmer_client) -> int:
    stored = farmer_client.get(
        f"{API}/recommendations?farm_id={farmer_client.farm_id}"
    ).json()
    return int(stored[0]["id"])
