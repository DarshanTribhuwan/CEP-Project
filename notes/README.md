# Lab notebook

This folder is a chronological research log for SAMPADA AI.

Keep entries short and honest:

- what was researched
- what was verified
- what failed
- what is still open
- next step

Mark uncertain items as `unverified` when needed.

Recent entries:

- 2026-09-12: read Chat.pdf fully; confirmed SAMPADA AI scope and MVP definition
- 2026-09-12: installed local Feynman under `feynman-local/`; verified local run path
- 2026-09-12: added `docs/research/FEYNMAN_PROJECT.md` and `.agents/skills/feynman/SAMPADA.md`

## 2026-09-30 — git init + explicit PoP coverage status

- **Verified (measured):** full suite 237/237 green on Python 3.14 (the 3.13
  install is gone from the machine); live server health shows
  `encryption_at_rest: enabled`, `data_mode: live` after moving
  `SAMPADA_ENC_KEY` into `backend/.env` (git-ignored; 16/16 mobiles and all
  farm locations re-encrypted at rest by the boot pass).
- **Diagnosed:** one crash on user startup — a stale OS-level
  `SAMPADA_ENC_KEY` from an earlier manual session overrode the correct `.env`
  value (loader prefers existing env vars). `_decode_key` now prints a
  truncated value plus the exact remediation (fresh terminal / unset var).
- **Repo:** `git init` at project root (was 5 months with no VCS). Root
  `.gitignore` excludes `.env`, `*.db`, `var/`, `node_modules/`,
  `feynman-local/`, `.agents/`. Baseline commit `73e4c08` (147 files).
- **Added:** `pop_status` (`verified`|`unavailable`), `pop_available`,
  `pop_sources`, and localized `pop_message` (en/mr/hi) on `GET /crops` —
  clients now distinguish sourced PoP from not-yet-sourced without inferring
  from null, per the data-governance review. Advisory only: scoring never
  reads PoP (verified by grep: zero `pop` references in the recommendation
  pipeline). Commit `e9d7217`.
- **Open:** MPKV publication for the 11 remaining crops; native-speaker review
  of the new mr/hi `pop.unavailable` strings (drafted, unverified); OTP
  delivery; rain calibration.

## 2026-09-17 — documentation drift audit

- **Verified (measured):** the five `SAMPADA_AI_800_Page_Documentation/*.docx`
  files carry **chapter titles that match** the design conversation but **bodies
  that do not**. 1504 paragraphs per part, 630 unique in Part 3 (58%
  duplication), one boilerplate sentence repeated 20×, zero tables, ~108k words
  total (~215 pages at 500 w/page) labelled "800 pages".
- **Verified (searched):** pilot plot, Crop Saturation Index, crisis mode, cold
  start, black swan, GPS, Agmarknet, Soil Health Card and market arrivals all
  appear **0 times** across all five documents. "Controlled Random Allocation &
  Oversupply Prevention" (Part 3, Ch. 9) contains none of the algorithm.
- **Failed / broken:** the pre-existing `sampada-ai/backend/app/main.py` imported
  six modules that did not exist, so the backend could not start. No
  `requirements.txt`, no database, no tests, no frontend.
- **Found (undocumented values):** `config.toml` contained
  `min_confidence_for_recommendation = 0.45` and
  `community_oversupply_threshold = 0.85`, neither of which appears in the source
  conversation. Retained, now documented as unvalidated defaults.
- Written up in `docs/spec/DOC_DRIFT_AUDIT.md`.
- **Decision:** keep the `.docx` files as reference structure only; build from a
  spec derived from `chat_extracted.txt`.

## 2026-09-17 — MVP backend built

- **Built:** FastAPI + stdlib-sqlite backend, 24 routes, 8 modules.
  83 tests passing. Verified starting under uvicorn and serving `/docs`.
- **Verified (live):** Open-Meteo weather works from this machine and returns
  real Pune forecast data — so weather is genuinely live, keyless.
