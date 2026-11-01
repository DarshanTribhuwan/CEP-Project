"""Outbound HTTP with retry, caching and an explicit offline mode.

Every network call in the backend goes through :func:`fetch_json`. Putting the
policy in one place is what makes three otherwise scattered guarantees true:

    retry      transient failures (timeouts, connection resets, 429, 5xx) are
               attempted a bounded number of times with increasing delay;
    cache      a successful response is kept on disk, so a later request - even
               after a restart - does not depend on the source being reachable;
    offline    when the source is unreachable and a cache entry exists, the
               system keeps working on the last real response and says how old
               it is.

The line this module refuses to cross: **a cached response is never dressed up
as a live one.** A stale payload is returned with ``stale=True``, an age in
seconds and a note naming the failure that caused the fallback, so the caller
can tag its provenance and the farmer can be told the data is not current. When
there is no cache entry and no network, the answer is unavailable - never a
default, never last season's number rounded to something plausible.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from ..core import httpcache
from ..core.config import settings
from ..core.retry import RetryOutcome, retry_call

OFFLINE_ENV_VAR = "SAMPADA_OFFLINE"

# Sent as the User-Agent on every outbound request unless the caller supplies
# its own. VERIFIED 2026-09-23: api.data.gov.in (NIC) delays the default
# Python user-agent (httpx/urllib) until the client times out, while a
# non-Python UA receives the same response in well under a second. This is
# default-UA filtering, not rate limiting, so the fix is simply to identify
# the application honestly - which a well-behaved API client should do anyway.
DEFAULT_USER_AGENT = "SAMPADA-AI/1.0"

# Fallback windows, used when a caller does not state its own. These are
# UNVALIDATED DEFAULTS: a 12 hour window suits a rainfall forecast, and is too
# generous for anything price-like, which is why price callers override them.
DEFAULT_FRESH_TTL_SECONDS = 1800.0
DEFAULT_MAX_STALE_SECONDS = 43200.0


@dataclass(slots=True)
class HttpOutcome:
    """The outcome of one logical fetch, whatever combination served it."""

    payload: Any = None
    url: str = ""
    from_cache: bool = False
    stale: bool = False
    age_seconds: int | None = None
    attempts: int = 0
    retried: bool = False
    reason: str | None = None
    served_offline: bool = False
    failure_trace: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.payload is not None

    @property
    def provenance_note(self) -> list[str]:
        """Notes suitable for attaching to a :class:`DataProvenance`."""
        return list(self.notes)


def offline_mode() -> bool:
    """True when no outbound call may be attempted.

    ``SAMPADA_OFFLINE=1`` wins over config, because an operator taking a machine
    offline needs that to be immediate and unambiguous. The variable is read per
    call rather than at import so it can be toggled in tests and in a running
    process.
    """
    raw = os.environ.get(OFFLINE_ENV_VAR, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(settings.get("providers.http.offline", False))


def _retry_settings(attempts: int | None, base_delay: float | None) -> tuple[int, float, float]:
    resolved_attempts = (
        attempts
        if attempts is not None
        else int(settings.get("providers.http.retries", 3))
    )
    resolved_base = (
        base_delay
        if base_delay is not None
        else float(settings.get("providers.http.backoff_seconds", 0.6))
    )
    max_delay = float(settings.get("providers.http.max_backoff_seconds", 6.0))
    return max(1, resolved_attempts), max(0.0, resolved_base), max(0.0, max_delay)


def fetch_json(
    url: str,
    *,
    kind: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    fresh_ttl_seconds: float = DEFAULT_FRESH_TTL_SECONDS,
    max_stale_seconds: float = DEFAULT_MAX_STALE_SECONDS,
    attempts: int | None = None,
    base_delay: float | None = None,
    offline: bool | None = None,
    transport: httpx.BaseTransport | None = None,
) -> HttpOutcome:
    """Fetch a JSON document, retrying, and falling back to the cache offline.

    ``fresh_ttl_seconds``  how long a cached entry counts as current.
    ``max_stale_seconds``  how old a cached entry may be and still be served as
                           a labelled fallback. ``0`` disables stale fallback.
    ``transport``          optional httpx transport. Left unset in production; a
                           deployment can route through a proxy with it, and the
                           test suite uses it to exercise retry behaviour without
                           a socket.
    """
    key = httpcache.make_key(kind, url, params)
    cached = httpcache.read(key)
    is_offline = offline_mode() if offline is None else offline
    effective_timeout = (
        timeout if timeout is not None else float(settings.get("providers.http.timeout_seconds", 8))
    )

    # 1. A current cache entry answers outright. No network call at all.
    if cached is not None and cached.age_seconds <= fresh_ttl_seconds:
        return HttpOutcome(
            payload=cached.payload,
            url=url,
            from_cache=True,
            stale=False,
            age_seconds=cached.age_seconds,
            attempts=0,
            served_offline=is_offline,
            notes=[f"served from the on-disk cache, {cached.human_age} old"],
        )

    # 2. Offline: use the cache if it is still within the staleness window, else
    #    report unavailability. Never reach for the network here.
    if is_offline:
        if cached is not None and max_stale_seconds > 0 and cached.age_seconds <= max_stale_seconds:
            return HttpOutcome(
                payload=cached.payload,
                url=url,
                from_cache=True,
                stale=True,
                age_seconds=cached.age_seconds,
                served_offline=True,
                notes=[
                    f"offline mode is on ({OFFLINE_ENV_VAR} or providers.http.offline); "
                    f"using the last cached response from {cached.human_age} ago"
                ],
            )
        return HttpOutcome(
            url=url,
            served_offline=True,
            reason=(
                "offline mode is on and no cached response is available for this request"
                if cached is None
                else (
                    f"offline mode is on and the cached response is {cached.human_age} old, "
                    f"beyond the {round(max_stale_seconds / 3600)} hour staleness limit"
                )
            ),
            notes=["offline mode: no outbound request was attempted"],
        )

    # 3. Try the network, with retries.
    resolved_attempts, resolved_base, max_delay = _retry_settings(attempts, base_delay)
    outcome = _call_with_retry(
        url,
        params=params,
        headers=headers,
        timeout=effective_timeout,
        attempts=resolved_attempts,
        base_delay=resolved_base,
        max_delay=max_delay,
        transport=transport,
    )

    if outcome.ok:
        httpcache.write(key, outcome.value, url=url, extra={"kind": kind})
        note = f"live fetch, {outcome.attempt_count} attempt(s)"
        if outcome.retried:
            note += f" after {outcome.total_delay_seconds}s of backoff"
        return HttpOutcome(
            payload=outcome.value,
            url=url,
            from_cache=False,
            stale=False,
            attempts=outcome.attempt_count,
            retried=outcome.retried,
            notes=[note],
        )

    # 4. Network failed. Fall back to the last real response, labelled.
    trace = outcome.failure_summary
    if cached is not None and max_stale_seconds > 0 and cached.age_seconds <= max_stale_seconds:
        return HttpOutcome(
            payload=cached.payload,
            url=url,
            from_cache=True,
            stale=True,
            age_seconds=cached.age_seconds,
            attempts=outcome.attempt_count,
            retried=outcome.retried,
            served_offline=False,
            failure_trace=trace,
            reason=f"the source could not be reached ({trace})",
            notes=[
                f"source unreachable after {outcome.attempt_count} attempt(s) ({trace}); "
                f"falling back to the last cached response from {cached.human_age} ago - "
                "this value is NOT current"
            ],
        )

    reason = (
        f"the source could not be reached after {outcome.attempt_count} attempt(s) ({trace})"
        if cached is None
        else (
            f"the source could not be reached ({trace}) and the cached response is "
            f"{cached.human_age} old, beyond the staleness limit"
        )
    )
    return HttpOutcome(
        url=url,
        attempts=outcome.attempt_count,
        retried=outcome.retried,
        failure_trace=trace,
        reason=reason,
    )


def _call_with_retry(
    url: str,
    *,
    params: dict[str, Any] | None,
    headers: dict[str, str] | None,
    timeout: float,
    attempts: int,
    base_delay: float,
    max_delay: float,
    transport: httpx.BaseTransport | None = None,
) -> RetryOutcome:
    """One GET, retried per policy. Returns the parsed JSON body."""

    def attempt() -> Any:
        # Caller headers win; the default UA only fills the gap when none was
        # given. Overridable in config.toml under providers.http.user_agent.
        merged_headers = {"User-Agent": str(settings.get("providers.http.user_agent", DEFAULT_USER_AGENT))}
        if headers:
            merged_headers.update(headers)
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.get(url, params=params, headers=merged_headers)
            response.raise_for_status()
            return response.json()

    return retry_call(
        attempt,
        attempts=attempts,
        base_delay=base_delay,
        max_delay=max_delay,
    )


def describe_offline_state() -> dict[str, Any]:
    """Diagnostics for the provider-status endpoint."""
    return {
        "offline": offline_mode(),
        "offline_env_var": OFFLINE_ENV_VAR,
        "configured_offline": bool(settings.get("providers.http.offline", False)),
        "retries": int(settings.get("providers.http.retries", 3)),
        "backoff_seconds": float(settings.get("providers.http.backoff_seconds", 0.6)),
        "timeout_seconds": float(settings.get("providers.http.timeout_seconds", 8)),
        "cache": httpcache.stats(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
