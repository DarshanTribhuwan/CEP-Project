"""Encryption at rest for personally-identifying stored values.

Scope, exactly as the spec names it (MVP_SPEC §8.5): mobile numbers and farm
locations. The repository layer is the only code that touches those columns,
so encryption lives there and every caller keeps working on plaintext values
in memory - the database file is the thing that stops carrying them.

Design, stated so it can be attacked:

    * algorithm     AES-256-GCM via ``cryptography`` (a standard, audited
      implementation - nothing hand-rolled), random 12-byte nonce per value.
    * ciphertext    ``enc1:<b64(nonce)>.<b64(ct+tag)>``. The ``enc1:`` prefix
      makes encrypted cells recognisable at a glance (tests, migrations,
      audits) and leaves room for a future ``enc2:`` format.
    * non-determinism  a fresh nonce per call means the same plaintext never
      produces the same ciphertext, so the ciphertext column itself does not
      leak equality patterns.
    * lookup hash   searchability without decryption. SQLite cannot query
      AES-GCM ciphertext, and the system must look a farmer up by mobile and
      group recommendations by district. Both use
      ``HMAC-SHA256(key, "lookup-v1:" + value)``: keyed, so a leaked database
      cannot dictionary-attack the hash column (mobile numbers live in a
      ~10^10 space and an unkeyed hash would fall instantly), stable, so
      equality lookups work, and domain-separated from the encryption key
      usage. Stored in ``*_lookup`` columns; the UNIQUE constraint lives on
      the hash column.
    * key source    the ``SAMPADA_ENC_KEY`` environment variable (base64
      32 bytes) or a key file (``backend/var/enc_key`` / config
      ``security.enc_key_file``), read once at import. The backend/.env
      loader in ``app.core.config`` already picks up SAMPADA_ENC_KEY from
      ``backend/.env`` - the same place the market API key lives, and the
      file .gitignore already excludes.
    * no key, no encryption  with no key configured the system keeps working
      in plaintext (existing dev/demo behaviour) and ``/health`` reports
      ``encryption_at_rest: "disabled"``. The state is visible, never
      pretended. Setting ``security.encryption_required = true`` in config
      flips that to fail-closed: startup is refused rather than piloting on
      plaintext.
    * fail on unreadable data  decrypting garbage raises - a wrong or rotated-
      away key must be loud, not silently decoded as junk.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from .config import BACKEND_DIR, settings

ENV_VAR = "SAMPADA_ENC_KEY"
ENV_VARS = "SAMPADA_ENC_KEYS"
_PREFIX = "enc1:"
_LOOKUP_DOMAIN = b"lookup-v1:"

_KEYS: list[bytes] | str = "unresolved"  # sentinel until first resolution


def _resolve_keys() -> list[bytes] | None:
    """The configured keys, in order, or None when encryption is disabled.

    ``SAMPADA_ENC_KEYS`` is a comma-separated list: the FIRST key encrypts
    every new write; ALL of them are tried when decrypting. That is key
    rotation as a config edit plus a restart - see app/core/db.py's at-rest
    pass, which re-encrypts everything under the first key on boot.

    Resolution order: the environment variable, then the config-named key
    file. There is no silent generation: a key that appeared without the
    operator putting it somewhere durable would be lost on the next machine,
    and encrypted data with a lost key is gone. /health's note states exactly
    how to configure one.
    """
    global _KEYS
    if _KEYS != "unresolved":
        return _KEYS  # type: ignore[return-value]

    multi_raw = os.environ.get(ENV_VARS, "").strip()
    single_raw = os.environ.get(ENV_VAR, "").strip()
    if multi_raw and single_raw:
        raise RuntimeError(
            f"both {ENV_VARS} and {ENV_VAR} are set; configure exactly one - "
            f"{ENV_VARS} (preferred, enables rotation) or the single {ENV_VAR}"
        )

    raw = multi_raw or single_raw
    if raw:
        _KEYS = [_decode_key(part, ENV_VARS if multi_raw else ENV_VAR) for part in raw.split(",") if part.strip()]
        return _KEYS  # type: ignore[return-value]

    file_setting = str(settings.get("security.enc_key_file", "") or "").strip()
    if file_setting:
        path = Path(file_setting)
        if not path.is_absolute():
            path = BACKEND_DIR / path
        if path.exists():
            _KEYS = [_decode_key(path.read_text(encoding="utf-8").strip(), ENV_VAR)]
            return _KEYS  # type: ignore[return-value]
        raise RuntimeError(
            f"security.enc_key_file points at {path}, which does not exist"
        )

    _KEYS = None
    return _KEYS  # type: ignore[return-value]


def _decode_key(raw: str, source: str = ENV_VAR) -> bytes:
    shown = raw.strip()
    if len(shown) > 12:
        shown = shown[:6] + "..." + shown[-4:]
    try:
        key = base64.urlsafe_b64decode(raw.strip())
    except Exception as exc:  # noqa: BLE001 - any decode failure is operator error
        raise RuntimeError(
            f"{source} entry {shown!r} ({len(shown)} chars) is not valid base64 - "
            "it must decode to exactly 32 bytes. If the value looks right, an "
            f"environment variable from an old shell session may be overriding "
            f"backend/.env: an OS-set {source} wins over the file, so check "
            f"'echo $env:{source}' in PowerShell (or 'set {source}' in cmd) "
            "and remove it, or simply open a fresh terminal."
        ) from exc
    if len(key) != 32:
        raise RuntimeError(
            f"{source} entries must decode to exactly 32 bytes, got {len(key)}"
        )
    return key


def reset_key_cache() -> None:
    """Forget the resolved keys. Tests and the migration script need this; the
    server process itself never calls it."""
    global _KEYS
    _KEYS = "unresolved"


def encryption_enabled() -> bool:
    """True when at least one key is available. Plain reading of the state."""
    return _resolve_keys() is not None


def key_count() -> int:
    """How many keys are configured (0 = encryption disabled). Reported by
    /health so a rotation mid-flight (new key first, old key still listed)
    is visible from outside the process."""
    keys = _resolve_keys()
    return len(keys) if keys else 0


def encryption_required() -> bool:
    """Config flips the plaintext fallback from allowed to refused."""
    return bool(settings.get("security.encryption_required", False))


def ensure_key_policy_satisfied() -> None:
    """Fail closed when the operator demanded encryption and there is no key.

    Called at startup (db init) so a misconfigured pilot refuses to run
    rather than silently storing PII in clear text.
    """
    if encryption_required() and not encryption_enabled():
        raise RuntimeError(
            "security.encryption_required is true but no encryption key is "
            f"configured; set {ENV_VARS} (base64, 32 bytes per key, comma-"
            f"separated; the first key encrypts new writes) or the single "
            f"{ENV_VAR} environment variable, or point security.enc_key_file "
            "at a key file. Refusing to start with unencrypted personal data."
        )


# ---------------------------------------------------------------------------
# Value encryption
# ---------------------------------------------------------------------------
def encrypt(plaintext: str | None) -> str | None:
    """AES-256-GCM encrypt with a random nonce. None passes through so NULL
    columns stay NULL (a missing village must not become an empty ciphertext)."""
    if plaintext is None:
        return None
    keys = _resolve_keys()
    if not keys:
        # Encryption disabled: store as-is. The repositories are the only
        # callers, so plaintext never escapes the storage layer.
        return plaintext
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    sealed = AESGCM(keys[0]).encrypt(nonce, plaintext.encode("utf-8"), None)
    token = (
        base64.urlsafe_b64encode(nonce).rstrip(b"=").decode("ascii")
        + "."
        + base64.urlsafe_b64encode(sealed).rstrip(b"=").decode("ascii")
    )
    return _PREFIX + token


def decrypt_if_current(stored: str | None) -> str | None:
    """Plaintext when the ACTIVE (first) key decrypts this cell, else None.

    The at-rest pass uses this to skip cells that are already under the
    active key without rewriting them on every boot. None does NOT mean the
    data is lost - an older configured key may still decrypt it (the rotation
    window), and :func:`decrypt` will find it; None only means "not current".
    """
    if not stored or not stored.startswith(_PREFIX):
        return None
    keys = _resolve_keys()
    if not keys:
        return None
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    token = stored[len(_PREFIX) :]
    try:
        nonce_part, sealed_part = token.split(".", 1)
        nonce = base64.urlsafe_b64decode(nonce_part + "=" * (-len(nonce_part) % 4))
        sealed = base64.urlsafe_b64decode(sealed_part + "=" * (-len(sealed_part) % 4))
        return AESGCM(keys[0]).decrypt(nonce, sealed, None).decode("utf-8")
    except Exception:  # noqa: BLE001 - not the active key's cell, or corrupt
        return None


def decrypt(stored: str | None) -> str | None:
    """Reverse :func:`encrypt`. Unprefixed values pass through untouched so
    pre-migration plaintext rows read cleanly during the migration window;
    anything carrying the ``enc1:`` prefix must decrypt or the error is loud."""
    if stored is None:
        return None
    if not stored.startswith(_PREFIX):
        return stored
    keys = _resolve_keys()
    if not keys:
        raise RuntimeError(
            "found encrypted data but no encryption key is configured; set "
            f"{ENV_VARS} to the key(s) that encrypted this database"
        )
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    token = stored[len(_PREFIX) :]
    try:
        nonce_part, sealed_part = token.split(".", 1)
        nonce = base64.urlsafe_b64decode(nonce_part + "=" * (-len(nonce_part) % 4))
        sealed = base64.urlsafe_b64decode(sealed_part + "=" * (-len(sealed_part) % 4))
    except Exception as exc:  # noqa: BLE001 - corruption must be loud, not a junk decode
        raise RuntimeError(
            "decryption failed: the stored value is corrupt (unreadable token)"
        ) from exc
    # No stored key-id on purpose: GCM's auth tag answers "which key?" with
    # certainty, and a stored id would break the moment the operator removes
    # an old key and the list shifts. Try each key in order.
    for key in keys:
        try:
            return AESGCM(key).decrypt(nonce, sealed, None).decode("utf-8")
        except Exception:  # noqa: BLE001 - not this key's cell; try the next
            continue
    raise RuntimeError(
        "decryption failed: none of the configured keys matches the one that "
        "encrypted this data, or the stored value is corrupt"
    )


# ---------------------------------------------------------------------------
# Searchability without decryption
# ---------------------------------------------------------------------------
def lookup_hash(plaintext: str, *, key: bytes | None = None) -> str:
    """Keyed equality token for exact-match lookups (HMAC-SHA256).

    Keyed hashing is the whole point: Indian mobile numbers live in a ~10^10
    space, so an unkeyed hash column would be a plain-text equivalent for
    anyone with the database. With the HMAC, the hash is only invertible by
    whoever already has the key.
    """
    material = key if key is not None else _active_key()
    if material is None:
        # Plaintext mode: a plain hash keeps lookups working; it is weaker,
        # and /health reports that encryption is disabled.
        return "plain:" + hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return hmac.new(material, _LOOKUP_DOMAIN + plaintext.encode("utf-8"), hashlib.sha256).hexdigest()


def _active_key() -> bytes | None:
    keys = _resolve_keys()
    return keys[0] if keys else None


def lookup_hashes(plaintext: str) -> list[str]:
    """The lookup tokens a stored value could match, one per configured key.

    A row's hash column carries the hash of whichever key encrypted it. During
    a rotation window - the new key is first, the at-rest pass has not yet
    rewritten every row - equality lookups must match under ANY configured
    key, or farmers cannot log in and district aggregation goes blind between
    the config change and the end of the pass."""
    keys = _resolve_keys()
    if not keys:
        return [lookup_hash(plaintext)]
    return [
        hmac.new(key, _LOOKUP_DOMAIN + plaintext.encode("utf-8"), hashlib.sha256).hexdigest()
        for key in keys
    ]
