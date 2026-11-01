"""Feedback and outcome schemas.

These exist so the Part 4 learning loop has something real to evaluate later.
Distinguishing "the recommendation was wrong" from "the farmer did not follow
it" is explicitly required by the design: the two have completely different
implications for which module needs fixing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Followed = Literal["YES", "PARTIAL", "NO"]
FeedbackReason = Literal[
    "COST", "DISAGREED", "WEATHER_CHANGED", "MARKET_CHANGED",
    "NO_RESOURCES", "FAMILY_DECISION", "OTHER",
]


class FeedbackCreate(BaseModel):
    """The farmer's response. The recommendation id travels in the URL path,
    not the body, so the same payload shape can be reused elsewhere."""

    followed: Followed
    reason: FeedbackReason | None = None
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackOut(BaseModel):
    id: int
    recommendation_id: int
    followed: str
    reason: str | None
    comment: str | None
    created_at: datetime


class OutcomeCreate(BaseModel):
    """Recorded at the end of the season, so predictions can be scored."""

    farm_id: int
    crop_id: str
    recommendation_id: int | None = None
    season: Literal["KHARIF", "RABI", "SUMMER", "PERENNIAL"] | None = None
    year: int | None = Field(default=None, ge=1980, le=2100)
    area_ha: float | None = Field(default=None, gt=0)
    yield_quintal: float | None = Field(default=None, ge=0)
    cost_inr: float | None = Field(default=None, ge=0)
    sell_price_inr_per_quintal: float | None = Field(default=None, ge=0)
    market: str | None = None
    transport_cost_inr: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=2000)


class OutcomeOut(BaseModel):
    id: int
    farm_id: int
    recommendation_id: int | None
    crop_id: str
    season: str | None
    year: int | None
    yield_quintal: float | None
    cost_inr: float | None
    sell_price_inr_per_quintal: float | None
    profit_inr: float | None
    created_at: datetime
