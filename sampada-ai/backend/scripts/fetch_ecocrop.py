"""Fetch crop requirement data from FAO EcoCrop.

Why this script exists
----------------------
``app/data/crops.json`` originally held "plausible magnitude" agronomic values
written by the development team and marked ``UNVERIFIED``. This script is how
those values get replaced with sourced ones.

EcoCrop is FAO's crop environmental-requirements database (Land & Water
Division). It publishes, per species, optimal and absolute ranges for
temperature, rainfall, soil pH, soil texture, soil drainage, soil depth and
fertility, plus the crop cycle length. That covers every field the suitability
scorer in ``app/services/suitability.py`` reads.

Reproducibility
---------------
Raw HTML for every record is written to ``app/data/sources/ecocrop_html/`` so a
reviewer can diff the parsed table against the page it came from without network
access. Parsed output goes to ``app/data/sources/ecocrop.json``.

Offline and retry
-----------------
This script is designed to run again on a machine with no internet at all, which
is the normal case for a reviewer or a CI job:

    --offline   never make a request. Every record must already be in the HTML
                cache; anything missing is reported and the run exits non-zero
                WITHOUT writing a partial ecocrop.json over the good one.
    (default)   use the HTML cache when it is present, fetch what is missing.
    --refresh   ignore the cache and re-download everything.

Individual requests are retried with backoff (see app/core/retry.py) so a single
502 from the FAO server does not lose a whole run. A failure never aborts the
whole job: every record is attempted, and the failures are listed at the end.

Run:  python scripts/fetch_ecocrop.py
Then: python scripts/build_crop_table.py   (merges this with FAO-56)
"""

from __future__ import annotations

import html as html_mod
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    # The retry policy is shared with the runtime providers rather than duplicated
    # here, so this script runs against the same backoff rules as the API.
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.retry import retry_call  # noqa: E402  (path set up above)

SOURCE_DIR = BACKEND_DIR / "app" / "data" / "sources"
HTML_DIR = SOURCE_DIR / "ecocrop_html"
PARSED_PATH = SOURCE_DIR / "ecocrop.json"

BASE_URL = "https://ecocrop.apps.fao.org/ecocrop/srv/en/dataSheet?id={code}"
USER_AGENT = "SAMPADA-AI/0.1 (research; contact: project maintainers)"
REQUEST_DELAY_SECONDS = 1.0
REQUEST_ATTEMPTS = 3
REQUEST_BACKOFF_SECONDS = 1.5


class OfflineCacheMiss(RuntimeError):
    """Raised when --offline is set and a record is not in the HTML cache.

    Deliberately fatal for that record only: the run continues, reports it, and
    refuses to write output. Inventing a record to keep the build green is the
    one thing this project does not do.
    """

# Our stable crop id -> the EcoCrop species record it is sourced from.
# Codes were read from https://ecocrop.apps.fao.org/ecocrop/srv/en/cropList
#
# ``fallback_code`` records a second EcoCrop record to consult when the primary
# one is incomplete. This is not padding: the Zea mays and Saccharum officinarum
# records genuinely ship with blank soil-texture and soil-drainage cells, and a
# blank is not the same as "any soil will do". Each fallback is reported in the
# built table so the substitution is visible rather than silent.
ECOCROP_CROPS: dict[str, dict[str, object]] = {
    "soybean": {"code": 1150, "scientific_name": "Glycine max"},
    "tur": {"code": 576, "scientific_name": "Cajanus cajan"},
    "cotton": {"code": 1160, "scientific_name": "Gossypium hirsutum"},
    "maize": {
        "code": 238663,
        "scientific_name": "Zea mays ssp. mays",
        "note": (
            "Cultivated maize. EcoCrop's generic 'Zea mays' record (id 2175) has blank "
            "soil texture and drainage cells; this subspecies record has both."
        ),
    },
    "jowar": {"code": 48747, "scientific_name": "Sorghum bicolor"},
    "bajra": {"code": 8418, "scientific_name": "Pennisetum glaucum"},
    "groundnut": {"code": 2199, "scientific_name": "Arachis hypogaea"},
    "onion": {"code": 364, "scientific_name": "Allium cepa"},
    "sugarcane": {
        "code": 1884,
        "scientific_name": "Saccharum officinarum",
        "fallback_code": 9432,
        "fallback_scientific_name": "Saccharum barberi",
        "fallback_fields": ["soil_texture", "soil_drainage"],
        "note": (
            "The Saccharum officinarum record has blank soil texture and drainage cells. "
            "Saccharum barberi (Indian cane) supplies those two fields only."
        ),
    },
    "rice": {"code": 1574, "scientific_name": "Oryza sativa"},
    "wheat": {"code": 2114, "scientific_name": "Triticum aestivum"},
    "gram": {"code": 2479, "scientific_name": "Cicer arietinum"},
    "moong": {"code": 2150, "scientific_name": "Vigna radiata"},
    "urad": {"code": 2149, "scientific_name": "Vigna mungo"},
    "sunflower": {"code": 1191, "scientific_name": "Helianthus annuus"},
    "safflower": {"code": 2514, "scientific_name": "Carthamus tinctorius"},
}

