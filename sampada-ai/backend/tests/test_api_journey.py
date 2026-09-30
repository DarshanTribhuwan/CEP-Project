"""End-to-end API journey.

Walks the MVP path the design sessions specified: register -> farm -> soil ->
recommendation -> feedback -> outcome. Uses fake providers only, so it neither
depends on nor hits the network.
"""

from __future__ import annotations

import re

from tests.conftest import API


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def test_register_then_login(client):
    payload = {"name": "Ramesh", "mobile": "9876512345", "language": "mr", "pin": "4321"}
    created = client.post(f"{API}/auth/register", json=payload)
    assert created.status_code == 201
    assert created.json()["farmer"]["language"] == "mr"
    assert "development" in created.json()["note"].lower()

    logged_in = client.post(
        f"{API}/auth/login", json={"mobile": payload["mobile"], "pin": payload["pin"]}
    )
    assert logged_in.status_code == 200
    assert logged_in.json()["farmer"]["mobile"] == payload["mobile"]


def test_duplicate_mobile_is_rejected(client):
    payload = {"name": "A", "mobile": "9876511111", "language": "en", "pin": "1234"}
    assert client.post(f"{API}/auth/register", json=payload).status_code == 201
    second = client.post(f"{API}/auth/register", json=payload)
    assert second.status_code == 422
    assert "already exists" in second.json()["error"]["message"]


def test_wrong_pin_and_unknown_number_give_the_same_error(client):
    """Prevents using the login endpoint to enumerate registered numbers."""
    client.post(
        f"{API}/auth/register",
        json={"name": "B", "mobile": "9876522222", "language": "en", "pin": "1234"},
    )
    wrong_pin = client.post(f"{API}/auth/login", json={"mobile": "9876522222", "pin": "9999"})
    unknown = client.post(f"{API}/auth/login", json={"mobile": "9876599999", "pin": "1234"})
    assert wrong_pin.status_code == unknown.status_code == 401
    assert wrong_pin.json() == unknown.json()


