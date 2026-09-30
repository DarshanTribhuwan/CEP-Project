"""Recommendation, feedback, outcome and prediction persistence.

The ``predictions`` table exists so that Part 4 can later ask "was the system
right?" without re-deriving history. Each row records the model version that
produced a value, because comparing two models is impossible if you cannot tell
which one produced a given number.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..core import crypto
from ..core.db import get_conn, now_iso
from .farmers import dumps, loads


def _row_to_recommendation(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"],
        "farm_id": row["farm_id"],
        "season": row["season"],
        # District and the input-context blob carry location data, so both are
        # stored encrypted and decrypted at this boundary (v4). The lookup
        # hash column serves the community aggregation, which only needs
        # equality, not the name.
        "district": crypto.decrypt(row["district"]),
        "crop_id": row["crop_id"],
        "rank": row["rank"],
        "final_score": row["final_score"],
        "confidence": row["confidence"],
        # Rows written before the v2 migration have no factor_scores column;
        # they report None rather than an empty snapshot pretending to be one.
        "factor_scores": loads(row["factor_scores"], None) if "factor_scores" in keys else None,
        # Same rule for the v3 input context (stored encrypted since v4):
        # a pre-v3 row has no context to show, and a missing snapshot must
        # not be disguised as an empty one.
        "input_context": (
            loads(crypto.decrypt(row["input_context"]), None)
            if "input_context" in keys and row["input_context"]
            else None
        ),
        "price_estimate": loads(row["price_estimate"], None),
        "risk": loads(row["risk"], {}),
        "reasons": loads(row["reasons"], []),
        "unavailable": loads(row["unavailable"], []),
        "pilot_plot_ha": row["pilot_plot_ha"],
        "model_versions": loads(row["model_versions"], {}),
        "status": row["status"],
        "created_at": row["created_at"],
    }


def save_recommendations(
    farm_id: int,
    season: str,
    district: str | None,
    options: list[dict[str, Any]],
    unavailable: list[dict[str, Any]],
    model_versions: dict[str, str],
) -> list[dict[str, Any]]:
    """Persist a whole ranked set inside one transaction."""
    stamp = now_iso()
    saved: list[dict[str, Any]] = []
    with get_conn() as conn:
        for option in options:
            cursor = conn.execute(
                """
                INSERT INTO recommendations (
                    farm_id, season, district, district_lookup, crop_id, rank, final_score, confidence,
                    factor_scores, input_context, price_estimate, risk, reasons,
                    unavailable, pilot_plot_ha, model_versions, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    farm_id,
                    season,
                    crypto.encrypt(district) if district is not None else None,
                    crypto.lookup_hash(district) if district is not None else None,
                    option["crop_id"],
                    option["rank"],
                    option["final_score"],
                    option["confidence"],
                    dumps(option.get("factor_scores")),
                    # The input context embeds village and coordinates, so it
                    # is stored encrypted like the location columns it
                    # protects; the decrypt happens in _row_to_recommendation.
                    crypto.encrypt(dumps(option.get("input_context"))),
                    dumps(option.get("price_estimate")),
                    dumps(option.get("risk", {})),
                    dumps(option.get("reasons", [])),
                    dumps(unavailable),
                    option.get("pilot_plot_ha"),
                    dumps(model_versions),
                    "proposed",
                    stamp,
                ),
            )
            recommendation_id = int(cursor.lastrowid)
            if option.get("price_estimate"):
                conn.execute(
                    """
                    INSERT INTO predictions (recommendation_id, kind, payload, model_version, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        recommendation_id,
                        "price_range",
                        dumps(option["price_estimate"]),
                        model_versions.get("price_model_version", "unknown"),
                        stamp,
                    ),
                )
            row = conn.execute(
                "SELECT * FROM recommendations WHERE id = ?", (recommendation_id,)
            ).fetchone()
            saved.append(_row_to_recommendation(row))
    return saved


def get_recommendation(recommendation_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM recommendations WHERE id = ?", (recommendation_id,)
        ).fetchone()
    return _row_to_recommendation(row) if row else None


def list_recommendations(farm_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM recommendations WHERE farm_id = ? ORDER BY id DESC LIMIT ?",
            (farm_id, limit),
        ).fetchall()
    return [_row_to_recommendation(row) for row in rows]


def set_status(recommendation_id: int, status: str) -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            "UPDATE recommendations SET status = ? WHERE id = ?", (status, recommendation_id)
        )
        return cursor.rowcount > 0


def app_issued_area(
    season: str, district: str | None, exclude_farm_id: int | None = None
) -> dict[str, float]:
    """Area this app has already recommended per crop, for a season and district.

    This is the only regional production signal the MVP genuinely owns: what the
    app itself has told its own users. It is NOT total district production and
    must never be described as such - see app/services/community.py, which
    reports it with that limitation attached.
    """
    # Equality joins on the keyed lookup hash: the district name itself is
    # encrypted at rest. The hash is HMAC-SHA256(key, value) - see
    # app/core/crypto.py - so this aggregation never needs the plaintext name.
    # Matching tries a hash made under ANY configured key (IN, not =), so the
    # aggregation stays correct during a key-rotation window, when stored rows
    # may still carry the previous key's hash. `IS NULL` keeps matching a NULL
    # district filter (a farm with no district), as before.
    if district is None:
        clauses = ["r.season = ?", "r.rank = 1", "r.district_lookup IS NULL"]
        params: list[Any] = [season]
    else:
        hashes = crypto.lookup_hashes(district)
        clauses = [
            "r.season = ?",
            "r.rank = 1",
            "r.district_lookup IN (%s)" % ", ".join("?" for _ in hashes),
        ]
        params = [season, *hashes]
    if exclude_farm_id is not None:
        clauses.append("r.farm_id != ?")
        params.append(exclude_farm_id)

    query = (
        "SELECT r.crop_id, SUM(COALESCE(f.land_area_ha, 0)) AS area_ha "
        "FROM recommendations r "
        "JOIN farms f ON f.id = r.farm_id "
        f"WHERE {' AND '.join(clauses)} "
        "GROUP BY r.crop_id"
    )

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return {row["crop_id"]: float(row["area_ha"] or 0.0) for row in rows}


def add_feedback(
    recommendation_id: int, followed: str, reason: str | None, comment: str | None
) -> dict[str, Any]:
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO feedback (recommendation_id, followed, reason, comment, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (recommendation_id, followed, reason, comment, now_iso()),
        )
        feedback_id = int(cursor.lastrowid)
        row = conn.execute("SELECT * FROM feedback WHERE id = ?", (feedback_id,)).fetchone()
        # A farmer's response is also the recommendation's lifecycle status.
        conn.execute(
            "UPDATE recommendations SET status = ? WHERE id = ?",
            ({"YES": "accepted", "PARTIAL": "partially_accepted", "NO": "rejected"}[followed],
             recommendation_id),
        )
    return dict(row)


def list_feedback(recommendation_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM feedback WHERE recommendation_id = ? ORDER BY id",
            (recommendation_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _row_to_outcome(row: sqlite3.Row) -> dict[str, Any]:
    """Map DB column names onto the public API field names.

    The column is named ``sell_price_inr_q`` to match the crop-history table,
    but the API spells the unit out, so the rename happens here rather than
    leaking a terse column name into the contract.
    """
    return {
        "id": row["id"],
        "farm_id": row["farm_id"],
        "recommendation_id": row["recommendation_id"],
        "crop_id": row["crop_id"],
        "season": row["season"],
        "year": row["year"],
        "yield_quintal": row["yield_quintal"],
        "cost_inr": row["cost_inr"],
        "sell_price_inr_per_quintal": row["sell_price_inr_q"],
        "profit_inr": row["profit_inr"],
        "created_at": row["created_at"],
    }


def add_outcome(payload: dict[str, Any]) -> dict[str, Any]:
    yield_q = payload.get("yield_quintal")
    price = payload.get("sell_price_inr_per_quintal")
    cost = payload.get("cost_inr")
    transport = payload.get("transport_cost_inr")
    profit = None
    if yield_q is not None and price is not None:
        profit = round(yield_q * price - (cost or 0.0) - (transport or 0.0), 2)

    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO outcomes (
                recommendation_id, farm_id, crop_id, season, year, area_ha,
                yield_quintal, cost_inr, sell_price_inr_q, market,
                transport_cost_inr, profit_inr, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("recommendation_id"),
                payload["farm_id"],
                payload["crop_id"],
                payload.get("season"),
                payload.get("year"),
                payload.get("area_ha"),
                yield_q,
                cost,
                price,
                payload.get("market"),
                transport,
                profit,
                payload.get("notes"),
                now_iso(),
            ),
        )
        outcome_id = int(cursor.lastrowid)
        row = conn.execute("SELECT * FROM outcomes WHERE id = ?", (outcome_id,)).fetchone()
    return _row_to_outcome(row)


def list_outcomes(farm_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM outcomes WHERE farm_id = ? ORDER BY id DESC", (farm_id,)
        ).fetchall()
    return [_row_to_outcome(row) for row in rows]


def outcome_count_for_farm(farm_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM outcomes WHERE farm_id = ?", (farm_id,)
        ).fetchone()
    return int(row["n"] or 0)
