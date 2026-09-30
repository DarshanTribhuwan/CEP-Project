"""Localization layer.

Architectural rule this module exists to enforce (from the design sessions):

    Language only affects presentation.

Farmer answers are stored as standardized, language-independent codes
(``irrigation_type = "PARTIAL"``) and rendered into the farmer's language at
the edge. The AI/decision layer never sees Marathi text, so recommendations do
not change when a farmer switches language.

Loads ``app/locales/<lang>.json``. Missing keys fall back to the default
language and then to the key itself, so a missing translation degrades into
something visible rather than an empty string.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import LOCALES_DIR, settings

_LANG_RE = re.compile(r"^[a-z]{2}(-[A-Za-z0-9]+)?$")


@lru_cache(maxsize=None)
def _load(lang: str) -> dict[str, str]:
    path: Path = LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return {k: v for k, v in data.items() if isinstance(v, str)}


def supported_languages() -> list[str]:
    return settings.supported_languages


def default_language() -> str:
    return settings.default_language


def match_lang(candidate: str | None) -> str | None:
    """Return a supported code for the candidate, or None if it matches nothing.

    Distinct from :func:`normalize_lang` because callers sometimes need to know
    that a preference was absent rather than merely unsupported.
    """
    supported = supported_languages()
    if not candidate:
        return None
    # "mr-IN,en;q=0.8" -> ["mr-IN", "en"]
    for chunk in str(candidate).split(","):
        tag = chunk.split(";")[0].strip().replace("_", "-")
        if not tag or not _LANG_RE.match(tag):
            continue
        if tag in supported:
            return tag
        base = tag.split("-")[0]
        if base in supported:
            return base
    return None


def normalize_lang(candidate: str | None) -> str:
    """Map an Accept-Language header / ``?lang=`` value onto a supported code."""
    return match_lang(candidate) or default_language()


def t(lang: str, key: str, **fmt: Any) -> str:
    """Look up ``key`` in ``lang``, falling back to default language then key."""
    normalized = normalize_lang(lang)
    text = _load(normalized).get(key)
    if text is None and normalized != default_language():
        text = _load(default_language()).get(key)
    if text is None:
        return key
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError):
            return text
    return text


def enum_label(lang: str, group: str, code: str) -> str:
    """Translate a standardized code such as ``irrigation_type.PARTIAL``."""
    return t(lang, f"enum.{group}.{code}", code=code)


def enum_options(lang: str, group: str) -> list[dict[str, str]]:
    """All codes and labels for one enum group, so clients render pickers
    without hardcoding any farmer-facing text."""
    prefix = f"enum.{group}."
    base = _load(normalize_lang(lang)) or _load(default_language())
    codes = sorted(key[len(prefix) :] for key in base if key.startswith(prefix))
    return [{"code": code, "label": enum_label(lang, group, code)} for code in codes]


def translation_status(lang: str) -> dict[str, Any]:
    """Report translation completeness so gaps are visible rather than silent.

    The Marathi and Hindi strings shipped with the MVP were written by the
    development team and have NOT been reviewed by a native speaker or an
    agricultural extension officer. This endpoint is how the project surfaces
    that instead of hoping nobody asks.
    """
    base = set(_load(default_language()).keys())
    target = set(_load(normalize_lang(lang)).keys())
    missing = sorted(base - target)
    return {
        "language": normalize_lang(lang),
        "total_keys": len(base),
        "translated_keys": len(base & target),
        "missing_keys": missing,
        "reviewed_by_native_speaker": False,
    }
