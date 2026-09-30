# SAMPADA AI — MVP specification

**Derived from:** `chat_extracted.txt` (full text of `Chat.pdf`, 6,342 lines).
**Supersedes for build purposes:** `SAMPADA_AI_800_Page_Documentation/` — see
`DOC_DRIFT_AUDIT.md` for why.
**Status:** implemented in `sampada-ai/backend` (226 tests passing).

---

## 1. What the system is

A multilingual agricultural decision-support platform for farmers in
Maharashtra. It produces **estimates with confidence levels and explanations**,
never guarantees, and it reports missing data as *unavailable* rather than
substituting a plausible value.

The objective the design conversation converged on, after it was challenged
repeatedly, is **not** "maximise this farmer's profit":

> minimise avoidable loss and reduce the probability and impact of overproduction
> **among participating farmers**.

## 2. The five principles the source conversation fixed

1. **Minimise avoidable losses.** Balance expected profit against risk, market
   volatility, weather, production, transport and community-level risk.
2. **Community-aware.** A recommendation repeated to every farmer destroys the
   price that made it attractive. The system must reason about its own influence.
3. **Continuous improvement.** Record prediction → recommendation → farmer
   decision → real outcome, attribute failures to the responsible module, and
   deploy an improved model only after it beats the incumbent on unseen data.
4. **Explainability.** Every recommendation answers "why", with the evidence it
   rests on.
5. **The farmer decides.** Accept / reject / ignore / try on a small area.

## 3. Scope of this MVP

Implemented and running:

| Area | State |
|---|---|
| Registration and sessions | Mobile + PIN, PBKDF2-hashed, hashed bearer tokens |
| Multilingual layer | mr / hi / en; language affects presentation only |
| Farmer + farm profile | Area normalised to hectares; irrigation, water source, location |
| Onboarding question set | Served localized from locale files; no hardcoded farmer-facing text |
| Soil intelligence | **Multi-source evidence fusion** with explicit confidence maths |
| Weather intelligence | **Live** Open-Meteo forecast; unavailable state when unreachable |
| Market intelligence | **Live** Agmarknet via data.gov.in (verified 2026-09-23, commodity mapping 15/16 verified; sugarcane confirmed absent from the daily APMC feed) |
| Price estimation | Versioned naive seasonal baseline returning a **range** |
| Crop suitability | Soil, water, season and weather scoring |
| Crop recommendation | Six-factor weighted blend, renormalised over available factors; market weight scaled by per-crop measured reliability |
| Community signal | App-scoped oversupply indicator with its limitation attached |
| Explanation engine | Reason codes with evidence class per claim |
| Risk engine | Per-category risk with unmodelled categories returned as `null` |
| Pilot plot | Small-area trial for farmers with no recorded history |
| Feedback + outcomes | Stored for later evaluation, with model versions |
| Learning loop | **Outcome + prediction storage only** — see §6 |

Deferred deliberately, and **reported as `not_implemented`** by
`GET /api/v1/providers/status`:

- photo-based soil classification (no trained model exists in this repository)
- news / policy monitoring
- transport and shared-logistics optimisation
- alternative-income recommendation
- automated retraining, backtesting and model promotion

## 4. Architecture

    app/
      core/          config, sqlite persistence, i18n, error envelope
      locales/       mr.json, hi.json, en.json
      data/          crops.json, soil_types.json, sample/
      providers/     ALL external I/O; real-vs-unavailable decided here
      schemas/       pydantic contracts
      repositories/  the only layer that writes SQL
      services/      the decision logic
      api/           thin routers

Maps onto the four team parts from the source conversation:

- **Part 1** → `api/` + `core/i18n.py` + `locales/`
- **Part 2** → `providers/` + `data/` + `core/db.py`
- **Part 3** → `services/`
- **Part 4** → `repositories/recommendations.py` outcome/prediction tables,
  plus the evaluation surface still to build

