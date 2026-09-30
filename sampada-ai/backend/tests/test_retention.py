"""Data retention: explicit, config-driven, never silent.

The spec's encryption section (§8.5) deliberately left a retention policy
open. This module closes it with the project's standing bias: deleting a
farmer's evidence is a decision someone must make, not background noise.

Design under test:

    * opt-in        every threshold defaults to 0 = keep forever. A deployment
                    that never configures ``[retention]`` never loses a row.
    * what can age  three classes: ``outcomes`` (season results),
                    ``recommendations`` (the system's stored statements, whose
                    feedback and predictions cascade with them), and
                    ``auth_tokens`` (dead sessions). Soil estimates and crop
                    history are farm-level reference data and do not age.
    * cascade shape an outcome older than a purged recommendation SURVIVES
                    with ``recommendation_id`` set to NULL - the season
                    happened; only the pointer to the aged-out statement is
                    lost. That is the honest shape, not a disguised deletion.
    * who purges    nobody automatic. ``scripts/apply_retention.py`` is the
                    operator's tool: dry-run by default, ``--apply`` to
                    execute, and it prints exactly what it would delete.
                    /health reports the configured policy so it is visible
                    from outside the process.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core import db as db_module
from tests.conftest import API

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _policy(monkeypatch, **values) -> None:
    merged = {
        "outcomes_months": 0,
        "recommendations_months": 0,
        "auth_tokens_days": 0,
        **values,
    }
    monkeypatch.setitem(db_module.settings._data, "retention", merged)


def _backdate(table: str, row_id: int, months: int, days: int = 0) -> str:
    """Rewrite created_at to an age in the past, exactly as the pre-existing
    build would have stored it (same ISO format, same timezone)."""
    old = (
        datetime.now(timezone.utc) - timedelta(days=30 * months + days)
    ).isoformat(timespec="seconds")
    with db_module.get_conn() as conn:
        conn.execute(
            f"UPDATE {table} SET created_at = ? WHERE id = ?", (old, row_id)
        )
    return old


def _seed_evidence(farmer_client) -> dict[str, int]:
    """One full evidence chain via the API: recommendation -> feedback ->
    outcome. Returns the stored ids."""
    farmer_client.post(
        f"{API}/recommendations",
        json={"farm_id": farmer_client.farm_id, "season": "KHARIF"},
    )
    stored = farmer_client.get(
        f"{API}/recommendations?farm_id={farmer_client.farm_id}"
    ).json()
    rec_id = stored[0]["id"]
    farmer_client.post(
        f"{API}/recommendations/{rec_id}/feedback",
        json={"followed": "YES", "reason": "OTHER"},
    )
    outcome = farmer_client.post(
        f"{API}/outcomes",
        json={
            "farm_id": farmer_client.farm_id,
            "crop_id": "soybean",
            "recommendation_id": rec_id,
            "season": "KHARIF",
            "year": 2026,
            "yield_quintal": 10.0,
            "cost_inr": 20000,
            "sell_price_inr_per_quintal": 4400,
        },
    ).json()
    return {"recommendation": rec_id, "feedback": stored[0]["id"], "outcome": outcome["id"]}


# ---------------------------------------------------------------------------
# 1. Default: keep forever
# ---------------------------------------------------------------------------
class TestDefaultKeepForever:
    def test_policy_defaults_to_zero(self, monkeypatch):
        from app.core import retention

        monkeypatch.delitem(db_module.settings._data, "retention", raising=False)
        assert retention.policy() == {
            "outcomes_months": 0,
            "recommendations_months": 0,
            "auth_tokens_days": 0,
        }

    def test_old_rows_are_never_deleted_without_a_policy(self, monkeypatch, farmer_client):
        from app.core import retention

        ids = _seed_evidence(farmer_client)
        for table, row_id in (
            ("recommendations", ids["recommendation"]),
            ("outcomes", ids["outcome"]),
        ):
            _backdate(table, row_id, months=60)  # five years old

        monkeypatch.delitem(db_module.settings._data, "retention", raising=False)
        assert retention.expired_counts() == {
            "outcomes": 0,
            "recommendations": 0,
            "feedback": 0,
            "predictions": 0,
            "auth_tokens": 0,
        }
        assert retention.purge() == retention.expired_counts()
        with db_module.get_conn() as conn:
            assert conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# 2. Age-based selection
# ---------------------------------------------------------------------------
class TestSelection:
    def test_only_rows_older_than_the_threshold_are_counted(
        self, monkeypatch, farmer_client
    ):
        from app.core import retention

        _policy(monkeypatch, outcomes_months=24)
        ids = _seed_evidence(farmer_client)
        _backdate("outcomes", ids["outcome"], months=30)
        assert retention.expired_counts()["outcomes"] == 1

        _backdate("outcomes", ids["outcome"], months=6)
        assert retention.expired_counts()["outcomes"] == 0

    def test_boundary_row_is_kept(self, monkeypatch, farmer_client):
        """``created_at < cutoff`` is strict: a row exactly at the cutoff is
        kept, so a monthly job never races a row written the same instant."""
        from app.core import retention

        _policy(monkeypatch, outcomes_months=24)
        ids = _seed_evidence(farmer_client)
        _backdate("outcomes", ids["outcome"], months=24)
        assert retention.expired_counts()["outcomes"] == 0

        _backdate("outcomes", ids["outcome"], months=24, days=1)
        assert retention.expired_counts()["outcomes"] == 1


# ---------------------------------------------------------------------------
# 3. The purge and its cascade shape
# ---------------------------------------------------------------------------
class TestPurge:
    def test_purge_removes_old_recommendations_with_feedback_and_keeps_outcomes(
        self, monkeypatch, farmer_client
    ):
        from app.core import retention

        _policy(monkeypatch, recommendations_months=18)
        ids = _seed_evidence(farmer_client)
        # POST /recommendations persists one row per option (3 crops by
        # default); the whole generated set ages together.
        _backdate("recommendations", ids["recommendation"], months=24)
        with db_module.get_conn() as conn:
            for row in conn.execute(
                "SELECT id FROM recommendations WHERE created_at >= ?",
                (
                    (
                        datetime.now(timezone.utc) - timedelta(days=18 * 30)
                    ).isoformat(timespec="seconds"),
                ),
            ).fetchall():
                _backdate("recommendations", row["id"], months=24)

        deleted = retention.purge()
        assert deleted["recommendations"] == 3  # one row per candidate crop
        assert deleted["feedback"] == 1  # cascaded with its recommendation
        with db_module.get_conn() as conn:
            assert conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 0
            # The season happened. The outcome survives, honestly unlinked.
            row = conn.execute(
                "SELECT recommendation_id FROM outcomes WHERE id = ?",
                (ids["outcome"],),
            ).fetchone()
            assert row["recommendation_id"] is None

    def test_fresh_rows_are_never_touched(self, monkeypatch, farmer_client):
        from app.core import retention

        _policy(
            monkeypatch, recommendations_months=6, outcomes_months=6, auth_tokens_days=30
        )
        _seed_evidence(farmer_client)
        assert retention.purge() == {
            "outcomes": 0,
            "recommendations": 0,
            "feedback": 0,
            "predictions": 0,
            "auth_tokens": 0,
        }
        with db_module.get_conn() as conn:
            for table in ("recommendations", "feedback", "outcomes", "auth_tokens"):
                assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] >= 1

    def test_expired_session_is_purged_and_fresh_one_kept(self, monkeypatch, client):
        from app.core import retention
        from app.repositories import farmers as farmers_repo

        _policy(monkeypatch, auth_tokens_days=30)
        login = client.post(
            f"{API}/auth/login", json={"mobile": "9876543210", "pin": "1234"}
        )
        assert login.status_code == 401  # farmer_client not used yet in this test

        reg = client.post(
            f"{API}/auth/register",
            json={"name": "R", "mobile": "9812345678", "language": "en", "pin": "1234"},
        )
        assert reg.status_code == 201
        fresh_token = reg.json()["access_token"]

        stale_token = farmers_repo.issue_token(reg.json()["farmer"]["id"])
        # auth_tokens is keyed by token_hash (no id column): backdate by hash.
        stale_created = (
            datetime.now(timezone.utc) - timedelta(days=60)
        ).isoformat(timespec="seconds")
        with db_module.get_conn() as conn:
            conn.execute(
                "UPDATE auth_tokens SET created_at = ? WHERE token_hash = ?",
                (stale_created, farmers_repo.hash_token(stale_token)),
            )

        assert retention.purge()["auth_tokens"] == 1
        assert farmers_repo.farmer_for_token(stale_token) is None
        assert farmers_repo.farmer_for_token(fresh_token) is not None


# ---------------------------------------------------------------------------
# 4. Visibility and the operator path
# ---------------------------------------------------------------------------
class TestVisibilityAndOperatorPath:
    def test_health_reports_the_policy(self, monkeypatch, client):
        _policy(monkeypatch, outcomes_months=36, auth_tokens_days=14)
        body = client.get(f"{API}/health").json()
        assert body["retention"]["outcomes_months"] == 36
        assert body["retention"]["auth_tokens_days"] == 14
        assert body["retention"]["recommendations_months"] == 0
        assert "apply_retention" in body["retention"]["note"]

    def test_cli_dry_run_deletes_nothing_and_reports(self, monkeypatch, farmer_client):
        """The CLI is exercised in-process (runpy) so it sees the same
        monkeypatched settings as the test; a subprocess would read the real
        config.toml and test nothing about the dry-run path itself."""
        import runpy

        _policy(monkeypatch, outcomes_months=12)
        ids = _seed_evidence(farmer_client)
        _backdate("outcomes", ids["outcome"], months=24)

        monkeypatch.setattr(sys, "argv", ["apply_retention.py"])
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_path(str(BACKEND_DIR / "scripts" / "apply_retention.py"), run_name="__main__")
        assert excinfo.value.code in (0, None)
        with db_module.get_conn() as conn:
            assert conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 1

    def test_cli_apply_deletes_exactly_the_expired_rows(
        self, monkeypatch, farmer_client
    ):
        import runpy

        _policy(monkeypatch, outcomes_months=12)
        ids = _seed_evidence(farmer_client)
        _backdate("outcomes", ids["outcome"], months=24)

        monkeypatch.setattr(sys, "argv", ["apply_retention.py", "--apply"])
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_path(str(BACKEND_DIR / "scripts" / "apply_retention.py"), run_name="__main__")
        assert excinfo.value.code in (0, None)
        with db_module.get_conn() as conn:
            assert conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 0
            # The recommendation chain was young: untouched.
            assert conn.execute(
                "SELECT COUNT(*) FROM recommendations"
            ).fetchone()[0] >= 1
