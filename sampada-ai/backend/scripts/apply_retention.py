"""Operator CLI for the data-retention policy.

Retention is configured in ``config.toml``::

    [retention]
    outcomes_months         = 36   # season results older than this are expired
    recommendations_months  = 24   # stored statements age with their feedback
    auth_tokens_days        = 30   # dead sessions

Every threshold defaults to 0 = keep forever: a deployment that never sets
``[retention]`` never deletes a row. This script is the only thing that
purges, and it is deliberately not wired into startup or a scheduler.

Dry run by default: prints what is expired and deletes nothing. ``--apply``
executes the purge in one transaction. Either way it prints the full blast
radius (feedback and predictions that go with aged-out recommendations), and
it refuses to run at all when no threshold is configured, so a fat-fingered
invocation cannot turn into a surprise.

Usage (from ``backend/``)::

    python scripts/apply_retention.py            # dry run: report only
    python scripts/apply_retention.py --apply    # execute the deletions
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core import retention  # noqa: E402
from app.core.db import init_db  # noqa: E402

LABELS = {
    "outcomes": "season outcomes",
    "recommendations": "stored recommendations",
    "feedback": "feedback rows (go with their recommendation)",
    "predictions": "prediction rows (go with their recommendation)",
    "auth_tokens": "expired auth sessions",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually delete; without this flag nothing is written",
    )
    args = parser.parse_args()

    init_db()
    limits = retention.policy()
    if not any(limits.values()):
        print(
            "no retention thresholds are configured ([retention] in config.toml, "
            "0 = keep forever); nothing to do. Configure one first - this tool "
            "refuses to guess what to delete."
        )
        return 2

    expired = retention.expired_counts()
    print("retention policy:", json.dumps(limits, sort_keys=True))
    if args.apply:
        deleted = retention.purge()
        print("deleted:")
        for key in sorted(deleted):
            print(f"  {LABELS.get(key, key):44s} {deleted[key]}")
        total = sum(deleted.values())
        print(f"  {'total':44s} {total}")
        return 0

    print("DRY RUN - nothing has been deleted. Expired rows:")
    for key in sorted(expired):
        print(f"  {LABELS.get(key, key):44s} {expired[key]}")
    print("run again with --apply to delete them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
