"""Error types and the honest-response envelope.

The single most important rule this module enforces:

    Missing data is reported as *unavailable*, never guessed.

Every data-bearing API response carries a :class:`DataProvenance` block so a
client can tell apart a real measurement, a prediction, an assumption, an
explicitly-marked demo value, and a source we could not reach.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Provenance(str, Enum):
    """Where a value came from. Ordered from strongest to weakest evidence."""

    LIVE = "live"                 # fetched from a real, named external source, just now
    CACHED = "cached"             # a real earlier response, served again while the source is
                                  # unreachable or the value is still within its TTL; is_stale
                                  # says whether it has aged out of "current"
    DERIVED = "derived"           # computed from live data by a versioned model
    FARMER_REPORTED = "farmer_reported"  # the farmer told us; not independently verified
    SAMPLE = "sample"             # demo data, explicitly enabled, never real
    UNAVAILABLE = "unavailable"   # the source could not be reached or is not configured


class DataProvenance(BaseModel):
    """Attached to every response that carries data."""

    data_source: Provenance
    is_demo: bool = Field(
        default=False,
        description="True whenever any part of this payload is sample/demo data.",
    )
    source_name: str | None = Field(default=None, description="Human-readable source, e.g. 'Open-Meteo'.")
    source_url: str | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_stale: bool = Field(
        default=False,
        description=(
            "True when the value came from a cached response that is no longer current. "
            "A client must show this differently from a live value."
        ),
    )
    cached_age_seconds: int | None = Field(
        default=None,
        description="How old the served cache entry is, when data_source == cached.",
    )
    model_version: str | None = None
    unavailable_reason: str | None = Field(
        default=None,
        description="Why the data is unavailable. Present only when data_source == unavailable.",
    )
    notes: list[str] = Field(default_factory=list)


class ApiError(Exception):
    """Base class for errors that map onto an HTTP status."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class DataUnavailable(ApiError):
    """A required external source could not be reached or is not configured.

    Deliberately *not* a 404 and deliberately *not* a silent empty result: the
    client must be able to render "we cannot tell you right now" instead of
    showing the farmer something invented.
    """

    status_code = 503
    code = "data_unavailable"


class NotFound(ApiError):
    status_code = 404
    code = "not_found"


class ValidationFailure(ApiError):
    status_code = 422
    code = "validation_failed"


class AuthFailure(ApiError):
    status_code = 401
    code = "unauthorized"


def unavailable(reason: str, *, source_name: str | None = None) -> DataProvenance:
    """Convenience constructor for an unavailable provenance block."""
    return DataProvenance(
        data_source=Provenance.UNAVAILABLE,
        is_demo=False,
        source_name=source_name,
        unavailable_reason=reason,
    )
