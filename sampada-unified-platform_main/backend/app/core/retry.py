"""Retry with bounded exponential backoff.

Deliberately dependency-free: no ``httpx``, no ``urllib``, no logging setup. The
caller supplies the work function and, when it knows better than this module,
a predicate saying which failures are worth retrying. That keeps the policy
testable without a network and keeps the transport-specific knowledge in the
provider layer where it belongs.

Why retry at all
----------------
Every external source this project uses is either a free public API (Open-Meteo,
data.gov.in) or a research database (FAO EcoCrop). Those services intermittently
return 502s, reset connections during busy periods, and time out under load. A
single attempt turns a two-second blip into "data unavailable" on the farmer's
screen. Retrying a small number of times with increasing delay removes most of
that noise.

What this module must never do
------------------------------
Retrying must not turn a *permanent* failure into a hang, and it must never be
used to paper over a source that is simply not configured. A 404 or a rejected
API key is final: retrying it just delays an honest "unavailable" by seconds.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# Provider URLs carry credentials (the data.gov.in feed takes ``?api-key=``),
# and httpx exception texts embed the full request URL. Any string derived
# from a transport exception therefore passes through :func:`redact_secrets`
# before it can reach a log, a provenance note or an API response.
_URL_WITH_QUERY_RE = re.compile(r"(https?://[^'\"\s<>]+?)\?[^'\"\s<>]*")


def redact_secrets(text: str) -> str:
    """Strip query strings from any URL embedded in an exception message.

    ``Server error '502 Bad Gateway' for url 'https://host/path?api-key=X'``
    becomes ``... for url 'https://host/path?<redacted>'``. The host and path
    stay - they are exactly what a debuggable failure message needs - while
    everything after ``?`` (credentials among it) is dropped.
    """
    if not text:
        return text
    return _URL_WITH_QUERY_RE.sub(r"\1?<redacted>", text)

# Attempt counts and delays are UNVALIDATED DEFAULTS chosen for politeness to
# free public APIs: three attempts spread over roughly 0.6s + 1.5s of waiting.
# They are configurable (providers.http.retries / backoff_seconds) so they can be
# tuned without a code change.
DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 0.6
DEFAULT_MAX_BACKOFF_SECONDS = 6.0
DEFAULT_JITTER = 0.25


@dataclass(slots=True)
class Attempt:
    """One try at the work function."""

    number: int
    error: str | None = None
    status_code: int | None = None
    delay_before_seconds: float = 0.0

    @property
    def failed(self) -> bool:
        return self.error is not None


@dataclass(slots=True)
class RetryOutcome:
    """Result of running the work function under the retry policy."""

    value: Any = None
    error: BaseException | None = None
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def retried(self) -> bool:
        return self.attempt_count > 1

    @property
    def total_delay_seconds(self) -> float:
        return round(sum(item.delay_before_seconds for item in self.attempts), 3)

    @property
    def failure_summary(self) -> str:
        """A short human-readable trace, e.g. ``502 Service Unavailable -> timeout``.

        Re-redacted here as defence in depth: the individual attempt errors
        were redacted when recorded, but this property is the string that
        actually travels into provenance reasons, so it re-applies the rule.
        """
        parts = [redact_secrets(item.error) if item.error else "unknown" for item in self.attempts if item.failed]
        return " -> ".join(parts) if parts else "no failure"


def transient_exception_names() -> frozenset[str]:
    """Exception class names that are always worth another try.

    Matched by name so this module needs no transport import. These are the
    connection-level failures shared by ``httpx`` and ``urllib``: the request
    never produced a usable response, which is the definition of transient here.
    """
    return frozenset(
        {
            # httpx
            "ConnectError",
            "ConnectTimeout",
            "ReadTimeout",
            "WriteTimeout",
            "PoolTimeout",
            "ReadError",
            "WriteError",
            "RemoteProtocolError",
            "NetworkError",
            "TimeoutException",
            # urllib
            "URLError",
            "HTTPError",
            "timeout",
            "TimeoutError",
            "ConnectionError",
            "ConnectionResetError",
            "ConnectionAbortedError",
            "IncompleteRead",
        }
    )


def is_transient(exc: BaseException) -> bool:
    """Default predicate: retry connection-level failures and 5xx/429 responses.

    Anything else - a 404, a 401 from a bad API key, a JSON parse error - is
    treated as permanent. Retrying those only delays the honest answer.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status == 408 or status == 429 or status >= 500
    return type(exc).__name__ in transient_exception_names()


def backoff_delay(
    attempt: int,
    *,
    base: float = DEFAULT_BACKOFF_SECONDS,
    maximum: float = DEFAULT_MAX_BACKOFF_SECONDS,
    jitter: float = DEFAULT_JITTER,
    rng: random.Random | None = None,
) -> float:
    """Delay before ``attempt`` (1-indexed): ``base * 2^(n-1)``, capped, jittered.

    Jitter is applied downward by up to ``jitter`` fraction. Spreading retries
    out matters when several candidates are scored in one request and all of
    them hit the same unreachable host.
    """
    exponential = base * (2 ** max(0, attempt - 1))
    capped = min(exponential, maximum)
    draw = (rng or random).random() * jitter
    return round(max(0.0, capped * (1.0 - draw)), 4)


def retry_call(
    work: Callable[[], T],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BACKOFF_SECONDS,
    max_delay: float = DEFAULT_MAX_BACKOFF_SECONDS,
    jitter: float = DEFAULT_JITTER,
    should_retry: Callable[[BaseException], bool] = is_transient,
    sleep: Callable[[float], None] = time.sleep,
    on_attempt: Callable[[Attempt], None] | None = None,
) -> RetryOutcome:
    """Run ``work`` until it succeeds, the budget is spent, or failure is final.

    ``sleep`` is injectable so tests exercise the backoff schedule instantly
    instead of actually waiting.
    """
    total = max(1, int(attempts))
    history: list[Attempt] = []

    for number in range(1, total + 1):
        delay = 0.0 if number == 1 else backoff_delay(
            number - 1, base=base_delay, maximum=max_delay, jitter=jitter
        )
        if delay:
            sleep(delay)

        try:
            value = work()
        except BaseException as exc:  # noqa: BLE001 - re-raised or reported below
            status = getattr(exc, "status_code", None)
            if status is None:
                status = getattr(getattr(exc, "response", None), "status_code", None)
            record = Attempt(
                number=number,
                # Redacted at the record point: exception texts embed full
                # request URLs, and provider URLs carry credentials in their
                # query strings. Nothing downstream should ever see them.
                error=(redact_secrets(f"{type(exc).__name__}: {exc}") if str(exc) else type(exc).__name__),
                status_code=status if isinstance(status, int) else None,
                delay_before_seconds=delay,
            )
            history.append(record)
            if on_attempt is not None:
                on_attempt(record)

            last = number == total
            if last or not should_retry(exc):
                return RetryOutcome(error=exc, attempts=history)
            continue

        history.append(Attempt(number=number, delay_before_seconds=delay))
        if on_attempt is not None:
            on_attempt(history[-1])
        return RetryOutcome(value=value, attempts=history)

    # Unreachable: the loop always returns.
    return RetryOutcome(error=RuntimeError("retry loop exhausted"), attempts=history)
