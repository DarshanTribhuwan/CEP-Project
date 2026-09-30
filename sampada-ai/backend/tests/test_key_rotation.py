"""Key rotation for encryption at rest.

The v4 encryption work made "operator adds a key and restarts" a complete
enablement path. This suite completes the hygiene loop the spec left open:
replacing a key that may have leaked, or is simply due for scheduled
rotation, without losing data and without breaking logins.

Design under test, stated so it can be attacked:

    * multiple keys  ``SAMPADA_ENC_KEYS`` holds an ordered, comma-separated
      list of base64 32-byte keys: the FIRST is the active key used for
      every new write; ALL of them are tried for decryption. Setting both
      this and the single ``SAMPADA_ENC_KEY`` is refused (loud, not "one of
      them wins").
    * no version bytes  AES-GCM's auth tag already answers "which key
      encrypted this cell?" decisively - a tag only verifies under its own
      key. A stored key-id would additionally claim that a key can never be
      dropped from a list whose positions may shift; try-each-key does not.
    * rotation = restart  operator sets ``SAMPADA_ENC_KEYS`` to
      ``new,old`` and restarts; the at-rest pass re-encrypts every cell
      under the new active key and re-derives the lookup hashes. The pass
      is idempotent, so a second restart is a no-op.
    * rotation window  between "env now lists the new key first" and "the
      at-rest pass has finished", rows still carry hashes of the old key.
      Equality lookups (login by mobile, community aggregation by district)
      therefore match a hash made under ANY configured key.
    * removing the old key afterwards must be safe: any cell the pass missed
      fails loudly (GCM tag), never decodes to junk.
    * /health reports the number of configured keys so the rotation state
      is visible from outside the process.
"""

from __future__ import annotations

import base64
import sqlite3

import pytest

from app.core import db as db_module
from tests.conftest import API

TEST_KEY_NEW = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
TEST_KEY_OLD = base64.urlsafe_b64encode(bytes(range(32, 64))).decode("ascii")

ENV_KEYS = "SAMPADA_ENC_KEYS"
ENV_SINGLE = "SAMPADA_ENC_KEY"
ENCRYPTED_PREFIX = "enc1:"


@pytest.fixture
def rotated_env(monkeypatch):
    """New key active, old key still accepted - the mid-rotation state."""
    monkeypatch.delenv(ENV_SINGLE, raising=False)
    monkeypatch.setenv(ENV_KEYS, f"{TEST_KEY_NEW},{TEST_KEY_OLD}")
    db_module.reset_db()
    return monkeypatch


@pytest.fixture
def old_key_env(monkeypatch):
    """Only the OLD key configured, set BEFORE the database and any farmer
    registration (fixture ordering: this runs before farmer_client). Tests
    then rotate by rewriting the env and restarting via init_db."""
    monkeypatch.delenv(ENV_SINGLE, raising=False)
    monkeypatch.setenv(ENV_KEYS, TEST_KEY_OLD)
    db_module.crypto.reset_key_cache()
    db_module.reset_db()
    return monkeypatch


