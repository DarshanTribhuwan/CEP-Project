"""Promotion flow: incumbent vs challenger, judged on held-out outcomes.

This is the last piece of the Part 4 pipeline. The building blocks that
already existed:

    * ``app/evaluation/heldout.py``   - the time-split window with a leakage
      guard, pre-registration, and one-shot consumption of each held-out
      segment;
    * schema v2 factor-score snapshots - what actually drove each stored
      recommendation, so an evaluation is computed from storage alone with no
      recomputation that could drift from what the farmer saw;
    * ``app/evaluation/factor_evidence.py`` - the per-factor evidence bar a
      proposal must clear before a weight change may even be considered.

What this module adds is the flow that joins them:

    propose -> pre-register -> score incumbent AND challenger on the same
    held-out outcomes, once -> record both verdicts and the evidence-bar
    status -> a human adopts or rejects, on the record.

Honesty constraints, enforced in code rather than prose:

    * Nothing here writes a weight to ``config.toml``. "Adopted" is a ledger
      entry recording a human decision; applying it is a separate, deliberate
      edit by the operator. There is no code path from evaluation to weights.
    * A held-out segment judges exactly one proposal (``heldout.consume_heldout``
      is called once per promotion attempt), so candidate-after-candidate
      scoring on the same window is structurally impossible.
    * Below the factor's evidence bar the result is recorded as
      ``insufficient_evidence``. The metrics are still reported - they are
      just labelled as not decision-grade, and adopting on them requires an
      explicit override with a written reason.
    * Every metric is computed identically for incumbent and challenger, from
      the same rows. There is no model-specific advantage anywhere in the
      scoring code.

Metric, and its limits: for each held-out outcome the stored factor scores are
recombined under a candidate weight vector exactly as the engine would
(available factors only, renormalised). Kendall tau between that score and the
realized profit, plus a top-half/bottom-half profit spread, say whether
*higher blended score tracked better outcomes* under one weighting versus the
other. This is a ranking-quality check on the recommendation the system
actually made - not a counterfactual "what if the farmer had planted another
crop", which the stored data cannot answer.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..core.db import get_conn, now_iso
from . import factor_evidence
from .heldout import Proposal, Window, consume_heldout

LEDGER_FILENAME = "promotions.jsonl"
# Row floor for the tau/spread numbers to mean anything at all. A factor's
# registry bar (>= 100/200/300 outcomes) still applies on top of this.
MIN_ROWS_FOR_METRICS = 10

DECISION_PENDING = "pending_human_decision"
DECISION_ADOPTED = "adopted"
DECISION_REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class WeightModel:
    """A named weight vector: the incumbent's is whatever config holds."""

    name: str
    weights: dict[str, float]


# ---------------------------------------------------------------------------
# Stored evidence
# ---------------------------------------------------------------------------
_HELDOUT_QUERY = """
    SELECT r.id                AS recommendation_id,
           r.crop_id           AS crop_id,
           r.factor_scores     AS factor_scores,
           o.profit_inr        AS profit_inr,
           o.created_at        AS outcome_created_at
    FROM recommendations r
    JOIN outcomes o ON o.recommendation_id = r.id
    WHERE r.factor_scores IS NOT NULL
"""


def _outcome_date(iso: str) -> date:
    return date.fromisoformat(iso[:10])


def heldout_rows(window: Window) -> list[dict[str, Any]]:
    """The held-out segment exactly as ``window`` cuts it: one query, no
    temporal judgement beyond ``window.segment_for``."""
    rows: list[dict[str, Any]] = []
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(_HELDOUT_QUERY):
            record = dict(row)
            if window.segment_for(_outcome_date(record["outcome_created_at"])) != "heldout":
                continue
            record["factor_scores"] = json.loads(record["factor_scores"])
            rows.append(record)
    return rows


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def score_with(weights: dict[str, float], factor_scores: list[dict[str, Any]]) -> float | None:
    """Recombine stored factor scores under ``weights`` exactly as the engine
    does: available factors only, renormalised over what is available. A
    recommendation with no available factor scores returns ``None`` and is
    counted as missing - never scored as zero."""
    available = [f for f in factor_scores if f.get("available") and f.get("score") is not None]
    total_weight = sum(weights.get(f["factor"], 0.0) for f in available)
    if total_weight <= 0:
        return None
    return sum(
        weights.get(f["factor"], 0.0) * float(f["score"]) for f in available
    ) / total_weight


