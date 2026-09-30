"""Recommendation routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status

from ..core.errors import NotFound
from ..repositories import recommendations as rec_repo
from ..schemas.feedback import FeedbackCreate, FeedbackOut
from ..schemas.recommendation import RecommendationOut, RecommendationRequest
from ..services import recommendation as rec_service
from .deps import current_farmer, get_lang, owned_farm

router = APIRouter(tags=["recommendation"])


@router.post(
    "/recommendations",
    response_model=RecommendationOut,
    summary="Generate ranked crop options for a farm",
)
def create(
    payload: RecommendationRequest,
    farmer: dict[str, Any] = Depends(current_farmer),
    lang: str = Depends(get_lang),
) -> RecommendationOut:
    """Generate options and persist them.

    Persisting matters: the stored row keeps the model versions, the factor
    scores and the reasons that produced it, so a later outcome can be traced
    back to what the system actually said at the time.
    """
    farm = owned_farm(payload.farm_id, farmer)
    return rec_service.generate(
        farm,
        lang=lang,
        season=payload.season,
        include=payload.include_crops,
        exclude=payload.exclude_crops,
        max_results=payload.max_results,
    )


@router.get("/recommendations", summary="Past recommendations for a farm")
def list_for_farm(
    farm_id: int = Query(...),
    limit: int = Query(default=50, ge=1, le=200),
    farmer: dict[str, Any] = Depends(current_farmer),
) -> list[dict[str, Any]]:
    owned_farm(farm_id, farmer)
    return rec_repo.list_recommendations(farm_id, limit)


@router.get("/recommendations/{recommendation_id}", summary="One recommendation")
def get_one(
    recommendation_id: int,
    farmer: dict[str, Any] = Depends(current_farmer),
) -> dict[str, Any]:
    record = rec_repo.get_recommendation(recommendation_id)
    if record is None:
        raise NotFound(f"recommendation {recommendation_id} not found")
    owned_farm(int(record["farm_id"]), farmer)
    return record


@router.post(
    "/recommendations/{recommendation_id}/feedback",
    response_model=FeedbackOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record whether the farmer followed a recommendation",
)
def add_feedback(
    recommendation_id: int,
    payload: FeedbackCreate,
    farmer: dict[str, Any] = Depends(current_farmer),
) -> FeedbackOut:
    record = rec_repo.get_recommendation(recommendation_id)
    if record is None:
        raise NotFound(f"recommendation {recommendation_id} not found")
    owned_farm(int(record["farm_id"]), farmer)
    row = rec_repo.add_feedback(
        recommendation_id, payload.followed, payload.reason, payload.comment
    )
    return FeedbackOut(**row)
