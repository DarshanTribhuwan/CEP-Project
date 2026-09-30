# SAMPADA AI — backend

Agricultural decision-support API. FastAPI + SQLite (stdlib `sqlite3`, no ORM).

> **This system returns estimates with confidence levels, never guarantees.**
> Where a source is unavailable it says so explicitly and returns HTTP 503
> rather than substituting something plausible.

## Run it

```bash
cd sampada-ai/backend
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000
```

- **Farmer frontend: http://127.0.0.1:8000/app** — values only, labels not codes
- **API console: http://127.0.0.1:8000/console** — click *Run full journey*
- Docs: http://127.0.0.1:8000/docs
- Source status: http://127.0.0.1:8000/api/v1/providers/status
- The SQLite file is created on first start at `backend/sampada.db` (git-ignored).

## Farmer frontend (/app)

`/app` (`app/appui/index.html`, static, no build step) is the values-only
client: forms whose dropdowns are filled from `/enums` and `/crops`, results
displayed as **localized labels, ₹ amounts, percentages and ha — never API
codes**. Unavailable sources render as explicit notices with the API's own
reason. The full farmer journey is six steps: account → farm → soil → live
weather & mandi prices → recommendation → feedback + season outcome (the
outcome is what the evaluation layer later learns from).

## API console

`/console` is a single static page (`app/console/index.html`, vanilla JS, no
build step) that calls the live API and renders every response verbatim —
status code, timing, a table for list payloads, and the raw JSON underneath.
It ships **no data of its own**: dropdown-free, hardcoded nothing, so what you
see is exactly what the API said, including honest `503 data_unavailable`
errors for sources that are not configured.

- **Run full journey** drives the whole flow one-click: register → create farm →
  soil estimate → live weather → mandi prices → recommendation → feedback →
  outcome → stored evidence (showing the `factor_scores` and `input_context`
  snapshots the evaluation layer will later consume).
- Individual endpoint buttons run single calls; `{farm_id}` in a path is
  filled from the session context.
- Browser-only verification lives at `var/console_smoke.py` (Playwright,
  git-ignored); the served-page and registration-body contracts are pinned by
  `tests/test_console.py`.

## Test it

```bash
cd sampada-ai/backend
python -m pytest
```

235 tests. They use a temporary database and **fake every external provider**, so
the suite never touches the network and never passes because a live call
happened to succeed.

## What works out of the box

| Capability | State |
|---|---|
| Registration, sessions, farm profile | working |
| Multilingual farmer-facing text (mr / hi / en) | working |
| Soil evidence fusion with confidence | working |
| Weather | **live** via Open-Meteo (keyless) |
| Mandi prices | **live** via data.gov.in / Agmarknet (personal key in `.env`) |
| Retry, response cache, offline fallback | working (see *Working offline* below) |
| Recommendation with reasons, risks, confidence | working |
| Feedback and season-outcome recording | working |
| Geographic soil lookup | **unavailable** until you supply a dataset |
| Photo soil model, news, transport, alternative income | **not implemented** |

## Enabling the optional data sources

Mandi prices (Agmarknet via data.gov.in) are **enabled on this machine**: the
resource id is in `config.toml` and the personal key lives in `backend/.env`
(git-ignored). On a fresh deployment:

```bash
# 1. get a free key from https://data.gov.in (My Account -> API Key)
# 2. resource id is already set in config.toml
# 3. put the key in backend/.env (never commit it):
#    SAMPADA_DATA_GOV_IN_KEY=your-key-here
#    (an exported environment variable also works and wins over the file)
```

The daily feed serves only the current day, 500 records per call. Historical
prices for backtests come from resource `35985678-0d79-46b4-9ed6-6f13308a1d24`
(variety-wise daily) — the same key works for both.

`api.data.gov.in` delays the default Python user-agent until the client times
out; `app/providers/http.py` therefore sends `SAMPADA-AI/1.0` on every outbound
request (configurable under `providers.http.user_agent`).

Geographic soil lookup:

```toml
[providers.soil_geo]
enabled = true
dataset_path = "data/soil_datasets/maharashtra_soil.geojson"
soil_type_attribute = "soil_type"
```

The Agmarknet commodity-name mapping in `app/providers/market.py` was
**verified against the live feed on 2026-09-23**: 15 of 16 crops matched their
Agmarknet commodity string. Sugarcane is confirmed absent from the daily APMC
feed (cane is bought by sugar mills at statutory prices), so it reports a
specific unavailability reason instead of a generic one.

## Working offline, and retrying

Every outbound request goes through `app/providers/http.py`, so weather and mandi
prices behave identically:

| Situation | What happens |
|---|---|
| Source responds | response stored on disk, tagged `live` |
| Transient failure (timeout, refused connection, 429, 5xx) | retried up to `providers.http.retries` times with exponential backoff |
| Permanent failure (404, rejected API key) | **not** retried — waiting does not fix it |
| Still unreachable, cached response exists | the last real response is served, tagged `cached`, with its age and a note that it is **not current** |
| Unreachable, nothing cached | `503 data_unavailable` with an actionable reason |
| Cache older than the staleness limit | `503` — past a point, an old value is worse than no value |

Forcing offline, for a machine with no route out:

```bash
export SAMPADA_OFFLINE=1     # no outbound request is attempted at all
```

`SAMPADA_OFFLINE` overrides `config.toml` and is read per request, so it can be
toggled without a restart. `GET /api/v1/providers/status` reports the current
state under `transport`, including how many responses are cached and how old they
are.

**A cached value is never presented as a live one.** A stale response carries
`data_source: "cached"`, `is_stale: true` and `cached_age_seconds`; a
recommendation built on one lists it under `stale_inputs`, adds a `weather_stale`
or `price_stale` reason, and leads its `disclaimers` with a notice. Cached
payloads are stored verbatim — the cache never interpolates, extends or repairs a
response, and a corrupt entry is deleted rather than fixed.

Staleness windows in `config.toml`: `weather.max_stale_minutes` (720) and
`market.max_stale_minutes` (2880). Four days is the ceiling for prices on purpose.

### Rebuilding the crop table offline

The knowledge base is generated from cached sources and needs the network only
once, ever:

```bash
python scripts/fetch_ecocrop.py             # cached HTML where present, fetch the rest
python scripts/fetch_ecocrop.py --offline   # no requests at all; requires the cache
python scripts/fetch_ecocrop.py --refresh   # re-download every record
python scripts/build_crop_table.py          # offline; merges EcoCrop + FAO-56
```

`fetch_ecocrop.py` retries each record, never aborts the run on a single failure,
and **refuses to overwrite `ecocrop.json` when any record is missing** — a partial
download cannot quietly thin the crop table.

## Encryption at rest

Mobile numbers and farm locations (village, taluka, district, coordinates — and
the recommendation input-context blobs that embed them) are stored
**AES-256-GCM encrypted** (schema v4, `app/core/crypto.py` via the audited
`cryptography` package). Lookups that need equality without decryption — login
by mobile, community aggregation by district — go through a keyed
`HMAC-SHA256` hash column, so the plaintext is never searchable from the
database alone.

```bash
# enable: put a base64 32-byte key in backend/.env (git-ignored):
#   SAMPADA_ENC_KEY=<python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())">
# then restart — every existing row is encrypted in place on boot.
```

Without a key the system keeps working in plaintext for development and
`GET /api/v1/health` reports `encryption_at_rest: "disabled"` with a note saying
exactly that — the state is visible, never pretended. Setting
`security.encryption_required = true` in `config.toml` flips this to
**fail-closed**: startup is refused rather than piloting on unencrypted personal
data. There is no silent key generation: a key that appears without the operator
storing it durably would be lost, and encrypted data with a lost key is gone.

### Key rotation

Rotation is the same operation as enablement, with a key list. Generate a new
key, put it **first** in `SAMPADA_ENC_KEYS` (comma-separated), keep the old one
listed, and restart:

```bash
# backend/.env — first key encrypts new writes; all listed keys decrypt
SAMPADA_ENC_KEYS=<new-key>,<old-key>
```

On boot the at-rest pass re-encrypts every cell under the first key and
re-derives the lookup hashes; a second restart is a no-op. Logins and the
district aggregation match a hash made under **any** configured key, so the
rotation window never locks farmers out. When `/health` shows
`encryption_key_count: 1`, the old key has done its job and can be dropped from
the list. Setting `SAMPADA_ENC_KEY` and `SAMPADA_ENC_KEYS` together is refused
at startup — configure exactly one. A cell encrypted under a key that is no
longer listed fails loudly (GCM auth), it never decodes to junk.

## Retention policy

Evidence deletion is **opt-in and operator-invoked** — never background noise.
Configure thresholds in `config.toml`:

```toml
[retention]
outcomes_months = 36          # recorded season results (0 = keep forever)
recommendations_months = 24   # stored statements; their feedback goes with them
auth_tokens_days = 30         # dead sessions
```

Every threshold defaults to 0 = keep forever: a deployment that never sets
`[retention]` never deletes a row. The purge itself is deliberate:

```bash
python scripts/apply_retention.py            # dry run: prints what is expired
python scripts/apply_retention.py --apply    # executes the deletions
```

An outcome whose recommendation was purged **survives** with its
`recommendation_id` set to NULL — the season happened; only the pointer to the
aged-out statement is lost. Soil estimates and crop history do not age (they
are farm-level reference data that keep informing the engine). The configured
policy is visible at `GET /api/v1/health` under `retention`.

## Model promotion (Part 4)