_CELL_RE = re.compile(r"<(th|td)\b([^>]*)>(.*?)</\1>", re.S | re.I)
_ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def clean(fragment: str) -> str:
    """Strip tags/entities and collapse whitespace, treating ``---`` as blank."""
    text = _TAG_RE.sub(" ", fragment)
    text = html_mod.unescape(text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return "" if set(text) <= {"-"} else text


def row_fields(row_html: str) -> dict[str, list[str]]:
    """Map each ``<th>`` label in a row to the ``<td>`` values that follow it."""
    cells: list[tuple[str, str]] = [
        (match.group(1).lower(), clean(match.group(3)))
        for match in _CELL_RE.finditer(row_html)
    ]
    fields: dict[str, list[str]] = {}
    index = 0
    while index < len(cells):
        tag, label = cells[index]
        if tag != "th" or not label:
            index += 1
            continue
        values: list[str] = []
        cursor = index + 1
        while cursor < len(cells) and cells[cursor][0] == "td":
            values.append(cells[cursor][1])
            cursor += 1
        if values:
            fields.setdefault(label, values)
        index = cursor
    return fields


def numbers(values: list[str]) -> list[float] | None:
    """Extract float values from EcoCrop cells, or None if none are numeric."""
    out: list[float] = []
    for value in values:
        match = _NUMBER_RE.search(value)
        if match is None:
            return None
        out.append(float(match.group(0)))
    return out or None


def split_list(value: str) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def parse(html: str) -> dict[str, object]:
    """Turn one EcoCrop data-sheet page into a flat, structured record."""
    fields: dict[str, list[str]] = {}
    for row_html in _ROW_RE.findall(html):
        for label, values in row_fields(row_html).items():
            fields.setdefault(label, values)

    record: dict[str, object] = {}

    temperature = numbers(fields.get("Temperat. requir.", []) or [])
    if temperature and len(temperature) == 4:
        record["temp_optimal_c"] = temperature[:2]
        record["temp_absolute_c"] = temperature[2:]
    elif temperature and len(temperature) == 2:
        record["temp_absolute_c"] = temperature

    rainfall = numbers(fields.get("Rainfall (annual)", []) or [])
    if rainfall and len(rainfall) == 4:
        record["rainfall_optimal_mm"] = rainfall[:2]
        record["rainfall_absolute_mm"] = rainfall[2:]
    elif rainfall and len(rainfall) == 2:
        record["rainfall_absolute_mm"] = rainfall

    ph = numbers(fields.get("Soil PH", []) or [])
    if ph and len(ph) == 4:
        record["ph_optimal"] = ph[:2]
        record["ph_absolute"] = ph[2:]
    elif ph and len(ph) == 2:
        record["ph_absolute"] = ph

    cycle = numbers(fields.get("Crop cycle", []) or [])
    if cycle:
        record["crop_cycle_days"] = cycle[:2] if len(cycle) >= 2 else [cycle[0], cycle[0]]

    for label, key in (
        ("Soil texture", "soil_texture"),
        ("Soil drainage", "soil_drainage"),
        ("Soil depth", "soil_depth"),
        ("Soil fertility", "soil_fertility"),
        ("Soil salinity", "soil_salinity"),
        ("Photoperiod", "photoperiod"),
        ("Climate zone", "climate_zone"),
        ("Life span", "life_span"),
        ("Category", "category"),
    ):
        values = fields.get(label)
        if not values:
            continue
        if len(values) >= 2:
            record[f"{key}_optimal"] = split_list(values[0])
            record[f"{key}_absolute"] = split_list(values[1])
        else:
            record[key] = split_list(values[0])

    return record


def cached_html(code: int) -> str | None:
    """Return the cached page for a code, or None when it is absent/too small."""
    path = HTML_DIR / f"{code}.html"
    if path.exists() and path.stat().st_size > 2000:
        return path.read_text(encoding="utf-8", errors="replace")
    return None


def download(code: int, *, use_cache: bool, offline: bool) -> str:
    """Cached page, or a re-download with retries. Never fabricates content."""
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    cached = cached_html(code)

    if use_cache and cached is not None:
        return cached
    if offline:
        raise OfflineCacheMiss(
            f"--offline was given but no cached HTML exists at {HTML_DIR / f'{code}.html'}. "
            "Run without --offline once, on a machine with internet access, to fill the cache."
        )

    url = BASE_URL.format(code=code)

    def attempt() -> str:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8", errors="replace")

    outcome = retry_call(
        attempt,
        attempts=REQUEST_ATTEMPTS,
        base_delay=REQUEST_BACKOFF_SECONDS,
        should_retry=_is_retryable,
    )
    if not outcome.ok:
        # urllib reports HTTP failures as HTTPError, whose message is unhelpful;
        # add the code so a 404 and a 503 are distinguishable at a glance.
        raise RuntimeError(
            f"{type(outcome.error).__name__}: {outcome.error} (after "
            f"{outcome.attempt_count} attempt(s), {outcome.total_delay_seconds}s of backoff)"
        ) from outcome.error
    if outcome.retried:
        print(
            f"     code={code} succeeded on attempt {outcome.attempt_count} "
            f"after {outcome.total_delay_seconds}s of backoff",
            file=sys.stderr,
        )

    body = str(outcome.value)
    (HTML_DIR / f"{code}.html").write_text(body, encoding="utf-8")
    return body


def _is_retryable(error: BaseException) -> bool:
    """Retry transport failures and 5xx/429 responses; never a 404."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code == 408 or error.code == 429 or error.code >= 500
    return isinstance(
        error,
        (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            ConnectionResetError,
        ),
    )


USAGE = """Usage: python scripts/fetch_ecocrop.py [--offline | --refresh]

  (no flag)  use the cached HTML where present, download what is missing
  --offline  never make a request; require every record to be cached already
  --refresh  ignore the cache and re-download every record

The parsed output is only written when every record was retrieved, so a bad run
cannot replace a good ecocrop.json with an incomplete one.
"""


def main(argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        print(USAGE, end="")
        return 0

    offline = "--offline" in argv
    refresh = "--refresh" in argv
    if offline and refresh:
        print("--offline and --refresh contradict each other", file=sys.stderr)
        return 2

    # --offline implies cache-only; --refresh implies cache-ignoring.
    use_cache = not refresh
    results: dict[str, object] = {}
    failures: list[str] = []
    messages: dict[str, str] = {}

    if offline:
        print("offline mode: every record must be served from the HTML cache\n")

    def fetch(code: int) -> dict[str, object] | None:
        try:
            page = download(code, use_cache=use_cache, offline=offline)
        except OfflineCacheMiss as error:
            messages[code] = str(error)
            print(f"MISS code={code} {error}", file=sys.stderr)
            return None
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as error:
            messages[code] = str(error)
            print(f"FAIL code={code} {error}", file=sys.stderr)
            return None
        return parse(page)

    for crop_id, meta in ECOCROP_CROPS.items():
        code = int(meta["code"])  # type: ignore[arg-type]
        parsed = fetch(code)
        if parsed is None:
            failures.append(crop_id)
            continue
        if not parsed.get("temp_absolute_c"):
            print(f"WARN {crop_id:10s} code={code} parsed no temperature range", file=sys.stderr)
            failures.append(crop_id)
        parsed["ecocrop_id"] = code
        parsed["scientific_name"] = meta["scientific_name"]
        parsed["source_url"] = BASE_URL.format(code=code)
        print(f"ok   {crop_id:10s} code={code:<7d} {meta['scientific_name']}")

        fallback_code = meta.get("fallback_code")
        if fallback_code is not None:
            fallback = fetch(int(fallback_code))  # type: ignore[arg-type]
            if fallback is None:
                failures.append(crop_id)
            else:
                parsed["fallback"] = {
                    "ecocrop_id": int(fallback_code),  # type: ignore[arg-type]
                    "scientific_name": meta.get("fallback_scientific_name"),
                    "fields": list(meta.get("fallback_fields", [])),  # type: ignore[arg-type]
                    "source_url": BASE_URL.format(code=int(fallback_code)),  # type: ignore[arg-type]
                    "data": fallback,
                }
                print(
                    f"     {crop_id:10s} + fallback {fallback_code} "
                    f"({meta.get('fallback_scientific_name')})"
                )

        if meta.get("note"):
            parsed["sourcing_note"] = meta["note"]
        results[crop_id] = parsed
        if not use_cache:
            time.sleep(REQUEST_DELAY_SECONDS)

    # Refuse to write a partial table. Overwriting a complete ecocrop.json with
    # whatever happened to download would silently thin the crop table, and the
    # builder downstream has no way to tell that it did.
    if failures:
        print(
            "\nNOT WRITTEN: " + ", ".join(failures) + " could not be retrieved, so "
            f"{PARSED_PATH.relative_to(BACKEND_DIR)} was left untouched.",
            file=sys.stderr,
        )
        if offline:
            print(
                "Run once without --offline on a machine with internet access to fill "
                "the HTML cache.",
                file=sys.stderr,
            )
        return 1

    payload = {
        "_meta": {
            "source": "FAO EcoCrop (Land & Water Division)",
            "source_url": "https://ecocrop.apps.fao.org/ecocrop/srv/en/cropList",
            "record_url_template": BASE_URL,
            "retrieved": time.strftime("%Y-%m-%d"),
            "retrieval_mode": (
                "offline (parsed entirely from cached HTML)"
                if offline
                else "refreshed from the live source" if refresh else "cached HTML where present"
            ),
            "retrieval_method": "scripts/fetch_ecocrop.py; raw HTML cached in app/data/sources/ecocrop_html/",
            "field_semantics": {
                "*_optimal": "EcoCrop 'Optimal' range - the crop performs well here",
                "*_absolute": "EcoCrop 'Absolute' range - the crop can survive here, but may underperform",
                "crop_cycle_days": "EcoCrop 'Crop cycle' min/max days",
                "soil_texture_*": "EcoCrop texture classes: heavy / medium / light / organic",
                "soil_drainage_*": "EcoCrop drainage descriptions, verbatim",
            },
            "caveat": (
                "EcoCrop ranges are global species-level envelopes, not Maharashtra- or "
                "variety-specific recommendations. They are a citable improvement over "
                "invented placeholders, but local package-of-practices data should still "
                "override them before any field pilot."
            ),
        },
        "crops": results,
    }
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    PARSED_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {PARSED_PATH.relative_to(BACKEND_DIR)}  ({len(results)} crops)")
    print("next: python scripts/build_crop_table.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