def test_protected_routes_require_a_token(client):
    assert client.get(f"{API}/farms").status_code == 401
    assert client.get(f"{API}/farms", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_logout_revokes_the_token(client):
    token = client.post(
        f"{API}/auth/register",
        json={"name": "C", "mobile": "9876533333", "language": "en", "pin": "1234"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get(f"{API}/auth/me", headers=headers).status_code == 200
    assert client.post(f"{API}/auth/logout", headers=headers).status_code == 200
    assert client.get(f"{API}/auth/me", headers=headers).status_code == 401


# ---------------------------------------------------------------------------
# Farm + language independence
# ---------------------------------------------------------------------------
def test_farm_area_is_normalized_to_hectares(farmer_client):
    farm = farmer_client.get(f"{API}/farms/{farmer_client.farm_id}").json()
    assert farm["land_unit"] == "ACRE"
    assert farm["land_area_input"] == 5.0
    assert farm["land_area_ha"] == round(5 * 0.404686, 4)


def test_switching_language_does_not_change_stored_values(client, offline_providers):
    token = client.post(
        f"{API}/auth/register",
        json={"name": "D", "mobile": "9876544444", "language": "mr", "pin": "1234"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    farm = client.post(
        f"{API}/farms",
        headers=headers,
        json={
            "land_area": 3,
            "land_unit": "ACRE",
            "irrigation_type": "PARTIAL",
            "water_source": "BOREWELL",
            "district": "Pune",
            "latitude": 18.15,
            "longitude": 74.58,
        },
    ).json()

    assert client.patch(f"{API}/auth/me/language?language=en", headers=headers).status_code == 200

    # Stored values stay standardized codes regardless of presentation language.
    assert farm["irrigation_type"] == "PARTIAL"
    assert farm["water_source"] == "BOREWELL"
    assert client.get(f"{API}/farms/{farm['id']}", headers=headers).json()[
        "irrigation_type"
    ] == "PARTIAL"

    # Presentation changes.
    mr_questions = client.get(f"{API}/onboarding/questions?lang=mr").json()["questions"]
    en_questions = client.get(f"{API}/onboarding/questions?lang=en").json()["questions"]
    by_key_mr = {item["key"]: item["question"] for item in mr_questions}
    by_key_en = {item["key"]: item["question"] for item in en_questions}
    assert by_key_mr["q.biggest_problem"] != by_key_en["q.biggest_problem"]
    assert re.search(r"[\u0900-\u097F]", by_key_mr["q.biggest_problem"])
    assert not re.search(r"[\u0900-\u097F]", by_key_en["q.biggest_problem"])


def test_default_language_follows_the_farmers_saved_preference(client, offline_providers):
    """A returning farmer should not have to re-select their language."""
    token = client.post(
        f"{API}/auth/register",
        json={"name": "G", "mobile": "9876577777", "language": "hi", "pin": "1234"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # No ?lang and no Accept-Language header: the stored preference wins.
    questions = client.get(f"{API}/onboarding/questions", headers=headers).json()
    assert questions["language"] == "hi"

    # An explicit parameter still overrides it.
    override = client.get(f"{API}/onboarding/questions?lang=en", headers=headers).json()
    assert override["language"] == "en"


def test_onboarding_options_are_localized_enums(client):
    questions = client.get(f"{API}/onboarding/questions?lang=mr").json()["questions"]
    irrigated = next(item for item in questions if item["key"] == "q.irrigated")
    codes = {option["code"] for option in irrigated["options"]}
    assert {"FULL", "PARTIAL", "RAINFED"} <= codes
    assert all(option["label"] != option["code"] for option in irrigated["options"])


# ---------------------------------------------------------------------------
# Ownership isolation
# ---------------------------------------------------------------------------
def test_a_farmer_cannot_read_another_farm(client, offline_providers, farmer_client):
    other_token = client.post(
        f"{API}/auth/register",
        json={"name": "E", "mobile": "9876555555", "language": "en", "pin": "1234"},
    ).json()["access_token"]
    other = {"Authorization": f"Bearer {other_token}"}

    # 404 rather than 403, so the API does not confirm the id exists.
    assert client.get(f"{API}/farms/{farmer_client.farm_id}", headers=other).status_code == 404
    assert client.post(
        f"{API}/recommendations", headers=other, json={"farm_id": farmer_client.farm_id}
    ).status_code == 404


# ---------------------------------------------------------------------------
# Recommendation -> feedback -> outcome
# ---------------------------------------------------------------------------
def test_recommendation_is_ranked_explained_and_persisted(farmer_client):
    response = farmer_client.post(
        f"{API}/recommendations", json={"farm_id": farmer_client.farm_id}
    )
    assert response.status_code == 200
    body = response.json()

    assert body["season"] in {"KHARIF", "RABI", "SUMMER", "PERENNIAL"}
    assert body["options"], "expected at least one option"
    assert len(body["options"]) <= 3

    ranks = [option["rank"] for option in body["options"]]
    scores = [option["final_score"] for option in body["options"]]
    assert ranks == sorted(ranks)
    assert scores == sorted(scores, reverse=True)

    first = body["options"][0]
    assert first["crop"]["code"] and first["crop"]["label"]
    assert 0.0 < first["confidence"] <= 1.0
    assert first["reasons"], "every recommendation must explain itself"
    assert set(first["risk"]) >= {"overall"}
    assert body["model_versions"]["price_model_version"]

    # A brand-new farmer gets a pilot-plot suggestion.
    assert first["pilot_plot_ha"] is not None
    assert first["pilot_plot_ha"] < 2.0234  # strictly smaller than the whole farm

    stored = farmer_client.get(f"{API}/recommendations?farm_id={farmer_client.farm_id}")
    assert stored.status_code == 200
    assert len(stored.json()) == len(body["options"])


def test_effective_weights_are_reported_and_sum_to_one(farmer_client):
    body = farmer_client.post(
        f"{API}/recommendations", json={"farm_id": farmer_client.farm_id}
    ).json()
    scores = body["options"][0]["scores"]
    effective = sum(item["effective_weight"] for item in scores if item["available"])
    assert effective == 1.0 or abs(effective - 1.0) < 0.01
    for item in scores:
        if not item["available"]:
            assert item["effective_weight"] == 0.0


def test_recommendation_persists_model_versions_and_price_forecast(farmer_client):
    body = farmer_client.post(
        f"{API}/recommendations", json={"farm_id": farmer_client.farm_id}
    ).json()
    stored = farmer_client.get(
        f"{API}/recommendations?farm_id={farmer_client.farm_id}"
    ).json()
    # The list is newest-first, so pick the top-ranked row of the batch.
    top = next(record for record in stored if record["rank"] == 1)
    record = farmer_client.get(f"{API}/recommendations/{top['id']}").json()
    assert record["model_versions"]
    assert record["status"] == "proposed"
    assert record["crop_id"] == body["options"][0]["crop"]["code"]
    assert record["final_score"] == body["options"][0]["final_score"]


def test_feedback_updates_status_and_reason_is_stored(farmer_client):
    farmer_client.post(f"{API}/recommendations", json={"farm_id": farmer_client.farm_id})
    recommendation_id = farmer_client.get(
        f"{API}/recommendations?farm_id={farmer_client.farm_id}"
    ).json()[0]["id"]

    response = farmer_client.post(
        f"{API}/recommendations/{recommendation_id}/feedback",
        json={"followed": "NO", "reason": "COST", "comment": "seed was too expensive"},
    )
    assert response.status_code == 201
    assert response.json()["followed"] == "NO"

    updated = farmer_client.get(f"{API}/recommendations/{recommendation_id}").json()
    assert updated["status"] == "rejected"


def test_outcome_records_profit_and_stops_the_pilot_plot(farmer_client):
    farmer_client.post(f"{API}/recommendations", json={"farm_id": farmer_client.farm_id})
    recommendation_id = farmer_client.get(
        f"{API}/recommendations?farm_id={farmer_client.farm_id}"
    ).json()[0]["id"]

    outcome = farmer_client.post(
        f"{API}/outcomes",
        json={
            "farm_id": farmer_client.farm_id,
            "recommendation_id": recommendation_id,
            "crop_id": "soybean",
            "season": "KHARIF",
            "year": 2026,
            "area_ha": 0.2,
            "yield_quintal": 20,
            "cost_inr": 30000,
            "sell_price_inr_per_quintal": 5000,
            "transport_cost_inr": 2000,
            "market": "Baramati",
        },
    )
    assert outcome.status_code == 201
    # 20 * 5000 - 30000 - 2000
    assert outcome.json()["profit_inr"] == 68000.0

    # With one recorded season, the farmer is no longer brand new: the
    # pilot-plot suggestion and the history factor both change.
    again = farmer_client.post(
        f"{API}/recommendations", json={"farm_id": farmer_client.farm_id}
    ).json()
    assert again["options"][0]["pilot_plot_ha"] is None
    components = {item["component"] for item in again["unavailable_inputs"]}
    assert "farmer_history" not in components


def test_tied_options_are_disclosed_rather_than_faked_into_a_ranking(
    client, offline_providers, monkeypatch
):
    """With market data missing, several crops score identically.

    The API must admit the tie instead of presenting dictionary order as a
    meaningful ranking. This is the failure mode most likely to creep back in.
    """
    from tests.conftest import FakeMarketProvider

    monkeypatch.setattr(
        "app.services.recommendation.get_market_provider",
        lambda: FakeMarketProvider(unavailable_reason="no source configured"),
    )

    token = client.post(
        f"{API}/auth/register",
        json={"name": "H", "mobile": "9876588888", "language": "en", "pin": "1234"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
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
    client.post(
        f"{API}/farms/{farm['id']}/soil/estimate",
        headers=headers,
        json={"knows_soil_type": True, "soil_type": "BLACK"},
    )

    body = client.post(
        f"{API}/recommendations?lang=en", headers=headers, json={"farm_id": farm["id"]}
    ).json()

    scores = [option["final_score"] for option in body["options"]]
    if len(scores) >= 2 and (max(scores) - min(scores)) < 0.01:
        assert body["ranking_notes"], "a tie must be disclosed"
        assert "treat them as equally uncertain" in body["ranking_notes"][0]

    # Ranking is deterministic: the same request must give the same crop order.
    repeat = client.post(
        f"{API}/recommendations?lang=en", headers=headers, json={"farm_id": farm["id"]}
    ).json()
    assert [option["crop"]["code"] for option in repeat["options"]] == [
        option["crop"]["code"] for option in body["options"]
    ]


def test_crop_preferences_exclude_crops(farmer_client):
    farmer_client.put(
        f"{API}/farms/{farmer_client.farm_id}/preferences",
        json={"willing_crops": ["soybean", "tur"], "unwilling_crops": [], "resources": ["COW"]},
    )
    body = farmer_client.post(
        f"{API}/recommendations", json={"farm_id": farmer_client.farm_id}
    ).json()
    chosen = {option["crop"]["code"] for option in body["options"]}
    assert chosen <= {"soybean", "tur"}


def test_explicit_exclusions_are_respected(farmer_client):
    body = farmer_client.post(
        f"{API}/recommendations",
        json={"farm_id": farmer_client.farm_id, "exclude_crops": ["soybean", "tur"]},
    ).json()
    chosen = {option["crop"]["code"] for option in body["options"]}
    assert "soybean" not in chosen and "tur" not in chosen


def test_impossible_constraints_fail_loudly(farmer_client):
    response = farmer_client.post(
        f"{API}/recommendations",
        json={"farm_id": farmer_client.farm_id, "include_crops": ["nonexistent"]},
    )
    assert response.status_code == 422
    assert "no crops match" in response.json()["error"]["message"]


def test_community_signal_grows_after_recommendations_are_issued(client, offline_providers):
    """The oversupply signal is fed by this app's own issued recommendations."""
    token = client.post(
        f"{API}/auth/register",
        json={"name": "F", "mobile": "9876566666", "language": "en", "pin": "1234"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    farm_ids = []
    for index in range(3):
        farm = client.post(
            f"{API}/farms",
            headers=headers,
            json={
                "land_area": 10,
                "land_unit": "ACRE",
                "irrigation_type": "PARTIAL",
                "district": "Pune",
                "latitude": 18.15,
                "longitude": 74.58,
            },
        ).json()
        farm_ids.append(farm["id"])
        client.post(f"{API}/recommendations", headers=headers, json={"farm_id": farm["id"]})

    body = client.post(
        f"{API}/recommendations", headers=headers, json={"farm_id": farm_ids[0]}
    ).json()
    components = {item["component"] for item in body["unavailable_inputs"]}
    # With app-issued history present, the community factor is now scored.
    assert "community_saturation" not in components
    community_factors = [
        score for score in body["options"][0]["scores"]
        if score["factor"] == "community_saturation"
    ]
    assert community_factors and community_factors[0]["available"] is True
