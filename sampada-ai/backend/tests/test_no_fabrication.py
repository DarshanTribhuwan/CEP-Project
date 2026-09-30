"""Anti-fabrication tests.

The single most important property of this system, and the one most likely to
rot as features get added:

    missing data is reported as unavailable, never replaced by something
    plausible.

Each test here fails loudly if a later change quietly introduces a default that
looks like real data.
"""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.providers import get_market_provider, lookup_soil_type
from tests.conftest import API, FakeMarketProvider


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
def test_market_provider_reports_unavailable_without_a_configured_source():
    result = get_market_provider().price_history("soybean", "Pune")
    assert result.available is False
    assert "resource id" in (result.provenance.unavailable_reason or "")


def test_soil_geo_reports_unavailable_when_not_configured():
    result = lookup_soil_type(18.52, 73.85)
    assert result.available is False
    assert result.provenance.unavailable_reason


def test_sample_mode_data_is_always_flagged(monkeypatch):
    """Demo data may only ever enter through sample mode, and must be labelled."""
    monkeypatch.setattr(
        settings, "_data", {**settings._data, "data": {"mode": "sample"}}
    )
    assert settings.sample_mode is True
    result = get_market_provider().price_history("soybean", "Pune")
    assert result.available is True
    assert result.provenance.is_demo is True
    assert str(result.provenance.data_source).endswith("SAMPLE")
    assert any("DEMO DATA" in note for note in result.provenance.notes)


# ---------------------------------------------------------------------------
# API surfaces
# ---------------------------------------------------------------------------
def test_market_endpoint_returns_503_not_fake_prices(farmer_client, monkeypatch):
    """With no usable source the endpoint must fail loudly, not invent a price."""
    monkeypatch.setattr(
        "app.api.market.get_market_provider",
        lambda: FakeMarketProvider(unavailable_reason="no Agmarknet resource id configured"),
    )
    response = farmer_client.get(f"{API}/market/prices?crop_id=soybean&district=Pune")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "data_unavailable"
    assert "resource id" in body["error"]["message"]
    # No price fields anywhere in an unavailable response.
    assert "records" not in json.dumps(body)


def test_weather_endpoint_admits_using_the_configured_default(farmer_client, offline_providers):
    """The bare weather endpoint may fall back, but must say that it did.

    Providers are faked so this never depends on the network.
    """
    offline_providers()
    response = farmer_client.get(f"{API}/weather?days=3")
    assert response.status_code == 200
    notes = response.json()["provenance"]["notes"]
    assert any("default location" in note for note in notes)


def test_farm_without_coordinates_gets_no_invented_weather(farmer_client):
    farm = farmer_client.client.post(
        f"{API}/farms",
        headers=farmer_client.headers,
        json={"land_area": 2, "land_unit": "ACRE", "irrigation_type": "RAINFED"},
    )
    farm_id = farm.json()["id"]
    response = farmer_client.get(f"{API}/weather?farm_id={farm_id}")
    assert response.status_code == 503
    assert "no coordinates" in response.json()["error"]["message"]


def test_provider_status_reports_the_truth_and_leaks_no_secret(client):
    body = client.get(f"{API}/providers/status").json()
    providers = body["providers"]

    assert providers["market"]["status"] == "unavailable"
    assert providers["market"]["api_key_present"] is False
    assert providers["soil_image"]["status"] == "not_implemented"
    assert providers["news_and_policy"]["status"] == "not_implemented"
    assert body["demo_data_active"] is False

    # The key env var NAME may be reported; no secret value may appear anywhere.
    serialized = json.dumps(body)
    assert "api_key" in serialized
    assert "api-key=" not in serialized


def test_recommendation_omits_prices_rather_than_inventing_them(
    farmer_client, monkeypatch
):
    monkeypatch.setattr(
        "app.services.recommendation.get_market_provider",
        lambda: FakeMarketProvider(unavailable_reason="source not configured"),
    )
    response = farmer_client.post(
        f"{API}/recommendations", json={"farm_id": farmer_client.farm_id}
    )
    assert response.status_code == 200
    body = response.json()

    for option in body["options"]:
        assert option["price_estimate"] is None
        assert option["expected_return_note"]
        assert any(reason["code"] == "price_unavailable" for reason in option["reasons"])

    components = {item["component"] for item in body["unavailable_inputs"]}
    assert "market_price" in components

    # And the market factor must be reported as unavailable, not scored 0.
    market_factors = [
        score for score in body["options"][0]["scores"] if score["factor"] == "market_price"
    ]
    assert market_factors and market_factors[0]["available"] is False
    assert market_factors[0]["score"] is None


def test_recommendation_never_promises_a_guarantee(farmer_client):
    body = farmer_client.post(
        f"{API}/recommendations?lang=en", json={"farm_id": farmer_client.farm_id}
    ).json()
    joined = " ".join(body["disclaimers"]).lower()
    assert "estimate" in joined and "guarantee" in joined
    assert any("no accuracy figure" in item.lower() for item in body["disclaimers"])
    for option in body["options"]:
        assert option["confidence"] <= 1.0
        assert option["expected_return_note"]


def test_confidence_drops_when_soil_evidence_is_missing(client, offline_providers):
    """A recommendation made without soil evidence must be less confident."""
    with_soil = client.post(
        f"{API}/auth/register",
        json={"name": "A", "mobile": "9876500001", "language": "en", "pin": "1234"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {with_soil}"}

    farm = client.post(
        f"{API}/farms",
        headers=headers,
        json={
            "land_area": 5,
            "land_unit": "ACRE",
            "irrigation_type": "PARTIAL",
            "district": "Pune",
            "latitude": 18.15,
            "longitude": 74.58,
        },
    ).json()
    farm_id = farm["id"]

    without = client.post(
        f"{API}/recommendations", headers=headers, json={"farm_id": farm_id}
    ).json()

    client.post(
        f"{API}/farms/{farm_id}/soil/estimate",
        headers=headers,
        json={"knows_soil_type": True, "soil_type": "BLACK"},
    )
    with_evidence = client.post(
        f"{API}/recommendations", headers=headers, json={"farm_id": farm_id}
    ).json()

    assert without["options"][0]["confidence"] < with_evidence["options"][0]["confidence"]
    components = {item["component"] for item in without["unavailable_inputs"]}
    assert "soil" in components
    assert any(
        reason["code"] == "soil_unknown" for reason in without["options"][0]["reasons"]
    )


@pytest.mark.parametrize("lang", ["en", "mr", "hi"])
def test_reference_tables_expose_exactly_how_verified_they_are(client, lang):
    """Neither table may claim more verification than it has.

    Both are PARTIALLY_VERIFIED - the decision-driving fields are cited from
    EcoCrop/FAO-56, while a few remain curated judgement - and both have to say
    so per field, plus name the sources they were built from.
    """
    crops = client.get(f"{API}/crops?lang={lang}").json()
    assert crops["provenance"]["verification"] == "PARTIALLY_VERIFIED"
    assert crops["provenance"]["sources"]["ecocrop"]["url"].startswith("https://")
    assert crops["crops"], "the crop table must not be empty"
    for crop in crops["crops"]:
        # Per-field provenance is the whole point: a bare number is not exposed.
        assert crop["provenance"], f"{crop['id']} is missing per-field provenance"

    soils = client.get(f"{API}/soil-types?lang={lang}").json()
    assert soils["provenance"]["verification"] == "PARTIALLY_VERIFIED"
    assert soils["provenance"]["provenance"]["texture_class"]["status"]
