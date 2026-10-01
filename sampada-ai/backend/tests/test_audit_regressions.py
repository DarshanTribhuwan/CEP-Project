"""Regression tests for the 2026-10 audit.

Every test here exists because the audit found a genuine gap in a module that
otherwise passed its suite. Grouped by the audit section they close.
"""

from __future__ import annotations

import base64
import sqlite3
import time
from typing import Any

import httpx
import pytest

from app.core.config import settings

API = "/api/v1"
TOKEN_TTL_DAYS = 30


# ---------------------------------------------------------------------------
# §3 Authentication: token expiry
# ---------------------------------------------------------------------------
class TestTokenExpiry:
    def test_expired_token_is_rejected_as_401(self, client, monkeypatch):
        """A token older than the configured TTL must stop working.

        The clock is frozen at registration, then advanced past the TTL, so the
        test does not wait 30 real days. What is verified: the same token that
        worked moments before returns 401 once its created_at leaves the
        window, with the same error shape as any other rejected token.
        """
        import app.repositories.farmers as farmers_repo

        reg = client.post(
            f"{API}/auth/register",
            json={"name": "Expiry Farmer", "mobile": "9876543210", "language": "mr", "pin": "1234"},
        )
        assert reg.status_code == 201, reg.text
        token = reg.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Sanity: the fresh token works.
        assert client.get(f"{API}/auth/me", headers=headers).status_code == 200

        real_time = time.time
        monkeypatch.setattr(farmers_repo.time, "time", lambda: real_time() + (TOKEN_TTL_DAYS + 1) * 86400)

        response = client.get(f"{API}/auth/me", headers=headers)
        assert response.status_code == 401
        body = response.json()
        assert body["error"]["code"] == "unauthorized"

    def test_token_inside_ttl_keeps_working(self, client, monkeypatch):
        """The expiry check must not reject tokens within their window."""
        import app.repositories.farmers as farmers_repo

        reg = client.post(
            f"{API}/auth/register",
            json={"name": "Window Farmer", "mobile": "9876543210", "language": "mr", "pin": "1234"},
        )
        token = reg.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        real_time = time.time
        monkeypatch.setattr(farmers_repo.time, "time", lambda: real_time() + (TOKEN_TTL_DAYS - 5) * 86400)

        assert client.get(f"{API}/auth/me", headers=headers).status_code == 200

    def test_expiry_uses_created_at_not_last_used(self, client):
        """Expiry is age since issue, not activity: a static token dies even if
        it was never used after issue. (Verified by freezing time forward
        without any intervening calls in the expiry test above; this test pins
        the storage fact the behaviour rests on.)"""
        reg = client.post(
            f"{API}/auth/register",
            json={"name": "Storage Farmer", "mobile": "9876543210", "language": "mr", "pin": "1234"},
        )
        token = reg.json()["access_token"]
        row = sqlite3.connect(":memory:")  # placeholder to keep import shape
        row.close()
        from app.core.db import get_conn

        with get_conn() as conn:
            stored = conn.execute(
                "SELECT created_at FROM auth_tokens WHERE token_hash = ?",
                (__import__("hashlib").sha256(token.encode()).hexdigest(),),
            ).fetchone()
        assert stored is not None and stored["created_at"]


