"""The farmer-facing frontend (/app).

The values-only client: labels instead of codes, dropdowns filled from
/enums and /crops, unavailable sources as explicit notices. These tests pin
the served page and the API contracts it depends on; the click-through is
verified by the Playwright script in var/ (dev-only, git-ignored).
"""

from __future__ import annotations

from starlette.testclient import TestClient


def test_app_page_is_served(client: TestClient):
    response = client.get("/app/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "SAMPADA AI" in response.text


def test_root_advertises_the_app(client: TestClient):
    body = client.get("/").json()
    assert body["app"] == "/app"


def test_app_contract_enums_and_crops_have_what_the_ui_needs(client: TestClient):
    """The UI fills its pickers from these two endpoints; the groups and fields
    it reads must exist, or the frontend renders raw codes - which is exactly
    what it exists to avoid."""
    enums = client.get("/api/v1/enums?lang=en").json()["enums"]
    for group in (
        "land_unit",
        "irrigation_type",
        "water_source",
        "soil_type",
        "soil_rain_behaviour",
        "season",
        "feedback_reason",
    ):
        assert group in enums and enums[group], group
    crops = client.get("/api/v1/crops?lang=en").json()["crops"]
    assert crops and all(c["name"]["label"] for c in crops)