- **Verified (unavailable):** the Agmarknet mandi feed needs a resource id and a
  `data.gov.in` key, neither of which is configured, so market prices return
  `503 data_unavailable` with an actionable reason. No prices are invented.
- **Deliberate omission:** no photo-based soil model was written, because no
  trained model exists here and any accuracy figure would have been fabricated.
  `/api/v1/providers/status` reports it as `not_implemented`.
- **Known unverified:** `app/data/crops.json` agronomic values are placeholders
  with plausible magnitudes, marked `UNVERIFIED` in the file itself. The Marathi
  and Hindi strings have not been reviewed by a native speaker.
- **Still open:** backtest against real held-out mandi data; calibrate the factor
  weights; replace crop reference values with cited ICAR / MPKV Rahuri figures;
  native-speaker review of mr/hi; encryption at rest; the Part 4 retraining and
  model-promotion pipeline.

## 2026-09-23 — live mandi prices enabled (data.gov.in / Agmarknet)

- **Verified (live, measured):** the Agmarknet daily feed (resource
  `9ef84268-d588-465a-a308-a864a43d0070`) is reachable and returns real records
  dated today. Field ids match the parser exactly (`state, district, market,
  commodity, variety, grade, arrival_date, min/max/modal_price`); dates parse as
  `dd/mm/YYYY`; personal key allows 500 records/call (sample key: 10).
- **Verified (live):** 15/16 commodity mappings in `AGMARKNET_COMMODITY` match
  the feed's spellings. The 16th, sugarcane, returns **0 records by design** —
  cane is bought by sugar mills at statutory prices, not auctioned in mandis —
  so the provider now reports that precise reason instead of a generic
  "no records". UNVERIFIED flag on the mapping removed.
- **Root-caused:** the first end-to-end attempt timed out. Not our code —
  `api.data.gov.in` (NIC) tarpits the default Python user-agent (urllib/httpx
  time out at 30s) while curl or any non-Python UA gets 200 in ~0.4s. Fix: a
  single honest default UA, `SAMPADA-AI/1.0`, set in `app/providers/http.py`
  (the one outbound-HTTP chokepoint), overridable per-call and via
  `providers.http.user_agent`.
- **Built:** stdlib `.env` loader in `app/core/config.py`. Secrets live in
  `backend/.env` (git-ignored, already in `.gitignore`); loaded from beside
  `SAMPADA_CONFIG`, so the test suite — which points at a temp config — never
  sees developer secrets. Exported variables win over the file.
- **Verified (end-to-end):** `GET /api/v1/market/prices` returns 200 with real
  records for onion (500), soybean (150), tur (500); sugarcane returns 503 with
  the sugar-mill reason. Fresh cache, so these were genuine live calls.
- **Fixed:** `tests/conftest.py` now strips the market resource id from the
  test config and asserts the unconfigured state explicitly — three tests
  briefly tried live calls once a real key existed on the machine. 117/117
  passing after the fix.
- **Unblocked:** historical prices for the backtest come from the variety-wise
  daily resource `35985678-0d79-46b4-9ed6-6f13308a1d24`, same key. Daily feed
  serves only the current day; the historical one is required for held-out
  evaluation.
- **Next step:** build the backtest — pull months of variety-wise history for a
  few Maharashtra mandis, run the §5.3 price estimator over it as if live, and
  score its ranges against held-out outcomes.

## 2026-09-23 — price-estimator backtest built and run (first real numbers)

- **Built:** `app/evaluation/backtest.py` (walk-forward runner + MAE/RMSE/
  coverage/sharpness) plus `scripts/fetch_history.py` and
  `scripts/run_backtest.py`. The runner reuses the production price cleaner
  (`_usable_prices`) and drives `estimate_price` through `DataResult.live`, so
  the tested path is the production path.
- **Data:** variety-wise daily feed (resource `35985678-...`), 5 crop/district
  series in Maharashtra, ~10k raw records in `var/history/` (git-ignored). The
  historical index is ordered oldest-first, so the fetcher reads `total` and
  pages **backwards** from `total - N`. Commodity spellings verified for this
  feed too (`Soyabean` 356k, `Arhar (Tur/Red Gram)(Whole)` 301k in MH; `Tur`
  alone = 0).
