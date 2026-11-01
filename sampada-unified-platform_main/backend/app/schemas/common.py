"""Shared schema primitives."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ConfidenceBand = Literal["HIGH", "MEDIUM", "LOW"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]


def confidence_band(confidence: float) -> ConfidenceBand:
    """Coarse band for display. Thresholds are presentation choices only."""
    if confidence >= 0.75:
        return "HIGH"
    if confidence >= 0.55:
        return "MEDIUM"
    return "LOW"


def risk_from_score(risk: float) -> RiskLevel:
    """Map a 0..1 risk score onto three display levels."""
    if risk >= 0.66:
        return "HIGH"
    if risk >= 0.33:
        return "MEDIUM"
    return "LOW"


class Localized(BaseModel):
    """A standardized code plus its rendering in the requested language.

    Both are always returned. The client shows ``label`` and nothing downstream
    is allowed to depend on it - only on ``code``.
    """

    code: str
    label: str


class ReasonOut(BaseModel):
    """One human-readable justification, traceable to its evidence."""

    code: str = Field(description="stable reason code, e.g. 'soil_suitable'")
    message: str = Field(description="localized text for the farmer")
    evidence: str = Field(
        description="which input this rests on: live | farmer_reported | unavailable | derived"
    )


class UnavailableOut(BaseModel):
    """An input the engine needed and could not obtain."""

    component: str
    reason: str
