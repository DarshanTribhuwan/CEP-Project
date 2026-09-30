"""Tests for encryption at rest (schema v4).

Scope, exactly as the spec names it: mobile numbers and farm locations.
Concretely that is four kinds of stored value:

    1. ``farmers.mobile``          (with a keyed lookup hash for logins)
    2. ``farms.village/taluka/district`` and ``farms.latitude/longitude``
    3. ``recommendations.district`` (searchable only via a keyed lookup hash)
    4. ``recommendations.input_context`` (the v3 snapshot embeds village and
       coordinates, so the whole blob is encrypted)

Four concerns:

    1. at-rest      - with a key configured, the raw database contains no
                      plaintext PII anywhere in those columns;
    2. transparency - every API path that worked before still returns the same
                      plaintext values it always did (login, farm read,
                      stored recommendations, community aggregation);
    3. migration    - a database written by the pre-encryption build (v3,
                      plaintext) is re-encrypted in place when a key appears,
                      keeping every row and every API behaviour;
    4. fail-closed  - an operator who sets ``security.encryption_required``
                      without configuring a key gets a refused startup, not a
                      silently unencrypted pilot.

A database with no key configured keeps working in plaintext mode - that is the
existing developer/demo behaviour, and ``/health`` reports it as
``encryption_at_rest: "disabled"`` so the state is visible, never assumed.
"""

from __future__ import annotations

import base64
import sqlite3

import pytest

from app.core import db as db_module
from tests.conftest import API

# A fixed 32-byte test key. Generated once with the same scheme
# scripts/generate_enc_key.py prints; never a real secret.
TEST_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
ENV_VAR = "SAMPADA_ENC_KEY"

ENCRYPTED_PREFIX = "enc1:"


@pytest.fixture
def encrypted_env(monkeypatch):
    """A key configured BEFORE the database is (re)created."""
    monkeypatch.setenv(ENV_VAR, TEST_KEY)
    db_module.reset_db()
    return monkeypatch


