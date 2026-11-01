"""Market price providers.

Real source: the Agmarknet daily mandi price feed published on data.gov.in. That
feed requires an API key and a resource id, so this module reports the source as
*unavailable* until an operator supplies both. It never invents a price.

Demo source: ``app/data/sample/market_sample.json``, used only when
``data.mode = "sample"``. Everything it returns is tagged ``Provenance.SAMPLE``
with ``is_demo = true`` and is displayed behind a DEMO banner.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ..core.config import DATA_DIR, env_secret, settings
from .base import DataResult
from .http import fetch_json

DATAGOV_SOURCE = "data.gov.in / Agmarknet"
SAMPLE_SOURCE = "SAMPADA AI sample dataset (SYNTHETIC)"
SAMPLE_PATH = DATA_DIR / "sample" / "market_sample.json"

# Maps our internal crop codes onto the commodity strings used by the Agmarknet
# feed. VERIFIED 2026-09-23 against the live "Current Daily Price of Various
# Commodities from Various Markets (Mandi)" feed (resource
# 9ef84268-d588-465a-a308-a864a43d0070): every string below returned records.
# Re-check if the portal revises its commodity list; a mismatch produces an
# empty result set, which this provider reports as unavailable rather than as
# "no data exists".
AGMARKNET_COMMODITY: dict[str, str] = {
    "soybean": "Soyabean",
    "tur": "Arhar (Tur/Red Gram)(Whole)",
    "cotton": "Cotton",
    "maize": "Maize",
    "jowar": "Jowar(Sorghum)",
    "bajra": "Bajra(Pearl Millet/Cumbu)",
    "groundnut": "Groundnut",
    "onion": "Onion",
    "rice": "Paddy(Dhan)(Common)",
    "wheat": "Wheat",
    "gram": "Bengal Gram(Gram)(Whole)",
    "moong": "Green Gram (Moong)(Whole)",
    "urad": "Black Gram (Urd Beans)(Whole)",
    "sunflower": "Sunflower",
    "safflower": "Safflower",
}


@dataclass(slots=True)
class MarketPriceRecord:
    crop_id: str
    commodity: str
    market: str | None
    district: str | None
    state: str | None
    price_date: date | None
    min_price: float | None
    max_price: float | None
    modal_price: float | None
    unit: str = "INR per quintal"


def _as_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# Crops the daily APMC feed does not carry at all, each with the verified
# reason. Checked against the live feed on 2026-09-23; re-check if Agmarknet
# revises its commodity coverage. Listed here so the provider can report a
# precise reason instead of a generic "no records" result.
COMMODITY_NOT_IN_FEED: dict[str, str] = {
    "sugarcane": (
        "sugarcane is not traded on the daily APMC mandi feed - cane is bought "
        "by sugar mills at statutorily announced prices, not auctioned in the mandi"
    ),
}


class MarketProvider:
    """Interface. Implementations must indicate unavailability, not guess."""

    name: str = "market"

    def price_history(
        self, crop_id: str, district: str | None = None, days: int = 180
    ) -> DataResult[list[MarketPriceRecord]]:  # pragma: no cover - interface
        raise NotImplementedError


class DataGovAgmarknetProvider(MarketProvider):
    """Live provider. Requires resource_id plus SAMPADA_DATA_GOV_IN_KEY."""

    name = DATAGOV_SOURCE

    def price_history(
        self, crop_id: str, district: str | None = None, days: int = 180
    ) -> DataResult[list[MarketPriceRecord]]:
        """One recommendation request scores several candidate crops against this
        source, so the retry/cache policy in ``app/providers/http.py`` carries the
        load: a fresh entry answers without touching the network, and an outage
        falls back to the last real response instead of an empty result."""
        return self._fetch(crop_id, district, days)

    def _fetch(
        self, crop_id: str, district: str | None = None, days: int = 180
    ) -> DataResult[list[MarketPriceRecord]]:
        if not settings.get("providers.market.enabled", True):
            return DataResult.unavailable("market provider disabled in config.toml", source_name=self.name)

        resource_id = str(settings.get("providers.market.resource_id", "")).strip()
        if not resource_id:
            return DataResult.unavailable(
                "no Agmarknet resource id configured (providers.market.resource_id is empty)",
                source_name=self.name,
            )

        api_key_env = str(settings.get("providers.market.api_key_env", "SAMPADA_DATA_GOV_IN_KEY"))
        api_key = env_secret(api_key_env)
        if not api_key:
            return DataResult.unavailable(
                f"environment variable {api_key_env} is not set, so the mandi price feed cannot be read",
                source_name=self.name,
            )

        absent_reason = COMMODITY_NOT_IN_FEED.get(crop_id)
        if absent_reason:
            return DataResult.unavailable(absent_reason, source_name=self.name)

        commodity = AGMARKNET_COMMODITY.get(crop_id)
        if not commodity:
            return DataResult.unavailable(
                f"crop {crop_id!r} has no verified Agmarknet commodity mapping",
                source_name=self.name,
            )

        base_url = str(settings.require("providers.market.base_url")).rstrip("/")
        url = f"{base_url}/{resource_id}"
        timeout = float(settings.get("providers.market.timeout_seconds", 8))
        params: dict[str, Any] = {
            "api-key": api_key,
            "format": "json",
            "limit": 500,
            "filters[commodity]": commodity,
        }
        if district:
            params["filters[district]"] = district

        # Configuration problems are reported as-is and never consult the cache:
        # if an operator removes the API key, serving yesterday's cached prices
        # would look like the feed is still working.
        fresh_ttl = float(settings.get("providers.market.cache_ttl_minutes", 360)) * 60
        max_stale = float(settings.get("providers.market.max_stale_minutes", 2880)) * 60
        outcome = fetch_json(
            url,
            kind="market",
            params=params,
            timeout=timeout,
            fresh_ttl_seconds=fresh_ttl,
            max_stale_seconds=max_stale,
        )
        if not outcome.ok:
            return DataResult.unavailable(
                f"mandi price feed unavailable: {outcome.reason}", source_name=self.name
            )

        payload = outcome.payload
        records = payload.get("records") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            return DataResult.unavailable(
                "mandi price feed returned an unexpected shape (no records list)",
                source_name=self.name,
            )
        if not records:
            return DataResult.unavailable(
                f"mandi price feed returned no records for commodity {commodity!r}"
                + (f" in district {district!r}" if district else ""),
                source_name=self.name,
            )

        cutoff = date.today() - timedelta(days=days)
        parsed: list[MarketPriceRecord] = []
        for row in records:
            if not isinstance(row, dict):
                continue
            stamp = _as_date(row.get("arrival_date") or row.get("date"))
            if stamp is not None and stamp < cutoff:
                continue
            parsed.append(
                MarketPriceRecord(
                    crop_id=crop_id,
                    commodity=str(row.get("commodity", commodity)),
                    market=row.get("market"),
                    district=row.get("district"),
                    state=row.get("state"),
                    price_date=stamp,
                    min_price=_as_float(row.get("min_price")),
                    max_price=_as_float(row.get("max_price")),
                    modal_price=_as_float(row.get("modal_price")),
                )
            )

        if not parsed:
            return DataResult.unavailable(
                f"no records for {commodity!r} within the last {days} days", source_name=self.name
            )

        notes = [
            "commodity mapping verified against the live feed on 2026-09-23",
            f"{len(parsed)} records within {days} days",
        ] + outcome.provenance_note

        if outcome.from_cache:
            # Stale mandi prices are the most dangerous thing this system can
            # serve, so the age travels with the value and is marked stale once it
            # has passed its TTL rather than only when the network happened to fail.
            return DataResult.cached(
                parsed,
                source_name=self.name,
                source_url=url,
                age_seconds=outcome.age_seconds,
                is_stale=outcome.stale,
                notes=notes,
            )

        return DataResult.live(
            parsed,
            source_name=self.name,
            source_url=url,
            notes=notes,
        )


class SampleMarketProvider(MarketProvider):
    """Demo provider, active only when ``data.mode = "sample"``."""

    name = SAMPLE_SOURCE

    def price_history(
        self, crop_id: str, district: str | None = None, days: int = 180
    ) -> DataResult[list[MarketPriceRecord]]:
        if not settings.sample_mode:
            return DataResult.unavailable(
                "sample data is disabled (set data.mode = \"sample\" to enable demo data)",
                source_name=self.name,
            )
        if not SAMPLE_PATH.exists():
            return DataResult.unavailable(
                f"sample dataset missing at {SAMPLE_PATH}", source_name=self.name
            )

        with open(SAMPLE_PATH, encoding="utf-8") as handle:
            payload = json.load(handle)

        records = payload.get("records", {}) if isinstance(payload, dict) else {}
        rows = records.get(crop_id) if isinstance(records, dict) else None
        if not rows:
            return DataResult.unavailable(
                f"sample dataset has no rows for {crop_id!r}", source_name=self.name
            )

        parsed = [
            MarketPriceRecord(
                crop_id=crop_id,
                commodity=str(row.get("commodity", crop_id)),
                market=row.get("market"),
                district=row.get("district", district),
                state=row.get("state", "Maharashtra"),
                price_date=_as_date(row.get("date")),
                min_price=_as_float(row.get("min_price")),
                max_price=_as_float(row.get("max_price")),
                modal_price=_as_float(row.get("modal_price")),
            )
            for row in rows
            if isinstance(row, dict)
        ]
        meta = payload.get("_meta", {}) if isinstance(payload, dict) else {}
        warning = str(meta.get("warning", "")) if isinstance(meta, dict) else ""
        return DataResult.sample(
            parsed,
            source_name=self.name,
            notes=[warning] if warning else [],
        )


def get_market_provider() -> MarketProvider:
    """Choose the provider. Sample data is only ever reachable in sample mode."""
    if settings.sample_mode:
        return SampleMarketProvider()
    return DataGovAgmarknetProvider()
