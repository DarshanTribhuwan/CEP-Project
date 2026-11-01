"""Provider layer (Part 2 of the four-part split).

Every external fact the system uses enters here, and every provider returns a
:class:`DataResult` that states where the value came from. Providers must never
raise for a missing source and must never substitute a guess - they return an
unavailable result with a reason the API can show the farmer.

This is the enforcement point for the project rule: *no fabricated data*.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from ..core.errors import DataProvenance, Provenance

T = TypeVar("T")


@dataclass(slots=True)
class DataResult(Generic[T]):
    """A provider response plus its provenance."""

    value: T | None
    provenance: DataProvenance

    @property
    def available(self) -> bool:
        return self.value is not None

    @property
    def is_demo(self) -> bool:
        return self.provenance.is_demo

    @classmethod
    def unavailable(cls, reason: str, *, source_name: str | None = None) -> DataResult[T]:
        return cls(
            value=None,
            provenance=DataProvenance(
                data_source=Provenance.UNAVAILABLE,
                source_name=source_name,
                unavailable_reason=reason,
            ),
        )

    @classmethod
    def live(
        cls,
        value: T,
        *,
        source_name: str,
        source_url: str | None = None,
        notes: list[str] | None = None,
    ) -> DataResult[T]:
        return cls(
            value=value,
            provenance=DataProvenance(
                data_source=Provenance.LIVE,
                source_name=source_name,
                source_url=source_url,
                notes=notes or [],
            ),
        )

    @classmethod
    def derived(
        cls,
        value: T,
        *,
        source_name: str,
        model_version: str,
        notes: list[str] | None = None,
    ) -> DataResult[T]:
        return cls(
            value=value,
            provenance=DataProvenance(
                data_source=Provenance.DERIVED,
                source_name=source_name,
                model_version=model_version,
                notes=notes or [],
            ),
        )

    @classmethod
    def cached(
        cls,
        value: T,
        *,
        source_name: str,
        age_seconds: int | None,
        is_stale: bool,
        source_url: str | None = None,
        notes: list[str] | None = None,
    ) -> DataResult[T]:
        """A real earlier response, served again without a fresh fetch.

        This is *not* a fallback that invents anything: the value is exactly what
        the source returned at ``age_seconds`` ago. ``is_stale`` distinguishes
        "still current, just cached" from "older than its TTL and served because
        the source is unreachable". Callers must surface the difference.
        """
        return cls(
            value=value,
            provenance=DataProvenance(
                data_source=Provenance.CACHED,
                source_name=source_name,
                source_url=source_url,
                is_stale=is_stale,
                cached_age_seconds=age_seconds,
                notes=notes or [],
            ),
        )

    @classmethod
    def sample(
        cls,
        value: T,
        *,
        source_name: str,
        notes: list[str] | None = None,
    ) -> DataResult[T]:
        """Demo data. Always flagged, never silently mixed with real values."""
        return cls(
            value=value,
            provenance=DataProvenance(
                data_source=Provenance.SAMPLE,
                is_demo=True,
                source_name=source_name,
                notes=(notes or []) + ["DEMO DATA - must not be presented as real."],
            ),
        )


class Unavailable(Exception):
    """Raised internally by a provider chain; converted to a DataResult."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