- **Method:** each cutoff sees only records dated ≤ cutoff (burn-in 28d);
  horizon 7d, steps 7d; target = mean of that day's modal prices. No
  interpolation: dates with no observation are counted in `missing_weeks` and
  never filled.
- **Results (real, first ever for this project):** soybean Pune/Nashik MAE
  ≈ 5–6% of price, coverage 78–80% (target ≈ 80%) — baseline is serviceable.
  onion Pune/Nashik MAE ≈ 22%, coverage 50–62% even with 36–52%-wide ranges —
  too volatile for a naive baseline; tur Pune MAE ≈ 17%, coverage 52%, only 46
  evaluable weeks. Pooled: MAE 302 INR on mean price 3012 (10.0%), coverage 68%.
- **Honest caveats:** 86% of target dates had no observation (irregular mandi
  reporting) — reported as missing, never filled. Onion series mixing market
  venues within a district may inflate dispersion. No train/test hyperparameter
  tuning exists to leak: every constant is a pre-registered unvalidated default.
- **Decision:** per-crop reliability is now measured, so `market_price` factor
  weighting can be made crop-aware (soybean-like: keep; onion-like: discount).
  Next: calibrate the recommendation factor weights using these results, then
  Maharashtra/ICAR package-of-practices data.
- **Tests:** 127 passing (10 new backtest tests, synthetic series only, no
  network). Suite stayed green throughout.

## 2026-09-23 — crop-aware market_price weighting (first calibrated weight)

- **Built:** `app/services/price_reliability.py` + `app/data/price_reliability.json`
  (generated only by `scripts/export_price_reliability.py` from the backtest
  report) + integration in `recommendation.py`.
- **Rule (single source of truth in the service, script imports it):**
  `reliability = clamp(pooled_coverage / 0.80, 0.5, 1.0)`, pooled per crop over
  evaluated weeks. Coverage is the direct measure of whether the estimator's
  band does its job. Result: soybean 0.99, onion 0.71, tur 0.65.
- **Behaviour:** measured crops get `configured_weight × reliability` (onion:
  0.25 → 0.18); unmeasured crops get × 0.75 (default) plus a `price_unmeasured`
  reason code in mr/hi/en — "not measured yet" must not look like "fully
  trusted". Missing/corrupt table = feature inert (weights revert, no claims).
- **Visibility:** the scaled weight is reported as `configured_weight` in the
  score breakdown, so the API always shows what actually applied; existing
  weight tests needed no changes because none pinned the raw config value.
- **Honesty preserved:** these are measurements of the backtested sample, not
  accuracy guarantees; the table's `_meta` carries source, rule, and caveat.
  Re-run the backtest after every estimator change, then re-export.
- **Tests:** 141 passing (14 new: rule, pinned table, inert degradation,
  recommendation integration — all synthetic, no network).

## 2026-09-23 — factor-weight validation: protocol built, weights untouched

- **Instruction followed:** validate, don't tune. First classify which factors
  have measurable outcomes; establish the time-based held-out protocol before
  anything else; keep the five defaults unchanged and labelled.
- **Classification (`app/evaluation/factor_evidence.py`):** `market_price`
  measured (evidence-informed); `farmer_history` partial (onboarding history
  exists, its link to outcomes does not); soil/weather/water/community
  unmeasured — each now states the outcome it needs and a minimum evidence bar
  (≥100 outcomes, contrasts within crop-season cells, ≥300 for community).
- **Protocol (`app/evaluation/heldout.py`):** one cutoff fixes training vs
  held-out; a structural leakage guard closes fitting functions over the
  training segment (no call path for held-out data; extra args raise);
  proposals are pre-registered on disk before scoring; each held-out window
  judges exactly one proposal and is consumed — repeated scoring is weight
  search by stealth, and it raises instead.
