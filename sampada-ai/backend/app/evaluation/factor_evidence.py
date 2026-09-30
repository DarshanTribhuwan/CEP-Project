"""Factor evidence registry: what could validate each recommendation weight.

The instruction this module implements, verbatim from the project's standard:
*do not invent better-sounding weights.* A weight may only change when evidence
measured on held-out outcomes says so. This registry states, for each of the
six factors:

    status      measured | partial | unmeasured - is there validation evidence
                today, and of what kind?
    outcome     what recorded observation would test this factor's signal
    required    the minimum evidence bar before a weight change may even be
                *proposed* (the proposal itself stays a human decision)
    source      where that observation lives in this repository today

The one factor already measured - ``market_price`` - carries its measured
reliability from the walk-forward backtest via
``app/services/price_reliability.py``. Every other factor is labelled
``unmeasured`` on purpose: no held-out outcome evidence exists for them, so
their configured weights stay untouched and labelled unvalidated.
"""

from __future__ import annotations

from dataclasses import dataclass

from .backtest import HORIZON_DAYS
from ..services import price_reliability

MEASURED = "measured"
PARTIAL = "partial"
UNMEASURED = "unmeasured"


@dataclass(frozen=True, slots=True)
class FactorEvidence:
    factor: str
    status: str
    outcome_needed: str
    minimum_evidence: str
    evidence_source: str
    detail: str


# Market price is measured, so its bar is the one already met. The others
# state the bar they would have to clear before their weights change.
_REGISTRY: dict[str, FactorEvidence] = {
    "market_price": FactorEvidence(
        factor="market_price",
        status=MEASURED,
        outcome_needed=(
            "realized modal price HORIZON days after each cutoff, from the "
            "variety-wise Agmarknet history"
        ),
        minimum_evidence=(
            f"per-crop pooled coverage over >= 40 evaluated {HORIZON_DAYS}-day steps; "
            "weight scales by clamp(coverage/0.80, 0.5, 1.0)"
        ),
        evidence_source="app/evaluation/backtest.py -> app/data/price_reliability.json",
        detail=(
            "the only factor with held-out evidence; its weight is scaled per crop "
            "and the scaling is reported in the API score breakdown"
        ),
    ),
    "soil_suitability": FactorEvidence(
        factor="soil_suitability",
        status=UNMEASURED,
        outcome_needed=(
            "per-season yield/profit on farms whose soil estimate confidence is high, "
            "compared across crops that differ in soil fit"
        ),
        minimum_evidence=(
            ">= 100 recorded outcomes spanning soil-fit discordant pairs "
            "(same crop, different soil fit), time-split before any weight change"
        ),
        evidence_source="outcomes table joined to soil_estimates",
        detail=(
            "needs outcome accumulation across many farms; a single season cannot "
            "separate soil signal from weather or management"
        ),
    ),
    "weather_match": FactorEvidence(
        factor="weather_match",
        status=UNMEASURED,
        outcome_needed=(
            "season outcomes on rainfed farms, scored against the forecast the "
            "recommendation actually used"
        ),
        minimum_evidence=(
            ">= 100 recorded outcomes on rainfed farms across contrasting seasons, "
            "time-split before any weight change"
        ),
        evidence_source="outcomes table joined to stored forecast provenance",
        detail=(
            "irrigated farms mute the weather signal, so rainfed outcomes are the "
            "measurable subset"
        ),
    ),
    "water_availability": FactorEvidence(
        factor="water_availability",
        status=UNMEASURED,
        outcome_needed=(
            "outcomes where reported irrigation differs across farms growing the "
            "same crop in the same season"
        ),
        minimum_evidence=(
            ">= 100 outcomes with irrigation contrasts within crop-season cells, "
            "time-split before any weight change"
        ),
        evidence_source="outcomes table joined to farm profiles",
        detail="confounded with weather on rainfed farms; needs within-season contrasts",
    ),
    "farmer_history": FactorEvidence(
        factor="farmer_history",
        status=PARTIAL,
        outcome_needed=(
            "whether past-outcome crops actually led to better future outcomes than "
            "first-time crops, beyond crop mix"
        ),
        minimum_evidence=(
            ">= 200 outcomes with repeat-crop pairs, time-split before any weight change"
        ),
        evidence_source="outcomes + crop_history tables",
        detail=(
            "partial: crop_history rows exist from onboarding, but their link to "
            "recorded outcomes is exactly what remains unmeasured"
        ),
    ),
    "community_saturation": FactorEvidence(
        factor="community_saturation",
        status=UNMEASURED,
        outcome_needed=(
            "price outcomes in districts where app-issued area for a crop was high "
            "versus low at recommendation time"
        ),
        minimum_evidence=(
            ">= 300 outcomes across districts with issued-area variance, time-split; "
            "the signal itself is app-scoped and must not be treated as district oversupply"
        ),
        evidence_source="outcomes joined to app_issued_area at recommendation time",
        detail=(
            "the module already reports its limitation on every response; a weight "
            "change would need district-level contrast the MVP does not have"
        ),
    ),
}


def registry() -> dict[str, FactorEvidence]:
    """A copy of the registry, ordered by the engine's FACTOR_KEYS."""
    return dict(_REGISTRY)


def status_for(factor: str) -> FactorEvidence:
    return _REGISTRY[factor]


def unmeasured_factors() -> list[str]:
    return [f for f, e in _REGISTRY.items() if e.status == UNMEASURED]


def summary() -> dict[str, object]:
    """Machine-readable validation status for every factor weight."""
    factors: dict[str, object] = {}
    for factor, evidence in _REGISTRY.items():
        entry: dict[str, object] = {
            "status": evidence.status,
            "weight_validation": (
                "evidence-informed (per-crop scaling from measured coverage)"
                if evidence.status == MEASURED
                else "UNVALIDATED DEFAULT - held as configured until evidence exists"
            ),
            "outcome_needed": evidence.outcome_needed,
            "minimum_evidence": evidence.minimum_evidence,
            "evidence_source": evidence.evidence_source,
            "detail": evidence.detail,
        }
        if factor == "market_price" and price_reliability.active():
            entry["reliability_active"] = True
        factors[factor] = entry
    return {
        "protocol": (
            "weights change only on held-out evidence, never by intuition; see "
            "app/evaluation/heldout.py for the time-based split with leakage guard"
        ),
        "factors": factors,
        "unmeasured_count": len(unmeasured_factors()),
    }
