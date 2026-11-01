"""Persistent cache for outbound HTTP responses.

Purpose
-------
Two things, in this order of importance:

  1. **Offline operation.** When a source cannot be reached, the last real
     response is still on disk. Serving it - *labelled with its age* - lets the
     system keep working during an outage instead of going blank. This is not
     fabrication: the bytes are exactly what the source returned. It is
     staleness, and staleness is something the farmer can be told about.

  2. **Politeness.** Several candidate crops are scored per recommendation
     request, and each one may query the same free public API. A cache turns
     that into one call.

What this module will not do
----------------------------
It will not synthesise, interpolate or extend a cached response. A cache entry
is either the verbatim stored payload or nothing at all. If a corrupt file is
found it is deleted, not repaired - a repaired payload would be data this
project invented.

Entries are stored one file per key so that a partial write, a crash or a disk
error can only ever damage a single entry.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import BACKEND_DIR, settings

# Bump when the on-disk shape changes; older entries are then ignored rather
# than misread.
CACHE_FORMAT_VERSION = 1
_KEY_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(slots=True)
class CacheEntry:
    """A stored response plus everything needed to describe its age honestly."""

    payload: Any
    stored_at: float
    url: str
    age_seconds: int

    @property
    def stored_at_iso(self) -> str:
        return datetime.fromtimestamp(self.stored_at, tz=timezone.utc).isoformat()

    @property
    def human_age(self) -> str:
        seconds = self.age_seconds
        if seconds < 90:
            return f"{seconds} seconds"
        if seconds < 5400:
            return f"{round(seconds / 60)} minutes"
        if seconds < 172800:
            return f"{round(seconds / 3600)} hours"
        return f"{round(seconds / 86400)} days"


def cache_dir() -> Path:
    """Where entries live. Configurable so a deployment can mount a volume."""
    raw = str(settings.get("providers.http.cache_dir", "var/http_cache")).strip()
    if not raw:
        return BACKEND_DIR / "var" / "http_cache"
    path = Path(raw)
    return path if path.is_absolute() else (BACKEND_DIR / path)


def cache_enabled() -> bool:
    return bool(settings.get("providers.http.cache_enabled", True))


def make_key(kind: str, url: str, params: dict[str, Any] | None = None) -> str:
    """Stable key for a request, safe to use as a filename."""
    material = json.dumps(
        {"kind": kind, "url": url, "params": params or {}},
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    label = _KEY_SAFE.sub("-", kind)[:24] or "cache"
    return f"{label}-{digest}"


def entry_path(key: str) -> Path:
    return cache_dir() / f"{key}.json"


def read(key: str) -> CacheEntry | None:
    """Load an entry, or ``None`` when missing, unreadable or from an old format.

    An unreadable entry is deleted rather than reported as an error: a corrupt
    cache is an inconvenience, and keeping it around only invites a later code
    path to try to make sense of it.
    """
    if not cache_enabled():
        return None
    path = entry_path(key)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None

    try:
        document = json.loads(raw)
    except ValueError:
        _discard(path)
        return None

    if not isinstance(document, dict):
        _discard(path)
        return None
    if document.get("format_version") != CACHE_FORMAT_VERSION:
        return None
    if "payload" not in document:
        _discard(path)
        return None

    try:
        stored_at = float(document["stored_at"])
    except (KeyError, TypeError, ValueError):
        _discard(path)
        return None

    age = max(0, int(time.time() - stored_at))
    return CacheEntry(
        payload=document["payload"],
        stored_at=stored_at,
        url=str(document.get("url", "")),
        age_seconds=age,
    )


def write(key: str, payload: Any, *, url: str, extra: dict[str, Any] | None = None) -> bool:
    """Persist a response verbatim. Returns False if it could not be stored.

    Written to a temporary file and then moved into place, so a reader never
    observes a half-written entry.
    """
    if not cache_enabled():
        return False

    directory = cache_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    document: dict[str, Any] = {
        "format_version": CACHE_FORMAT_VERSION,
        "url": url,
        "stored_at": time.time(),
        "payload": payload,
    }
    if extra:
        document.update(extra)

    try:
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{key}.",
            suffix=".tmp",
            delete=False,
        )
        with handle:
            json.dump(document, handle, ensure_ascii=False)
        os.replace(handle.name, entry_path(key))
        return True
    except (OSError, ValueError, TypeError):
        return False


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def clear() -> int:
    """Delete every entry. Returns how many were removed."""
    directory = cache_dir()
    if not directory.exists():
        return 0
    removed = 0
    for path in directory.glob("*.json"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def stats() -> dict[str, Any]:
    """Summary for the provider-status endpoint. Never includes payloads."""
    directory = cache_dir()
    files = sorted(directory.glob("*.json")) if directory.exists() else []
    now = time.time()
    ages: list[int] = []
    for path in files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            ages.append(max(0, int(now - float(document.get("stored_at", now)))))
        except (OSError, ValueError, TypeError):
            continue
    return {
        "enabled": cache_enabled(),
        "directory": str(directory),
        "entries": len(files),
        "oldest_entry_age_seconds": max(ages) if ages else None,
        "newest_entry_age_seconds": min(ages) if ages else None,
    }