- **Enabler noted for later:** per-factor scores are NOT persisted on stored
  recommendations (only final blends + reasons). Validating soil/weather
  weights against recorded outcomes will require adding factor-score storage —
  schema change, deliberate deferral, recorded here so it is not a surprise.
- **Weights: zero changes.** The five defaults stay 0.25/0.20/0.15/0.10/0.05
  and are labelled UNVALIDATED DEFAULT — machine-readably at
  `GET /api/v1/evaluation/factor-status`, verified live.
- **Tests:** 156 passing (15 new: registry classification, window semantics,
  leakage guard, pre-registration, one-shot consumption, status API).

## 2026-09-23 — per-factor score persistence (schema v2, deferral closed)

- **Built:** the smallest backward-compatible migration — one nullable
  `factor_scores` JSON column on `recommendations`, added in place by an
  idempotent, row-preserving migration step (`db.py`, `SCHEMA_VERSION = 2`),
  following the existing `price_estimate`/`risk`/`reasons` pattern.
- **Snapshot contents:** per factor — score, quality, configured weight (post
  reliability-scaling, i.e. the weight that actually applied), effective
  weight, availability flag. Built from the same `Factor` objects that produced
  the blend, stored verbatim rather than recomputed, so later code changes
  cannot silently rewrite what the system said at the time.
- **Real bug caught by the fidelity test:** the persist block originally read
  a loop variable that still pointed at the *last* candidate scored, so every
  stored row would have carried the wrong crop's snapshot. Fixed by pairing
  options with their candidates explicitly (`zip`); the test now compares every
  stored factor against the live API response that generated it.
- **Backward compatibility:** rows written before the migration report
  `factor_scores: null` — a missing snapshot is not disguised as an empty one.
  API change is purely additive; all existing fields unchanged (regression
  tests pin them).
- **Weights: still zero changes** to the five unvalidated defaults.
- **Tests:** 163 passing (7 new: v1→v2 migration on a simulated old DB,
  idempotency, snapshot fidelity vs live response, effective-weight sum,
  scaled-weight recording, API regression).

## 2026-09-24 — input-context persistence (schema v3)

- **Instruction followed:** complete the evidence chain. Schema v2 stored what
  the system *concluded* (per-factor scores/weights); this adds what it
  *consumed* — the recommendation context future held-out validation needs,
  per the factor registry's own stated joins (forecast actually used,
  app_issued_area at recommendation time, farm profiles).
- **Built:** nullable `input_context` JSON column on `recommendations`
  (`SCHEMA_VERSION = 3`), idempotent in-place migration in `db.py`, same
  pattern as v2. Snapshot built in `recommendation.py` (`_snapshot_input_context`)
  from the exact objects that drove the blend — never recomputed: farm profile
  values (district/taluka/village/lat/lon/irrigation/water_source),
  `soil_estimate_id`, season, full candidate list, verbatim provenance blocks
  (data_source, staleness, cache age, retrieved_at) for the weather forecast
  and each candidate's market feed, and the community signal as it stood
  before persistence.
- **Capture-time subtlety (tested):** `app_issued_area` is computed before any
  row of the current set is saved, so the stored signal is the one the factors
  scored against — the first-ever recommendation records `available: false`
  with the empty issued area, not a post-save state that includes itself.
- **Immutability (tested):** editing the farm profile (PATCH village/latitude)
  leaves the stored context of earlier recommendations untouched; only new
  recommendations see the new values. Snapshot copies values, never references.
- **Honesty preserved:** pre-v3 rows report `input_context: null` — a missing
  context is not disguised as an empty one. API change purely additive; the v2
  factor-score fidelity guarantee is re-pinned in the new test file so it
  cannot silently regress.
- **Weights: zero changes** to the five unvalidated defaults.
- **Tests:** 173 passing (10 new: v2→v3 migration, idempotency, fidelity vs
  the generating response, per-crop market provenance, community capture time,
  farm-edit immutability, API regression, v2 fidelity re-check). Suite runtime
  on this machine is ~2 minutes — earlier "hangs" were timeouts, not stalls.

