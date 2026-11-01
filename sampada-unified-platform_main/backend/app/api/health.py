"""Health, provider-status and reference-data routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..core import crypto
from ..core import retention as retention_module
from ..core.config import env_secret, settings
from ..core.i18n import enum_options, supported_languages, t, translation_status
from ..core.db import db_path
from ..providers.http import OFFLINE_ENV_VAR, describe_offline_state
from ..services.crops import (
    crop_provenance,
    load_crops,
    load_soil_types,
    soil_provenance,
    typical_crops_for_soil,
)
from .deps import get_lang

router = APIRouter(tags=["meta"])


@router.get("/health", summary="Liveness and configuration summary")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "project": settings.require("project.name"),
        "version": settings.require("project.version"),
        "api_prefix": settings.get("backend.api_prefix", "/api/v1"),
        "default_language": settings.default_language,
        "supported_languages": supported_languages(),
        "data_mode": settings.data_mode,
        # Named, not pathed: a diagnostic endpoint describes the database file
        # without advertising the machine's directory layout. Operators who
        # need the location have the config; a response body that travels
        # through logs and screenshots should not carry it.
        "database": db_path().name,
        # Encryption at rest: report the real state. "Disabled" here means
        # the database currently stores mobile numbers and locations in
        # clear text - the honest thing is to surface that, not bury it.
        # key_count > 1 means a rotation is configured (first key active,
        # older keys still accepted for decryption) - visible from outside
        # the process, since a rotation's progress is invisible otherwise.
        "encryption_at_rest": (
            "enabled" if crypto.encryption_enabled() else "disabled"
        ),
        "encryption_key_count": crypto.key_count(),
        "encryption_note": (
            "mobile numbers and farm locations are stored AES-256-GCM encrypted"
            if crypto.encryption_enabled()
            else "NO encryption key configured: mobile numbers and farm locations "
            "are stored in clear text. Set SAMPADA_ENC_KEY (base64, 32 bytes) to "
            "enable encryption at rest; existing rows are encrypted on restart."
        ),
        # Retention: the policy itself, verbatim, so what would be deleted and
        # when is inspectable at runtime rather than buried in a config file.
        "retention": {
            **retention_module.policy(),
            "note": (
                "0 means keep forever. Purging is operator-invoked only: "
                "python scripts/apply_retention.py (dry run by default, --apply to execute)"
            ),
        },
    }


@router.get("/providers/status", summary="Which external sources are actually usable")
def provider_status() -> dict[str, Any]:
    """Report each data source's real state.

    This endpoint exists so that "we have no market data" is visible in the
    running system rather than discovered by a farmer seeing nothing. Secrets
    are reported as present/absent only - never echoed.
    """
    market_key_env = str(settings.get("providers.market.api_key_env", ""))
    resource_id = str(settings.get("providers.market.resource_id", "")).strip()
    soil_dataset = str(settings.get("providers.soil_geo.dataset_path", "")).strip()

    transport = describe_offline_state()

    return {
        "data_mode": settings.data_mode,
        "demo_data_active": settings.sample_mode,
        # How outbound requests behave right now: whether the machine is running
        # offline, how many retries a call gets, and what is in the response
        # cache. Reported here so "why is this value old?" is answerable from the
        # running system instead of from the logs.
        "transport": {
            **transport,
            "offline_note": (
                f"set {OFFLINE_ENV_VAR}=1 to run with no outbound requests; cached "
                "responses are reused and anything uncached is reported unavailable"
            ),
            "retry_note": (
                "only transient failures are retried (timeouts, refused connections, "
                "429 and 5xx); a bad key or a 404 fails immediately"
            ),
        },
        "providers": {
            "weather": {
                "configured": bool(settings.get("providers.weather.enabled", True)),
                "source": settings.get("providers.weather.primary"),
                "status": (
                    "disabled"
                    if not settings.get("providers.weather.enabled", True)
                    else "offline" if transport["offline"] else "usable"
                ),
                "note": (
                    "live keyless source; verified reachable from the development machine"
                    if not transport["offline"]
                    else "outbound requests are disabled, so only cached forecasts are served"
                ),
                "max_stale_minutes": settings.get("providers.weather.max_stale_minutes"),
            },
            "market": {
                "configured": bool(resource_id) and bool(env_secret(market_key_env)),
                "source": settings.get("providers.market.primary"),
                "resource_id_set": bool(resource_id),
                "api_key_env": market_key_env,
                "api_key_present": bool(env_secret(market_key_env)),
                "status": (
                    "usable"
                    if resource_id and env_secret(market_key_env)
                    else "unavailable"
                ),
                "note": (
                    "set providers.market.resource_id and the "
                    f"{market_key_env} environment variable to enable mandi prices"
                ),
            },
            "soil_geo": {
                "configured": bool(soil_dataset),
                "status": "usable" if soil_dataset else "unavailable",
                "note": "point providers.soil_geo.dataset_path at a downloaded soil GeoJSON",
            },
            "soil_image": {
                "configured": False,
                "status": "not_implemented",
                "note": "no trained photo model exists in this repository; nothing is claimed about its accuracy",
            },
            "news_and_policy": {
                "configured": False,
                "status": "not_implemented",
                "note": "news analysis and policy monitoring are out of scope for the MVP",
            },
            "transport_logistics": {
                "configured": False,
                "status": "not_implemented",
                "note": "shared-transport optimisation is out of scope for the MVP",
            },
        },
    }


@router.get("/i18n/status", summary="Translation completeness")
def i18n_status(lang: str = Depends(get_lang)) -> dict[str, Any]:
    """Coverage per language, including the honest review status."""
    return {
        "default": settings.default_language,
        "languages": [translation_status(code) for code in supported_languages()],
        "requested": lang,
        "review_notice": (
            "Marathi and Hindi strings were written by the development team and have "
            "NOT been reviewed by a native speaker or an agricultural extension officer"
        ),
    }


@router.get("/crops", summary="Crop reference table")
def crops(lang: str = Depends(get_lang)) -> dict[str, Any]:
    """The crop knowledge base, with per-field provenance.

    ``sources`` and each crop's ``provenance`` are returned so a client (or a
    reviewer) can tell which numbers are cited and which are curated judgement.
    """
    return {
        "provenance": crop_provenance(),
        "crops": [_crop_entry(crop, lang) for crop in load_crops().values()],
    }


def _crop_entry(crop: Any, lang: str) -> dict[str, Any]:
    """One crop for ``GET /crops``, with explicit PoP coverage status.

    ``pop_status`` lets a client distinguish "verified PoP exists" from "never
    sourced" without inferring it from the block's absence, and unavailable
    crops carry a localized farmer-facing explanation instead of an empty card.
    PoP is advisory metadata: no consumer of this endpoint changes scoring,
    so a missing block degrades the display, never the recommendation.
    """
    pop = crop.pop
    entry: dict[str, Any] = {
        "id": crop.id,
        "name": {"code": crop.id, "label": crop.name(lang)},
        "scientific_name": crop.scientific_name,
        "seasons": crop.seasons,
        "duration_days": crop.duration_days,
        "water_need_class": crop.water_need_class,
        "temp_optimal_c": crop.temp_optimal_c,
        "temp_absolute_c": crop.temp_absolute_c,
        "rainfall_optimal_mm": crop.rainfall_optimal_mm,
        "rainfall_absolute_mm": crop.rainfall_absolute_mm,
        "ph_optimal": crop.ph_optimal,
        "ph_absolute": crop.ph_absolute,
        "soil_types_optimal": crop.soil_types_optimal,
        "soil_types_tolerated": crop.soil_types_tolerated,
        "kc": crop.kc,
        "major_risks": crop.major_risks,
        "investment_level": crop.investment_level,
        "pop": pop,
        "pop_status": "verified" if pop else "unavailable",
        "pop_available": pop is not None,
        "provenance": crop.provenance,
    }
    if pop:
        entry["pop_sources"] = sorted({q["file"] for q in pop.get("quotes", []) if q.get("file")})
    else:
        # Localized at the edge: language affects presentation only.
        entry["pop_message"] = t(lang, "pop.unavailable")
    return entry


@router.get("/soil-types", summary="Soil type reference table")
def soil_types(lang: str = Depends(get_lang)) -> dict[str, Any]:
    typical = typical_crops_for_soil()
    return {
        "provenance": soil_provenance(),
        "soil_types": [
            {
                "id": soil.id,
                "name": {"code": soil.id, "label": soil.name(lang)},
                "texture_class": soil.texture_class,
                "ecocrop_drainage": soil.ecocrop_drainage,
                "water_retention": soil.water_retention,
                "drainage": soil.drainage,
                # Derived from the crop table (optimal matches only), so the two
                # reference tables cannot disagree with each other.
                "typical_crops": typical.get(soil.id, []),
            }
            for soil in load_soil_types().values()
        ],
    }


@router.get("/enums", summary="Standardized codes and their localized labels")
def enums(lang: str = Depends(get_lang)) -> dict[str, Any]:
    """Enumerations the client needs, so it hardcodes no farmer-facing text."""
    groups = [
        "irrigation_type",
        "water_source",
        "land_unit",
        "season",
        "soil_type",
        "soil_rain_behaviour",
        "confidence",
        "risk",
        "followed",
        "feedback_reason",
        "info_wanted",
        "problem",
        "resource",
    ]
    return {
        "language": lang,
        "enums": {group: enum_options(lang, group) for group in groups},
        "limits": {
            "guarantee": t(lang, "limits.guarantee"),
            "decision_support": t(lang, "limits.decision_support"),
            "no_model_accuracy_claim": t(lang, "limits.no_model_accuracy_claim"),
        },
    }
