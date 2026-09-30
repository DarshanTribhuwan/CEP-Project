"""Outcome routes.

Recording what actually happened is what makes the Part 4 learning loop
possible. Without it, "the system improves over time" is an assertion rather
than something that can be measured.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status

from ..core.errors import NotFound
from ..repositories import recommendations as rec_repo
from ..schemas.feedback import OutcomeCreate, OutcomeOut
from .deps import current_farmer, owned_farm, require_known_crop

router = APIRouter(tags=["outcomes"])


@router.post(
    "/outcomes",
    response_model=OutcomeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record what actually happened this season",
)
def create_outcome(
    payload: OutcomeCreate,
    farmer: dict[str, Any] = Depends(current_farmer),
) -> OutcomeOut:
    owned_farm(payload.farm_id, farmer)
    require_known_crop(payload.crop_id)

    if payload.recommendation_id is not None:
        # Link the outcome to the recommendation it evaluates, but only if the
        # caller owns the farm that recommendation was made for.
        record = rec_repo.get_recommendation(payload.recommendation_id)
        if record is None:
            raise NotFound(f"recommendation {payload.recommendation_id} not found")
        owned_farm(int(record["farm_id"]), farmer)

    row = rec_repo.add_outcome(payload.model_dump())
    return OutcomeOut(**row)


@router.get("/outcomes", summary="Recorded outcomes for a farm")
def list_outcomes(
    farm_id: int = Query(...),
    farmer: dict[str, Any] = Depends(current_farmer),
) -> list[dict[str, Any]]:
    owned_farm(farm_id, farmer)
    return rec_repo.list_outcomes(farm_id)
