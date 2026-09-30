"""Operator CLI for the model-promotion flow.

The promotion pipeline is deliberately not automated end to end: this script
is the human's tool. It can

    * ``evaluate``  pre-register a proposal, score incumbent vs challenger on
      the held-out segment once, and write the ledger record;
    * ``decide``    record a human decision (``adopt`` / ``reject``) against a
      proposal fingerprint - with the same override rule the flow enforces
      (adopting below the bar needs a written reason);
    * ``show``      print the ledger.

It never edits ``config.toml``. An "adopted" record is exactly that: a record
of a human decision. Applying the weight to the config is a separate, deliberate
edit by whoever owns the deployment, which the ledger's ``note`` field says on
every record.

Usage (from ``backend/``)::

    python scripts/promote_model.py evaluate \\
        --factor weather_match --old 0.20 --new 0.30 \\
        --rationale "outcome contrast seen in pilot districts" \\
        --cutoff 2026-09-01

    python scripts/promote_model.py decide \\
        --fingerprint <fp> --decision adopted --by "darshan"

    python scripts/promote_model.py show
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.core.db import init_db  # noqa: E402
from app.evaluation import promotion  # noqa: E402
from app.evaluation.heldout import (  # noqa: E402
    Proposal,
    Window,
    pre_register_proposal,
)

PROPOSALS_DIR = BACKEND_DIR / "var" / "evaluations"


def _incumbent_weights() -> dict[str, float]:
    weights = settings.get("recommendation.factor_weights") or {}
    return {str(k): float(v) for k, v in weights.items()}


def cmd_evaluate(args: argparse.Namespace) -> int:
    init_db()
    weights = _incumbent_weights()
    if args.factor not in weights:
        print(f"unknown factor {args.factor!r}; known: {', '.join(sorted(weights))}")
        return 2
    if args.old is not None and abs(weights[args.factor] - args.old) > 1e-9:
        print(
            f"refusing: configured {args.factor} weight is {weights[args.factor]}, "
            f"not the {args.old} you cited. Check the current config first - a "
            "proposal must state the weight it is actually replacing."
        )
        return 2

    window = Window(cutoff_date=date.fromisoformat(args.cutoff))
    proposal = Proposal(
        factor=args.factor,
        old_weight=weights[args.factor],
        new_weight=args.new,
        rationale=args.rationale,
        evidence_source=args.evidence_source,
        training_window=f"outcomes < {args.cutoff}",
        proposed_at=__import__("datetime").datetime.now().isoformat(timespec="seconds"),
        factor_versions={"configured_weights": "config.toml@proposal-time"},
    )
    pre_register_proposal(proposal, PROPOSALS_DIR)
    incumbent = promotion.WeightModel("incumbent:config.toml", weights)
    result = promotion.run_promotion(window, proposal, incumbent, PROPOSALS_DIR)
    record = result["record"]

    print(f"pre-registered proposal {proposal.fingerprint()}")
    print(f"held-out window: {record['window']}")
    metrics = record["metrics"]
    for name in ("incumbent", "challenger"):
        m = metrics[name]
        print(
            f"  {name:11s} n={m['n']:>4}  tau={m['kendall_tau']}  "
            f"top-bottom spread=₹{m['top_bottom_spread_inr']}"
        )
    bar = record["evidence_bar"]
    print(
        f"evidence bar: {bar['status']} (need >= {bar['required_outcomes']} "
        f"scored outcomes, have {bar['n_scored']})"
    )
    print(record["note"])
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    decision = {"adopt": promotion.DECISION_ADOPTED, "reject": promotion.DECISION_REJECTED}[
        args.decision
    ]
    try:
        record = promotion.record_decision(
            args.fingerprint,
            decision,
            decided_by=args.by,
            proposals_dir=PROPOSALS_DIR,
            override_reason=args.override_reason,
        )
    except (KeyError, ValueError) as exc:
        print(f"refused: {exc}")
        return 2
    print(f"recorded: {record['decision']} by {record['decided_by']} at {record['decided_at']}")
    if record["decision"] == promotion.DECISION_ADOPTED:
        print(
            "The ledger records this decision. config.toml was NOT changed - "
            "apply the weight yourself if and only if that is what you intend."
        )
    return 0


def cmd_show(_: argparse.Namespace) -> int:
    records = promotion.list_promotions(PROPOSALS_DIR)
    if not records:
        print("no promotion records yet")
        return 0
    for record in records:
        print(
            f"{record['proposal_fingerprint']}  {record['factor']} "
            f"{record['old_weight']} -> {record['new_weight']}  "
            f"[{record['decision']}]"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_eval = sub.add_parser("evaluate", help="pre-register + score one proposal")
    p_eval.add_argument("--factor", required=True)
    p_eval.add_argument("--old", type=float, default=None, help="must match config.toml")
    p_eval.add_argument("--new", type=float, required=True)
    p_eval.add_argument("--rationale", required=True)
    p_eval.add_argument("--evidence-source", default="pilot outcome analysis")
    p_eval.add_argument("--cutoff", required=True, help="held-out starts at this date")
    p_eval.set_defaults(func=cmd_evaluate)

    p_decide = sub.add_parser("decide", help="record the human decision")
    p_decide.add_argument("--fingerprint", required=True)
    p_decide.add_argument("--decision", choices=["adopt", "reject"], required=True)
    p_decide.add_argument("--by", required=True, help="who decided")
    p_decide.add_argument(
        "--override-reason",
        default=None,
        help="required to adopt a challenger that did not clear its bar",
    )
    p_decide.set_defaults(func=cmd_decide)

    p_show = sub.add_parser("show", help="print the ledger")
    p_show.set_defaults(func=cmd_show)

    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