### The rule that keeps it honest

**Nothing outside `app/providers/` may call an external API.** Every provider
returns a `DataResult` carrying its `Provenance`:

`LIVE` · `DERIVED` · `FARMER_REPORTED` · `SAMPLE` · `UNAVAILABLE`

A missing source returns `UNAVAILABLE` with a reason the API surfaces to the
farmer. It never returns a default that looks like data.

## 5. Algorithms, stated so they can be attacked

### 5.1 Soil evidence fusion

    mass[t]    = Σ_s w_s·p_s[t] / Σ_s w_s        over sources that supplied evidence
    coverage   = min(1, Σ_s w_s)
    confidence = clamp(max(mass) · coverage, 0, 0.95)
    disagreement between the strongest sources → × 0.85 with a note

Consequences: a lab report alone saturates at 0.95; a farmer's own answer alone
gives 0.55 — **below the 0.60 threshold**, so the system asks for a soil test
instead of pretending. No evidence returns `top_soil_type: null`, never a
default.

### 5.2 Recommendation blend

    factors: soil_suitability · weather_match · market_price
             water_availability · farmer_history · community_saturation

Configured weights (0.25 / 0.20 / 0.25 / 0.15 / 0.10 / 0.05) come from the
design conversation and are **unvalidated defaults**. A factor with missing data
scores `None`, its weight is redistributed proportionally, and its effective
weight is returned as 0 so a reviewer can see what really drove the answer.

**Crop-aware market weight (added 2026-09-23).** The `market_price` weight is
scaled per crop by that crop's *measured* price-signal reliability from the
backtest (§5.3): `reliability = clamp(pooled_coverage / 0.80, 0.5, 1.0)`, pooled
per crop over evaluated weeks. Measured so far: soybean 0.99, onion 0.71, tur
0.65 (soybean ≈ full weight; onion ≈ 0.18 instead of 0.25). Crops not yet
backtested get 0.75 × the configured weight and a `price_unmeasured` reason —
"not measured yet" is not "fully trusted". The table is generated only by
`scripts/export_price_reliability.py` from the backtest report
(`app/data/price_reliability.json`); a missing table makes the feature inert
(weights revert to the configured values, nothing is claimed). The scaled
weight is reported as `configured_weight` in the score breakdown, so the API
always shows the weight that actually applied.

    confidence = coverage × mean_weighted_quality
    coverage   = available configured weight ÷ total configured weight

Below 0.45 the response is marked `is_provisional`. When the top options score
within 0.01 of each other the response says so in `ranking_notes` rather than
presenting an arbitrary order as a ranking.

### 5.3 Price estimation

Median of recent prices, damped momentum trend, range from the coefficient of
variation. Output is always a **range plus a confidence**. With fewer than three
records it returns nothing at all.

**Backtested 2026-09-23** (`app/evaluation/backtest.py`, walk-forward, 1-week
horizon, 7-day steps, history-only inputs at every cutoff). Measured on real
Agmarknet variety-wise history for five crop/district series in Maharashtra:

- soybean (Pune, Nashik): MAE ≈ 5–6% of price a week out, range coverage 78–80%
  against a ~80% target — the baseline is serviceable here;
- onion (Pune, Nashik): MAE ≈ 22%, coverage 50–62% despite 36–52%-wide ranges —
  too volatile for the naive baseline to carry weight in a recommendation;
- tur (Pune): MAE ≈ 17%, coverage 52%, few evaluable weeks.

Consequence: per-crop reliability now measured, so the market-price factor can
be weighted per crop instead of uniformly. 86% of one-week-ahead target dates
had no mandi observation; they are counted as missing, never filled. No single
accuracy figure is claimed — the number depends on crop and mandi.

### 5.4 Community signal — the narrow, honest version

The conversation's conclusion was that the system **cannot** prevent
overproduction, because it does not control non-users and cannot see what anyone
plants. So the module computes only what the system genuinely owns:

    app_issued_area[crop] = hectares this app recommended for that crop,
                            in that district, for that season