def _kendall_tau(scores: list[float], profits: list[float]) -> float:
    """Kendall tau between two equal-length series (ties count half)."""
    n = len(scores)
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            ds = scores[i] - scores[j]
            dp = profits[i] - profits[j]
            if ds == 0 or dp == 0:
                concordant += 0.5
                discordant += 0.5
            elif (ds > 0) == (dp > 0):
                concordant += 1
            else:
                discordant += 1
    pairs = n * (n - 1) / 2
    return (concordant - discordant) / pairs if pairs else 0.0


def evaluate_model(model: WeightModel, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Ranking-quality metrics for one weight vector on one segment."""
    scored: list[tuple[float, float]] = []
    missing = 0
    for row in rows:
        score = score_with(model.weights, row["factor_scores"])
        profit = row["profit_inr"]
        if score is None or profit is None:
            missing += 1
            continue
        scored.append((score, float(profit)))

    metrics: dict[str, Any] = {
        "n": len(scored),
        "n_missing": missing,
    }
    if len(scored) < MIN_ROWS_FOR_METRICS:
        metrics["kendall_tau"] = None
        metrics["top_bottom_spread_inr"] = None
        return metrics

    scores = [s for s, _ in scored]
    profits = [p for _, p in scored]
    ordered = sorted(scored, key=lambda pair: pair[0])
    half = len(ordered) // 2
    bottom = [p for _, p in ordered[:half]]
    top = [p for _, p in ordered[-half:]] or [0.0]
    metrics["kendall_tau"] = round(_kendall_tau(scores, profits), 4)
    metrics["top_bottom_spread_inr"] = round(
        sum(top) / len(top) - sum(bottom) / len(bottom), 2
    )
    return metrics


# ---------------------------------------------------------------------------
# Evidence bar
# ---------------------------------------------------------------------------

def evidence_bar_for(factor: str) -> int:
    """The factor's minimum outcome count, parsed from the registry so there
    is exactly one source of truth for the bar."""
    text = factor_evidence.status_for(factor).minimum_evidence
    match = re.search(r">=\s*(\d+)", text)
    return int(match.group(1)) if match else MIN_ROWS_FOR_METRICS


def bar_status(factor: str, n_scored: int) -> dict[str, Any]:
    bar = evidence_bar_for(factor)
    met = n_scored >= bar
    return {
        "minimum_evidence": factor_evidence.status_for(factor).minimum_evidence,
        "required_outcomes": bar,
        "n_scored": n_scored,
        "met": met,
        "status": "met" if met else "insufficient_evidence",
    }


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------
def challenger_weights(proposal: Proposal, incumbent: dict[str, float]) -> dict[str, float]:
    """The challenger is the incumbent with the proposed factor reweighted -
    nothing else moves, so the comparison isolates exactly one change."""
    weights = dict(incumbent)
    weights[proposal.factor] = proposal.new_weight
    return weights


def run_promotion(
    window: Window,
    proposal: Proposal,
    incumbent: WeightModel,
    proposals_dir: Path,
) -> dict[str, Any]:
    """Score the proposal's challenger against the incumbent on the held-out
    segment, once, and write the ledger record. Raises :class:`LeakageError`
    if this window already judged a proposal.

    The proposal must already be pre-registered (``heldout.pre_register_proposal``)
    - the ledger references that file, and its timestamp is what would expose
    a weight chosen after seeing the held-out segment.
    """
    rows = heldout_rows(window)

    challenger = WeightModel(
        name=f"challenger:{proposal.factor}={proposal.new_weight}",
        weights=challenger_weights(proposal, incumbent.weights),
    )
    incumbent_metrics = evaluate_model(incumbent, rows)
    challenger_metrics = evaluate_model(challenger, rows)

    bar = bar_status(proposal.factor, int(challenger_metrics["n"]))

    # One consumption for the whole comparison: the window judged exactly one
    # proposal, and both models' metrics live in that one record.
    verdict = consume_heldout(
        window,
        proposal,
        metrics={"incumbent": incumbent_metrics, "challenger": challenger_metrics},
        proposals_dir=proposals_dir,
    )

    record = {
        "proposal_fingerprint": proposal.fingerprint(),
        "factor": proposal.factor,
        "old_weight": proposal.old_weight,
        "new_weight": proposal.new_weight,
        "incumbent_name": incumbent.name,
        "window": window.heldout_label,
        "metrics": verdict["metrics"],
        "evidence_bar": bar,
        "decision": DECISION_PENDING,
        "decided_by": None,
        "decided_at": None,
        "note": (
            "comparison recorded; adoption is a human decision. Nothing here "
            "changes config.toml - applying an adopted weight is a separate, "
            "deliberate operator edit."
        ),
        "evaluated_at": now_iso(),
    }
    ledger = _append_ledger(proposals_dir, record)
    return {"record": record, "ledger_path": str(ledger), "verdict": verdict}


def _append_ledger(proposals_dir: Path, record: dict[str, Any]) -> Path:
    proposals_dir.mkdir(parents=True, exist_ok=True)
    path = proposals_dir / LEDGER_FILENAME
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def record_decision(
    proposal_fingerprint: str,
    decision: str,
    decided_by: str,
    proposals_dir: Path,
    override_reason: str | None = None,
) -> dict[str, Any]:
    """Append the human decision for a proposal to the ledger.

    ``decision`` is ``adopted`` or ``rejected``. Adopting against the recorded
    metrics (challenger worse than incumbent, or the evidence bar unmet) is
    refused unless ``override_reason`` states why - the ledger must never hold
    an adoption its own numbers contradict in silence.
    """
    if decision not in {DECISION_ADOPTED, DECISION_REJECTED}:
        raise ValueError(f"decision must be adopted or rejected, got {decision!r}")

    record = _find_record(proposals_dir, proposal_fingerprint)
    if record is None:
        raise KeyError(f"no promotion record for {proposal_fingerprint}")

    if decision == DECISION_ADOPTED:
        bar = record.get("evidence_bar", {})
        challenger = record.get("metrics", {}).get("challenger", {})
        incumbent = record.get("metrics", {}).get("incumbent", {})
        better = _challenger_not_worse(challenger, incumbent)
        if (not bar.get("met", False) or not better) and not override_reason:
            raise ValueError(
                "adopting a challenger that has not cleared its evidence bar "
                "and/or is not better than the incumbent requires an explicit "
                "override_reason recorded in the ledger"
            )
        if override_reason:
            record["override_reason"] = override_reason

    record.update(
        decision=decision,
        decided_by=decided_by,
        decided_at=now_iso(),
    )
    _rewrite_ledger(proposals_dir, proposal_fingerprint, record)
    return record


def _challenger_not_worse(challenger: dict[str, Any], incumbent: dict[str, Any]) -> bool:
    """Compare on the two metrics; a metric absent for both (below the metric
    floor) does not block either way."""
    challenger_tau = challenger.get("kendall_tau")
    incumbent_tau = incumbent.get("kendall_tau")
    if challenger_tau is not None and incumbent_tau is not None:
        if challenger_tau < incumbent_tau:
            return False
    challenger_spread = challenger.get("top_bottom_spread_inr")
    incumbent_spread = incumbent.get("top_bottom_spread_inr")
    if challenger_spread is not None and incumbent_spread is not None:
        if challenger_spread < incumbent_spread:
            return False
    return True


def _find_record(proposals_dir: Path, fingerprint: str) -> dict[str, Any] | None:
    path = proposals_dir / LEDGER_FILENAME
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("proposal_fingerprint") == fingerprint:
            return record
    return None


def _rewrite_ledger(proposals_dir: Path, fingerprint: str, updated: dict[str, Any]) -> None:
    path = proposals_dir / LEDGER_FILENAME
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    records = [
        updated if r.get("proposal_fingerprint") == fingerprint else r for r in records
    ]
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def list_promotions(proposals_dir: Path) -> list[dict[str, Any]]:
    """The ledger, oldest first. Read-only surface for the API."""
    path = proposals_dir / LEDGER_FILENAME
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
