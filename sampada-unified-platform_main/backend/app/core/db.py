"""SQLite persistence for the SAMPADA AI MVP.

Uses the standard library ``sqlite3`` module instead of an ORM so the backend
runs with only FastAPI/Pydantic installed. Every table here exists because a
module in the spec needs to persist something across sessions - in particular
``recommendations``, ``feedback``, ``outcomes`` and ``predictions``, which are
the raw material the Part 4 learning loop will later evaluate.

Design notes:
  * Language-independent values only. We store ``irrigation_type = "PARTIAL"``,
    never a Marathi string (see app/core/i18n.py).
  * Predictions are stored with the model version that produced them, so that
    "did the system improve?" can be answered later instead of asserted.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import crypto
from .config import BACKEND_DIR, settings

SCHEMA_VERSION = 4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path() -> Path:
    """Resolve ``db_url`` from config into a filesystem path."""
    url = str(settings.get("backend.db_url", "sqlite:///./sampada.db"))
    if url.startswith("sqlite:///"):
        raw = url[len("sqlite:///") :]
    elif url.startswith("sqlite://"):
        raw = url[len("sqlite://") :]
    else:
        raise ValueError(f"only sqlite URLs are supported by the MVP, got: {url!r}")
    path = Path(raw)
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on failure."""
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS farmers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    mobile        TEXT    NOT NULL UNIQUE,
    mobile_lookup TEXT,
    language      TEXT    NOT NULL DEFAULT 'mr',
    pin_hash      TEXT    NOT NULL,
    pin_salt      TEXT    NOT NULL,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash    TEXT    PRIMARY KEY,
    farmer_id     INTEGER NOT NULL REFERENCES farmers(id) ON DELETE CASCADE,
    created_at    TEXT    NOT NULL,
    last_used_at  TEXT
);

