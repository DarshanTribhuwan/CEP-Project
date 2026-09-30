# SAMPADA AI — API Guide for Frontend & Voice Assistant

Everything the app/voice client needs to consume the backend. Last verified against
a live server on 2026-09-28 (38/38 endpoint walk; exact statuses below are from
that run).

---

## 1. Run it

```bash
cd "C:\Darshan\CODE\CEP Project\sampada-ai\backend"
python -m uvicorn app.main:app --reload --port 8000
```

| URL | What |
|---|---|
| http://127.0.0.1:8000/api/v1/health | liveness + honest transport status |
| http://127.0.0.1:8000/docs | interactive Swagger UI (try every endpoint here) |
| http://127.0.0.1:8000/app | the built-in farmer UI (reference implementation) |

All routes below are relative to the prefix `/api/v1`.

**CORS**: enabled for localhost origins, so a dev server (Vite/Next) on another
port works out of the box.

---

## 2. The 60-second journey

```text
1. POST /auth/register        -> token + farmer_id
2. POST /farms                -> farm_id   (crop history optional)
3. POST /soil/estimate        -> soil type + confidence (or skip; ask farmer)
4. POST /recommendations      -> ranked crop options + scores
5. POST /feedback             -> what the farmer thought
6. POST /outcomes             -> what actually happened (server computes profit)
```

Auth: `Authorization: Bearer <token>` on everything except register/login/health.

---

## 3. Endpoints by surface

### Reference / meta (no auth)

| Endpoint | Method | Notes |
|---|---|---|
| `/health` | GET | 200. Reports `encryption_at_rest`, `encryption_key_count`, retention policy verbatim. |
| `/providers/status` | GET | 200. Which live data feeds are configured. |
| `/crops` | GET | 200. **The knowledge base.** 16 crops, per-field provenance. Crops with sourced agronomy carry a `pop` block (sowing window, seed rate kg/ha, fertilizer NPKS, verbatim quotes) plus `pop_status: "verified"` and `pop_sources`; the rest return `pop: null`, `pop_status: "unavailable"`, and a localized `pop_message` to show as-is — never invent values client-side. |
| `/soil-types` | GET | 200. 7 soils with texture class and derived `typical_crops`. |
| `/enums` | GET | 200. Every enum + localized label — **hardcode no farmer-facing text**; pull labels from here (groups: season, soil_type, soil_rain_behaviour, feedback_reason, info_wanted, problem, resource, …). |
| `/i18n/status` | GET | 200. Translation completeness per language. |
| `/onboarding/questions` | GET | 200. The onboarding script, localized. |
| `/evaluation/factor-status`, `/evaluation/promotions` | GET | 200. Model status (read-only). |

### Auth

| Endpoint | Method | Body | Success | Failure modes |
|---|---|---|---|---|
| `/auth/register` | POST | `{name, mobile (10-digit), pin (4-digit), language}` | 201 → `{access_token, farmer}` | 422 duplicate mobile |
| `/auth/login` | POST | `{mobile, pin}` | 200 → token | 401 (identical message for wrong PIN and unknown mobile — by design, so login attempts reveal nothing) |
| `/auth/me` | GET | — | 200 farmer profile | 401 without/with bad token |
| `/auth/me/language` | PATCH | `{language}` | 200 | 422 bad code |
| `/auth/logout` | POST | — | 200; the token is rejected afterwards | — |

### Farmer / farms (auth)

| Endpoint | Method | Notes |
|---|---|---|
| `/farms` | GET | 200. List own farms. |
| `/farms` | POST | 201. `{village, taluka, district, latitude, longitude, area_acres, soil_type?}` — lat/lon optional but weather needs them. |
| `/farms/{id}` | GET / PATCH | 200. 404 if the farm belongs to another farmer (isolation is enforced and tested). |
| `/farms/{id}/preferences` | PUT | 200. Language + info preferences. |
| `/farms/{id}/crop-history` | POST / GET | 201 / 200. Season + crop + yield records. |
| `/farms/{id}/soil-estimates` | POST | 201. Farmer-reported soil + rain behaviour; server fuses with tested evidence. |

### Soil / weather / market (auth)

| Endpoint | Method | Notes |
|---|---|---|
| `/soil/estimate` | POST | 200. Knows-type + behaviour + optional lab test → type + confidence. |
| `/weather?farm_id=` | GET | 200. 7-day Open-Meteo forecast (live). Graceful degradation when the farm has no lat/lon. |
| `/market/prices?crop_id=&days=` | GET | **200 or 503.** 503 = `data_unavailable` with the exact reason (e.g. feed reachable but zero rows for that commodity/district today). This is honest-empty, NOT a bug — **render 503 as "no price today", not as an error.** |