## 2026-09-24 — API console (browser client for checking the API)

- **Built:** `/console` — one static page (`app/console/index.html`, vanilla
  JS, no build step, no CDN) mounted in `main.py` via StaticFiles. Renders
  **only what the API returns**: status pill, timing, a table for list
  payloads, raw JSON beneath. No hardcoded values; language switchable per
  request.
- **Journey button** drives the full flow one-click: register → farm → soil
  estimate → weather (live) → mandi prices → recommendation → feedback →
  outcome → stored evidence view. A second farmer is not needed because the
  community signal is honestly `unavailable` on first recommendation and the
  stored context proves it (capture-time test already covers that path).
- **Honesty preserved in the console itself:** an unconfigured market key shows
  the API's `503 data_unavailable` verbatim rather than hiding it; the page
  ships no data of its own.
- **Real bug the browser run caught:** the journey originally posted feedback
  to an id captured from the POST /recommendations *response object* — which
  carries no stored ids. The first browser run showed exactly 7 cards and no
  feedback/outcome. Fix: read stored ids from GET /recommendations (the
  evidence store) before posting. The unit suite passed either way — only a
  real browser against a real server caught this.
- **Verified (browser, Playwright headless):** 8 cards rendered; statuses
  201/200/503/200/200/200/201/201 exactly as designed; stored rows show
  `factor_scores` + `input_context`; zero unexpected JS errors; screenshot at
  `var/console_smoke.png`. Server processes stopped cleanly by PID after each
  run (no blanket taskkill of python.exe).
- **Tests:** 183 passing (3 new in `tests/test_console.py`: page served, root
  advertises /console, console's register body accepted by the API schema).
  The Playwright script stays in `var/` (git-ignored, dev-only) — pytest
  already covers the contracts without a browser dependency.

## 2026-09-24 — farmer frontend `/app` (values-only client)

- **Built:** `/app` (`app/appui/index.html`, static, no build step) — the
  farmer-facing client requested: forms only, **labels not codes**. Dropdowns
  filled live from `/enums` + `/crops`; results rendered as localized labels,
  ₹ amounts, percentages, hectares. Unavailable sources render as notices
  with the API's own reason (one notice for the market source, not one per
  crop). Full journey: account → farm → soil → weather+prices →
  recommendation → feedback+outcome. Three UI languages, honest-limit notices
  translated too. Raw-JSON review stays in `/console` deliberately.
- **Process lesson:** three bugs, all found only by driving the real page —
  two orphan `)` from a sloppy find/replace (a **syntax error that kills the
  whole script silently**, page renders but sends nothing — exactly mimics
  "all endpoints failing"), an empty success banner (zero-height, invisible),
  and a success banner overwritten by the details block. `node --check` on
  the extracted script is now the fast first check for any JS edit; a
  bracket-scanner found what eyeballing missed.
- **Verified (browser, Playwright headless):** full journey passes; sentinel
  assertion confirms no API codes (soil_suitability, KHARIF, BOREWELL, …)
  appear anywhere on the page in en or mr; live mandi rows and weather table
  render; feedback 201, outcome 201. Screenshot `var/appui_smoke.png`.
- **Note:** the live Agmarknet feed worked during these runs (real Baramati
  rows) — market names are data, not codes; the smoke sentinel list was
  corrected accordingly.
- **Tests:** 187 passing (3 new in `tests/test_appui.py`: page served, root
  advertises /app, the enum/crop groups the UI consumes actually exist).

## 2026-09-24 — encryption at rest (schema v4) + model promotion (Part 4 complete)

The last two code-finishable threads from the spec, both landed.

**Encryption at rest (spec §8.5, the field-pilot blocker):**

- **Built:** `app/core/crypto.py` — AES-256-GCM via the audited `cryptography`
  package (the one new dependency; nothing hand-rolled). Scope exactly as the
  spec names it: `farmers.mobile`, farm location fields (village/taluka/district/
  latitude/longitude), `recommendations.district`, and the v3 `input_context`
  blobs (they embed village + coordinates). Random nonce per value, `enc1:`
  prefix so encrypted cells are recognisable, non-deterministic so the
  ciphertext column leaks no equality.