CREATE TABLE IF NOT EXISTS farms (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    farmer_id      INTEGER NOT NULL REFERENCES farmers(id) ON DELETE CASCADE,
    label          TEXT,
    land_area_ha   REAL    NOT NULL,
    land_unit      TEXT    NOT NULL DEFAULT 'acre',
    land_area_input REAL,
    irrigation_type TEXT    NOT NULL,
    water_source   TEXT,
    district       TEXT,
    taluka         TEXT,
    village        TEXT,
    latitude       REAL,
    longitude      REAL,
    preferences    TEXT,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS soil_estimates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    farm_id         INTEGER NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
    top_soil_type   TEXT    NOT NULL,
    distribution    TEXT    NOT NULL,
    confidence      REAL    NOT NULL,
    evidence        TEXT    NOT NULL,
    model_version   TEXT    NOT NULL,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS crop_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    farm_id           INTEGER NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
    crop_id           TEXT    NOT NULL,
    season            TEXT,
    year              INTEGER,
    area_ha           REAL,
    yield_quintal     REAL,
    cost_inr          REAL,
    sell_price_inr_q  REAL,
    market            TEXT,
    created_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    farm_id          INTEGER NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
    season           TEXT    NOT NULL,
    district         TEXT,
    district_lookup  TEXT,
    crop_id          TEXT    NOT NULL,
    rank             INTEGER NOT NULL,
    final_score      REAL    NOT NULL,
    confidence       REAL    NOT NULL,
    factor_scores    TEXT,
    input_context    TEXT,
    price_estimate   TEXT,
    risk             TEXT,
    reasons          TEXT,
    unavailable      TEXT,
    pilot_plot_ha    REAL,
    model_versions   TEXT    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'proposed',
    created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER NOT NULL REFERENCES recommendations(id) ON DELETE CASCADE,
    followed          TEXT    NOT NULL,
    reason            TEXT,
    comment           TEXT,
    created_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS outcomes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER REFERENCES recommendations(id) ON DELETE SET NULL,
    farm_id          INTEGER NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
    crop_id          TEXT    NOT NULL,
    season           TEXT,
    year             INTEGER,
    area_ha          REAL,
    yield_quintal    REAL,
    cost_inr         REAL,
    sell_price_inr_q REAL,
    market           TEXT,
    transport_cost_inr REAL,
    profit_inr       REAL,
    notes            TEXT,
    created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS predictions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER REFERENCES recommendations(id) ON DELETE CASCADE,
    kind              TEXT    NOT NULL,
    payload           TEXT    NOT NULL,
    model_version     TEXT    NOT NULL,
    created_at        TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_farms_farmer        ON farms(farmer_id);
CREATE INDEX IF NOT EXISTS idx_soil_farm           ON soil_estimates(farm_id);
CREATE INDEX IF NOT EXISTS idx_rec_farm_season     ON recommendations(farm_id, season);
CREATE INDEX IF NOT EXISTS idx_rec_issue_area      ON recommendations(season, district, crop_id);
CREATE INDEX IF NOT EXISTS idx_feedback_rec        ON feedback(recommendation_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_farm       ON outcomes(farm_id);
"""


def init_db() -> Path:
    """Create the database file and schema if needed. Idempotent."""
    # Re-resolve the key from the environment on every boot: "the operator
    # added a key and restarted" must be a complete enablement path.
    crypto.reset_key_cache()
    crypto.ensure_key_policy_satisfied()
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        _apply_migrations(conn)
        # Every boot, not only during a version bump: an operator who adds a
        # key to an already-v4 (plaintext) database must be able to enable
        # encryption by restarting, and the pass is idempotent by design.
        _reencrypt_at_rest(conn)
    return path


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Bring an existing database up to ``SCHEMA_VERSION``, in place.

    Migrations must be backward-compatible and row-preserving: a database
    written by an older build keeps every recommendation, outcome and
    prediction it ever stored. Each step is guarded so it is also idempotent -
    a database created fresh with the current schema already has the new
    columns and is only stamped with the version.
    """
    version = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
    if version >= SCHEMA_VERSION:
        return

    if version < 2:
        # v2: per-factor score snapshots on stored recommendations, so later
        # outcome evaluation can see exactly what drove each answer.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(recommendations)")}
        if "factor_scores" not in columns:
            conn.execute("ALTER TABLE recommendations ADD COLUMN factor_scores TEXT")

    if version < 3:
        # v3: the input context each recommendation consumed - farm profile
        # values, soil estimate id, season, candidate list and the provenance
        # of the weather/market data - captured verbatim at generation time,
        # so future held-out evaluation joins outcomes to the actual inputs
        # (see app/evaluation/factor_evidence.py) instead of re-deriving them.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(recommendations)")}
        if "input_context" not in columns:
            conn.execute("ALTER TABLE recommendations ADD COLUMN input_context TEXT")

    if version < 4:
        # v4: encryption at rest for personal identifiers (spec §8.5). The
        # schema gains keyed-lookup columns - SQLite cannot search AES-GCM
        # ciphertext, so exact-match lookups (login by mobile, app_issued_area
        # by district) go through HMAC-SHA256(key, value) tokens instead. The
        # data conversion itself is _reencrypt_at_rest below, which also runs
        # on every later boot, so adding a key to an existing plaintext
        # database is just: set it, restart.
        farmer_cols = {row[1] for row in conn.execute("PRAGMA table_info(farmers)")}
        if "mobile_lookup" not in farmer_cols:
            conn.execute("ALTER TABLE farmers ADD COLUMN mobile_lookup TEXT")
        rec_cols = {row[1] for row in conn.execute("PRAGMA table_info(recommendations)")}
        if "district_lookup" not in rec_cols:
            conn.execute("ALTER TABLE recommendations ADD COLUMN district_lookup TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_farmers_mobile_lookup "
            "ON farmers(mobile_lookup)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rec_district_lookup "
            "ON recommendations(district_lookup)"
        )

    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


_ENC_PREFIX = "enc1:"


def _reencrypt_cell(
    conn: sqlite3.Connection, table: str, column: str, row_id: int, plain: str
) -> None:
    """Rewrite one personal-identifier cell under the ACTIVE (first) key.

    Every pass through this helper does three things at once: the plaintext is
    re-encrypted under the first configured key and the lookup hash is
    re-derived from it, so enablement and rotation are literally the same code
    path. The pass is idempotent: a cell already under the active key is
    rewritten to an equivalent fresh-nonce ciphertext, which a second boot
    does again harmlessly.

    The lookup hash is written unconditionally even when the ciphertext was
    already current. After a rotation, a hash left under a removed old key
    would silently blind the row's equality lookups on the next boot - a
    re-derived hash cannot be stale.
    """
    conn.execute(
        f"UPDATE {table} SET {column} = ? WHERE id = ?",
        (crypto.encrypt(plain), row_id),
    )
    lookup_column = {"farmers": "mobile_lookup", "recommendations": "district_lookup"}.get(
        table
    )
    if lookup_column is not None:
        conn.execute(
            f"UPDATE {table} SET {lookup_column} = ? WHERE id = ?",
            (crypto.lookup_hash(plain), row_id),
        )


def _reencrypt_at_rest(conn: sqlite3.Connection) -> None:
    """Bring every personal-identifier cell to the current key state.

    Runs on every startup. Two jobs, one code path:

    * enablement    a database written in plaintext is encrypted in place
                    (the v4 migration behaviour, unchanged);
    * rotation      cells encrypted under a PREVIOUS key are re-encrypted
                    under the ACTIVE (first) key and their lookup hashes
                    re-derived, so the operator's rotation completes by
                    restarting. Cells the active key already decrypts are
                    left untouched - the second boot is a no-op, verified
                    byte-for-byte by decrypt_if_current.

    With no key configured, only the (weaker, unkeyed) plaintext-mode lookup
    hashes are backfilled so logins keep working; /health reports that
    encryption is disabled rather than pretending otherwise.
    """
    if crypto.encryption_enabled():
        for row in conn.execute("SELECT id, mobile FROM farmers").fetchall():
            stored = row["mobile"]
            if stored is None:
                continue
            if isinstance(stored, str) and stored.startswith(_ENC_PREFIX):
                plain = crypto.decrypt_if_current(stored)
                if plain is not None:
                    continue  # already under the active key: leave it alone
                plain = crypto.decrypt(stored)  # an older key: rotate it
            else:
                plain = stored  # plaintext: enable encryption
            _reencrypt_cell(conn, "farmers", "mobile", int(row["id"]), plain)

        for row in conn.execute(
            "SELECT id, village, taluka, district, latitude, longitude FROM farms"
        ).fetchall():
            plains: dict[str, str] = {}
            for col in ("village", "taluka", "district"):
                value = row[col]
                if value is None:
                    continue
                if isinstance(value, str) and value.startswith(_ENC_PREFIX):
                    plain = crypto.decrypt_if_current(value)
                    if plain is not None:
                        continue  # already under the active key
                    plains[col] = crypto.decrypt(value)
                else:
                    plains[col] = value
            for col in ("latitude", "longitude"):
                value = row[col]
                if value is None:
                    continue
                text = str(value)
                if text.startswith(_ENC_PREFIX):
                    plain = crypto.decrypt_if_current(text)
                    if plain is not None:
                        continue
                    plains[col] = crypto.decrypt(text)
                else:
                    plains[col] = text
            for column, plain in plains.items():
                _reencrypt_cell(conn, "farms", column, int(row["id"]), plain)

        for row in conn.execute(
            "SELECT id, district, input_context FROM recommendations"
        ).fetchall():
            rec_id = int(row["id"])
            district = row["district"]
            if district is not None:
                text = str(district)
                if text.startswith(_ENC_PREFIX):
                    plain = crypto.decrypt_if_current(text)
                    if plain is not None:
                        plain = None  # already current: nothing to write
                    else:
                        plain = crypto.decrypt(text)
                else:
                    plain = text
                if plain is not None:
                    _reencrypt_cell(
                        conn, "recommendations", "district", rec_id, plain
                    )
            context = row["input_context"]
            if isinstance(context, str) and context:
                if context.startswith(_ENC_PREFIX):
                    plain = crypto.decrypt_if_current(context)
                    if plain is not None:
                        plain = None  # already current: nothing to write
                    else:
                        plain = crypto.decrypt(context)
                else:
                    plain = context
                if plain is not None:
                    _reencrypt_cell(
                        conn, "recommendations", "input_context", rec_id, plain
                    )
    else:
        # Plaintext mode: only the lookup backfill, and only for values that
        # are not already encrypted (those are unreadable without a key, and
        # their reads will fail loudly elsewhere).
        for row in conn.execute(
            "SELECT id, mobile FROM farmers "
            "WHERE mobile_lookup IS NULL AND mobile NOT LIKE 'enc1:%'"
        ).fetchall():
            conn.execute(
                "UPDATE farmers SET mobile_lookup = ? WHERE id = ?",
                (crypto.lookup_hash(row["mobile"]), row["id"]),
            )
        for row in conn.execute(
            "SELECT id, district FROM recommendations "
            "WHERE district IS NOT NULL AND district_lookup IS NULL "
            "AND district NOT LIKE 'enc1:%'"
        ).fetchall():
            conn.execute(
                "UPDATE recommendations SET district_lookup = ? WHERE id = ?",
                (crypto.lookup_hash(row["district"]), row["id"]),
            )


def seed_if_empty() -> None:
    """Seed demo rows only when the operator explicitly asked for sample mode.

    In ``live`` mode this is intentionally a no-op: the MVP has no invented
    baseline data to insert, and the spec forbids presenting sample data as real.
    """
    if not settings.sample_mode:
        return
    # Sample *market* data is loaded on demand by the sample provider (see
    # app/providers/market_sample.py) rather than inserted here, because prices
    # are read-only reference data and do not belong in the farmer tables.
    return


def reset_db() -> Path:
    """Drop and recreate every table. Destructive - tests and local dev only."""
    with get_conn() as conn:
        for table in (
            "predictions",
            "outcomes",
            "feedback",
            "recommendations",
            "crop_history",
            "soil_estimates",
            "farms",
            "auth_tokens",
            "farmers",
        ):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        # Born v0: a recreated database must run every migration step again
        # (including the lookup-index creation and the at-rest pass), not
        # inherit the previous database's version stamp.
        conn.execute("PRAGMA user_version = 0")
    return init_db()
