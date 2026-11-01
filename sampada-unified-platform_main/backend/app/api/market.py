"""Market price route.

With no verified source configured this returns HTTP 503 ``data_unavailable``
and explains what an operator must supply. It does not fall back to demo prices
unless the deployment was explicitly started in sample mode, in which case the
response is flagged ``is_demo = true``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ..core.errors import DataUnavailable
from ..providers import get_market_provider
from ..schemas.market import MarketPriceRecordOut, MarketPricesOut
from ..services.crops import crop_name
from .deps import current_farmer, get_lang, owned_farm, require_known_crop

router = APIRouter(tags=["market"])


@router.get("/market/prices", response_model=MarketPricesOut, summary="Mandi price history")
def prices(
    crop_id: str = Query(description="crop code, e.g. soybean"),
    district: str | None = Query(default=None),
    farm_id: int | None = Query(default=None, description="take the district from a farm"),
    days: int = Query(default=180, ge=7, le=1095),
    farmer: dict[str, Any] = Depends(current_farmer),
    lang: str = Depends(get_lang),
) -> MarketPricesOut:
    require_known_crop(crop_id)

    if farm_id is not None:
        farm = owned_farm(farm_id, farmer)
        district = district or farm.get("district")

    provider = get_market_provider()
    result = provider.price_history(crop_id, district, days)

    if not result.available or result.value is None:
        raise DataUnavailable(
            result.provenance.unavailable_reason or "no market price source available"
        )

    return MarketPricesOut(
        crop={"code": crop_id, "label": crop_name(crop_id, lang)},
        district=district,
        records=[
            MarketPriceRecordOut(
                market=record.market,
                district=record.district,
                state=record.state,
                price_date=record.price_date,
                min_price=record.min_price,
                max_price=record.max_price,
                modal_price=record.modal_price,
                unit=record.unit,
            )
            for record in result.value
        ],
        provenance=result.provenance,
    )
