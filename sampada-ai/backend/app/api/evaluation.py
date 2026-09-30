"""Evaluation status routes.

``GET /evaluation/factor-status`` is the machine-readable answer to "which
recommendation weights are actually validated?". It exists so the honesty of
this project is inspectable at runtime rather than only asserted in documents:
every unmeasured factor is labelled ``UNVALIDATED DEFAULT`` right next to the
configured value that is currently in force.

Like ``/providers/status``, this is unauthenticated: it leaks no farmer data
and no secrets, only the validation status of the system itself.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..core.config import BACKEND_DIR, settings
from ..core.errors import DataProvenance
from ..evaluation import factor_evidence, promotion
from ..evaluation.heldout import PROPOSALS_DIRNAME
from ..services import price_reliability

router = APIRouter(tags=["evaluation"])


def proposals_dir() -> Path:
    """Where evaluation ledgers live (git-ignored var/). A function so tests
    can point it at a temporary directory."""
    return BACKEND_DIR / PROPOSALS_DIRNAME


class FactorStatusOut(BaseModel):
    factor: str
    status: str = Field(description="measured | partial | unmeasured")
    configured_weight: float = Field(description="base weight in force from config.toml")
    weight_validation: str = Field(
        description="how this weight is currently justified, or the unvalidated label"
    )
    outcome_needed: str
    minimum_evidence: str
    evidence_source: str
    detail: str
    reliability_active: bool | None = Field(
        default=None,
        description="set only for factors whose weight is scaled by measured reliability",
    )


class FactorStatusResponse(BaseModel):
    protocol: str
    factors: list[FactorStatusOut]
    unmeasured_count: int
    per_crop_reliability: dict[str, float] | None = Field(
        default=None,
        description=(
            "for measured factors: the per-crop reliability the engine currently "
            "applies, plus the default applied to crops not yet backtested"
        ),
    )
    default_reliability: float | None = None
    provenance: DataProvenance


@router.get(
    "/evaluation/factor-status",
    response_model=FactorStatusResponse,
    summary="Validation status of every recommendation factor weight",
)
def factor_status() -> FactorStatusResponse:
    weights = {
        key: float(value)
        for key, value in (settings.get("recommendation.factor_weights") or {}).items()
    }
    summary = factor_evidence.summary()

    factors: list[FactorStatusOut] = []
    for factor, entry in summary["factors"].items():  # type: ignore[union-attr]
        factors.append(
            FactorStatusOut(
                factor=factor,
                status=str(entry["status"]),
                configured_weight=float(weights.get(factor, 0.0)),
                weight_validation=str(entry["weight_validation"]),
                outcome_needed=str(entry["outcome_needed"]),
                minimum_evidence=str(entry["minimum_evidence"]),
                evidence_source=str(entry["evidence_source"]),
                detail=str(entry["detail"]),
                reliability_active=bool(entry.get("reliability_active", False)) or None,
            )
        )

    per_crop: dict[str, float] | None = None
    default_reliability: float | None = None
    if price_reliability.active():
        # Only measured crops appear here; the default for unmeasured ones is
        # its own field, so a default is never disguised as a measurement.
        per_crop = price_reliability.reliabilities()
        default_reliability = price_reliability.DEFAULT_RELIABILITY

    return FactorStatusResponse(
        protocol=str(summary["protocol"]),
        factors=factors,
        unmeasured_count=int(summary["unmeasured_count"]),
        per_crop_reliability=per_crop,
        default_reliability=default_reliability,
        provenance=DataProvenance(
            data_source="derived",
            source_name="app/evaluation/factor_evidence.py",
            notes=[
                "validation status of the recommendation weights themselves; "
                "derived from the evaluation registry, not from live data",
            ],
        ),
    )


class PromotionOut(BaseModel):
    proposal_fingerprint: str
    factor: str
    old_weight: float
    new_weight: float
    incumbent_name: str
    window: str
    metrics: dict
    evidence_bar: dict
    decision: str
    decided_by: str | None = None
    decided_at: str | None = None
    override_reason: str | None = None
    evaluated_at: str


class PromotionsResponse(BaseModel):
    promotions: list[PromotionOut]
    note: str
    provenance: DataProvenance


@router.get(
    "/evaluation/promotions",
    response_model=PromotionsResponse,
    summary="Model-promotion ledger: incumbent vs challenger comparisons and human decisions",
)
def list_promotions() -> PromotionsResponse:
    """Read-only surface over the promotion ledger.

    Exists so the promotion process is inspectable at runtime: what was
    proposed, what the held-out segment said, and who decided what. Writing
    decisions is deliberately NOT an API - adoption is an operator action on
    the ledger (scripts/promote_model.py), never a POST anyone could hit.
    """
    return PromotionsResponse(
        promotions=promotion.list_promotions(proposals_dir()),
        note=(
            "each record compares the incumbent weight vector against one "
            "challenger on a held-out outcome segment, once; adoption is a "
            "human decision recorded in the ledger (scripts/promote_model.py), "
            "and no automatic path applies weights to config.toml"
        ),
        provenance=DataProvenance(
            data_source="derived",
            source_name="app/evaluation/promotion.py",
            notes=[
                "computed from stored factor-score snapshots (schema v2) and "
                "recorded outcomes only; no recomputation of recommendations",
            ],
        ),
    )
