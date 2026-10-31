"""Registration and session routes."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Header, status

from ..core.errors import AuthFailure, ValidationFailure
from ..core.i18n import normalize_lang, supported_languages
from ..repositories import farmers as farmers_repo
from ..schemas.auth import FarmerOut, LoginRequest, RegisterRequest, TokenResponse
from .deps import current_farmer

router = APIRouter(tags=["auth"])


def _clean_mobile(raw: str) -> str:
    cleaned = "".join(ch for ch in str(raw) if ch.isdigit() or ch == "+").lstrip("+")
    if cleaned.startswith("91") and len(cleaned) == 12:
        cleaned = cleaned[2:]
    if not re.match(r"^[6-9]\d{9}$", cleaned):
        raise ValidationFailure("mobile must be a 10-digit Indian mobile number")
    return cleaned


@router.post(
    "/auth/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a farmer",
)
def register(payload: RegisterRequest) -> TokenResponse:
    language = normalize_lang(payload.language)
    if language not in supported_languages():
        language = supported_languages()[0]
    farmer = farmers_repo.create_farmer(payload.name, payload.mobile, language, payload.pin)
    if farmer is None:
        raise ValidationFailure("a farmer with this mobile number already exists")
    token = farmers_repo.issue_token(farmer["id"])
    return TokenResponse(access_token=token, farmer=FarmerOut(**farmer))


@router.post("/auth/login", response_model=TokenResponse, summary="Log in")
def login(payload: LoginRequest) -> TokenResponse:
    farmer = farmers_repo.authenticate(_clean_mobile(payload.mobile), payload.pin)
    if farmer is None:
        # Deliberately identical message for unknown mobile and wrong PIN, so the
        # endpoint cannot be used to enumerate registered numbers.
        raise AuthFailure("invalid mobile number or PIN")
    token = farmers_repo.issue_token(farmer["id"])
    return TokenResponse(access_token=token, farmer=FarmerOut(**farmer))


@router.post("/auth/logout", summary="Revoke the current token")
def logout(
    farmer: dict[str, Any] = Depends(current_farmer),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    token = (authorization or "").split(" ", 1)[-1].strip()
    return {"revoked": farmers_repo.revoke_token(token)}


@router.get("/auth/me", response_model=FarmerOut, summary="Current farmer")
def me(farmer: dict[str, Any] = Depends(current_farmer)) -> FarmerOut:
    return FarmerOut(**farmer)


@router.patch("/auth/me/language", response_model=FarmerOut, summary="Change preferred language")
def set_language(
    language: str,
    farmer: dict[str, Any] = Depends(current_farmer),
) -> FarmerOut:
    resolved = normalize_lang(language)
    if not farmers_repo.set_language(int(farmer["id"]), resolved):
        raise ValidationFailure(f"could not set language {language!r}")
    updated = farmers_repo.get_farmer(int(farmer["id"])) or farmer
    return FarmerOut(**updated)
