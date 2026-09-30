"""Soil evidence fusion.

The design sessions rejected single-source soil identification: a photo alone is
unreliable (wet vs dry, lighting, camera differences, surface vs root zone), and
lab tests are the thing farmers actually cannot afford. The agreed approach was
"think of each source as casting a weighted vote", with the system returning
*probabilities and a confidence score* rather than a confident single answer,
and advising a lab test when the evidence is weak.

This module implements exactly that. The maths is stated here so a reviewer can
attack it:

    mass[t]      = Σ_s  w_s · p_s[t]  /  Σ_s w_s        (sources that supplied evidence)
    coverage     = min(1, Σ_s w_s)                      (how much reliability we hold)
    confidence   = clamp(max(mass) · coverage, 0, CEILING)
    disagreement → confidence × DISAGREEMENT_PENALTY, with a note attached

Consequences worth stating:
  * A lab report alone yields confidence 1.0 → clamped to CEILING. Nothing is
    certain, and a farmer may misread a card.
  * A farmer who simply says "black soil" alone yields ~0.55, which is BELOW the
    configured low-confidence threshold, so the system asks for a test instead
    of pretending to know.
  * A photo model is listed as unavailable (not silently omitted) because no
    trained model exists in this repository.

Every constant here is an unvalidated default from config.toml.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..core.config import settings
from ..providers import lookup_soil_type
from ..schemas.soil import SoilEvidenceItem, SoilEvidenceRequest, SoilEstimateOut
from .crops import (
    load_soil_types,
    soil_name,
    soil_provenance,
    water_behaviour_distribution,
)

CEILING = 0.95
DISAGREEMENT_PENALTY = 0.85


@dataclass(slots=True)
class Evidence:
    """One source's vote."""

    source: str
    weight: float
    distribution: dict[str, float] = field(default_factory=dict)
    value: str | None = None
    note: str | None = None
    provided: bool = False


def _weights() -> dict[str, float]:
    return settings.section("soil.evidence_weights")


def collect_evidence(
    request: SoilEvidenceRequest, latitude: float | None, longitude: float | None
) -> list[Evidence]:
    """Gather every available source of soil evidence."""
    weights = _weights()
    evidence: list[Evidence] = []

    # 1. Lab report: the only ground truth available.
    lab = request.lab_report_soil_type
    evidence.append(
        Evidence(
            source="lab_report",
            weight=float(weights.get("lab_report", 1.0)),
            distribution={lab: 1.0} if lab else {},
            value=lab,
            provided=lab is not None,
            note=None if lab else "no Soil Health Card / lab value supplied",
        )
    )

    # 2. The farmer's own answer when they know the type.
    known = request.soil_type if request.knows_soil_type else None
    evidence.append(
        Evidence(
            source="farmer_known_type",
            weight=float(weights.get("farmer_known_type", 0.55)),
            distribution={known: 1.0} if known else {},
            value=known,
            provided=known is not None,
            note=None if known else "farmer did not state a soil type",
        )
    )

    # 3. Observable behaviour - usable even when the farmer does not know the
    #    classification, which is the common real case.
    behaviour = request.after_rain_behaviour
    behaviour_dist = (
        water_behaviour_distribution(behaviour)
        if behaviour and behaviour != "DONT_KNOW"
        else {}
    )
    evidence.append(
        Evidence(
            source="farmer_observations",
            weight=float(weights.get("farmer_observations", 0.30)),
            distribution=behaviour_dist,
            value=behaviour,
            provided=bool(behaviour_dist),
            note=None if behaviour_dist else "no usable rain-behaviour answer",
        )
    )

    # 4. Geographic dataset - optional, operator-supplied.
    geo_weight = float(weights.get("geo_dataset", 0.60))
    if latitude is not None and longitude is not None:
        geo = lookup_soil_type(latitude, longitude)
        if geo.available and geo.value:
            evidence.append(
                Evidence(
                    source="geo_dataset",
                    weight=geo_weight,
                    distribution={geo.value: 1.0},
                    value=geo.value,
                    provided=True,
                    note=f"source: {geo.provenance.source_name}",
                )
            )
        else:
            evidence.append(
                Evidence(
                    source="geo_dataset",
                    weight=geo_weight,
                    note=geo.provenance.unavailable_reason,
                    provided=False,
                )
            )
    else:
        evidence.append(
            Evidence(
                source="geo_dataset",
                weight=geo_weight,
                note="farm has no coordinates, so no geographic lookup was possible",
                provided=False,
            )
        )

    # 5. Image model - not implemented. Reported, never faked.
    image_enabled = bool(settings.get("providers.soil_image.enabled", False))
    evidence.append(
        Evidence(
            source="image_model",
            weight=float(weights.get("image_model", 0.40)),
            note=(
                "enabled in config but no trained model is present in this repository"
                if image_enabled
                else "photo soil identification is not implemented in this version"
            ),
            provided=False,
        )
    )

    return evidence


