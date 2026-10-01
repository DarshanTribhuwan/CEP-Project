"""Farmer persistence and authentication.

Security posture, stated plainly so nobody mistakes this for finished auth:

  * PINs are hashed with PBKDF2-HMAC-SHA256 and a per-farmer random salt. They
    are never stored or logged in clear text.
  * Session tokens are random 32-byte values; only their SHA-256 hash is stored,
    so a database leak does not hand over live sessions.
  * Mobile numbers are stored AES-256-GCM encrypted (see app/core/crypto.py),
    with a keyed HMAC lookup hash for login and duplicate detection, so the
    database file no longer carries the number in clear text. With no key
    configured the system falls back to plaintext storage and /health says so.
  * A mobile number plus PIN is a weak factor. A real deployment needs OTP
    delivery or another verified second factor.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core import crypto
from ..core.config import settings
from ..core.db import get_conn, now_iso

PBKDF2_ITERATIONS = 200_000

# A session token stops working this long after it was issued. UNVALIDATED
# DEFAULT, deliberately generous: mobile-first farmers should not be logged
# out mid-season, but a database leak must not hand over sessions that work
# forever. Revocation (logout) stays immediate and independent of this.
TOKEN_TTL_DAYS = 30


def hash_pin(pin: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return digest.hex(), salt


def verify_pin(pin: str, pin_hash: str, salt: str) -> bool:
    candidate, _ = hash_pin(pin, salt)
    return hmac.compare_digest(candidate, pin_hash)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _row_to_farmer(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        # Decrypted here, at the last storage boundary: every caller above
        # this layer keeps seeing the plaintext value it always saw.
        "mobile": crypto.decrypt(row["mobile"]),
        "language": row["language"],
        "created_at": row["created_at"],
    }


def create_farmer(name: str, mobile: str, language: str, pin: str) -> dict[str, Any] | None:
    """Create a farmer. Returns None when the mobile number already exists.

    The number is stored encrypted; uniqueness and login lookup go through the
    keyed ``mobile_lookup`` hash, because ciphertext cannot be compared. The
    UNIQUE on the ciphertext column stays as defence in depth."""
    pin_hash, salt = hash_pin(pin)
    with get_conn() as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO farmers (name, mobile, mobile_lookup, language, pin_hash, pin_salt, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (name.strip(), crypto.encrypt(mobile), crypto.lookup_hash(mobile), language, pin_hash, salt, now_iso()),
            )
        except sqlite3.IntegrityError:
            return None
        farmer_id = int(cursor.lastrowid)
        row = conn.execute("SELECT * FROM farmers WHERE id = ?", (farmer_id,)).fetchone()
    return _row_to_farmer(row)


def get_farmer(farmer_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM farmers WHERE id = ?", (farmer_id,)).fetchone()
    return _row_to_farmer(row) if row else None


def get_farmer_by_mobile(mobile: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM farmers WHERE mobile_lookup IN (%s)"
            % ", ".join("?" for _ in crypto.lookup_hashes(mobile)),
            (*crypto.lookup_hashes(mobile),),
        ).fetchone()
    return _row_to_farmer(row) if row else None


def set_language(farmer_id: int, language: str) -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            "UPDATE farmers SET language = ? WHERE id = ?", (language, farmer_id)
        )
        return cursor.rowcount > 0


def authenticate(mobile: str, pin: str) -> dict[str, Any] | None:
    """Return the farmer on a correct mobile/PIN pair, else None.

    The lookup matches a hash made under ANY configured key: during a key
    rotation window (new key first, at-rest pass not yet finished) a row's
    stored hash may still be the old key's, and login must keep working."""
    hashes = crypto.lookup_hashes(mobile)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM farmers WHERE mobile_lookup IN (%s)"
            % ", ".join("?" for _ in hashes),
            (*hashes,),
        ).fetchone()
    if row is None:
        return None
    if not verify_pin(pin, row["pin_hash"], row["pin_salt"]):
        return None
    return _row_to_farmer(row)


def issue_token(farmer_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO auth_tokens (token_hash, farmer_id, created_at) VALUES (?, ?, ?)",
            (hash_token(token), farmer_id, now_iso()),
        )
    return token


def _token_ttl_days() -> int:
    """Configurable session lifetime; the constant above is the default."""
    try:
        return int(settings.get("backend.token_ttl_days", TOKEN_TTL_DAYS))
    except (TypeError, ValueError):
        return TOKEN_TTL_DAYS


def _token_created_at(value: str) -> datetime | None:
    """Parse a token's ``created_at`` stamp, or None when unreadable.

    An unreadable stamp must not read as "issued right now" (which would
    extend its life every boot) nor crash the request: the token is treated
    as expired, the safe direction for a security check.
    """
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def farmer_for_token(token: str) -> dict[str, Any] | None:
    """Resolve a live session token, or None.

    None covers invalid, revoked AND expired tokens: the endpoint maps all
    three to the same 401, so the response never tells an attacker which of
    the three conditions held.
    """
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT f.*, t.created_at AS token_created_at
            FROM auth_tokens t
            JOIN farmers f ON f.id = t.farmer_id
            WHERE t.token_hash = ?
            """,
            (hash_token(token),),
        ).fetchone()
        if row is None:
            return None
        created = _token_created_at(row["token_created_at"])
        ttl_days = _token_ttl_days()
        expired = created is None or (
            time.time() - created.timestamp() > ttl_days * 86400
        )
        if expired:
            return None
        conn.execute(
            "UPDATE auth_tokens SET last_used_at = ? WHERE token_hash = ?",
            (now_iso(), hash_token(token)),
        )
    return _row_to_farmer(row)


def revoke_token(token: str) -> bool:
    with get_conn() as conn:
        cursor = conn.execute("DELETE FROM auth_tokens WHERE token_hash = ?", (hash_token(token),))
        return cursor.rowcount > 0


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default
