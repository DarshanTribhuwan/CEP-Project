"""Soil schemas.

The soil module fuses whatever evidence exists. These models make the evidence
list explicit in the response, so a farmer (and a reviewer) can see exactly
which source produced the answer and which were missing.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ..core.errors import DataProvenance
from .common import ConfidenceBand, Localized
from .farmer import SoilRainBehaviour, SoilType


class SoilEvidenceRequest(BaseModel):
    """Evidence the farmer can supply. Everything is optional."""

    knows_soil_type: bool = False
    soil_type: SoilType | None = Field(
        default=None,
        description="farmer's own answer, when they know it",
    )
    after_rain_behaviour: SoilRainBehaviour | None = Field(
        default=None,
        description="observable behaviour, usable even when the farmer does not know the type",
    )
    soil_tested_before: bool | None = None
    lab_report_soil_type: SoilType | None = Field(
        default=None,
        description="from a Soil Health Card / lab report if the farmer has one",
    )


class SoilEvidenceItem(BaseModel):
    source: str = Field(description="lab_report | farmer_known_type | farmer_observations | geo_dataset | image_model")
    provided: bool
    weight: float = Field(description="configured evidence weight for this source")
    contribution: float = Field(description="share of the final fused distribution (0..1)")
    value: str | None = None
    note: str | None = None


class SoilEstimateOut(BaseModel):
    farm_id: int
    top_soil_type: Localized | None = Field(
        default=None,
        description="null when no evidence was supplied - the system does not guess a soil type",
    )
    distribution: dict[str, float] = Field(
        description="fused probability mass over soil type codes; sums to ~1.0"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_band: ConfidenceBand
    evidence: list[SoilEvidenceItem]
    needs_lab_test: bool
    advice: str
    model_version: str
    provenance: DataProvenance
    created_at: datetime