def fuse(evidence: list[Evidence]) -> tuple[dict[str, float], float, list[str]]:
    """Weighted vote. Returns (distribution, confidence, notes)."""
    supplied = [item for item in evidence if item.provided and item.distribution]
    if not supplied:
        return {}, 0.0, ["no soil evidence was available at all"]

    total_weight = sum(item.weight for item in supplied)
    if total_weight <= 0:
        return {}, 0.0, ["evidence weights are zero, so no fusion was possible"]

    mass: dict[str, float] = {}
    for item in supplied:
        share = item.weight / total_weight
        for soil_id, probability in item.distribution.items():
            mass[soil_id] = mass.get(soil_id, 0.0) + share * probability

    mass = {soil_id: value / sum(mass.values()) for soil_id, value in mass.items()}

    notes: list[str] = []
    top_id = max(mass, key=lambda key: mass[key])
    top_mass = mass[top_id]

    # Do the strongest sources agree on the answer?
    ranked = sorted(supplied, key=lambda item: item.weight, reverse=True)
    leaders = {max(item.distribution, key=lambda key: item.distribution[key]) for item in ranked}
    if len(leaders) > 1:
        notes.append(
            "sources disagree on the soil type ("
            + ", ".join(sorted(leaders))
            + "), so confidence was reduced"
        )
        confidence = top_mass * min(1.0, total_weight) * DISAGREEMENT_PENALTY
    else:
        confidence = top_mass * min(1.0, total_weight)

    confidence = max(0.0, min(CEILING, confidence))
    if total_weight < 1.0:
        notes.append(
            f"total evidence reliability is {total_weight:.2f} of a possible 2.85, "
            "so this is a weak estimate"
        )
    return mass, round(confidence, 4), notes


def estimate_soil(
    request: SoilEvidenceRequest,
    farm_id: int,
    latitude: float | None,
    longitude: float | None,
    lang: str,
) -> SoilEstimateOut:
    """Full pipeline: collect -> fuse -> explain."""
    evidence = collect_evidence(request, latitude, longitude)
    distribution, confidence, notes = fuse(evidence)

    threshold = float(settings.get("soil.low_confidence_threshold", 0.60))
    needs_lab_test = confidence < threshold

    if not distribution:
        # Nothing to identify. Return an explicit unknown - NOT a default soil
        # type, which would silently become a fabricated input to every
        # downstream crop decision.
        top: str | None = None
        distribution_out = {soil_id: 0.0 for soil_id in load_soil_types()}
        advice = _advice(lang, needs_lab_test=True, no_evidence=True)
    else:
        top = max(distribution, key=lambda key: distribution[key])
        distribution_out = {soil_id: round(value, 4) for soil_id, value in distribution.items()}
        advice = _advice(lang, needs_lab_test=needs_lab_test, no_evidence=False)

    total_weight = sum(item.weight for item in evidence if item.provided)
    items: list[SoilEvidenceItem] = []
    for item in evidence:
        contribution = 0.0
        if item.provided and item.distribution and total_weight > 0:
            peak = max(item.distribution.values())
            contribution = round((item.weight / total_weight) * peak, 4)
        items.append(
            SoilEvidenceItem(
                source=item.source,
                provided=item.provided,
                weight=item.weight,
                contribution=contribution,
                value=item.value,
                note=item.note,
            )
        )

    from ..core.errors import DataProvenance, Provenance

    provenance = DataProvenance(
        data_source=Provenance.DERIVED if distribution else Provenance.UNAVAILABLE,
        is_demo=False,
        source_name="soil evidence fusion",
        model_version=str(settings.get("ml.soil_model_version", "unknown")),
        unavailable_reason=None if distribution else "no soil evidence supplied",
        notes=notes + [str(soil_provenance().get("warning", ""))],
    )

    return SoilEstimateOut(
        farm_id=farm_id,
        top_soil_type={"code": top, "label": soil_name(top, lang)} if top else None,
        distribution=distribution_out,
        confidence=confidence,
        confidence_band=_band(confidence),
        evidence=items,
        needs_lab_test=needs_lab_test,
        advice=advice,
        model_version=str(settings.get("ml.soil_model_version", "unknown")),
        provenance=provenance,
        created_at=datetime.now(timezone.utc),
    )


def _band(confidence: float) -> str:
    if confidence >= 0.75:
        return "HIGH"
    if confidence >= 0.55:
        return "MEDIUM"
    return "LOW"


def _advice(lang: str, *, needs_lab_test: bool, no_evidence: bool) -> str:
    from ..core.i18n import t

    if no_evidence:
        return t(lang, "soil.needs_lab_test")
    if needs_lab_test:
        return t(lang, "soil.needs_lab_test")
    return t(lang, "soil.estimate_is_estimate")


def store_estimate(farm_id: int, estimate: SoilEstimateOut) -> int:
    """Persist an estimate so the recommendation that used it is reproducible."""
    import json

    from ..core.db import get_conn, now_iso

    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO soil_estimates (
                farm_id, top_soil_type, distribution, confidence, evidence,
                model_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                farm_id,
                estimate.top_soil_type.code if estimate.top_soil_type else "UNKNOWN",
                json.dumps(estimate.distribution),
                estimate.confidence,
                json.dumps([item.model_dump() for item in estimate.evidence]),
                estimate.model_version,
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def latest_estimate_row(farm_id: int) -> dict[str, Any] | None:
    from ..core.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM soil_estimates WHERE farm_id = ? ORDER BY id DESC LIMIT 1",
            (farm_id,),
        ).fetchone()
    return dict(row) if row else None
