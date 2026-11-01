"""Soil evidence and estimation routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..schemas.soil import SoilEstimateOut, SoilEvidenceRequest
from ..services import soil as soil_service
from .deps import current_farmer, get_lang, owned_farm

router = APIRouter(tags=["soil"])


@router.post(
    "/farms/{farm_id}/soil/estimate",
    response_model=SoilEstimateOut,
    summary="Estimate soil type from available evidence",
)
def estimate_soil(
    farm_id: int,
    payload: SoilEvidenceRequest,
    farmer: dict[str, Any] = Depends(current_farmer),
    lang: str = Depends(get_lang),
) -> SoilEstimateOut:
    """Fuse whatever soil evidence exists and report confidence honestly.

    Supplying no evidence is a valid call: it returns ``top_soil_type = null``
    with confidence 0 rather than guessing, because a guessed soil type would
    silently drive every later crop recommendation.
    """
    farm = owned_farm(farm_id, farmer)
    estimate = soil_service.estimate_soil(
        payload,
        farm_id=farm_id,
        latitude=farm.get("latitude"),
        longitude=farm.get("longitude"),
        lang=lang,
    )
    soil_service.store_estimate(farm_id, estimate)
    return estimate


@router.get(
    "/farms/{farm_id}/soil/estimate/latest",
    summary="Most recent stored soil estimate for a farm",
)
def latest_soil(
    farm_id: int,
    farmer: dict[str, Any] = Depends(current_farmer),
) -> dict[str, Any]:
    """Returns the stored estimate, or explicitly reports that none exists.

    Read-only: it does not recompute, so the stored ``model_version`` and
    confidence remain the ones that actually influenced past recommendations.
    """
    owned_farm(farm_id, farmer)
    row = soil_service.latest_estimate_row(farm_id)
    if row is None:
        return {
            "farm_id": farm_id,
            "available": False,
            "reason": "no soil estimate has been recorded for this farm yet",
        }
    import json

    return {
        "farm_id": farm_id,
        "available": True,
        "top_soil_type": row["top_soil_type"],
        "confidence": row["confidence"],
        "distribution": json.loads(row["distribution"]),
        "evidence": json.loads(row["evidence"]),
        "model_version": row["model_version"],
        "created_at": row["created_at"],
    }
