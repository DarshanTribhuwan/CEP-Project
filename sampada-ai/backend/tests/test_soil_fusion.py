"""Soil evidence fusion tests.

These pin down the confidence maths, because that number is what decides
whether a farmer is told "we know your soil" or "get a soil test".
"""

from __future__ import annotations

import pytest

from app.schemas.soil import SoilEvidenceRequest
from app.services import soil as soil_service
from app.services.soil import CEILING, Evidence, fuse


def _evidence(source: str, weight: float, soil_id: str) -> Evidence:
    return Evidence(
        source=source, weight=weight, distribution={soil_id: 1.0}, value=soil_id, provided=True
    )


# ---------------------------------------------------------------------------
# Fusion maths
# ---------------------------------------------------------------------------
def test_lab_report_alone_reaches_the_ceiling():
    distribution, confidence, _ = fuse([_evidence("lab_report", 1.0, "BLACK")])
    assert distribution == {"BLACK": 1.0}
    assert confidence == pytest.approx(CEILING)


def test_farmer_statement_alone_yields_its_configured_reliability():
    """A farmer saying "it's black soil" is decent evidence, not proof."""
    _distribution, confidence, _ = fuse([_evidence("farmer_known_type", 0.55, "BLACK")])
    assert confidence == pytest.approx(0.55)


def test_more_agreeing_evidence_raises_confidence():
    known_only, confidence_known, _ = fuse([_evidence("farmer_known_type", 0.55, "BLACK")])
    both, confidence_both, _ = fuse(
        [_evidence("farmer_known_type", 0.55, "BLACK"), _evidence("lab_report", 1.0, "BLACK")]
    )
    assert confidence_both > confidence_known
    assert max(both, key=both.get) == "BLACK"
    assert max(known_only, key=known_only.get) == "BLACK"


def test_disagreeing_sources_lose_confidence_and_ask_for_a_test():
    _distribution, confidence, notes = fuse(
        [_evidence("lab_report", 1.0, "BLACK"), _evidence("farmer_known_type", 0.55, "RED")]
    )
    assert confidence < 0.6
    assert any("disagree" in note for note in notes)


def test_no_evidence_yields_zero_confidence():
    distribution, confidence, notes = fuse([])
    assert distribution == {}
    assert confidence == 0.0
    assert notes


# ---------------------------------------------------------------------------
# End-to-end estimate
# ---------------------------------------------------------------------------
def test_no_evidence_returns_null_soil_type_not_a_guess():
    """The critical anti-fabrication guarantee for this module."""
    estimate = soil_service.estimate_soil(
        SoilEvidenceRequest(), farm_id=1, latitude=18.5, longitude=73.8, lang="en"
    )
    assert estimate.top_soil_type is None
    assert estimate.confidence == 0.0
    assert estimate.needs_lab_test is True
    assert all(value == 0.0 for value in estimate.distribution.values())


def test_distribution_sums_to_one_when_evidence_exists():
    estimate = soil_service.estimate_soil(
        SoilEvidenceRequest(knows_soil_type=True, soil_type="BLACK"),
        farm_id=1,
        latitude=None,
        longitude=None,
        lang="en",
    )
    assert sum(estimate.distribution.values()) == pytest.approx(1.0, abs=1e-6)


def test_every_configured_source_is_reported_including_missing_ones():
    """A farmer must be able to see which evidence was absent."""
    estimate = soil_service.estimate_soil(
        SoilEvidenceRequest(knows_soil_type=True, soil_type="BLACK"),
        farm_id=1,
        latitude=None,
        longitude=None,
        lang="en",
    )
    by_source = {item.source: item for item in estimate.evidence}
    assert set(by_source) == {
        "lab_report",
        "farmer_known_type",
        "farmer_observations",
        "geo_dataset",
        "image_model",
    }
    assert by_source["lab_report"].provided is False
    assert by_source["image_model"].provided is False
    # The image model must explain itself rather than appear silently absent.
    assert "not implemented" in (by_source["image_model"].note or "")


def test_marathi_soil_label_is_localized():
    estimate = soil_service.estimate_soil(
        SoilEvidenceRequest(knows_soil_type=True, soil_type="BLACK"),
        farm_id=1,
        latitude=None,
        longitude=None,
        lang="mr",
    )
    assert estimate.top_soil_type is not None
    assert estimate.top_soil_type.label != "Black soil"


def test_estimate_is_stored_with_its_model_version(client, farmer_client):
    """Stored estimates keep the model version that produced them."""
    farm_id = farmer_client.farm_id
    estimate = soil_service.estimate_soil(
        SoilEvidenceRequest(knows_soil_type=True, soil_type="BLACK"),
        farm_id=farm_id,
        latitude=None,
        longitude=None,
        lang="en",
    )
    soil_service.store_estimate(farm_id, estimate)
    row = soil_service.latest_estimate_row(farm_id)
    assert row is not None
    assert row["top_soil_type"] == "BLACK"
    assert row["model_version"] == estimate.model_version
    assert row["confidence"] == estimate.confidence
