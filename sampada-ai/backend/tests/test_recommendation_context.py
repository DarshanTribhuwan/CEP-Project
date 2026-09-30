"""Tests for recommendation input-context persistence (schema v3).

Schema v2 stored *what the system concluded* (per-factor scores and weights).
These tests pin the new half of the evidence chain: *what the system consumed*
at recommendation time.

    1. migration      - a database written by the v2 schema keeps every row and
                        gains the input_context column in place;
    2. fidelity       - the stored context is exactly the input state that
                        generated the recommendation, not a recomputation;
    3. capture time   - the community signal is frozen as it was BEFORE the
                        recommendation itself was saved (the value the factor
                        actually scored against), per the evidence registry's
                        "app_issued_area at recommendation time";
    4. immutability   - later farm-profile edits do not rewrite stored context;
    5. regression     - the stored-recommendation API keeps its old shape; the
                        new field is purely additive, and rows written before
                        the migration report ``null`` rather than a fake
                        context.

No test here touches the network: the offline provider fakes from conftest
supply every external input.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.core import db as db_module
from tests.conftest import API


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _create_v2_db(path) -> None:
    """Build the v2 recommendations table exactly as that schema had it."""
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
            factor_scores    TEXT,
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
            factor_scores, price_estimate, risk, reasons, unavailable,
            pilot_plot_ha, model_versions, status, created_at
        ) VALUES (1, 'KHARIF', 'Pune', 'soybean', 1, 0.62, 0.55,
                  '[]', NULL, '{}', '[]', '[]', NULL, '{}', 'proposed',
                  '2026-01-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()


def _stored_top(farmer_client) -> dict:
    stored = farmer_client.get(
        f"{API}/recommendations?farm_id={farmer_client.farm_id}"
    ).json()
    return next(r for r in stored if r["rank"] == 1)


def _second_farmer_with_farm(client, district: str) -> tuple[dict[str, str], int]:
    """Register another farmer with a farm in the given district.

    Returns (auth headers, farm id).
    """
    response = client.post(
        f"{API}/auth/register",
        json={"name": "Other Farmer", "mobile": "9990001111", "language": "en", "pin": "5678"},
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    farm = client.post(
        f"{API}/farms",
        headers=headers,
        json={
            "label": "Neighbour farm",
            "land_area": 3,
            "land_unit": "ACRE",
            "irrigation_type": "RAINFED",
            "district": district,
        },
    )
    assert farm.status_code == 201, farm.text
    return headers, int(farm.json()["id"])


# ---------------------------------------------------------------------------
# 1. Migration from a v2 database
# ---------------------------------------------------------------------------
def test_v2_database_migrates_in_place_and_keeps_rows(tmp_path):
    db_file = tmp_path / "old.db"
    _create_v2_db(db_file)

    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

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

    assert "input_context" in columns
    assert version == db_module.SCHEMA_VERSION
    assert len(rows) == 1  # the pre-existing row survived
    assert rows[0]["crop_id"] == "soybean"
    # A pre-migration row reports no context rather than an empty one that
    # would look like a recorded snapshot.
    assert rows[0]["input_context"] is None


def test_fresh_database_is_v3_and_migration_is_idempotent():
    db_module.reset_db()  # fresh v3 schema
    conn = sqlite3.connect(db_module.db_path())
    columns = {row[1] for row in conn.execute("PRAGMA table_info(recommendations)")}
    conn.close()
    assert "input_context" in columns
    # Running init again must not fail or duplicate anything.
    db_module.init_db()


# ---------------------------------------------------------------------------
# 2. Snapshot fidelity
# ---------------------------------------------------------------------------
class TestContextFidelity:
    def test_stored_context_matches_the_inputs_that_generated_it(self, farmer_client):
        body = farmer_client.post(
            f"{API}/recommendations?lang=en",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        ).json()

        stored = _stored_top(farmer_client)
        context = stored["input_context"]
        assert isinstance(context, dict)

        # Farm profile, exactly as the farm was created in the fixture.
        assert context["farm"] == {
            "district": "Pune",
            "taluka": "Baramati",
            "village": "Testvillage",
            "latitude": 18.15,
            "longitude": 74.58,
            "irrigation_type": "PARTIAL",
            "water_source": "BOREWELL",
        }
        # The season the factors were scored against is the response's season.
        assert context["season"] == body["season"]
        # Candidates actually considered; the persisted options are a subset.
        option_codes = {option["crop"]["code"] for option in body["options"]}
        assert option_codes <= set(context["candidate_crop_ids"])
        # The soil estimate row that fed the soil factor.
        assert isinstance(context["soil_estimate_id"], int)

        # Weather provenance, verbatim from the provider result.
        weather = context["weather"]
        assert weather["data_source"] == "live"
        assert weather["source_name"] == "FakeWeather"
        assert weather["is_stale"] is False
        assert weather["retrieved_at"]

    def test_market_provenance_recorded_per_candidate(self, farmer_client):
        farmer_client.post(
            f"{API}/recommendations?lang=en",
            json={
                "farm_id": farmer_client.farm_id,
                "season": "KHARIF",
                "include_crops": ["soybean"],
            },
        )
        stored = _stored_top(farmer_client)
        assert stored["crop_id"] == "soybean"
        market = stored["input_context"]["market"]
        # One entry per candidate, keyed by crop id, carrying the provenance
        # that produced this option's price estimate.
        assert set(market) == {"soybean"}
        entry = market["soybean"]
        assert entry["data_source"] == "live"
        assert entry["source_name"] == "FakeMarket"
        assert entry["is_stale"] is False

    def test_farm_profile_values_are_copied_not_referenced(self, farmer_client):
        """The context must be a snapshot: values copied at generation time,
        so a later profile edit cannot silently redefine what was consumed."""
        farmer_client.post(
            f"{API}/recommendations?lang=en",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        )
        context = _stored_top(farmer_client)["input_context"]
        assert context["farm"]["district"] == "Pune"
        assert context["farm"]["irrigation_type"] == "PARTIAL"
        assert isinstance(context["farm"]["latitude"], float)


# ---------------------------------------------------------------------------
# 3. Capture time: community signal frozen pre-persistence
# ---------------------------------------------------------------------------
class TestCommunityCaptureTime:
    def test_first_recommendation_records_the_unavailable_state(self, farmer_client):
        """With no prior history the signal is unavailable - and the stored
        context must say exactly that, not a post-save state where this very
        recommendation has already populated app_issued_area."""
        farmer_client.post(
            f"{API}/recommendations?lang=en",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        )
        community = _stored_top(farmer_client)["input_context"]["community"]
        assert community["available"] is False
        assert community["issued_area_ha"] == {}
        assert community["reason"]

    def test_later_recommendation_sees_earlier_issued_area(self, client, farmer_client):
        """After another farm's recommendation exists, this farm's stored
        context carries the app-issued area as it stood at generation time."""
        other_headers, other_farm_id = _second_farmer_with_farm(client, "Pune")
        response = client.post(
            f"{API}/recommendations?lang=en",
            headers=other_headers,
            json={"farm_id": other_farm_id, "season": "KHARIF"},
        )
        assert response.status_code == 200, response.text

        farmer_client.post(
            f"{API}/recommendations?lang=en",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        )
        community = _stored_top(farmer_client)["input_context"]["community"]
        assert community["available"] is True
        assert sum(community["issued_area_ha"].values()) > 0
        assert "total_issued_area_ha" in community


# ---------------------------------------------------------------------------
# 4. Immutability under later profile edits
# ---------------------------------------------------------------------------
class TestContextImmutability:
    def test_farm_edit_does_not_rewrite_stored_context(self, farmer_client):
        farmer_client.post(
            f"{API}/recommendations?lang=en",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        )
        first = _stored_top(farmer_client)["input_context"]

        patch = farmer_client.patch(
            f"{API}/farms/{farmer_client.farm_id}",
            json={"village": "Newvillage", "latitude": 19.0},
        )
        assert patch.status_code == 200, patch.text

        farmer_client.post(
            f"{API}/recommendations?lang=en",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        )
        stored = farmer_client.get(
            f"{API}/recommendations?farm_id={farmer_client.farm_id}"
        ).json()
        by_created = sorted(stored, key=lambda r: r["id"])
        before, after = by_created[0]["input_context"], by_created[-1]["input_context"]

        # The old row still records the world as it was.
        assert before["farm"]["village"] == first["farm"]["village"] == "Testvillage"
        assert before["farm"]["latitude"] == first["farm"]["latitude"] == 18.15
        # The new row records the world as it now is.
        assert after["farm"]["village"] == "Newvillage"
        assert after["farm"]["latitude"] == 19.0


# ---------------------------------------------------------------------------
# 5. API regression
# ---------------------------------------------------------------------------
class TestStoredRecommendationRegression:
    def test_old_fields_are_untouched_and_context_is_additive(self, farmer_client):
        farmer_client.post(
            f"{API}/recommendations?lang=en",
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
            "factor_scores",
            "price_estimate",
            "risk",
            "reasons",
            "unavailable",
            "model_versions",
            "status",
            "created_at",
        ):
            assert field in record, field
        # v3 adds exactly one field to the API shape.
        assert isinstance(record["input_context"], dict)

    def test_factor_scores_snapshot_still_matches_the_live_scores(self, farmer_client):
        """The v2 fidelity guarantee must survive the v3 change untouched."""
        body = farmer_client.post(
            f"{API}/recommendations?lang=en",
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
            assert stored_scores[factor]["score"] == live["score"], factor
            assert stored_scores[factor]["configured_weight"] == live["configured_weight"], factor
            assert stored_scores[factor]["effective_weight"] == live["effective_weight"], factor


def _first_stored_id(farmer_client) -> int:
    stored = farmer_client.get(
        f"{API}/recommendations?farm_id={farmer_client.farm_id}"
    ).json()
    return int(stored[0]["id"])


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
