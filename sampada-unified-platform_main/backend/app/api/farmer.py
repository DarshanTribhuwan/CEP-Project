"""Farm, crop-history and onboarding routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status

from ..core.i18n import enum_options, t
from ..repositories import farms as farms_repo
from ..schemas.farmer import (
    CropHistoryCreate,
    FarmCreate,
    FarmOut,
    FarmUpdate,
    OnboardingQuestionsOut,
    ResourcesPayload,
)
from .deps import current_farmer, get_lang, owned_farm

router = APIRouter(tags=["farmer"])

# One entry per onboarding question. The ``key`` resolves to text in the locale
# files, so no farmer-facing string is hardcoded in the client or the server.
ONBOARDING_QUESTIONS: list[dict[str, Any]] = [
    {"key": "q.location", "type": "LOCATION"},
    {"key": "q.land_area", "type": "NUMBER"},
    {"key": "q.land_unit", "type": "ENUM", "group": "land_unit"},
    {"key": "q.irrigated", "type": "ENUM", "group": "irrigation_type"},
    {"key": "q.water_source", "type": "ENUM", "group": "water_source"},
    {"key": "q.know_soil_type", "type": "BOOLEAN"},
    {"key": "q.soil_type", "type": "ENUM", "group": "soil_type", "optional": True},
    {"key": "q.soil_after_rain", "type": "ENUM", "group": "soil_rain_behaviour"},
    {"key": "q.soil_tested_before", "type": "BOOLEAN", "optional": True},
    {"key": "q.current_crop", "type": "CROP", "optional": True},
    {"key": "q.previous_crop", "type": "CROP", "optional": True},
    {"key": "q.resources", "type": "MULTI_ENUM", "group": "resource"},
    {"key": "q.sell_where", "type": "TEXT", "optional": True},
    {"key": "q.decide_sell_where", "type": "ENUM_MULTI", "optional": True,
     "options": [
         {"code": "HIGHEST_PRICE", "label": "Highest price"},
         {"code": "NEAREST_MARKET", "label": "Nearest market"},
         {"code": "TRUSTED_BUYER", "label": "Trusted buyer"},
         {"code": "TRADER", "label": "Trader"},
         {"code": "COOPERATIVE", "label": "Cooperative"},
     ]},
    {"key": "q.transport", "type": "TEXT", "optional": True},
    {"key": "q.willing_crops", "type": "MULTI_CROP"},
    {"key": "q.unwilling_crops", "type": "MULTI_CROP", "optional": True},
    {"key": "q.try_new_crop", "type": "ENUM", "options": [
        {"code": "YES", "label": t("en", "common.yes")},
        {"code": "NO", "label": t("en", "common.no")},
        {"code": "MAYBE", "label": t("en", "common.maybe")},
    ]},
    {"key": "q.info_wanted", "type": "MULTI_ENUM", "group": "info_wanted"},
    {"key": "q.biggest_problem", "type": "MULTI_ENUM", "group": "problem"},
]


@router.get(
    "/onboarding/questions",
    response_model=OnboardingQuestionsOut,
    summary="Localized onboarding question set",
)
def onboarding_questions(lang: str = Depends(get_lang)) -> OnboardingQuestionsOut:
    """The question set the app renders during onboarding.

    Returned in the farmer's language so nothing is hardcoded client-side. The
    ``key`` is stable and is what a client should switch on.
    """
    questions: list[dict[str, Any]] = []
    for entry in ONBOARDING_QUESTIONS:
        item: dict[str, Any] = {
            "key": entry["key"],
            "question": t(lang, entry["key"]),
            "type": entry["type"],
            "optional": bool(entry.get("optional", False)),
            "options": [],
        }
        if entry.get("group"):
            item["options_key"] = entry["group"]
            item["options"] = enum_options(lang, entry["group"])
        elif entry.get("options"):
            # Inline options that have no enum group of their own.
            item["options"] = [
                {"code": option["code"], "label": t(lang, option["label"]) if option["label"].startswith("common.") else option["label"]}
                for option in entry["options"]
            ]
        questions.append(item)
    return OnboardingQuestionsOut(language=lang, questions=questions)


@router.post(
    "/farms",
    response_model=FarmOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a farm",
)
def create_farm(
    payload: FarmCreate,
    farmer: dict[str, Any] = Depends(current_farmer),
) -> FarmOut:
    data = payload.model_dump()
    data["land_area_ha"] = payload.area_ha()
    data["land_area_input"] = payload.land_area
    data.pop("land_area", None)
    return FarmOut(**farms_repo.create_farm(int(farmer["id"]), data))


@router.get("/farms", response_model=list[FarmOut], summary="List my farms")
def list_farms(farmer: dict[str, Any] = Depends(current_farmer)) -> list[FarmOut]:
    return [FarmOut(**farm) for farm in farms_repo.list_farms(int(farmer["id"]))]


@router.get("/farms/{farm_id}", response_model=FarmOut, summary="Get one farm")
def get_farm(
    farm_id: int, farmer: dict[str, Any] = Depends(current_farmer)
) -> FarmOut:
    return FarmOut(**owned_farm(farm_id, farmer))


@router.patch("/farms/{farm_id}", response_model=FarmOut, summary="Update a farm")
def update_farm(
    farm_id: int,
    payload: FarmUpdate,
    farmer: dict[str, Any] = Depends(current_farmer),
) -> FarmOut:
    owned_farm(farm_id, farmer)
    changes = payload.model_dump(exclude_unset=True)
    if "land_area" in changes and changes["land_area"] is not None:
        from ..schemas.farmer import LAND_UNIT_TO_HA

        unit = changes.get("land_unit") or "ACRE"
        changes["land_area_ha"] = round(changes.pop("land_area") * LAND_UNIT_TO_HA[unit], 4)
        changes["land_area_input"] = payload.land_area
    updated = farms_repo.update_farm(farm_id, changes)
    return FarmOut(**updated)


@router.put(
    "/farms/{farm_id}/preferences",
    response_model=FarmOut,
    summary="Set resources, crop preferences and constraints",
)
def set_preferences(
    farm_id: int,
    payload: ResourcesPayload,
    farmer: dict[str, Any] = Depends(current_farmer),
) -> FarmOut:
    owned_farm(farm_id, farmer)
    updated = farms_repo.update_farm(farm_id, {"preferences": payload.model_dump()})
    return FarmOut(**updated)


@router.post(
    "/farms/{farm_id}/crop-history",
    status_code=status.HTTP_201_CREATED,
    summary="Record a past season (all fields optional)",
)
def add_crop_history(
    farm_id: int,
    payload: CropHistoryCreate,
    farmer: dict[str, Any] = Depends(current_farmer),
) -> dict[str, Any]:
    owned_farm(farm_id, farmer)
    from .deps import require_known_crop

    require_known_crop(payload.crop_id)
    return farms_repo.add_crop_history(farm_id, payload.model_dump())


@router.get("/farms/{farm_id}/crop-history", summary="List recorded seasons")
def list_crop_history(
    farm_id: int, farmer: dict[str, Any] = Depends(current_farmer)
) -> list[dict[str, Any]]:
    owned_farm(farm_id, farmer)
    return farms_repo.list_crop_history(farm_id)