This is reported with three standing caveats attached: it covers only farms
using this app, it is relative crowding rather than verified market oversupply,
and issued area is not planted area. With no history it reports `unavailable`
and the factor is dropped.

### 5.5 Soil and temperature scoring, from cited ranges

`app/data/crops.json` is generated by `scripts/build_crop_table.py`. Each crop
carries two soil lists, taken from EcoCrop's *optimal* and *absolute* texture
ranges, and they score differently:

    soil_types_optimal    the crop performs well here        1.0
    soil_types_tolerated  the crop can be grown here         0.7
    unlisted                                                 0.0
    optimal is null       the source publishes no optimal texture for this crop,
                          so the soil factor is reported unavailable and its
                          weight redistributes

Rice is the `null` case: paddy is grown in standing water, so EcoCrop records no
optimal texture for it. Scoring it 0.0 would punish the crop for a gap in the
source, so nothing is claimed instead. The rule this replaced — award credit when
a soil shared a `water_retention` class with a preferred one — was the project's
own invention, and it has been removed.

Temperature is scored against the optimal band, falling back to the absolute
(survival) band, as the fraction of forecast days inside it. Forecast rainfall is
deliberately **not** converted into a water requirement: doing that properly needs
reference evapotranspiration, which is not implemented.

## 6. What "learning" means here, honestly

The MVP **stores** what a learning loop needs: every recommendation with its
model versions, factor scores and reasons; the farmer's response and why; the
season's actual yield, cost, price and profit; and the price prediction that was
made. `app_issued_area` already consumes that history.

It does **not** retrain anything, and the source conversation explicitly
required that a candidate model never replace a production model without passing
a held-out comparison.

**Validation infrastructure (added 2026-09-23), ahead of the data:**

- `app/evaluation/factor_evidence.py` — per-factor evidence registry. Exactly
  one factor (`market_price`) is `measured`; `farmer_history` is `partial`;
  the rest are `unmeasured`, each stating the outcome it needs and the minimum
  evidence bar (≥100 outcomes, contrasts within crop-season cells, etc.) before
  a weight change may even be proposed. Exposed at
  `GET /api/v1/evaluation/factor-status` with every unvalidated weight labelled.
- `app/evaluation/heldout.py` — the time-based split protocol with a structural
  leakage guard: one `cutoff_date` fixes training vs held-out; fitting
  functions are closed over the training segment only; every weight proposal is
  pre-registered on disk before scoring; each held-out window judges exactly
  one proposal and is then consumed, so repeated scoring cannot become weight
  search. Adoption is a human decision, never automatic.

The guard-rails exist **before** the outcome volume does — retrofitting a split
after seeing which weights look good is how self-deception enters a pipeline.

**Evidence capture (schema v2, 2026-09-23):** stored recommendations now carry
a per-factor snapshot — score, quality, configured weight (post
reliability-scaling), effective weight and availability — taken from the same
`Factor` objects that produced the blend. Future outcome evaluation therefore
sees exactly what drove each stored answer, with no recomputation to drift.
Pre-v2 rows report `factor_scores: null`; the field is purely additive.

**Model promotion (added 2026-09-24).** The flow joins the existing pieces into
a pipeline: `app/evaluation/promotion.py` pre-registers a proposal, scores the
incumbent and the challenger on the same held-out outcome segment exactly once
(from the stored v2 factor-score snapshots — no recomputation that could drift
from what the farmer saw), applies the per-factor evidence bar from the registry
(`insufficient_evidence` below it), and records the human adoption decision in a
ledger (`var/evaluations/promotions.jsonl`). Two structural guarantees: a window
consumes on one proposal (the heldout one-shot guard holds end-to-end), and no
code path writes weights to `config.toml` — adoption is a ledger entry; applying
it is a separate, deliberate operator edit (`scripts/promote_model.py`).
Exposed read-only at `GET /api/v1/evaluation/promotions`. The metric is
ranking quality of the recommendation actually made (Kendall tau and
top/bottom-half profit spread between blended score and realized profit) — a
comparison of weightings, not a counterfactual of what another crop would have
earned.

