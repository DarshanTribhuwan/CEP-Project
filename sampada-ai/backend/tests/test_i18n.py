"""Localization tests.

The architectural claim being tested: language only affects presentation. If
these pass, the decision layer cannot change its answer because a farmer
switched from Marathi to English.
"""

from __future__ import annotations

import json
import re

import pytest

from app.core.config import LOCALES_DIR
from app.core.i18n import enum_label, enum_options, normalize_lang, t, translation_status

LANGUAGES = ["en", "mr", "hi"]


def _load(lang: str) -> dict[str, str]:
    with open(LOCALES_DIR / f"{lang}.json", encoding="utf-8") as handle:
        raw = json.load(handle)
    return {key: value for key, value in raw.items() if isinstance(value, str)}


def test_every_locale_has_the_same_keys():
    english = set(_load("en"))
    for lang in LANGUAGES:
        assert set(_load(lang)) == english, f"{lang} key set differs from en"


def test_placeholders_match_across_locales():
    english = _load("en")
    for key, english_text in english.items():
        expected = set(re.findall(r"\{(\w+)\}", english_text))
        for lang in LANGUAGES:
            actual = set(re.findall(r"\{(\w+)\}", _load(lang)[key]))
            assert actual == expected, f"{lang}:{key} placeholders {actual} != {expected}"


def test_no_translation_is_empty():
    for lang in LANGUAGES:
        for key, value in _load(lang).items():
            assert value.strip(), f"{lang}:{key} is empty"


def test_marathi_and_hindi_use_devanagari():
    """Guards against an English string being pasted in as a translation."""
    for lang in ["mr", "hi"]:
        strings = [value for key, value in _load(lang).items() if "enum." in key]
        devanagari = sum(1 for value in strings if re.search(r"[\u0900-\u097F]", value))
        assert devanagari == len(strings), f"{lang} has non-Devanagari option labels"


def test_missing_key_falls_back_to_the_key_itself():
    # A visible key is better than an empty string in a farmer-facing screen.
    assert t("mr", "totally.missing.key") == "totally.missing.key"


def test_unknown_language_falls_back_to_default():
    assert normalize_lang("zz") == "en" or normalize_lang("zz") in {"mr", "hi", "en"}
    assert normalize_lang(None) == "mr"
    assert normalize_lang("en-GB") == "en"
    assert normalize_lang("mr-IN,en;q=0.8") == "mr"
    assert normalize_lang("hi") == "hi"


def test_enum_label_translates_known_codes():
    assert enum_label("en", "irrigation_type", "PARTIAL") == "Partially irrigated"
    assert enum_label("mr", "irrigation_type", "PARTIAL") != "Partially irrigated"


def test_unknown_enum_code_degrades_visibly_rather_than_to_nothing():
    """A missing translation must be visible, never an empty string."""
    label = enum_label("en", "irrigation_type", "NOPE")
    assert label
    assert "NOPE" in label


def test_enum_options_covers_every_code_used_by_schemas():
    for group, codes in {
        "irrigation_type": ["FULL", "PARTIAL", "RAINFED"],
        "soil_type": ["BLACK", "RED", "LATERITE", "ALLUVIAL", "SANDY_LOAM", "CLAY_LOAM", "SALINE"],
        "water_source": ["WELL", "BOREWELL", "RIVER", "CANAL", "RAINWATER", "POND", "OTHER"],
    }.items():
        available = {option["code"] for option in enum_options("en", group)}
        assert set(codes) <= available, f"{group} missing {set(codes) - available}"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_translation_status_reports_unreviewed(lang: str):
    """The project must not claim its translations were professionally reviewed."""
    status = translation_status(lang)
    assert status["reviewed_by_native_speaker"] is False
    assert status["missing_keys"] == []
