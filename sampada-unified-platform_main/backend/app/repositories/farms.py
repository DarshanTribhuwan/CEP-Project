"""Farm, crop-history and preference persistence.

Location fields (village, taluka, district, latitude, longitude) are personal
data: they are stored AES-256-GCM encrypted (see app/core/crypto.py) and
decrypted at this boundary, so every caller above keeps working on plaintext
values while the database file stops carrying them. With no key configured
the storage falls back to plaintext and /health reports it.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..core import crypto
from ..core.db import get_conn, now_iso
from .farmers import dumps, loads


def _dec_location(value: Any, *, number: bool = False) -> Any:
    """Read one location cell back to its plaintext API value.

    Encrypted cells are ``enc1:`` strings; plaintext-mode cells are whatever
    sqlite stored (text for places, a float for coordinates). Both map back
    to the value the caller wrote.
    """
    if value is None:
        return None
    text = crypto.decrypt(value) if isinstance(value, str) else str(value)
    return float(text) if number else text


def _enc_location(value: Any) -> str | None:
    if value is None:
        return None
    return crypto.encrypt(value if isinstance(value, str) else str(value))


def _row_to_farm(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "farmer_id": row["farmer_id"],
        "label": row["label"],
        "land_area_ha": row["land_area_ha"],
        "land_area_input": row["land_area_input"],
        "land_unit": row["land_unit"],
        "irrigation_type": row["irrigation_type"],
        "water_source": row["water_source"],
        "district": _dec_location(row["district"]),
        "taluka": _dec_location(row["taluka"]),
        "village": _dec_location(row["village"]),
        "latitude": _dec_location(row["latitude"], number=True),
        "longitude": _dec_location(row["longitude"], number=True),
        "preferences": loads(row["preferences"], {}) or {},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_farm(farmer_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    stamp = now_iso()
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO farms (
                farmer_id, label, land_area_ha, land_unit, land_area_input,
                irrigation_type, water_source, district, taluka, village,
                latitude, longitude, preferences, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                farmer_id,
                payload.get("label"),
                payload["land_area_ha"],
                payload["land_unit"],
                payload.get("land_area_input"),
                payload["irrigation_type"],
                payload.get("water_source"),
                _enc_location(payload.get("district")),
                _enc_location(payload.get("taluka")),
                _enc_location(payload.get("village")),
                _enc_location(payload.get("latitude")),
                _enc_location(payload.get("longitude")),
                dumps(payload.get("preferences", {})),
                stamp,
                stamp,
            ),
        )
        farm_id = int(cursor.lastrowid)
        row = conn.execute("SELECT * FROM farms WHERE id = ?", (farm_id,)).fetchone()
    return _row_to_farm(row)


_ALLOWED_UPDATE_FIELDS = {
    "label",
    "land_area_ha",
    "land_unit",
    "land_area_input",
    "irrigation_type",
    "water_source",
    "district",
    "taluka",
    "village",
    "latitude",
    "longitude",
    "preferences",
}


def update_farm(farm_id: int, changes: dict[str, Any]) -> dict[str, Any] | None:
    fields = {k: v for k, v in changes.items() if k in _ALLOWED_UPDATE_FIELDS and v is not None}
    if "preferences" in fields:
        fields["preferences"] = dumps(fields["preferences"])
    # Changed location values are re-encrypted like any other write; a partial
    # update touches only the fields the farmer actually changed.
    for name in ("district", "taluka", "village", "latitude", "longitude"):
        if name in fields:
            fields[name] = _enc_location(fields[name])
    with get_conn() as conn:
        if fields:
            assignments = ", ".join(f"{name} = ?" for name in fields)
            conn.execute(
                f"UPDATE farms SET {assignments}, updated_at = ? WHERE id = ?",
                (*fields.values(), now_iso(), farm_id),
            )
        row = conn.execute("SELECT * FROM farms WHERE id = ?", (farm_id,)).fetchone()
    return _row_to_farm(row) if row else None


def get_farm(farm_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM farms WHERE id = ?", (farm_id,)).fetchone()
    return _row_to_farm(row) if row else None


def list_farms(farmer_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM farms WHERE farmer_id = ? ORDER BY id", (farmer_id,)
        ).fetchall()
    return [_row_to_farm(row) for row in rows]


def add_crop_history(farm_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO crop_history (
                farm_id, crop_id, season, year, area_ha, yield_quintal,
                cost_inr, sell_price_inr_q, market, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                farm_id,
                payload["crop_id"],
                payload.get("season"),
                payload.get("year"),
                payload.get("area_ha"),
                payload.get("yield_quintal"),
                payload.get("cost_inr"),
                payload.get("sell_price_inr_per_quintal"),
                payload.get("market"),
                now_iso(),
            ),
        )
        record_id = int(cursor.lastrowid)
        row = conn.execute("SELECT * FROM crop_history WHERE id = ?", (record_id,)).fetchone()
    return dict(row)


def list_crop_history(farm_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM crop_history WHERE farm_id = ? ORDER BY year DESC, id DESC",
            (farm_id,),
        ).fetchall()
    return [dict(row) for row in rows]


_HISTORY_QUERY = """
    SELECT crop_id,
           COUNT(*) AS records,
           AVG(yield_quintal) AS mean_yield,
           AVG({price_column}) AS mean_price
    FROM {table}
    WHERE farm_id = ?
    GROUP BY crop_id
"""


def history_summary(farm_id: int) -> dict[str, dict[str, float]]:
    """Aggregate this farm's past performance per crop.

    Reads BOTH sources of history, because they mean different things and both
    count as experience with a crop:

      * ``crop_history`` - seasons the farmer described during onboarding
        (may be entered from memory, so weaker evidence)
      * ``outcomes`` - seasons the system recorded alongside a recommendation
        (stronger, because the prediction that preceded it is also on file)

    Returns ``{crop_id: {"records": n, "mean_yield": y, "mean_price": p}}``.
    Means are combined as record-weighted averages.
    """
    with get_conn() as conn:
        described = conn.execute(
            _HISTORY_QUERY.format(price_column="sell_price_inr_q", table="crop_history"),
            (farm_id,),
        ).fetchall()
        recorded = conn.execute(
            _HISTORY_QUERY.format(price_column="sell_price_inr_q", table="outcomes"),
            (farm_id,),
        ).fetchall()

    summary: dict[str, dict[str, float]] = {}
    for row in list(described) + list(recorded):
        crop_id = row["crop_id"]
        count = float(row["records"] or 0)
        mean_yield = float(row["mean_yield"]) if row["mean_yield"] is not None else 0.0
        mean_price = float(row["mean_price"]) if row["mean_price"] is not None else 0.0

        entry = summary.setdefault(
            crop_id, {"records": 0.0, "mean_yield": 0.0, "mean_price": 0.0}
        )
        total = entry["records"] + count
        if total > 0:
            entry["mean_yield"] = (
                entry["mean_yield"] * entry["records"] + mean_yield * count
            ) / total
            entry["mean_price"] = (
                entry["mean_price"] * entry["records"] + mean_price * count
            ) / total
        entry["records"] = total
    return summary