The held-out evaluation protocol (`app/evaluation/heldout.py`) now has the
promotion flow on top of it (`app/evaluation/promotion.py`): a weight change is
proposed and pre-registered, the incumbent and the challenger are scored on the
same held-out outcomes **once** (from the stored factor-score snapshots — no
recomputation), the evidence bar from `factor_evidence.py` gates the result, and
adoption is a human decision recorded in a ledger. No code path writes weights
to `config.toml`.

```bash
python scripts/promote_model.py evaluate --factor weather_match --new 0.30 \
    --rationale "..." --cutoff 2026-09-01
python scripts/promote_model.py decide --fingerprint <fp> --decision adopted --by "darshan"
python scripts/promote_model.py show
```

`GET /api/v1/evaluation/promotions` exposes the ledger read-only.

## Demo mode

```toml
[data]
mode = "sample"
```

This is the **only** way sample data can enter the system. Every response derived
from it is tagged `is_demo: true`, carries a DEMO banner, and
`/api/v1/providers/status` reports `demo_data_active: true`. The bundled
`app/data/sample/market_sample.json` is explicitly labelled *"THIS IS INVENTED
DATA."* Never present it as a real price.

## Architecture rules

1. **Only `app/providers/` may call an external API.** Everything else consumes
   a `DataResult` carrying its provenance (`live`, `cached`, `derived`,
   `farmer_reported`, `sample`, `unavailable`). `cached` is distinct from `live`
   on purpose: it means *real, but retrieved earlier*, and `is_stale` says whether
   it is still current.
2. **Only `app/repositories/` writes SQL.**
3. **Language affects presentation only.** Farmer answers are stored as
   standardized codes (`irrigation_type = "PARTIAL"`), never Marathi text, so the
   decision layer is language-independent. Farmer-facing strings live in
   `app/locales/`, so nothing is hardcoded in the client.
4. **Missing data is `None`, never a neutral default.** The recommendation engine
   redistributes weight and reports the effective weight it used.
5. **Every prediction is stored with the model version that produced it**, so
   "did it improve?" can be answered later instead of asserted.

```
app/
  core/          config, sqlite, i18n, error envelope
  locales/       mr.json  hi.json  en.json
  data/          crops.json  soil_types.json  sample/
  providers/     weather · market · soil_geo     <- all external I/O
  schemas/       pydantic contracts
  repositories/  SQL
  services/      soil · price · suitability · community · recommendation
  api/           routers (thin: validate, authorise, delegate)
```

## Honest limitations

- **Crop and soil reference tables are `PARTIALLY_VERIFIED`, and say so per
  field.** The values that drive a recommendation are cited: temperature,
  rainfall, pH and soil ranges from FAO EcoCrop, durations and crop coefficients
  from FAO-56 Tables 11–12 (see `_meta.sources` in `app/data/crops.json`).
  `seasons`, `investment_level` and `major_risks` remain curated judgement and are
  labelled as such rather than mixed in silently. EcoCrop publishes *global
  species-level* envelopes, not Maharashtra package-of-practices data; the first
  local tranche is now transcribed verbatim (`app/data/sources/pop_transcribed.json`,
  NFSM 2018 + DCD Cotton Ready Reckoner + ICAR-IISR 2023) as advisory `pop`
  blocks on soybean, cotton, groundnut, sunflower and safflower, with the
  NFSM-documented vertisol preference applied to soybean/safflower. The other 11
  crops still await the state (MPKV) publication, and
  `_meta.local_review_needed` lists every remaining known conflict, including the
  two cases where a field is deliberately left unusable (rice has no sourced
  optimal soil texture, so its soil factor is reported unavailable rather than
  scored).
- **The Marathi and Hindi strings have not been reviewed by a native speaker.**
  `GET /api/v1/i18n/status` reports `reviewed_by_native_speaker: false`.
- **Accuracy is now measured, not claimed.** The price estimator has been
  walk-forward backtested per series (`app/evaluation/backtest.py`, report under
  `var/history/`): soybean ≈ 5–6% weekly MAE with 78–80% range coverage; onion ≈
  22% MAE with 50–62% coverage. No single accuracy figure is claimed — error
  depends on crop and mandi, and the other models remain unbacktested.
- **Auth is development-grade.** Mobile + PIN, PBKDF2-hashed. Production needs
  OTP or another verified second factor. Mobile numbers and farm locations are
  now **encrypted at rest** (schema v4); what remains open is formal key-management
  hygiene (rotation, HSM/secret-manager integration) beyond the env-var/key-file
  source.
- **The community signal is app-scoped only.** It is relative crowding among
  this app's own users, not district-level oversupply, and it says so on every
  response.
- **Factor weights are unvalidated defaults** taken from the design discussion.

Full specification: `../../docs/spec/MVP_SPEC.md`.
Documentation drift audit: `../../docs/spec/DOC_DRIFT_AUDIT.md`.