- **Searchability without decryption:** login-by-mobile and
  `app_issued_area`-by-district cannot query ciphertext, so equality goes
  through `HMAC-SHA256(key, "lookup-v1:" + value)` columns (`mobile_lookup`,
  `district_lookup`). Keyed is the point — mobile numbers live in a ~10^10
  space; an unkeyed hash column would be plaintext-equivalent. UNIQUE moved to
  the lookup column (ciphertext UNIQUE stays as defence in depth).
- **Migration = restart:** the at-rest pass runs on every boot and is idempotent
  (cells already `enc1:` are skipped). An operator adds `SAMPADA_ENC_KEY` to
  `.env`, restarts, and every existing plaintext row is encrypted in place with
  lookup hashes backfilled. Verified against a seeded plaintext v3 database:
  zero plaintext leaks after restart, login/farm-read/recommendation-read all
  keep working, second restart is a no-op.
- **Fail-closed mode:** `security.encryption_required = true` + no key → startup
  refused, not a silently unencrypted pilot. With no key, dev/demo plaintext
  mode keeps working and `/health` reports `encryption_at_rest: "disabled"`
  with a note saying exactly what that means. No silent key generation: a key
  that appeared without the operator storing it durably would be lost, and
  encrypted data with a lost key is gone.
- **Process catches (3 real bugs my tests found in my own work):** (1) I
  encrypted reads + migration but missed the INSERT — fresh `input_context`
  blobs were plaintext; (2) the lookup UNIQUE index lived only in the migration
  step, so databases recreated at v4 (`reset_db` keeps `user_version`) never got
  it — duplicate registration passed; indexes now live in `_SCHEMA` and
  `reset_db` stamps v0; (3) my first key resolver auto-generated a key into
  `var/`, which would have made "disabled" unreachable and tests order-dependent
  — removed before it could bite anyone. Also: a v1-shaped old table makes
  `CREATE TABLE IF NOT EXISTS` skip recreation, so an index referencing a
  not-yet-ALTERed column fails the whole script — indexes belong in the
  migration step, not the schema script.

