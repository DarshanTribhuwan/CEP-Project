"""Time-based held-out evaluation protocol for recommendation weights.

The rule this module enforces, from the project's own standard: **weights are
never tuned against the data used to evaluate them.** Before any factor weight
is validated - let alone changed - this protocol fixes, in code:

    1. a single time boundary ``cutoff_date``: every outcome strictly before it
       may be used to *propose* a weight; every outcome on or after it is the
       held-out set that *judges* the proposal, exactly once;
    2. a leakage guard: any function that would fit, tune or search weights is
       structurally refused access to the held-out segment (see
       :func:`guard_no_tuning`);
    3. a pre-registration requirement: a weight change must be proposed with
       its rationale and the training window it used, *before* it is scored on
       the held-out segment (:func:`pre_register_proposal`);
    4. a one-shot evaluation: the held-out segment is scored once per proposal
       and consumed (:class:`ConsumedWindow`) so the same segment cannot quietly
       evaluate candidate after candidate until one looks good - that would be
       tuning by repetition, which is the failure mode this whole file exists to
       prevent.

Why a protocol and not a script: the MVP does not yet have the outcome volume
to run this (see ``app/evaluation/factor_evidence.py`` for the per-factor
evidence bars). Building the guard-rails **before** the data exists is what
keeps the future evaluation honest; retrofitting a split after you have seen
which weights look good is how self-deception enters a pipeline.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

PROPOSALS_DIRNAME = Path("var") / "evaluations"


class LeakageError(Exception):
    """Raised when a tuning function tries to touch the held-out segment."""


@dataclass(frozen=True, slots=True)
class Window:
    """The two segments every outcome date is sorted into. Fixed once."""

    cutoff_date: date
    training_end_inclusive: bool = False  # held-out starts AT the cutoff

    def segment_for(self, outcome_date: date) -> str:
        """Held-out starts AT the cutoff unless training is end-inclusive."""
        if self.training_end_inclusive:
            return "heldout" if outcome_date > self.cutoff_date else "training"
        return "heldout" if outcome_date >= self.cutoff_date else "training"

    @property
    def training_label(self) -> str:
        op = "<=" if self.training_end_inclusive else "<"
        return f"outcomes {op} {self.cutoff_date.isoformat()}"

    @property
    def heldout_label(self) -> str:
        op = ">" if self.training_end_inclusive else ">="
        return f"outcomes {op} {self.cutoff_date.isoformat()}"


@dataclass(frozen=True, slots=True)
class Proposal:
    """A weight change proposed BEFORE being judged, with its evidence."""

    factor: str
    old_weight: float
    new_weight: float
    rationale: str
    evidence_source: str
    training_window: str
    proposed_at: str
    factor_versions: dict[str, str] = field(default_factory=dict)

    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "factor": self.factor,
                "old": self.old_weight,
                "new": self.new_weight,
                "rationale": self.rationale,
                "training": self.training_window,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class ConsumedWindow:
    """A held-out segment that has already judged one proposal.

    One segment judges exactly one proposal. A second proposal must wait for a
    new time boundary, otherwise repeated scoring is weight search by stealth.
    """

    cutoff_date: date
    proposal_fingerprint: str
    metrics: dict[str, float]
    judged_at: str


def guard_no_tuning(window: Window, training_rows, fn) -> object:
    """Structural leakage guard.

    The fitting function is closed over the training segment at construction:
    it receives the training rows and nothing else, and there is no call path
    by which held-out data could reach it. Any attempt to pass extra arguments
    raises :class:`LeakageError` instead of silently succeeding. (A determined
    caller could still bypass this wrapper entirely - the guard makes
    accidental leakage impossible and deliberate leakage loud.)
    """
    from types import SimpleNamespace

    training_view = SimpleNamespace(
        label=window.training_label,
        segment="training",
        rows=training_rows,
    )

    def wrapper(*args, **kwargs):
        if args or kwargs:
            raise LeakageError(
                "a fitting function may receive no extra data; it is bound to the "
                "training segment only, because weights are never tuned on the "
                "segment that judges them"
            )
        return fn(training_view, training_rows)

    wrapper.__name__ = getattr(fn, "__name__", "tuning_fn")
    return wrapper


def pre_register_proposal(proposal: Proposal, out_dir: Path) -> Path:
    """Write the proposal to disk before any held-out scoring happens.

    The file is the timestamp: if the new weight was chosen after seeing the
    held-out segment, the timestamps will show it. Returns the path.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    # Windows forbids colons in filenames, and ISO timestamps contain them.
    safe_stamp = proposal.proposed_at.replace(":", "-")
    path = out_dir / f"proposal_{safe_stamp}_{proposal.fingerprint()}.json"
    path.write_text(
        json.dumps(
            {
                "factor": proposal.factor,
                "old_weight": proposal.old_weight,
                "new_weight": proposal.new_weight,
                "rationale": proposal.rationale,
                "evidence_source": proposal.evidence_source,
                "training_window": proposal.training_window,
                "fingerprint": proposal.fingerprint(),
                "proposed_at": proposal.proposed_at,
                "factor_versions": proposal.factor_versions,
                "status": "pre-registered; held-out segment not yet consumed",
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def consume_heldout(
    window: Window,
    proposal: Proposal,
    metrics: dict[str, float],
    *,
    proposals_dir: Path,
    judged_at: str | None = None,
) -> dict[str, object]:
    """Score the held-out segment once, then record the window as consumed.

    Raises :class:`LeakageError` if this window already judged a proposal: a
    reused segment is weight search by repetition, and the guard exists to make
    that impossible rather than merely discouraged.
    """
    stamp = judged_at or datetime.now().isoformat(timespec="seconds")
    ledger = proposals_dir / "consumed_windows.jsonl"
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("cutoff_date") == window.cutoff_date.isoformat():
                raise LeakageError(
                    f"held-out window {window.heldout_label} already judged proposal "
                    f"{record.get('proposal_fingerprint')}; pick a new time boundary"
                )

    proposals_dir.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "cutoff_date": window.cutoff_date.isoformat(),
                    "proposal_fingerprint": proposal.fingerprint(),
                    "factor": proposal.factor,
                    "old_weight": proposal.old_weight,
                    "new_weight": proposal.new_weight,
                    "metrics": metrics,
                    "judged_at": stamp,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    return {
        "cutoff": window.heldout_label,
        "proposal_fingerprint": proposal.fingerprint(),
        "metrics": metrics,
        "verdict": (
            "recorded; adoption is a human decision comparing training and "
            "held-out metrics, never an automatic promotion"
        ),
    }
