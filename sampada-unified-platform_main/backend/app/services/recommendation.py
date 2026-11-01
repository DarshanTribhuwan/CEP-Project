"""Recommendation engine (Part 3 of the four-part split).

Implements the objective the design sessions converged on:

    maximise profit(individually)   ->  reduce the probability and impact of
                                        avoidable loss across a community

Concretely, each candidate crop is scored on six factors, then blended with the
weights from ``config.toml``:

    soil_suitability · weather_match · market_price
    water_availability · farmer_history · community_saturation

Rules this module enforces:

  * A factor whose data is missing scores ``None`` and its weight is
    redistributed proportionally. Missing data never becomes a neutral pass.
  * Effective weights are reported, so a reviewer can see what actually drove
    the answer.
  * Confidence falls when factors are missing, and the recommendation is marked
    provisional below the configured threshold.
  * Every claim in ``reasons`` carries the evidence class it rests on.
  * No output claims a guaranteed price or profit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..core.config import settings
from ..core.errors import ValidationFailure
from ..core.i18n import enum_label, t
from ..providers import get_forecast, get_market_provider
from ..repositories import farms as farms_repo
from ..repositories import recommendations as rec_repo
from ..schemas.common import (
    ReasonOut,
    UnavailableOut,
    confidence_band,
    risk_from_score,
)
from ..schemas.recommendation import (
    CropOptionOut,
    RecommendationOut,
    RiskBreakdownOut,
    ScoreBreakdownOut,
    StaleInputOut,
)
from . import community
from . import price as price_service
from . import price_reliability
from . import suitability
from .crops import crop_name, infer_season, load_crops

# Two options closer than this are treated as tied for display purposes.
TIE_EPSILON = 0.01

FACTOR_KEYS = (
    "soil_suitability",
    "weather_match",
    "market_price",
    "water_availability",
    "farmer_history",
    "community_saturation",
)

# Confidence assigned to each factor's own input data. These describe how much
# the system trusts the *input*, not how well the model performs.
QUALITY = {
    "soil_suitability": None,   # taken from the stored soil estimate's confidence
    "weather_match": 0.75,      # live forecast, short horizon
    "market_price": None,       # taken from the price estimate's confidence
    "water_availability": 0.70, # farmer-reported irrigation, not independently verified
    "farmer_history": None,     # scaled by number of recorded seasons
    "community_saturation": 0.50,  # app-scoped signal only; deliberately low
}


@dataclass(slots=True)
class Factor:
    key: str
    score: float | None
    quality: float | None
    configured_weight: float
    effective_weight: float = 0.0


@dataclass(slots=True)
class Candidate:
    crop_id: str
    factors: dict[str, Factor] = field(default_factory=dict)
    final_score: float = 0.0
    confidence: float = 0.0
    factor_scores: list[dict[str, Any]] = field(default_factory=list)
    estimate: price_service.PriceEstimate | None = None
    suit: suitability.SuitabilityResult | None = None
    risk: dict[str, float | None] = field(default_factory=dict)
    reasons: list[ReasonOut] = field(default_factory=list)
    pilot_plot_ha: float | None = None


def _weights() -> dict[str, float]:
    section = settings.section("recommendation.factor_weights")
    return {key: float(section.get(key, 0.0)) for key in FACTOR_KEYS}


def _candidate_crops(
    season: str,
    include: list[str],
    exclude: list[str],
    preferences: dict[str, Any],
) -> list[str]:
    crops = load_crops()
    willing = set(preferences.get("willing_crops") or [])
    unwilling = set(preferences.get("unwilling_crops") or [])

    selected: list[str] = []
    for crop_id, crop in crops.items():
        if include and crop_id not in include:
            continue
        if crop_id in exclude or crop_id in unwilling:
            continue
        if willing and crop_id not in willing:
            continue
        if "PERENNIAL" not in crop.seasons and season not in crop.seasons:
            continue
        selected.append(crop_id)

    if not selected:
        raise ValidationFailure(
            "no crops match these constraints for the given season",
            details={"season": season, "include": include, "exclude": exclude},
        )
    return selected


def _market_factor(
    estimate: price_service.PriceEstimate | None,
    all_mids: list[float],
) -> tuple[float | None, float | None]:
    """Return (score, quality) for the market factor."""
    if estimate is None or not all_mids:
        return None, None
    low, high = min(all_mids), max(all_mids)
    return_score = 0.5 if high <= low else (estimate.mid - low) / (high - low)
    stability = price_service.stability_score(estimate)
    score = round(0.65 * return_score + 0.35 * stability, 4)
    return score, estimate.confidence


def _history_factor(
    crop_id: str, history: dict[str, dict[str, float]]
) -> tuple[float | None, float | None]:
    if not history:
        return None, None
    total_records = sum(entry.get("records", 0.0) for entry in history.values())
    quality = round(min(1.0, total_records / 5.0), 4)
    entry = history.get(crop_id)
    if entry is None:
        # Never grown here before: weak negative, not a disqualification.
        return 0.35, quality
    score = round(0.60 + 0.40 * min(1.0, entry.get("records", 0.0) / 3.0), 4)
    return score, quality


def _blend(factors: dict[str, Factor]) -> tuple[float, float, float]:
    """Returns (final_score, confidence, coverage)."""
    available = [f for f in factors.values() if f.score is not None and f.quality is not None]
    total_configured = sum(f.configured_weight for f in factors.values())
    if not available or total_configured <= 0:
        return 0.0, 0.05, 0.0

    covered_weight = sum(f.configured_weight for f in available)
    coverage = covered_weight / total_configured

    for factor in available:
        factor.effective_weight = round(factor.configured_weight / covered_weight, 4)

    final_score = sum(f.effective_weight * float(f.score) for f in available)
    mean_quality = sum(f.effective_weight * float(f.quality) for f in available)
    confidence = max(0.05, min(0.95, coverage * mean_quality))
    return round(final_score, 4), round(confidence, 4), round(coverage, 4)


def _snapshot_input_context(
    *,
    season: str,
    farm: dict[str, Any],
    soil_row: dict[str, Any] | None,
    forecast_result: Any,
    market_results: dict[str, Any],
    community_signal: community.CommunitySignal,
    candidate_crop_ids: list[str],
) -> dict[str, Any]:
    """Freeze the input state this recommendation was generated from.

    The factor_scores snapshot (schema v2) records what the system *concluded*;
    this records what it *consumed*: the farm profile values the factors were
    scored against, the soil estimate row that fed the soil factor, the season,
    the candidate list, and the provenance of the weather and market data
    (including whether it was served from cache and how old it was).

    Everything is copied verbatim from the objects that drove the blend - never
    recomputed at save time - so later held-out evaluation can join an outcome
    to the exact inputs named in ``app/evaluation/factor_evidence.py`` ("scored
    against the forecast the recommendation actually used", "app_issued_area at
    recommendation time"). Two recommendations with identical scores but
    different input provenance are not the same evidence.
    """
    farm_profile = {
        key: farm.get(key)
        for key in (
            "district",
            "taluka",
            "village",
            "latitude",
            "longitude",
            "irrigation_type",
            "water_source",
        )
    }

    def provenance(result: Any) -> dict[str, Any]:
        return result.provenance.model_dump(mode="json")

    return {
        "season": season,
        "farm": farm_profile,
        "soil_estimate_id": int(soil_row["id"]) if soil_row else None,
        "candidate_crop_ids": list(candidate_crop_ids),
        "weather": provenance(forecast_result),
        "market": {
            crop_id: provenance(result) for crop_id, result in market_results.items()
        },
        "community": {
            "available": community_signal.available,
            "issued_area_ha": dict(community_signal.issued_area_ha),
            "shares": dict(community_signal.shares),
            "total_issued_area_ha": community_signal.total_issued_area_ha,
            "reason": community_signal.reason,
        },
    }


def _age_hours(seconds: int | None) -> int:
    """Round a cache age to whole hours, never reporting "0 hours old"."""
    if not seconds or seconds <= 0:
        return 1
    return max(1, round(seconds / 3600))


def _stale_inputs(
    lang: str,
    forecast_result: Any,
    market_results: dict[str, Any],
) -> list[StaleInputOut]:
    """Describe inputs that were served from cache past their freshness window.

    Only *stale* entries appear here. A cache hit that is still within its TTL is
    current enough to be treated as live, and reporting it would be noise.
    """
    items: list[StaleInputOut] = []

    weather_age = forecast_result.provenance.cached_age_seconds
    if forecast_result.provenance.is_stale:
        items.append(
            StaleInputOut(
                component="weather",
                age_seconds=weather_age,
                note=t(lang, "rec.stale_weather", hours=_age_hours(weather_age)),
            )
        )

    stale_market = [
        result
        for result in market_results.values()
        if result.available and result.provenance.is_stale
    ]
    if stale_market:
        oldest = max(
            (result.provenance.cached_age_seconds or 0) for result in stale_market
        )
        items.append(
            StaleInputOut(
                component="market_price",
                age_seconds=oldest,
                note=t(lang, "rec.stale_price", hours=_age_hours(oldest)),
            )
        )

    return items


def _build_reasons(
    lang: str,
    crop_id: str,
    suit: suitability.SuitabilityResult,
    estimate: price_service.PriceEstimate | None,
    community_signal: community.CommunitySignal,
    history_score: float | None,
    irrigation_label: str,
    season_label: str,
    stale_factors: set[str] | None = None,
) -> list[ReasonOut]:
    reasons: list[ReasonOut] = []
    stale = stale_factors or set()

    def add(code: str, evidence: str, **fmt: Any) -> None:
        reasons.append(
            ReasonOut(code=code, message=t(lang, f"reason.{code}", **fmt), evidence=evidence)
        )

    # Soil -----------------------------------------------------------------
    if suit.soil is None:
        add("soil_unknown", "unavailable")
    elif suit.soil >= 0.75:
        add("soil_suitable", "farmer_reported")
    else:
        add("soil_marginal", "farmer_reported")

    # Water ----------------------------------------------------------------
    if suit.water is not None:
        if suit.water >= 1.0:
            add("water_fits", "farmer_reported", irrigation=irrigation_label)
        else:
            add("water_short", "farmer_reported")

    # Season ---------------------------------------------------------------
    if suit.season is not None:
        if suit.season >= 1.0:
            add("season_matches", "derived", season=season_label)
        else:
            add("season_mismatch", "derived", season=season_label)

    # Weather --------------------------------------------------------------
    if suit.weather is None:
        add("weather_unavailable", "unavailable")
    elif suit.weather >= 0.5:
        add("weather_favourable", "live")
    else:
        add("weather_unfavourable", "live")
    if "weather" in stale:
        # Scored on a forecast that is past its freshness window, so the score is
        # only as good as the age of the data behind it.
        add("weather_stale", "cached")

    # Market ---------------------------------------------------------------
    if estimate is None:
        add("price_unavailable", "unavailable")
    elif price_service.stability_score(estimate) >= 0.5:
        add("price_stable", "derived")
    else:
        add("price_volatile", "derived")
    if "price" in stale:
        add("price_stale", "cached")
    if estimate is not None and not price_reliability.measured(crop_id):
        # This crop scored, but on the unmeasured default weight rather than
        # evidence from the backtest. Say so rather than let it pass as measured.
        add("price_unmeasured", "derived")

    # Community ------------------------------------------------------------
    score = community_signal.score_for(crop_id, len(load_crops()))
    if score is None:
        add("community_unknown", "unavailable")
    elif score >= 0.5:
        add("community_ok", "derived")
    else:
        add("community_saturated", "derived")

    # History --------------------------------------------------------------
    if history_score is None:
        add("no_history", "unavailable")
    elif history_score >= 0.6:
        add("history_good", "farmer_reported")
    else:
        add("history_poor", "farmer_reported")

    return reasons


def generate(
    farm: dict[str, Any],
    lang: str,
    season: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    max_results: int | None = None,
    persist: bool = True,
) -> RecommendationOut:
    """Produce ranked, explained crop options for one farm."""
    farm_id = int(farm["id"])
    district = farm.get("district")
    season = season or infer_season(datetime.now(timezone.utc).month)
    preferences = farm.get("preferences") or {}

    crop_ids = _candidate_crops(
        season, include or [], exclude or [], preferences
    )

    # --- Shared inputs, fetched once ------------------------------------
    soil_row = _latest_soil(farm_id)
    soil_distribution = _soil_distribution(soil_row)
    soil_confidence = float(soil_row["confidence"]) if soil_row else None

    forecast_result = _forecast_for(farm)
    forecast = forecast_result.value if forecast_result.available else None

    market_provider = get_market_provider()
    # The whole DataResult is kept, not just the estimate: its provenance is what
    # records whether these prices are current or a labelled cache fallback.
    market_results: dict[str, Any] = {
        crop_id: market_provider.price_history(crop_id, district) for crop_id in crop_ids
    }
    estimates: dict[str, price_service.PriceEstimate | None] = {
        crop_id: price_service.estimate_price(result)
        for crop_id, result in market_results.items()
    }
    all_mids = [e.mid for e in estimates.values() if e is not None]

    history = farms_repo.history_summary(farm_id)
    issued = rec_repo.app_issued_area(season, district, exclude_farm_id=farm_id)
    community_signal = community.compute_signal(issued, crop_ids)

    weights = _weights()
    irrigation_label = enum_label(lang, "irrigation_type", farm["irrigation_type"])
    season_label = enum_label(lang, "season", season)
    has_outcomes = rec_repo.outcome_count_for_farm(farm_id) > 0
    pilot_pct = float(settings.get("recommendation.pilot_plot_percentage", 0.10))

    # --- Score every candidate -------------------------------------------
    candidates: list[Candidate] = []
    for crop_id in crop_ids:
        crop = load_crops()[crop_id]
        suit = suitability.evaluate(
            crop,
            soil_distribution,
            farm["irrigation_type"],
            farm.get("water_source"),
            season,
            forecast,
        )
        estimate = estimates[crop_id]
        market_score, market_quality = _market_factor(estimate, all_mids)
        # The market factor's weight is scaled by how reliably the price signal
        # has been MEASURED to work for this crop (walk-forward backtest,
        # app/services/price_reliability.py). The scaled value is reported as
        # configured_weight, so the score breakdown always shows the weight
        # that actually applied - and the price_unmeasured reason says when a
        # crop is riding the default instead of its own evidence.
        market_weight = round(
            weights["market_price"] * price_reliability.reliability_for(crop_id), 6
        )

        # Which of this crop's inputs are real but not current. Carried into the
        # reasons so the farmer sees it next to the number it applies to.
        stale_factors: set[str] = set()
        if forecast_result.provenance.is_stale:
            stale_factors.add("weather")
        market_result = market_results[crop_id]
        if market_result.available and market_result.provenance.is_stale:
            stale_factors.add("price")
        history_score, history_quality = _history_factor(crop_id, history)
        community_score = community_signal.score_for(crop_id, len(crop_ids))

        factors = {
            "soil_suitability": Factor("soil_suitability", suit.soil, soil_confidence, weights["soil_suitability"]),
            "weather_match": Factor("weather_match", suit.weather, QUALITY["weather_match"], weights["weather_match"]),
            "market_price": Factor("market_price", market_score, market_quality, market_weight),
            "water_availability": Factor("water_availability", suit.water, QUALITY["water_availability"], weights["water_availability"]),
            "farmer_history": Factor("farmer_history", history_score, history_quality, weights["farmer_history"]),
            "community_saturation": Factor("community_saturation", community_score, QUALITY["community_saturation"] if community_signal.available else None, weights["community_saturation"]),
        }

        final_score, confidence, _coverage = _blend(factors)
        # Snapshot exactly what drove this answer, at the moment it drove it.
        # This is the raw material future outcome evaluation needs: per-factor
        # scores, availability, the weight the config asked for and the weight
        # that actually applied (post reliability scaling). Stored verbatim;
        # recomputing it later from code would risk drift if weights change.
        factor_snapshot = [
            {
                "factor": key,
                "score": factors[key].score,
                "quality": factors[key].quality,
                "configured_weight": factors[key].configured_weight,
                "effective_weight": factors[key].effective_weight,
                "available": factors[key].score is not None and factors[key].quality is not None,
            }
            for key in FACTOR_KEYS
        ]

        candidate = Candidate(
            crop_id=crop_id,
            factors=factors,
            final_score=final_score,
            confidence=confidence,
            factor_scores=factor_snapshot,
            estimate=estimate,
            suit=suit,
        )
        candidate.risk = _risk(crop, suit, estimate, community_signal, crop_id, len(crop_ids))
        candidate.reasons = _build_reasons(
            lang, crop_id, suit, estimate, community_signal, history_score,
            irrigation_label, season_label, stale_factors,
        )
        if not has_outcomes:
            candidate.pilot_plot_ha = round(float(farm["land_area_ha"]) * pilot_pct, 4)
        candidates.append(candidate)

    # Deterministic ordering. Ties are broken by crop id rather than by
    # dictionary insertion order, so the same inputs always produce the same
    # ranking - important when the ranking is written to the database and later
    # evaluated against an outcome.
    candidates.sort(key=lambda c: (-c.final_score, c.crop_id))
    limit = max_results or int(settings.get("recommendation.max_crops_per_recommendation", 3))
    candidates = candidates[:limit]

    # With factors missing, several crops can legitimately score the same. Say so
    # rather than presenting an arbitrary order as a meaningful ranking.
    ranking_notes: list[str] = []
    if len(candidates) >= 2:
        tied = [
            candidate
            for candidate in candidates
            if candidates[0].final_score - candidate.final_score < TIE_EPSILON
        ]
        if len(tied) >= 2:
            ranking_notes.append(t(lang, "rec.tied_options", count=len(tied)))

    # --- Assemble the response -------------------------------------------
    model_versions = {
        "soil_model_version": str(settings.get("ml.soil_model_version", "unknown")),
        "price_model_version": str(settings.get("ml.price_model_version", "unknown")),
        "suitability_model_version": str(settings.get("ml.suitability_model_version", "unknown")),
        "community_model_version": str(settings.get("ml.community_model_version", "unknown")),
    }

    unavailable = _unavailable_inputs(
        soil_row, forecast_result, all_mids, community_signal, history
    )

    min_confidence = float(settings.get("recommendation.min_confidence_for_recommendation", 0.45))
    is_provisional = bool(candidates) and candidates[0].confidence < min_confidence

    options: list[CropOptionOut] = []
    for rank, candidate in enumerate(candidates, start=1):
        crop = load_crops()[candidate.crop_id]
        options.append(
            CropOptionOut(
                crop={"code": candidate.crop_id, "label": crop_name(candidate.crop_id, lang)},
                rank=rank,
                final_score=candidate.final_score,
                confidence=candidate.confidence,
                confidence_band=confidence_band(candidate.confidence),
                suitability=candidate.suit.agronomic_mean if candidate.suit else None,
                price_estimate=candidate.estimate.as_dict() if candidate.estimate else None,
                expected_return_note=t(lang, "rec.return_unavailable"),
                risk=_risk_out(lang, candidate.risk),
                reasons=candidate.reasons,
                scores=[
                    ScoreBreakdownOut(
                        factor=key,
                        score=candidate.factors[key].score,
                        configured_weight=candidate.factors[key].configured_weight,
                        effective_weight=candidate.factors[key].effective_weight,
                        available=candidate.factors[key].score is not None,
                    )
                    for key in FACTOR_KEYS
                ],
                pilot_plot_ha=candidate.pilot_plot_ha,
            )
        )

    stale_inputs = _stale_inputs(lang, forecast_result, market_results)

    disclaimers = [
        t(lang, "limits.guarantee"),
        t(lang, "limits.decision_support"),
        t(lang, "limits.no_model_accuracy_claim"),
    ]
    if is_provisional:
        disclaimers.insert(0, t(lang, "rec.provisional"))
    if stale_inputs:
        disclaimers.insert(0, t(lang, "rec.stale_disclaimer"))

    response = RecommendationOut(
        farm_id=farm_id,
        season=season,  # type: ignore[arg-type]
        generated_at=datetime.now(timezone.utc),
        options=options,
        unavailable_inputs=unavailable,
        stale_inputs=stale_inputs,
        model_versions=model_versions,
        is_provisional=is_provisional,
        ranking_notes=ranking_notes,
        disclaimers=disclaimers,
    )

    if persist and options:
        # Frozen here, at the moment of persistence, from the very objects that
        # produced the blend above. The community signal in particular was
        # computed BEFORE any of these rows were saved, so it is the state the
        # factors actually scored against - not a post-save state in which this
        # recommendation has already populated app_issued_area.
        input_context = _snapshot_input_context(
            season=season,
            farm=farm,
            soil_row=soil_row,
            forecast_result=forecast_result,
            market_results=market_results,
            community_signal=community_signal,
            candidate_crop_ids=crop_ids,
        )
        # options and candidates are the same length and order (both derive
        # from candidates[:limit]), so zip pairs each option with the candidate
        # that actually produced it - not, as a bare loop variable would, the
        # last candidate scored.
        rec_repo.save_recommendations(
            farm_id=farm_id,
            season=season,
            district=district,
            options=[
                {
                    "crop_id": option.crop.code,
                    "rank": option.rank,
                    "final_score": option.final_score,
                    "confidence": option.confidence,
                    # mode="json" so nested models and dates serialize cleanly.
                    "price_estimate": (
                        option.price_estimate.model_dump(mode="json")
                        if option.price_estimate
                        else None
                    ),
                    "factor_scores": option_candidate.factor_scores,
                    "input_context": input_context,
                    "risk": option.risk.model_dump(mode="json"),
                    "reasons": [reason.model_dump(mode="json") for reason in option.reasons],
                    "pilot_plot_ha": option.pilot_plot_ha,
                }
                for option, option_candidate in zip(options, candidates)
            ],
            unavailable=[item.model_dump() for item in unavailable],
            model_versions=model_versions,
        )
    return response


def _risk_out(lang: str, risk: dict[str, float | None]) -> RiskBreakdownOut:
    levels: dict[str, Any] = {}
    for key in ("weather", "market", "production", "transport", "policy", "oversupply"):
        value = risk.get(key)
        levels[key] = risk_from_score(value) if value is not None else None
    overall = risk.get("overall")
    # Transport and policy are not modelled in this version, so their rows are
    # returned as null. The reason is carried in unavailable_inputs rather than
    # asserted here, to avoid implying they were assessed and found low.
    return RiskBreakdownOut(
        **levels,
        overall=risk_from_score(overall if overall is not None else 0.5),
        notes=[],
    )


def _risk(
    crop: Any,
    suit: suitability.SuitabilityResult,
    estimate: price_service.PriceEstimate | None,
    community_signal: community.CommunitySignal,
    crop_id: str,
    candidate_count: int,
) -> dict[str, float | None]:
    weather = None if suit.weather is None else round(1.0 - suit.weather, 4)
    market = None if estimate is None else round(1.0 - price_service.stability_score(estimate), 4)

    water_deficit = 1.0 - suit.water if suit.water is not None else 0.5
    production = round(
        min(1.0, 0.15 * len(crop.major_risks) + 0.55 * water_deficit), 4
    )

    risk: dict[str, float | None] = {
        "weather": weather,
        "market": market,
        "production": production,
        # Not modelled in the MVP. Reported as absent rather than assumed low.
        "transport": None,
        "policy": None,
        "oversupply": community.oversupply_risk(community_signal, crop_id, candidate_count),
    }
    known = [value for value in risk.values() if value is not None]
    risk["overall"] = round(sum(known) / len(known), 4) if known else None
    return risk


def _latest_soil(farm_id: int) -> dict[str, Any] | None:
    from .soil import latest_estimate_row

    return latest_estimate_row(farm_id)


def _soil_distribution(soil_row: dict[str, Any] | None) -> dict[str, float] | None:
    if not soil_row:
        return None
    import json

    try:
        distribution = json.loads(soil_row["distribution"])
    except (TypeError, ValueError):
        return None
    if not isinstance(distribution, dict) or not any(v > 0 for v in distribution.values()):
        return None
    return {str(key): float(value) for key, value in distribution.items()}


def _forecast_for(farm: dict[str, Any]) -> Any:
    latitude = farm.get("latitude")
    longitude = farm.get("longitude")
    if latitude is None or longitude is None:
        from ..providers.base import DataResult

        return DataResult.unavailable(
            "farm has no coordinates, so no weather could be retrieved"
        )
    return get_forecast(float(latitude), float(longitude), days=7)


def _unavailable_inputs(
    soil_row: dict[str, Any] | None,
    forecast_result: Any,
    all_mids: list[float],
    community_signal: community.CommunitySignal,
    history: dict[str, dict[str, float]],
) -> list[UnavailableOut]:
    items: list[UnavailableOut] = []
    if not soil_row or not _soil_distribution(soil_row):
        items.append(
            UnavailableOut(
                component="soil",
                reason="no soil estimate on file for this farm; submit soil evidence first",
            )
        )
    if not forecast_result.available:
        items.append(
            UnavailableOut(
                component="weather",
                reason=forecast_result.provenance.unavailable_reason or "forecast unavailable",
            )
        )
    if not all_mids:
        items.append(
            UnavailableOut(
                component="market_price",
                reason="no verified mandi price source is configured, so returns could not be estimated",
            )
        )
    if not community_signal.available:
        items.append(
            UnavailableOut(
                component="community_saturation",
                reason=community_signal.reason or "no app-issued allocation history",
            )
        )
    if not history:
        items.append(
            UnavailableOut(
                component="farmer_history",
                reason="no previous seasons recorded for this farm",
            )
        )
    items.append(
        UnavailableOut(
            component="transport_and_policy",
            reason="transport logistics and news/policy monitoring are not implemented in this version",
        )
    )
    return items