**Model promotion (Part 4's missing flow):**

- **Built:** `app/evaluation/promotion.py` — joins the heldout protocol, the
  factor-evidence bars, and the v2 factor-score snapshots into a pipeline:
  propose → pre-register → score incumbent AND challenger on the same held-out
  outcomes exactly once → evidence-bar gate (`insufficient_evidence` below it,
  adoption on it needs a written override reason in the ledger) → human
  decision recorded (`pending/adopted/rejected`, `decided_by`, `decided_at`).
- **Fairness by construction:** identical rows, identical metric code, the
  challenger differs from the incumbent by exactly one factor; metrics are
  Kendall tau + top/bottom-half profit spread between the blended score (recombining
  the stored snapshots under each weight vector, renormalised exactly as the
  engine does) and realized profit. Ranking-quality check on the recommendation
  actually made — not a counterfactual the stored data cannot answer.
- **No automatic weights:** no function in the flow writes `config.toml`
  (pinned by a test); adoption is a ledger entry, applying it is a deliberate
  operator edit. Operator CLI: `scripts/promote_model.py evaluate|decide|show`
  (evaluate cross-checks `--old` against live config and refuses a mismatch).
  Read-only API: `GET /api/v1/evaluation/promotions` — deliberately no POST;
  nobody adopts a model over HTTP.
- **Honest limits:** one-shot windows mean each cutoff judges exactly one
  proposal; the metric floor is 10 scored outcomes (the registry bars —
  ≥100/200/300 — still gate on top); below the floor the metrics are reported
  as null, never as numbers that look decision-grade.

**Verification:** 206 tests passing (27 new: 14 encryption, 13 promotion), zero
regressions across the 187 existing — the encryption layer is transparent to
every pre-existing API behaviour. Requirements now pin `cryptography>=42,<46`.
Spec §6/§8.5/§9, README (new sections), and this notebook updated.

**Still open (not code-finishable here):** Maharashtra package-of-practices
(ICAR/MPKV), native-speaker review of mr/hi, `rain_behaviour_likelihood`
calibration, OTP delivery, secret-manager integration for key storage.

## 2026-09-25 — full API validation (live, end-to-end)

- **Instruction followed:** validate every endpoint through a real server with
  a fresh Bearer token; no weight or architecture changes; record exact
  statuses; fix only genuine implementation issues.
- **Method:** `var/api_validate.py` (dev-only, git-ignored) walks all surfaces
  in dependency order — reference/meta, auth gate (401 without token),
  register/login (201/200; duplicate 422; wrong PIN 401), farm CRUD +
  preferences + crop-history, soil estimate + latest, weather, market prices,
  recommendation → feedback → outcome, cross-tenant isolation (farmer B gets
  404 on farmer A's farm), logout revocation (token rejected after).
- **Result: 38/38 calls as expected, zero failures, zero code changes needed.**
  Weather live 200 (7-day Open-Meteo forecast, real rain total); recommendation
  200 with top `final_score=0.87`, `provisional=false`; outcome 201 with
  server-computed `profit_inr=37300`. Market prices honestly 503 on this run —
  the feed was reachable but returned zero Pune soybean rows today
  (`data_unavailable`: "no records for commodity 'Soyabean' in district
  'Pune'"), the designed honest-empty behaviour, not a failure.
- **Suite:** 206/206 unchanged.

## 2026-09-28 — package-of-practices transcription (the "needs external data" item, unblocked) + handoff guide

The one remaining item that was blocked on external data is now partially closed -
not with invented numbers, but with a transcription pipeline that can absorb the
real state publications as they are hunted down.

**Sources actually fetched and cached (verbatim, in the repo):**

- NFSM Ready Reckoner, Package of Practices for Oilseeds 2018 (nfsm.gov.in) -
  36pp; soybean, groundnut, sunflower, safflower sections.
- Directorate of Cotton Development, Cotton Ready Reckoner (cotton.dac.gov.in) -
  156pp; zone-wise, Maharashtra = Central zone.
- ICAR-IISR Soybean Extension Bulletin 2023 (icar-nsri.res.in) - 77pp; zone-wise
  Table 2 (sowing/seed rate/spacing) and Table 3 (NPKS) read for Central zone.

The MPKV/MahaBTC state PoP itself is still not located online as a PDF; the NFSM
and ICAR zone tables are national/zone publications that explicitly cover
Maharashtra, and they are the best sourced substitute. Every quote in the repo
carries file + line numbers, verified by grep against the cached text.

**Built:**

- `app/data/sources/pop_transcribed.json` - structured PoP values with verbatim
  quotes; `window_basis` records exactly what each source published (ICAR gives
  dates, NFSM month bands, cotton's start is the event "onset of monsoon" - the
  JSON never claims more precision than the source).
- `crops.json` schema: crops now carry an advisory `pop` block (sowing window,
  seed rate kg/ha, fertilizer NPKS, quotes). Untranscribed crops carry NO block;
  the API returns `pop: null` - silence means "not yet sourced", never "none".
- Scoring change, source-driven: soybean and safflower promoted to optimal on
  BLACK (vertisol) per NFSM text - this RESOLVES the soybean conflict that sat in
  `local_review_needed` since schema 2 (now 6 items, was 7). The `pop` block is
  advisory-only: the engine still reads seasons, not sowing months.
- `GET /crops` exposes the block + sources under provenance
  `package_of_practices` / `VERBATIM_QUOTE`.
- 9 new tests (`test_pop_data.py`): coverage honesty (transcribed vs not), every
  quote cites an existing repo file, override applied + provenance-stamped, old
  conflict flag gone, API surface. Two suitability tests updated to the corrected
  data (soybean-on-BLACK now 1.0, was the tolerated 0.7 the old source implied).
- `docs/api/FRONTEND_GUIDE.md` - the handoff pack for the frontend + voice
  assistant teammate: full endpoint table with observed statuses, the 503-is-
  honest-empty rule, pop-reading rules for voice, error shapes, isolation and
  encryption guarantees.

**Verification:** 235 tests passing (was 226). Live server check: soybean pop
window 06-20 -> 07-05, seed rate 55-65 kg/ha, NPKS 25:100:50:50, BLACK optimal,
6 quotes attached; rice pop is null as designed.

**Still open (unchanged):** PoP for the 11 remaining crops (cereals, pulses,
onion, sugarcane, rice - needs MPKV/state publication), native-speaker review of
mr/hi, rain_behaviour_likelihood calibration, OTP delivery, secret-manager
integration.

## 2026-09-25 — key rotation (the hygiene loop closed) + retention policy

The two items the §8.5 closure explicitly left open, both landed.

**Key rotation:**

- **Built:** multi-key support in `app/core/crypto.py` — `SAMPADA_ENC_KEYS`
  (ordered, comma-separated; FIRST key encrypts new writes, ALL keys are tried
  for decryption), with `key_count()` surfaced at `/health` as
  `encryption_key_count`. Setting `SAMPADA_ENC_KEY` and `SAMPADA_ENC_KEYS`
  together is refused at first use (loud, not "one of them wins").
- **No key-id bytes, deliberately:** GCM's auth tag already answers "which key
  encrypted this cell?" with certainty; a stored positional id would break the
  moment the operator removes an old key and the list shifts. A cell under a
  dropped key fails loudly, never decodes to junk.
- **Rotation = the same at-rest pass:** the boot-time pass now normalizes every
  encrypted cell to the ACTIVE key (`decrypt_if_current` skips cells the first
  key already decrypts; cells under a previous key are re-encrypted and their
  lookup hashes re-derived). Enablement and rotation are literally one code
  path; second restart verified a no-op. The old behaviour — skipping every
  already-`enc1:` cell — would have left rows under the old key forever and
  killed the data when the old key was removed; the rotation test caught this
  in the red phase, exactly as designed.
- **Rotation window is safe:** login-by-mobile and the district aggregation
  match a hash made under ANY configured key (`crypto.lookup_hashes`, `IN`
  queries), so between the config change and the end of the pass farmers are
  never locked out.

**Retention policy:**

- **Built:** `app/core/retention.py` + `scripts/apply_retention.py`. Three
  opt-in classes — `outcomes_months`, `recommendations_months`,
  `auth_tokens_days` in `[retention]` (config.toml) — each defaulting to
  0 = keep forever. Purging is operator-invoked only: dry-run by default,
  `--apply` to execute, refused outright when no threshold is configured.
- **Honest cascade shape:** an outcome whose recommendation was purged
  SURVIVES with `recommendation_id = NULL` — the season happened; only the
  pointer to the aged-out statement is lost. Feedback/predictions go with
  their recommendation (that is what they are evidence of). Soil estimates and
  crop history do not age. Boundary is strict (`created_at < cutoff`), so a
  monthly job never races a same-instant row into deletion.
- **Visibility:** `/health` reports the policy verbatim with a note naming the
  purge tool; the dry run prints the full blast radius (feedback and
  predictions that go with aged-out recommendations).

**Process note (own bugs, red phase):** (1) the at-rest skip made rotation a
no-op — found by the remove-old-key test before it could ship; (2) `purge()`
returned policy keys instead of count keys on the zero path; (3) the retention
CLI test initially shelled out to a subprocess, which reads the real
`config.toml` rather than the monkeypatched settings — exercised in-process
via `runpy` instead.

**Verification:** 226 tests passing (20 new: 12 rotation, 8 retention), zero
regressions. Spec §8.5, README, and this notebook updated.

**Now genuinely still open (needs people/data/services, not code):**
Maharashtra package-of-practices (ICAR/MPKV), native-speaker review of mr/hi,
`rain_behaviour_likelihood` calibration, OTP delivery, secret-manager
integration for key storage.