def _raw(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or db_module.db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _plaintext_scan(conn: sqlite3.Connection, mobile: str, village: str) -> list[str]:
    """Return every encrypted-scope cell still holding recognizable plaintext."""
    leaks: list[str] = []
    for table, column in (
        ("farmers", "mobile"),
        ("farms", "village"),
        ("farms", "taluka"),
        ("farms", "district"),
        ("recommendations", "district"),
        ("recommendations", "input_context"),
    ):
        for row in conn.execute(f"SELECT {column} AS v FROM {table}"):
            value = row["v"]
            if value is None:
                continue
            text = str(value)
            if not text.startswith(ENCRYPTED_PREFIX) and (
                mobile in text or village in text
            ):
                leaks.append(f"{table}.{column}")
    return leaks


# ---------------------------------------------------------------------------
# 1. At-rest guarantees
# ---------------------------------------------------------------------------
class TestEncryptedAtRest:
    def test_no_pii_in_the_raw_database(self, encrypted_env, farmer_client):
        farmer_client.post(
            f"{API}/recommendations",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        )
        with _raw() as conn:
            leaks = _plaintext_scan(conn, farmer_client.mobile, "Testvillage")
        assert leaks == []

    def test_mobile_lookup_hash_is_keyed_and_stable(self, encrypted_env, farmer_client):
        """Same number -> same hash (so login works); hash is keyed (so the
        number cannot be brute-forced from the hash column alone)."""
        from app.core import crypto

        with _raw() as conn:
            stored = conn.execute(
                "SELECT mobile_lookup FROM farmers WHERE id = ?", (1,)
            ).fetchone()["mobile_lookup"]
        assert stored and not stored.startswith(ENCRYPTED_PREFIX)
        assert stored == crypto.lookup_hash(farmer_client.mobile)
        # Keyed: not the bare SHA-256 a leaked database could be dictionary-
        # attacked with, and different under a different key.
        import hashlib

        assert stored != hashlib.sha256(farmer_client.mobile.encode()).hexdigest()
        assert crypto.lookup_hash(farmer_client.mobile, key=b"other".ljust(32, b"0")) != stored

    def test_ciphertext_is_not_a_deterministic_mapping(self, encrypted_env):
        """Two rows with the same number must not produce the same ciphertext
        (random nonce), or the ciphertext column itself leaks equality."""
        from app.core import crypto

        first = crypto.encrypt("9876543210")
        second = crypto.encrypt("9876543210")
        assert first != second
        assert crypto.decrypt(first) == crypto.decrypt(second) == "9876543210"


# ---------------------------------------------------------------------------
# 2. Transparency: the API behaves exactly as before
# ---------------------------------------------------------------------------
class TestApiTransparency:
    def test_login_and_duplicate_detection_still_work(
        self, encrypted_env, client, farmer_client
    ):
        response = client.post(
            f"{API}/auth/login",
            json={"mobile": farmer_client.mobile, "pin": "1234"},
        )
        assert response.status_code == 200
        assert response.json()["farmer"]["mobile"] == farmer_client.mobile

        duplicate = client.post(
            f"{API}/auth/register",
            json={
                "name": "Other Farmer",
                "mobile": farmer_client.mobile,
                "language": "en",
                "pin": "5678",
            },
        )
        assert duplicate.status_code != 201  # uniqueness survives encryption

    def test_farm_read_returns_plaintext_location(self, encrypted_env, farmer_client):
        farm = farmer_client.get(f"{API}/farms/{farmer_client.farm_id}").json()
        assert farm["village"] == "Testvillage"
        assert farm["district"] == "Pune"
        assert farm["taluka"] == "Baramati"
        assert farm["latitude"] == pytest.approx(18.15)
        assert farm["longitude"] == pytest.approx(74.58)

    def test_farm_patch_reencrypts_changed_locations(self, encrypted_env, farmer_client):
        response = farmer_client.patch(
            f"{API}/farms/{farmer_client.farm_id}",
            json={"village": "Newvillage", "latitude": 18.2},
        )
        assert response.status_code == 200
        assert response.json()["village"] == "Newvillage"
        with _raw() as conn:
            row = conn.execute(
                "SELECT village, latitude FROM farms WHERE id = ?",
                (farmer_client.farm_id,),
            ).fetchone()
        assert str(row["village"]).startswith(ENCRYPTED_PREFIX)
        assert str(row["latitude"]).startswith(ENCRYPTED_PREFIX)

    def test_stored_recommendation_decrypted_through_the_api(
        self, encrypted_env, farmer_client
    ):
        farmer_client.post(
            f"{API}/recommendations",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        )
        stored = farmer_client.get(
            f"{API}/recommendations?farm_id={farmer_client.farm_id}"
        ).json()
        top = next(r for r in stored if r["rank"] == 1)
        assert top["district"] == "Pune"
        context = top["input_context"]
        assert context is not None
        assert context["farm"]["village"] == "Testvillage"
        assert context["farm"]["latitude"] == pytest.approx(18.15)

    def test_community_aggregation_survives_encryption(
        self, encrypted_env, farmer_client
    ):
        """``app_issued_area`` groups by district. With encrypted districts the
        SQL cannot compare plaintext, so the keyed lookup hash must do it."""
        from app.repositories import recommendations as recs

        farmer_client.post(
            f"{API}/recommendations",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        )
        issued = recs.app_issued_area("KHARIF", "Pune")
        assert issued, "no app-issued area found for an encrypted district"
        assert sum(issued.values()) > 0

    def test_health_reports_encryption_state(self, monkeypatch, client):
        """Enabled/disabled is the real on-disk state, so the test simulates a
        process restart (crypto resolves the key once at first use) between
        states."""
        from app.core import crypto

        monkeypatch.setenv(ENV_VAR, TEST_KEY)
        crypto.reset_key_cache()
        body = client.get(f"{API}/health").json()
        assert body["encryption_at_rest"] == "enabled"

        monkeypatch.delenv(ENV_VAR, raising=False)
        crypto.reset_key_cache()
        body = client.get(f"{API}/health").json()
        assert body["encryption_at_rest"] == "disabled"
        assert "SAMPADA_ENC_KEY" in body["encryption_note"]


# ---------------------------------------------------------------------------
# 3. Migration: plaintext v3 database -> encrypted in place
# ---------------------------------------------------------------------------
def _seed_plaintext_rows() -> None:
    """Write farmer + farm + recommendation through the repos with NO key set,
    exactly as the pre-encryption build stored them."""
    from app.repositories import farms as farms_repo
    from app.repositories import farmers as farmers_repo
    from app.repositories import recommendations as recs_repo

    farmer = farmers_repo.create_farmer("Legacy Farmer", "9876543210", "mr", "1234")
    farm = farms_repo.create_farm(
        farmer["id"],
        {
            "land_area_ha": 2.0,
            "land_unit": "ACRE",
            "irrigation_type": "PARTIAL",
            "water_source": "BOREWELL",
            "district": "Pune",
            "taluka": "Baramati",
            "village": "Testvillage",
            "latitude": 18.15,
            "longitude": 74.58,
        },
    )
    recs_repo.save_recommendations(
        farm_id=farm["id"],
        season="KHARIF",
        district="Pune",
        options=[
            {
                "crop_id": "soybean",
                "rank": 1,
                "final_score": 0.6,
                "confidence": 0.55,
                "input_context": {"farm": {"village": "Testvillage", "latitude": 18.15}},
            }
        ],
        unavailable=[],
        model_versions={"recommender": "test"},
    )


class TestMigrationFromPlaintext:
    def test_plaintext_database_is_reencrypted_in_place(self, monkeypatch):
        monkeypatch.delenv(ENV_VAR, raising=False)
        db_module.reset_db()
        _seed_plaintext_rows()

        with _raw() as conn:
            assert conn.execute("SELECT COUNT(*) FROM farmers").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM farms").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0] == 1
            leaks_before = _plaintext_scan(conn, "9876543210", "Testvillage")
        assert leaks_before, "seed must be plaintext for this test to mean anything"

        # The operator adds a key and restarts the backend.
        monkeypatch.setenv(ENV_VAR, TEST_KEY)
        db_module.init_db()

        with _raw() as conn:
            leaks = _plaintext_scan(conn, "9876543210", "Testvillage")
        assert leaks == []
        version = int(
            sqlite3.connect(db_module.db_path()).execute(
                "PRAGMA user_version"
            ).fetchone()[0]
        )
        assert version == db_module.SCHEMA_VERSION

    def test_migrated_rows_keep_working_through_the_api(self, monkeypatch, client):
        monkeypatch.delenv(ENV_VAR, raising=False)
        db_module.reset_db()
        _seed_plaintext_rows()
        monkeypatch.setenv(ENV_VAR, TEST_KEY)
        db_module.init_db()

        # Login by mobile: the lookup hash was backfilled during migration.
        response = client.post(
            f"{API}/auth/login", json={"mobile": "9876543210", "pin": "1234"}
        )
        assert response.status_code == 200
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

        farms = client.get(f"{API}/farms", headers=headers).json()
        assert farms[0]["village"] == "Testvillage"

        stored = client.get(
            f"{API}/recommendations?farm_id={farms[0]['id']}", headers=headers
        ).json()
        assert stored[0]["input_context"]["farm"]["village"] == "Testvillage"

    def test_restarted_server_does_not_double_migrate(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, TEST_KEY)
        db_module.crypto.reset_key_cache()
        db_module.reset_db()
        _seed_plaintext_rows()
        db_module.init_db()
        # A second boot must be a no-op, not a corruption pass.
        db_module.init_db()
        with _raw() as conn:
            leaks = _plaintext_scan(conn, "9876543210", "Testvillage")
        assert leaks == []


# ---------------------------------------------------------------------------
# 4. Fail-closed pilot mode
# ---------------------------------------------------------------------------
class TestFailClosed:
    def test_encryption_required_without_key_refuses_to_start(self, monkeypatch):
        monkeypatch.delenv(ENV_VAR, raising=False)
        monkeypatch.setitem(
            db_module.settings._data, "security", {"encryption_required": True}
        )
        db_module.crypto.reset_key_cache()
        with pytest.raises(RuntimeError, match="encryption"):
            db_module.init_db()

    def test_plaintext_mode_is_the_visible_default(self, monkeypatch, client):
        """No key configured: the dev/demo default keeps working, and /health
        says so loudly instead of pretending."""
        from app.core import crypto

        monkeypatch.delenv(ENV_VAR, raising=False)
        crypto.reset_key_cache()
        body = client.get(f"{API}/health").json()
        assert body["encryption_at_rest"] == "disabled"
        assert body["encryption_note"]
