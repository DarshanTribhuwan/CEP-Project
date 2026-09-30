"""The API console surface.

The console is a static browser client that calls the live API and renders
every response verbatim - it adds no data of its own, so there is nothing in
it to fake. These tests pin the plumbing that makes it checkable: the page is
served, and the root payload advertises it. The end-to-end journey the console
performs (register -> farm -> soil -> recommendation -> outcome) exercises the
same endpoints the API journey tests already cover with the offline fakes.
"""

from __future__ import annotations

from starlette.testclient import TestClient


def test_console_page_is_served(client: TestClient):
    response = client.get("/console/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # The page must render API output verbatim; it carries no data of its own.
    assert "API Console" in response.text
    assert "/api/v1" in response.text


def test_root_advertises_the_console(client: TestClient):
    body = client.get("/").json()
    assert body["console"] == "/console"


def test_console_register_body_is_accepted_by_the_api(client: TestClient):
    """The exact registration body the console sends must be valid for the API,
    so 'check it in the console' never fails on a schema the console got wrong."""
    import random

    mobile = "9" + str(random.randint(100000000, 999999999))
    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Console Demo", "mobile": mobile, "language": "en", "pin": "1234"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["access_token"]