def _raw() -> sqlite3.Connection:
    conn = sqlite3.connect(db_module.db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _plaintext_scan(conn: sqlite3.Connection, mobile: str, village: str) -> list[str]:
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


def _set_keys(monkeypatch, spec: str | None) -> None:
    """Rewrite the key configuration and simulate the process restart."""
    monkeypatch.delenv(ENV_SINGLE, raising=False)
    if spec is None:
        monkeypatch.delenv(ENV_KEYS, raising=False)
    else:
        monkeypatch.setenv(ENV_KEYS, spec)
    db_module.crypto.reset_key_cache()


# ---------------------------------------------------------------------------
# 1. Multi-key resolution
# ---------------------------------------------------------------------------
class TestMultiKeyResolution:
    def test_first_key_is_active_for_new_writes(self, rotated_env):
        """A fresh write must be decryptable under the FIRST key alone; if the
        old key were still active, removing it would destroy the data."""
        token = db_module.crypto.encrypt("9876543210")
        _set_keys(rotated_env, TEST_KEY_NEW)
        assert db_module.crypto.decrypt(token) == "9876543210"

        _set_keys(rotated_env, TEST_KEY_OLD)
        with pytest.raises(RuntimeError, match="decryption failed"):
            db_module.crypto.decrypt(token)

    def test_every_configured_key_can_decrypt_its_own_cells(self, rotated_env):
        """Cells written under the old key stay readable while it is listed,
        even though it is no longer first."""
        _set_keys(rotated_env, TEST_KEY_OLD)
        legacy_token = db_module.crypto.encrypt("legacy value")

        _set_keys(rotated_env, f"{TEST_KEY_NEW},{TEST_KEY_OLD}")
        assert db_module.crypto.decrypt(legacy_token) == "legacy value"

    def test_both_env_vars_set_is_refused(self, monkeypatch):
        """``SAMPADA_ENC_KEY`` and ``SAMPADA_ENC_KEYS`` together is an operator
        mistake; guessing which one wins is how keys quietly diverge."""
        monkeypatch.setenv(ENV_SINGLE, TEST_KEY_NEW)
        monkeypatch.setenv(ENV_KEYS, f"{TEST_KEY_NEW},{TEST_KEY_OLD}")
        db_module.crypto.reset_key_cache()
        with pytest.raises(RuntimeError, match="SAMPADA_ENC_KEY"):
            db_module.crypto.encrypt("x")

    def test_malformed_key_in_the_list_is_loud(self, monkeypatch):
        monkeypatch.delenv(ENV_SINGLE, raising=False)
        monkeypatch.setenv(ENV_KEYS, f"not-valid-base64!!,{TEST_KEY_NEW}")
        db_module.crypto.reset_key_cache()
        with pytest.raises(RuntimeError, match="32 bytes"):
            db_module.crypto.encrypt("x")


# ---------------------------------------------------------------------------
# 2. The rotation flow
# ---------------------------------------------------------------------------
class TestRotationFlow:
    def test_rotation_reencrypts_everything_under_the_new_key(
        self, monkeypatch, old_key_env, farmer_client
    ):
        """The full path: data written under the old key, the operator adds a
        new first key and restarts, every cell (including lookup hashes) ends
        up under the new key, and the API keeps working throughout."""
        farmer_client.post(
            f"{API}/recommendations",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        )
        with _raw() as conn:
            assert _plaintext_scan(conn, farmer_client.mobile, "Testvillage") == []

        # The rotation: new key first, old key retained, restart.
        _set_keys(monkeypatch, f"{TEST_KEY_NEW},{TEST_KEY_OLD}")
        db_module.init_db()

        with _raw() as conn:
            assert _plaintext_scan(conn, farmer_client.mobile, "Testvillage") == []

        # Prove the cells are under the NEW key: with only it configured,
        # everything decrypts; with only the old key, it fails loudly.
        _set_keys(monkeypatch, TEST_KEY_NEW)
        with _raw() as conn:
            stored_mobile = conn.execute("SELECT mobile FROM farmers").fetchone()["mobile"]
            context = conn.execute(
                "SELECT input_context FROM recommendations WHERE input_context IS NOT NULL"
            ).fetchone()
        assert db_module.crypto.decrypt(stored_mobile) == farmer_client.mobile
        assert context is not None, "recommendation with input_context must exist"
        db_module.crypto.decrypt(context["input_context"])

        _set_keys(monkeypatch, TEST_KEY_OLD)
        with _raw() as conn:
            stored_mobile = conn.execute("SELECT mobile FROM farmers").fetchone()["mobile"]
        with pytest.raises(RuntimeError, match="decryption failed"):
            db_module.crypto.decrypt(stored_mobile)

    def test_login_and_data_survive_the_rotation(
        self, monkeypatch, old_key_env, farmer_client, client
    ):
        _set_keys(monkeypatch, f"{TEST_KEY_NEW},{TEST_KEY_OLD}")
        db_module.init_db()

        response = client.post(
            f"{API}/auth/login",
            json={"mobile": farmer_client.mobile, "pin": "1234"},
        )
        assert response.status_code == 200
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        farm = client.get(
            f"{API}/farms/{farmer_client.farm_id}", headers=headers
        ).json()
        assert farm["village"] == "Testvillage"
        assert farm["district"] == "Pune"

    def test_lookups_match_a_hash_made_under_any_configured_key(
        self, monkeypatch
    ):
        """The rotation window: a row's lookup hash was made under the old
        key, but the process now lists the new key first. Equality lookups
        must try every configured key, or farmers cannot log in until the
        at-rest pass has rewritten their row."""
        from app.repositories import farmers as farmers_repo

        _set_keys(monkeypatch, TEST_KEY_OLD)
        db_module.reset_db()
        farmers_repo.create_farmer("Window Farmer", "9876500000", "mr", "1234")

        _set_keys(monkeypatch, f"{TEST_KEY_NEW},{TEST_KEY_OLD}")
        found = farmers_repo.get_farmer_by_mobile("9876500000")
        assert found is not None, "lookup must match under a non-first key"
        assert farmers_repo.authenticate("9876500000", "1234") is not None

    def test_removing_the_old_key_afterwards_is_safe(
        self, monkeypatch, old_key_env, farmer_client, client
    ):
        """After a completed rotation the old key must be droppable. Any cell
        the pass had missed fails loudly here instead of rotting silently."""
        farmer_client.post(
            f"{API}/recommendations",
            json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
        )

        _set_keys(monkeypatch, f"{TEST_KEY_NEW},{TEST_KEY_OLD}")
        db_module.init_db()
        _set_keys(monkeypatch, TEST_KEY_NEW)  # old key gone
        db_module.init_db()

        response = client.post(
            f"{API}/auth/login",
            json={"mobile": farmer_client.mobile, "pin": "1234"},
        )
        assert response.status_code == 200
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        farm = client.get(
            f"{API}/farms/{farmer_client.farm_id}", headers=headers
        ).json()
        assert farm["village"] == "Testvillage"

    def test_second_restart_is_a_no_op(self, monkeypatch, old_key_env, farmer_client):
        _set_keys(monkeypatch, f"{TEST_KEY_NEW},{TEST_KEY_OLD}")
        db_module.init_db()
        db_module.init_db()
        with _raw() as conn:
            assert _plaintext_scan(conn, farmer_client.mobile, "Testvillage") == []


# ---------------------------------------------------------------------------
# 3. Visibility
# ---------------------------------------------------------------------------
class TestHealthRotationSurface:
    def test_health_reports_the_number_of_configured_keys(self, monkeypatch, client):
        _set_keys(monkeypatch, f"{TEST_KEY_NEW},{TEST_KEY_OLD}")
        body = client.get(f"{API}/health").json()
        assert body["encryption_at_rest"] == "enabled"
        assert body["encryption_key_count"] == 2

        _set_keys(monkeypatch, TEST_KEY_NEW)
        body = client.get(f"{API}/health").json()
        assert body["encryption_key_count"] == 1

        _set_keys(monkeypatch, None)
        body = client.get(f"{API}/health").json()
        assert body["encryption_at_rest"] == "disabled"
        assert body["encryption_key_count"] == 0
