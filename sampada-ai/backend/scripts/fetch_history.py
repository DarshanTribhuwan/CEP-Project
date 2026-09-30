"""Fetch historical variety-wise mandi prices for the backtest.

Pulls the variety-wise daily feed (resource 35985678-0d79-46b4-9ed6-6f13308a1d24)
for a fixed list of (commodity, district) series in Maharashtra and stores the
raw JSONL under ``var/history/`` - git-ignored, and never edited after download.

Honesty rules:

    - the raw response is stored verbatim; nothing is cleaned, interpolated
      or repaired at fetch time;
    - series that come back empty or short are recorded as such, because
      "the source has nothing here" is a result, not an error to hide;
    - the daily feed's window is reported per series so the backtest output
      can state exactly what it was tested on.

The historical index is ordered oldest-first, so to get the most recent N
records we first read ``total`` and then page backwards from ``total - N``.

Usage:

    python scripts/fetch_history.py                  # fetch everything below
    python scripts/fetch_history.py --max 3000       # more history per series
    python scripts/fetch_history.py --offline        # report what is cached, fetch nothing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Make `app` importable when the script is run from anywhere.
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

HISTORY_RESOURCE = "35985678-0d79-46b4-9ed6-6f13308a1d24"
BASE_URL = f"https://api.data.gov.in/resource/{HISTORY_RESOURCE}"
USER_AGENT = "SAMPADA-AI/1.0 (backtest history fetch)"
API_KEY_ENV = "SAMPADA_DATA_GOV_IN_KEY"

# The backtest series. Commodity strings verified against this feed on
# 2026-09-23 (they match AGMARKNET_COMMODITY in app/providers/market.py).
SERIES: list[dict[str, str]] = [
    {"crop_id": "onion", "state": "Maharashtra", "district": "Pune"},
    {"crop_id": "onion", "state": "Maharashtra", "district": "Nashik"},
    {"crop_id": "soybean", "state": "Maharashtra", "district": "Pune"},
    {"crop_id": "soybean", "state": "Maharashtra", "district": "Nashik"},
    {"crop_id": "tur", "state": "Maharashtra", "district": "Pune"},
]

OUT_DIR = Path(__file__).resolve().parents[1] / "var" / "history"


def _api_key() -> str:
    # .env lives beside config.toml; load it the same way the app does so the
    # script works without an exported variable.
    from app.core.config import env_secret

    key = env_secret(API_KEY_ENV)
    if not key:
        sys.exit(
            f"no API key found: set {API_KEY_ENV} in the environment or in backend/.env"
        )
    return key


def _get(url: str, params: dict[str, object], timeout: float = 30.0) -> dict:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except Exception as exc:  # noqa: BLE001 - retried below
            last_error = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"fetch failed after retries: {last_error}")


def fetch_series(key: str, series: dict[str, str], max_records: int) -> dict:
    """Page one series backwards from the newest record. Raw rows only."""
    label = f"{series['crop_id']}/{series['district']}"
    base_filters = {
        "api-key": key,
        "format": "json",
        "filters[State]": series["state"],
        "filters[Commodity]": series["commodity"],
        "filters[District]": series["district"],
    }

    probe = _get(BASE_URL, {**base_filters, "limit": 1})
    total = int(probe.get("total", 0))
    if total == 0:
        return {"series": series, "records": [], "note": "source has no records for this filter"}

    wanted = min(total, max_records)
    rows: list[dict] = []
    offset = total - wanted
    while len(rows) < wanted and offset < total:
        page = _get(BASE_URL, {**base_filters, "limit": 1000, "offset": offset})
        batch = page.get("records", [])
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        time.sleep(0.35)  # polite delay; this is a free public API

    return {"series": series, "records": rows, "total_in_feed": total}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max", type=int, default=2000, help="max records per series")
    parser.add_argument(
        "--offline", action="store_true", help="list cached files and exit; fetch nothing"
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.offline:
        for path in sorted(OUT_DIR.glob("*.jsonl")):
            count = sum(1 for _ in path.open(encoding="utf-8"))
            print(f"{path.name}: {count} records (cached)")
        return

    key = _api_key()
    # The historical feed's commodity spellings equal the daily ones, except
    # where noted in SERIES (verified 2026-09-23).
    from app.providers.market import AGMARKNET_COMMODITY

    for series in SERIES:
        series = {**series, "commodity": AGMARKNET_COMMODITY[series["crop_id"]]}
        label = f"{series['crop_id']}/{series['district']}"
        result = fetch_series(key, series, args.max)
        out_path = OUT_DIR / f"{series['crop_id']}_{series['district']}.jsonl"
        with out_path.open("w", encoding="utf-8") as handle:
            for row in result["records"]:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        note = result.get("note", "")
        print(
            f"{label:24s} wrote {len(result['records']):5d} records "
            f"(feed total: {result.get('total_in_feed', 'n/a')}) {note}"
        )


if __name__ == "__main__":
    main()
