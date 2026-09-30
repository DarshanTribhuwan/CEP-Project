"""Turn the backtest report into ``app/data/price_reliability.json``.

The recommendation engine scales each crop's ``market_price`` factor weight by
that crop's measured price-signal reliability. This script is the only writer
of that table, so the chain stays auditable:

    walk-forward backtest  ->  var/history/backtest_report.json  (raw, git-ignored)
                           ->  this script                       (stated rule)
                           ->  app/data/price_reliability.json   (committed, cited)

The rule lives in exactly one place -
``app/services/price_reliability.py::reliability_from_coverage`` - which this
script imports, so the exporter and the engine cannot drift apart:

    reliability = clamp(pooled_coverage / COVERAGE_TARGET, RELIABILITY_FLOOR, 1.0)

    pooled_coverage is hits / evaluated weeks for the whole crop (all its
    series), and COVERAGE_TARGET is 0.80 - the expected hit rate of the
    estimator's ~10th-90th percentile band. A crop whose bands hold up gets
    full weight; a crop whose bands miss gets proportionally less, floored at
    0.5 so a measured signal is never discarded entirely. Crops that were not
    backtested get ``DEFAULT_RELIABILITY`` (0.75) and a visible reason code -
    "not measured yet" is not "fully trusted".

Usage:

    python scripts/export_price_reliability.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.price_reliability import (  # noqa: E402
    COVERAGE_TARGET,
    DEFAULT_RELIABILITY,
    RELIABILITY_FLOOR,
    reliability_from_coverage,
)

REPORT_PATH = BACKEND_DIR / "var" / "history" / "backtest_report.json"
OUT_PATH = BACKEND_DIR / "app" / "data" / "price_reliability.json"


def main() -> None:
    if not REPORT_PATH.exists():
        sys.exit(f"no backtest report at {REPORT_PATH}; run scripts/run_backtest.py first")
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    # Pool per crop across its series, weighted by evaluated weeks.
    crops: dict[str, dict[str, float]] = {}
    for series in report["series"]:
        crop_id = series["key"].split("_", 1)[0]
        entry = crops.setdefault(
            crop_id, {"evaluated": 0, "hits": 0, "mae_pct_weighted": 0.0}
        )
        evaluated = int(series["evaluated"])
        hits = evaluated - len(series["misses"])
        entry["evaluated"] += evaluated
        entry["hits"] += hits
        entry["mae_pct_weighted"] += (series.get("mae_pct_of_mean") or 0.0) * evaluated

    table: dict[str, dict] = {}
    for crop_id, pooled in sorted(crops.items()):
        if pooled["evaluated"] == 0:
            continue
        coverage = pooled["hits"] / pooled["evaluated"]
        mae_pct = pooled["mae_pct_weighted"] / pooled["evaluated"]
        table[crop_id] = {
            "reliability": reliability_from_coverage(coverage),
            "evidence": {
                "pooled_coverage": round(coverage, 4),
                "evaluated_weeks": pooled["evaluated"],
                "mae_pct_of_mean": round(mae_pct, 2),
            },
        }

    if not table:
        sys.exit("backtest report contains no evaluated series; nothing to export")

    payload = {
        "_meta": {
            "generated_from": "var/history/backtest_report.json",
            "generated_at": date.today().isoformat(),
            "method": "walk-forward backtest of the price estimator; see app/evaluation/backtest.py",
            "data_source": (
                "Agmarknet variety-wise daily prices via data.gov.in "
                "(resource 35985678-0d79-46b4-9ed6-6f13308a1d24), Maharashtra districts"
            ),
            "rule": (
                f"reliability = clamp(pooled_coverage / {COVERAGE_TARGET}, "
                f"{RELIABILITY_FLOOR}, 1.0), pooled per crop over evaluated weeks"
            ),
            "coverage_target": COVERAGE_TARGET,
            "horizon_days": 7,
            "caveat": (
                "measurements of the backtested sample, not a general accuracy "
                "guarantee; re-run the backtest after every estimator change"
            ),
            "default_reliability_note": (
                "crops not measured yet get default_reliability and a visible "
                "reason code; an absent/malformed file makes the feature inert "
                "(reliability 1.0 everywhere)"
            ),
        },
        "default_reliability": DEFAULT_RELIABILITY,
        "crops": table,
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for crop_id, entry in table.items():
        print(
            f"{crop_id:10s} reliability={entry['reliability']:.2f} "
            f"coverage={entry['evidence']['pooled_coverage']:.4f} "
            f"weeks={entry['evidence']['evaluated_weeks']} "
            f"mae%={entry['evidence']['mae_pct_of_mean']}"
        )
    print(f"\nwritten to {OUT_PATH}")


if __name__ == "__main__":
    main()