**Input-context capture (schema v3, 2026-09-24):** what the system *consumed* is
now stored alongside what it *concluded*. Each stored recommendation carries an
`input_context` snapshot taken from the objects that drove the blend at
generation time: the farm profile values the factors were scored against
(district, taluka, village, coordinates, irrigation, water source), the soil
estimate row that fed the soil factor, the season, the full candidate list, and
the provenance block of the weather forecast and of each candidate's market
feed — data source, source name, staleness, cache age, retrieval time. The
community signal is frozen **as it stood before persistence**, i.e. the state
the factor actually scored against, not a post-save state in which the
recommendation itself has already populated `app_issued_area`. Two
recommendations with identical scores but different input provenance are not
the same evidence; the registry's stated joins ("scored against the forecast
the recommendation actually used", "app_issued_area at recommendation time",
"outcomes joined to farm profiles") are now directly queryable. Pre-v3 rows
report `input_context: null`; the field is purely additive. Weights: still zero
changes to the five unvalidated defaults.

## 7. Data sources: real, or explicitly absent

| Source | State |
|---|---|
| Weather | **Live** — Open-Meteo, keyless, verified reachable |
| Mandi prices | **Live** — Agmarknet daily feed, verified 2026-09-23; history for backtests comes from the variety-wise daily resource with the same key |
| Geographic soil | **Unavailable** until a GeoJSON dataset is configured |
| Photo soil model | **Not implemented**; nothing is claimed about its accuracy |
| Crop / soil reference tables | Present, `PARTIALLY_VERIFIED`; decision-driving fields cited (FAO EcoCrop, FAO-56 Tables 11–12), judgement fields labelled per field |

`data.mode = "sample"` is the **only** path for demo data to enter. Everything it
produces is tagged `is_demo: true` with a DEMO banner in its notes. Default is
`live`.

### 7.1 Resilience: retry, cache, offline

All outbound I/O goes through `app/providers/http.py`, which applies one policy to
every source:

    retry    transient failures (timeouts, refused connections, 429, 5xx) are
             attempted `providers.http.retries` times with exponential backoff;
             permanent failures (404, a rejected API key) are not retried at all,
             because waiting does not fix them
    cache    every successful response is stored verbatim on disk and reused,
             which also stops one recommendation request from querying a free
             public API once per candidate crop
    offline  when a source is unreachable the last real response is served with
             its age; `SAMPADA_OFFLINE=1` (or `providers.http.offline`) disables
             outbound calls entirely

The honesty constraint on that fallback is the whole reason it is acceptable:

    a cached response is NEVER presented as current. It carries
    data_source = "cached", is_stale, cached_age_seconds and a note naming the
    failure that caused the fallback. Past `max_stale_minutes` it is refused
    outright and the API returns data_unavailable. The cache never interpolates,
    extends or repairs a payload, and a corrupt entry is deleted rather than read.

A recommendation built on stale inputs lists them in `stale_inputs`, emits a
`weather_stale` or `price_stale` reason, and leads its `disclaimers` with a notice,
so the staleness is visible at every layer a client reads rather than hidden in a
header. Staleness windows: weather 720 minutes, mandi prices 2880.

## 8. Known gaps to close before a field pilot