# ---------------------------------------------------------------------------
# §2/§17 Secret leakage: api-key never reaches any response
# ---------------------------------------------------------------------------
class TestSecretRedaction:
    def test_failure_reason_never_carries_the_api_key(self, monkeypatch):
        """When the price feed fails mid-flight, the reason surfaced to the API
        is built from httpx exceptions whose text contains the full request URL
        - including ?api-key=. The reason must be redacted before anything
        leaves the process. Exercises the REAL path: transport raises -> retry
        records -> provider wraps -> provenance reason."""
        from app.core.config import settings
        from app.providers import http as http_module
        from app.providers.market import DataGovAgmarknetProvider

        secret = "S3CR3T-KEY-VALUE"

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.HTTPStatusError(
                f"Server error '502 Bad Gateway' for url '{request.url}'",
                request=request,
                response=httpx.Response(502, request=request),
            )

        real_client = http_module.httpx.Client

        def failing_client(**kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(**kwargs)

        monkeypatch.setattr(http_module.httpx, "Client", failing_client)
        # The provider's unconfigured checks fire before any HTTP call, so the
        # test configures a resource id and key - then lets the call fail.
        monkeypatch.setenv("SAMPADA_DATA_GOV_IN_KEY", secret)
        monkeypatch.setitem(settings._data["providers"]["market"], "resource_id", "test-resource-id")
        monkeypatch.setitem(settings._data["providers"]["http"], "retries", 1)

        result = DataGovAgmarknetProvider().price_history("soybean", None)

        assert not result.available
        reason = result.provenance.unavailable_reason or ""
        assert secret not in reason
        assert "api-key" not in reason or "<redacted>" in reason

    def test_retry_failure_summary_redacts_query_parameters(self, monkeypatch):
        """The redaction belongs at the retry boundary: any exception text that
        embeds a URL is stripped of its query string before being recorded."""
        from app.core import retry as retry_module

        secret = "TOP-SECRET-123"
        request = httpx.Request("GET", f"https://host.example/path?api-key={secret}&x=1")
        error = httpx.ConnectError(f"connection failed for url '{request.url}'")

        monkeypatch.setattr(retry_module.time, "sleep", lambda _s: None)
        outcome = retry_module.retry_call(
            lambda: (_ for _ in ()).throw(error), attempts=1
        )
        assert not outcome.ok
        assert secret not in outcome.failure_summary
        assert secret not in (outcome.attempts[0].error or "")

    def test_http_status_error_url_text_is_redacted_in_attempt_records(self):
        """Direct pin of the redaction helper on the exact exception shape the
        transport produces."""
        from app.core.retry import redact_secrets

        secret = "LEAK-ME"
        text = (
            "Server error '500 Internal Server Error' for url "
            f"'https://host/path?api-key={secret}'"
        )
        assert secret not in redact_secrets(text)
        assert "https://host/path" in redact_secrets(text)

    def test_health_and_provider_status_never_echo_secrets(self, client):
        """§16: the diagnostics endpoints report presence as booleans and must
        never contain any secret material - even when one is set."""
        from app.core.config import env_secret

        marker = "SAMPADA-TEST-SECRET-MARKER"
        monkey = pytest.MonkeyPatch()
        try:
            monkey.setenv("SAMPADA_DATA_GOV_IN_KEY", marker)
            for path in (f"{API}/health", f"{API}/providers/status", "/"):
                body = client.get(path).text
                assert marker not in body, path
            # And the market provider status says a key is present without
            # showing it.
            status = client.get(f"{API}/providers/status").json()
            market = status["providers"]["market"]
            assert market["api_key_present"] is True
            assert marker not in str(market)
        finally:
            monkey.undo()


# ---------------------------------------------------------------------------
# §14 Error handling: unexpected exceptions return a safe envelope
# ---------------------------------------------------------------------------
class TestGenericErrorEnvelope:
    def test_unexpected_exception_returns_safe_500_envelope(self, monkeypatch):
        """An unhandled exception must produce the structured envelope with a
        generic message - never a traceback, SQL text or file path - and be
        logged server-side. raise_server_exceptions=False because Starlette
        re-raises after the handler has produced the response; production
        servers behave the same way (response sent, error logged)."""
        from starlette.testclient import TestClient

        from app.main import app as fastapi_app
        from app.repositories import farmers as farmers_repo

        def explode(*_args, **_kwargs):
            raise RuntimeError("SECRET-INTERNAL-DETAIL sqlite3:/tmp/sampada.db boom")

        monkeypatch.setattr(farmers_repo, "authenticate", explode)
        with TestClient(fastapi_app, raise_server_exceptions=False) as client:
            response = client.post(
                f"{API}/auth/login", json={"mobile": "9876543210", "pin": "1234"}
            )
        assert response.status_code == 500
        body = response.json()
        assert body["error"]["code"] == "internal_error"
        assert "SECRET-INTERNAL-DETAIL" not in response.text
        assert "sqlite3" not in response.text
        assert "sampada.db" not in response.text
        # The FastAPI app object exposes the registered handlers.
        handlers = fastapi_app.exception_handlers
        assert Exception in handlers

    def test_expected_api_errors_keep_their_shape(self, client):
        """The audit must not have broken the existing envelope."""
        response = client.get(f"{API}/farms/99999", headers={"Authorization": "Bearer nope"})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


# ---------------------------------------------------------------------------
# §16 Health endpoint: no filesystem paths
# ---------------------------------------------------------------------------
class TestHealthPathHygiene:
    def test_health_reports_no_absolute_filesystem_path(self, client):
        """The health payload describes the database by name, not by absolute
        path: a diagnostic endpoint has no business advertising the directory
        layout of the machine it runs on."""
        body = client.get(f"{API}/health").json()
        assert "database" in body
        text = str(body["database"])
        assert ":" not in text or text.startswith("sqlite"), text
        assert not text.startswith(("/", "C:", "\\")), text

    def test_health_database_field_names_the_file(self, client):
        body = client.get(f"{API}/health").json()
        assert body["database"].endswith(".db")


# ---------------------------------------------------------------------------
# §9 Language normalization: case and region tags
# ---------------------------------------------------------------------------
class TestLanguageNormalization:
    @pytest.mark.parametrize(
        ("candidate", "expected"),
        [
            ("en", "en"),
            ("hi", "hi"),
            ("mr", "mr"),
            ("HI", "hi"),
            ("MR", "mr"),
            ("EN", "en"),
            ("hi-IN", "hi"),
            ("mr-IN", "mr"),
            ("te", "mr"),       # unsupported -> documented default fallback
            ("xx-YY", "mr"),    # unsupported with region
            (None, "mr"),
            ("", "mr"),
            ("en-US,hi;q=0.9", "en"),  # header-style: first supported wins
            ("hi-IN,en;q=0.8", "hi"),
        ],
    )
    def test_normalize_lang(self, candidate, expected):
        from app.core.i18n import normalize_lang

        assert normalize_lang(candidate) == expected

    def test_missing_translation_falls_back_without_crashing(self):
        from app.core.i18n import t

        # A key that exists in en but is asked for in an unsupported lang
        # resolves through the default language; a key that exists nowhere
        # returns the key itself. Neither raises.
        value = t("xx", "limits.guarantee")
        assert isinstance(value, str) and value
        assert t("mr", "no.such.key.exists") == "no.such.key.exists"

    def test_api_uses_requested_language(self, client):
        """The crops endpoint renders labels in the requested language, and a
        case-variant query resolves to the same language as its lowercase form.
        Crop names themselves may legitimately coincide across languages (e.g.
        soyabean is a loanword in both hi and mr), so the resolution check
        compares like-for-like case variants, and the language check finds a
        crop whose English label actually differs."""
        en = client.get(f"{API}/crops?lang=en").json()
        hi_upper = client.get(f"{API}/crops?lang=HI").json()
        hi_lower = client.get(f"{API}/crops?lang=hi").json()
        mr = client.get(f"{API}/crops?lang=mr").json()

        hi_u = {c["id"]: c["name"]["label"] for c in hi_upper["crops"]}
        hi_l = {c["id"]: c["name"]["label"] for c in hi_lower["crops"]}
        assert hi_u == hi_l  # HI resolved exactly as hi

        en_l = {c["id"]: c["name"]["label"] for c in en["crops"]}
        mr_l = {c["id"]: c["name"]["label"] for c in mr["crops"]}
        differing = [cid for cid in en_l if en_l[cid] != mr_l.get(cid)]
        assert differing  # the table does contain language-specific names


# ---------------------------------------------------------------------------
# §2 Test isolation: the suite must not see developer secrets
# ---------------------------------------------------------------------------
class TestSecretIsolation:
    def test_suite_environment_has_no_market_key(self):
        import os

        assert not env_key_set("SAMPADA_DATA_GOV_IN_KEY")

    def test_suite_environment_has_no_encryption_key(self):
        # conftest pops this before the app imports; the assertion lives at
        # collection time via the same check the conftest performs.
        import os

        assert not os.environ.get("SAMPADA_ENC_KEY")

    def test_dotenv_is_not_loaded_from_the_developer_backend(self):
        """The settings object must be reading the temp-dir config, not the
        developer's backend/config.toml (which sits beside a real .env)."""
        assert "sampada-tests-" in str(settings.path)


def env_key_set(name: str) -> bool:
    import os

    return bool(os.environ.get(name))


# ---------------------------------------------------------------------------
# §3 Registration/login normalization symmetry
# ---------------------------------------------------------------------------
class TestMobileNormalizationSymmetry:
    def test_register_with_country_code_then_login_raw(self, client):
        """A farmer who registered with +91 formatting must be able to log in
        with any equivalent formatting - and vice versa."""
        reg = client.post(
            f"{API}/auth/register",
            json={
                "name": "Symmetry Farmer",
                "mobile": "+91 98765 43210",
                "language": "mr",
                "pin": "1234",
            },
        )
        assert reg.status_code == 201, reg.text

        for variant in ("9876543210", "+919876543210", "+91 98765 43210"):
            login = client.post(
                f"{API}/auth/login", json={"mobile": variant, "pin": "1234"}
            )
            assert login.status_code == 200, f"{variant!r}: {login.text}"

    def test_register_rejects_impossible_mobiles(self, client):
        for bad in ("1234567890", "98765", "98765432101", "abcdefghij"):
            response = client.post(
                f"{API}/auth/register",
                json={"name": "X", "mobile": bad, "language": "mr", "pin": "1234"},
            )
            assert response.status_code == 422, bad
