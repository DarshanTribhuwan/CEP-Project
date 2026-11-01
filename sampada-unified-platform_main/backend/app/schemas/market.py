"""Market price schemas."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from ..core.errors import DataProvenance
from .common import Localized


class MarketPriceRecordOut(BaseModel):
    market: str | None
    district: str | None
    state: str | None
    price_date: date | None
    min_price: float | None
    max_price: float | None
    modal_price: float | None
    unit: str


class PriceEstimateOut(BaseModel):
    """A forecast range, never a single confident number."""

    low: float
    mid: float
    high: float
    unit: str = "INR per quintal"
    horizon_days: int
    confidence: float = Field(ge=0.0, le=1.0)
    method: str
    model_version: str
    based_on_records: int
    notes: list[str] = Field(default_factory=list)


class MarketPricesOut(BaseModel):
    crop: Localized
    district: str | None
    records: list[MarketPriceRecordOut] = Field(default_factory=list)
    provenance: DataProvenance
