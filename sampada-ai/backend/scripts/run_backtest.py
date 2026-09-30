"""Run the price-estimator backtest on fetched history and write a report.

Reads the raw JSONL files produced by ``scripts/fetch_history.py``, converts
them into the same ``MarketPriceRecord`` shape the live provider emits, and
runs the walk-forward backtest exactly as production would run the estimator.

The report is written to ``var/history/backtest_report.json`` and printed.
Numbers in it are derived only from the fetched records - nothing is
interpolated, and series whose windows are too short are reported as skipped.

Usage:

    python scripts/run_backtest.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# Make `app` importable when the script is run from anywhere.
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.evaluation.backtest import run_backtest, summarize
from app.providers.market import MarketPriceRecord

HISTORY_DIR = Path(__file__).resolve().parents[1] / "var" / "history"
REPORT_PATH = HISTORY_DIR / "backtest_report.json"


def _as_date(value: object) -> object:
    from app.providers.market import _as_date

    return _as_date(value)


def load_series(path: Path) -> list[MarketPriceRecord]:
    records: list[MarketPriceRecord] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            records.append(
                MarketPriceRecord(
                    crop_id=path.stem.split("_")[0],
                    commodity=str(row.get("Commodity", "")),
                    market=row.get("Market"),
                    district=row.get("District"),
                    state=row.get("State"),
                    price_date=_as_date(row.get("Arrival_Date")),
                    min_price=_as_float(row.get("Min_Price")),
                    max_price=_as_float(row.get("Max_Price")),
                    modal_price=_as_float(row.get("Modal_Price")),
                )
            )
    return records


def _as_float(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def main() -> None:
    paths = sorted(HISTORY_DIR.glob("*.jsonl"))
    if not paths:
        sys.exit("no history found; run scripts/fetch_history.py first")

    reports = []
    for path in paths:
        records = load_series(path)
        report = run_backtest(path.stem, records)
        reports.append(report)
        print(
            f"{path.stem:22s} window={report.first_date}..{report.last_date} "
            f"evaluated={report.evaluated:4d} missing={report.missing_weeks:4d} "
            f"MAE%={report.mae_pct_of_mean} "
            f"coverage={report.coverage} sharpness={report.sharpness_pct}%"
        )

    summary = summarize(reports)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "series": [
            {
                "key": r.key,
                "first_date": str(r.first_date),
                "last_date": str(r.last_date),
                "predictions": r.predictions,
                "evaluated": r.evaluated,
                "missing_weeks": r.missing_weeks,
                "skipped_cold_start": r.skipped_cold_start,
                "mae": r.mae,
                "rmse": r.rmse,
                "mae_pct_of_mean": r.mae_pct_of_mean,
                "mean_actual_price": r.mean_actual_price,
                "coverage": r.coverage,
                "sharpness_pct": r.sharpness_pct,
                "misses": [
                    {
                        "cutoff": str(m.cutoff),
                        "target": str(m.target_date),
                        "low": m.low,
                        "high": m.high,
                        "actual": m.actual,
                        "kind": m.kind,
                    }
                    for m in r.misses
                ],
                "notes": r.notes,
            }
            for r in reports
        ],
    }
    REPORT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nsummary: {json.dumps(summary, ensure_ascii=False)}")
    print(f"report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
