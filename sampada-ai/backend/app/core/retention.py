"""Data retention: age-based purging of expired evidence, operator-invoked.

The spec's encryption section (§8.5) deliberately left a retention policy
open. This module closes it, with the project's standing bias applied:

    * opt-in        every threshold defaults to 0 = keep forever. A deployment
                    that never configures ``[retention]`` never deletes a row.
                    Deleting a farmer's evidence is a decision someone must
                    make; it is never background noise.
    * what ages     three classes, configured independently:
                      outcomes_months         recorded season results
                      recommendations_months  the system's stored statements,
                                              whose feedback rows (and any
                                              predictions) go with them
                      auth_tokens_days        dead sessions
                    Soil estimates and crop history are farm-level reference
                    data that keep informing the soil and history factors;
                    they do not age.
    * boundary      ``created_at < cutoff`` is STRICT: a row written exactly
                    at the cutoff survives. A monthly job never races a row
                    created the same instant into deletion.
    * cascade shape an outcome whose recommendation was purged SURVIVES with
                    ``recommendation_id = NULL`` - the season happened; only
                    the pointer to the aged-out statement is lost. Deleting
                    the outcome itself would be a disguised second deletion.
    * who purges    nobody automatic. ``scripts/apply_retention.py`` is the
                    operator's tool: dry-run by default, ``--apply`` to
                    execute, printing exactly what is affected either way.
                    /health reports the configured policy so it is visible
                    from outside the process.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import settings
from .db import get_conn, now_iso

DEFAULT_POLICY: dict[str, int] = {
    "outcomes_months": 0,
    "recommendations_months": 0,
    "auth_tokens_days": 0,
}


def policy() -> dict[str, int]:
    """The configured thresholds, 0 = keep forever. Absent section and
    absent keys both mean the default; unknown keys are ignored so a typo in
    config cannot silently enable a class of deletion the operator did not
    spell out here."""
    configured = settings.get("retention") or {}
    merged = dict(DEFAULT_POLICY)
    for key in DEFAULT_POLICY:
        try:
            merged[key] = max(0, int(configured.get(key, 0) or 0))
        except (TypeError, ValueError):
            merged[key] = 0
    return merged


def _cutoff_months(months: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=30 * months)).isoformat(
        timespec="seconds"
    )


def _cutoff_days(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds"
    )


def expired_counts() -> dict[str, int]:
    """How many rows of each class are older than their configured threshold.

    Purely read-only; this is what the dry run prints. Classes with a 0
    threshold always report 0 - "keep forever" is a real policy, not the
    absence of one.
    """
    limits = policy()
    counts: dict[str, int] = {}
    with get_conn() as conn:
        counts["outcomes"] = (
            conn.execute(
                "SELECT COUNT(*) FROM outcomes WHERE created_at < ?",
                (_cutoff_months(limits["outcomes_months"]),),
            ).fetchone()[0]
            if limits["outcomes_months"] > 0
            else 0
        )
        counts["recommendations"] = (
            conn.execute(
                "SELECT COUNT(*) FROM recommendations WHERE created_at < ?",
                (_cutoff_months(limits["recommendations_months"]),),
            ).fetchone()[0]
            if limits["recommendations_months"] > 0
            else 0
        )
        counts["auth_tokens"] = (
            conn.execute(
                "SELECT COUNT(*) FROM auth_tokens WHERE created_at < ?",
                (_cutoff_days(limits["auth_tokens_days"]),),
            ).fetchone()[0]
            if limits["auth_tokens_days"] > 0
            else 0
        )
        # The dependents that go with an aged-out recommendation (feedback
        # always; predictions via the FK CASCADE). Shown so the dry run
        # states the full blast radius, not just the primary rows.
        counts["feedback"] = (
            conn.execute(
                """
                SELECT COUNT(*) FROM feedback
                WHERE recommendation_id IN (
                    SELECT id FROM recommendations WHERE created_at < ?
                )
                """,
                (_cutoff_months(limits["recommendations_months"]),),
            ).fetchone()[0]
            if limits["recommendations_months"] > 0
            else 0
        )
        counts["predictions"] = (
            conn.execute(
                """
                SELECT COUNT(*) FROM predictions
                WHERE recommendation_id IN (
                    SELECT id FROM recommendations WHERE created_at < ?
                )
                """,
                (_cutoff_months(limits["recommendations_months"]),),
            ).fetchone()[0]
            if limits["recommendations_months"] > 0
            else 0
        )
    return counts


def purge() -> dict[str, int]:
    """Delete the expired rows. Returns what was actually deleted.

    Order matters and is explicit: dependents first (feedback by subquery,
    predictions by their FK CASCADE when recommendations go), then tokens,
    then outcomes re-pointed to NULL, then the recommendations themselves -
    all in one transaction, so a crash mid-way deletes nothing partial.
    """
    limits = policy()
    if not any(limits.values()):
        return {
            "outcomes": 0,
            "recommendations": 0,
            "feedback": 0,
            "predictions": 0,
            "auth_tokens": 0,
        }

    deleted: dict[str, int] = {}
    with get_conn() as conn:
        if limits["recommendations_months"] > 0:
            cutoff = _cutoff_months(limits["recommendations_months"])
            deleted["feedback"] = conn.execute(
                """
                DELETE FROM feedback WHERE recommendation_id IN (
                    SELECT id FROM recommendations WHERE created_at < ?
                )
                """,
                (cutoff,),
            ).rowcount
            deleted["predictions"] = conn.execute(
                """
                DELETE FROM predictions WHERE recommendation_id IN (
                    SELECT id FROM recommendations WHERE created_at < ?
                )
                """,
                (cutoff,),
            ).rowcount
            deleted["recommendations"] = conn.execute(
                "DELETE FROM recommendations WHERE created_at < ?", (cutoff,)
            ).rowcount
        else:
            deleted["feedback"] = 0
            deleted["predictions"] = 0
            deleted["recommendations"] = 0

        if limits["auth_tokens_days"] > 0:
            deleted["auth_tokens"] = conn.execute(
                "DELETE FROM auth_tokens WHERE created_at < ?",
                (_cutoff_days(limits["auth_tokens_days"]),),
            ).rowcount
        else:
            deleted["auth_tokens"] = 0

        if limits["outcomes_months"] > 0:
            deleted["outcomes"] = conn.execute(
                "DELETE FROM outcomes WHERE created_at < ?",
                (_cutoff_months(limits["outcomes_months"]),),
            ).rowcount
        else:
            deleted["outcomes"] = 0

        # Survivors of a purged recommendation: honestly unlinked, not deleted.
        conn.execute(
            """
            UPDATE outcomes SET recommendation_id = NULL
            WHERE recommendation_id IS NOT NULL
              AND recommendation_id NOT IN (SELECT id FROM recommendations)
            """
        )
    return deleted
