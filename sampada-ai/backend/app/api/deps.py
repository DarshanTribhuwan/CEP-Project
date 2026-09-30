"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, Query, Request

from ..core.errors import AuthFailure, NotFound, ValidationFailure
from ..core.i18n import default_language, match_lang, normalize_lang
from ..repositories import farmers as farmers_repo
from ..repositories import farms as farms_repo


def optional_farmer(
    authorization: str | None = Header(default=None),
) -> dict[str, Any] | None:
    """Resolve the caller if a valid token is present, else None.

    Unlike :func:`current_farmer` this never raises, so it can be used by
    dependencies of endpoints that are also reachable anonymously.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    return farmers_repo.farmer_for_token(token)


def get_lang(
    request: Request,
    farmer: dict[str, Any] | None = Depends(optional_farmer),
    lang: str | None = Query(
        default=None,
        description=(
            "mr | hi | en. Resolution order: this parameter, then Accept-Language, "
            "then the logged-in farmer's saved preference, then the configured default."
        ),
    ),
) -> str:
    """Resolve the presentation language. Never affects stored values."""
    if lang:
        return normalize_lang(lang)
    from_header = match_lang(request.headers.get("accept-language"))
    if from_header:
        return from_header
    if farmer:
        # A returning farmer should not have to re-select their language.
        from_farmer = match_lang(farmer.get("language"))
        if from_farmer:
            return from_farmer
    return default_language()


def current_farmer(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Resolve the caller from a Bearer token."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthFailure("missing Bearer token")
    token = authorization.split(" ", 1)[1].strip()
    farmer = farmers_repo.farmer_for_token(token)
    if farmer is None:
        raise AuthFailure("invalid or revoked token")
    return farmer


def owned_farm(farm_id: int, farmer: dict[str, Any]) -> dict[str, Any]:
    """Fetch a farm and assert the caller owns it.

    Returns NotFound (not Forbidden) for someone else's farm, so the API does
    not confirm that an id exists to a caller who has no business knowing.
    """
    farm = farms_repo.get_farm(farm_id)
    if farm is None or int(farm["farmer_id"]) != int(farmer["id"]):
        raise NotFound(f"farm {farm_id} not found")
    return farm


def require_known_crop(crop_id: str) -> str:
    from ..services.crops import load_crops

    if crop_id not in load_crops():
        raise ValidationFailure(f"unknown crop id: {crop_id!r}")
    return crop_id
