"""Recommendation schemas.

Every recommendation carries: a ranked list of options, the reasons behind each,
a risk breakdown, a confidence value, the list of inputs that were unavailable,
and an explicit statement that the output is an estimate rather than a
guarantee. Those fields are not optional - the design sessions made them the
core of what makes this system trustworthy rather than a black box.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .common import ConfidenceBand, Localized, ReasonOut, RiskLevel, UnavailableOut
from .farmer import Season
from .market import PriceEstimateOut


class RecommendationRequest(BaseModel):
    farm_id: int
    season: Season | None = Field(
        default=None,
        description="defaults to the season inferred from the current month",
    )
    include_crops: list[str] = Field(
        default_factory=list,
        description="restrict candidates to these crop ids",
    )
    exclude_crops: list[str] = Field(default_factory=list)
    max_results: int | None = Field(default=None, ge=1, le=10)


class RiskBreakdownOut(BaseModel):
    weather: RiskLevel | None = None
    market: RiskLevel | None = None
    production: RiskLevel | None = None
    transport: RiskLevel | None = None
    policy: RiskLevel | None = None
    oversupply: RiskLevel | None = None
    overall: RiskLevel
    notes: list[str] = Field(default_factory=list)


class ScoreBreakdownOut(BaseModel):
    """Which factors contributed, and how much weight each actually carried.

    Weights are renormalized when a factor's data is unavailable, so this shows
    the *effective* weight rather than the configured one.
    """

    factor: str
    score: float | None
    configured_weight: float
    effective_weight: float
    available: bool


class CropOptionOut(BaseModel):
    crop: Localized
    rank: int
    final_score: float = Field(ge=0.0, le=1.0, description="weighted blend, 0..1")
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_band: ConfidenceBand
    suitability: float | None = Field(default=None, ge=0.0, le=1.0)
    price_estimate: PriceEstimateOut | None = None
    expected_return_note: str | None = None
    risk: RiskBreakdownOut
    reasons: list[ReasonOut]
    scores: list[ScoreBreakdownOut]
    pilot_plot_ha: float | None = Field(
        default=None,
        description="area to trial first, when the farmer has no recorded history",
    )


class StaleInputOut(BaseModel):
    """An input that was served from cache and is no longer current.

    Stale is not the same as missing. The value is real - it is exactly what the
    source returned - but it may have aged out, typically because the source is
    unreachable. Keeping it in its own list lets a client say "this price is from
    two days ago" instead of either hiding a usable number or presenting it as
    current.
    """

    component: str
    age_seconds: int | None = Field(
        default=None, description="how old the served cache entry is"
    )
    note: str


class RecommendationOut(BaseModel):
    farm_id: int
    season: Season
    generated_at: datetime
    options: list[CropOptionOut]
    unavailable_inputs: list[UnavailableOut] = Field(default_factory=list)
    stale_inputs: list[StaleInputOut] = Field(
        default_factory=list,
        description=(
            "inputs served from cache that are no longer current, with their age; "
            "a client must not render these as fresh readings"
        ),
    )
    model_versions: dict[str, str] = Field(default_factory=dict)
    is_provisional: bool
    ranking_notes: list[str] = Field(
        default_factory=list,
        description=(
            "localized warnings about the ranking itself, e.g. when the top options "
            "are effectively tied because factors were unavailable"
        ),
    )
    disclaimers: list[str] = Field(
        default_factory=list,
        description="localized statements that this is an estimate, not a guarantee",
    )