### Recommendation → outcome (auth)

| Endpoint | Method | Notes |
|---|---|---|
| `/recommendations` | POST | 200. Farm + season in → **3 ranked options** (the API stores 3) with factor scores, confidence, and reasons per option. |
| `/recommendations` | GET | 200. History. |
| `/recommendations/{id}` | GET | 200. One recommendation with full factor breakdown. |
| `/recommendations/{id}/feedback` | POST | 201. `{followed, reason?}` enums from `/enums`. |
| `/outcomes` | POST | 201. `{recommendation_id, crop_id, yield_quintals, price_per_quintal, cost_inr}` → server computes `profit_inr`. Feed these — they drive evaluation and future promotion. |
| `/outcomes` | GET | 200. History. |

---

## 4. The `pop` block (what the voice assistant should read aloud)

```json
"pop": {
  "season_note": "kharif, rainfed dominant in Maharashtra",
  "sowing": {
    "window": ["06-20", "07-05"],
    "window_basis": "ICAR-IISR 2023 Table 2, Central zone: '20th June-5th July' …",
    "quotes": [{ "text": "…verbatim…", "file": "pop_pdfs/…", "lines": "1014" }]
  },
  "seed_rate_kg_ha": [55, 65],
  "fertilizer": { "N": 25, "P": 100, "K": 50, "S": 50 },
  "quotes": [ … ]
}
```

Rules for clients:

1. **Quote, don't paraphrase numbers.** Every number carries a verbatim quote +
   file + line citation. Show/cite the source when the farmer asks "who says?".
2. **`window_basis` tells you the precision.** Some windows are exact dates
   (ICAR table), some are month bands, cotton's start is an event ("onset of
   monsoon") — say it the way the source says it.
3. **Coverage status is a field, not an inference.** Every crop carries
   `pop_status` (`"verified"` | `"unavailable"`), `pop_available` (bool), and
   for unavailable crops a localized `pop_message` you can render directly:
   show a "PoP data unavailable" card with that message, never a fake number.
   Currently verified: **soybean, cotton, groundnut, sunflower, safflower**.
   The other 11 (cereals, pulses, onion, sugarcane, rice) are pending a state
   source — they return `pop_status: "unavailable"` honestly. Verified crops
   also carry `pop_sources` (the cited files) for "who says?" questions.
4. Fertilizer values are kg/ha of nutrient (N, P, K, S) — not product quantity.

---

## 5. Error shape & statuses you will actually see

| Status | When | How to render |
|---|---|---|
| 401 | missing/expired token, wrong PIN | "please log in again" |
| 404 | other farmer's farm, unknown id | generic not-found |
| 422 | validation (bad mobile, enum, duplicate mobile) | show `detail` verbatim — messages are already farmer-appropriate and localized |
| 503 | market/weather feed reachable but empty/down | "no data today" with the given reason — never a red error |
| 200/201 | success | — |

---

## 6. Data guarantees the backend makes (so you can rely on them)

- **Isolation**: farmer A can never read farmer B's farm (404) — tested.
- **PII encrypted at rest** (AES-256-GCM; mobile + locations), with keyed lookup
  hashes so login still works. Nothing client-visible changes.
- **Verbatim data culture**: every agronomic number in `/crops` carries
  provenance (status + citation). If a field has no source, it is labelled
  curated/unvalidated — surface that honestly rather than stripping it.
- **Feedback/outcomes are one-way evidence**: POST them once per season; they
  feed model evaluation (not the farmer's score).

---

## 7. Voice-assistant specifics

- The voice flows map 1:1 to the onboarding script (`/onboarding/questions`) —
  question ids, option ids and localized strings all come from the API.
- All farmer-facing strings exist in `en/mr/hi` via `/enums` + crop/soil labels
  (mr/hi not yet native-reviewed — see `/i18n/status` before promising quality).
- For a "what should I sow?" conversation, the pattern is:
  `/soil/estimate` → `/weather` → `/recommendations` → read the top option's
  `pop` block aloud (sowing window in the farmer's words, seed rate, fertilizer)
  → log `/feedback`.
- Numbers in `mr`/`hi` responses use Western digits; units are metric (kg/ha,
  quintal, acres for land input).

---

## 8. Test coverage you inherit

235 backend tests (2026-09-28): auth, farms, soil, recommendations, feedback,
outcomes, weather/market degradation, i18n completeness, encryption at rest, key
rotation, retention, evaluation/promotion, PoP data integrity. If you build a
client against this guide and something contradicts it, the guide is wrong —
flag it, don't code around it.