1. ~~Replace `crops.json` agronomic values with **cited** values.~~ **Partly
   done:** `scripts/build_crop_table.py` now generates the table from FAO EcoCrop
   and FAO-56 Tables 11–12, and `_meta.local_review_needed` lists every field it
   knows to be weak. **Package-of-practices, first tranche landed (2026-09-28):**
   `app/data/sources/pop_transcribed.json` transcribes verbatim, line-cited values
   from NFSM Oilseeds 2018, the DCD Cotton Ready Reckoner (Central zone) and the
   ICAR-IISR Soybean Bulletin 2023 - covered: soybean, cotton, groundnut,
   sunflower, safflower (advisory `pop` block; soybean/safflower vertisol override
   applied and provenance-stamped). Still missing: the state publication itself
   (MPKV Rahuri) for the 11 remaining crops (cereals, pulses, onion, sugarcane,
   rice); EcoCrop envelopes remain the sole source for those.
2. Have Marathi and Hindi reviewed by a native speaker and, ideally, an
   agricultural extension officer.
3. Calibrate `rain_behaviour_likelihood` against real observation/soil-test
   pairs; it is currently our own estimate.
4. Factor weights: `market_price` is now evidence-informed (per-crop measured
   reliability, §5.2). The other five remain **unvalidated defaults** held as
   configured — each has a stated evidence bar in
   `app/evaluation/factor_evidence.py`, and the held-out protocol
   (`app/evaluation/heldout.py`) is in place for when outcome volume exists.
5. ~~Encryption at rest~~ — **Done 2026-09-24 (schema v4)**: mobile numbers, farm
   locations and the input-context blobs are stored AES-256-GCM encrypted
   (`app/core/crypto.py`, audited `cryptography` package), with keyed HMAC lookup
   hashes for the equality lookups the system needs (login by mobile, community
   aggregation by district). Adding a key to an existing plaintext database is a
   restart: the at-rest pass encrypts every row in place, idempotently. With no
   key the plaintext dev mode stays available and `/health` reports it as
   `disabled`; `security.encryption_required = true` makes a keyless startup
   **fail closed**.
   **Key rotation — done 2026-09-25**: `SAMPADA_ENC_KEYS` is an ordered list
   (first key encrypts new writes, all keys decrypt); rotation is the at-rest
   pass re-encrypting every cell under the new active key on restart, with
   equality lookups matching a hash under any configured key so the rotation
   window never locks anyone out. Setting both env vars is refused; `/health`
   reports `encryption_key_count`.
   **Retention policy — done 2026-09-25**: opt-in, config-driven
   (`[retention]` in `config.toml`, every threshold defaulting to 0 = keep
   forever), purged only by `scripts/apply_retention.py` (dry-run by default).
   An outcome whose recommendation is purged survives unlinked rather than
   being deleted as a side effect.
6. OTP or another verified second factor; mobile + PIN is a weak factor.
7. ~~A backtest against real held-out mandi data before any accuracy claim is made.~~
   **Done 2026-09-23** for the price estimator — see §5.3. Re-run after every
   estimator change: `python scripts/fetch_history.py` then
   `python scripts/run_backtest.py`.

## 9. API surface

24 paths under `/api/v1`, documented at `/docs`. Highlights:

```
GET  /api/v1/health                    configuration summary (encryption
                                       state, key count, retention policy)
GET  /api/v1/providers/status          which sources are actually usable
GET  /api/v1/i18n/status               translation coverage + review status
GET  /api/v1/onboarding/questions      localized question set with options
GET  /api/v1/enums                     every standardized code and its label
POST /api/v1/auth/register|login       mobile + PIN
POST /api/v1/farms                     create a farm
POST /api/v1/farms/{id}/soil/estimate  fuse soil evidence
GET  /api/v1/weather                   live forecast or 503
GET  /api/v1/market/prices             mandi prices or 503
POST /api/v1/recommendations           ranked, explained crop options
POST /api/v1/recommendations/{id}/feedback   accept / partial / reject + why
POST /api/v1/outcomes                  what actually happened
GET  /api/v1/evaluation/promotions     promotion ledger (read-only)
```

Errors use one envelope: `{"error": {"code", "message", "details"}}`. An
unavailable source is `503` with `code: "data_unavailable"`.
