# SAMPADA AI

Decision-support backend for Maharashtra smallholder farmers — crop suitability,
soil estimation, package-of-practices reference, live weather and market data,
and profit tracking, in Marathi, Hindi and English.

> **Decision support only.** Outputs are estimates with confidence levels and
> are not guarantees of price, yield or profit.

**Status:** backend complete · 237/237 tests green (Python 3.14) · validated live end-to-end (38/38 endpoint walk)

---

## Quick start

```bash
git clone https://github.com/DarshanTribhuwan/CEP-Project.git
cd CEP-Project/sampada-ai/backend

python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt

cp .env.example .env    # add your own key (see file for how to generate it)

python -m uvicorn app.main:app --reload --port 8000
```

| URL | What |
|---|---|
| http://127.0.0.1:8000/docs | Swagger — every endpoint, try it live |
| http://127.0.0.1:8000/app | Farmer UI |
| http://127.0.0.1:8000/console | Dev console (raw API responses) |
| http://127.0.0.1:8000/api/v1/health | Liveness + config (reports encryption state honestly) |

## What the backend does

- **Auth** — mobile + 4-digit PIN, bearer tokens; unknown-mobile and wrong-PIN are indistinguishable by design
- **Farms & preferences** — land, irrigation, district/taluka/village (stored encrypted)
- **Soil estimation** — behaviour-based (drainage, cracking, water retention) fused with lab evidence when the farmer has it
- **Recommendations** — 16 crops scored with factor-level explanations; schema-versioned factor snapshots so any stored recommendation can be re-evaluated
- **Outcomes & evaluation** — logged harvests → profit computation → held-out evaluation → model promotion only on pre-registered wins
- **Live data** — Open-Meteo weather (real forecast), data.gov.in market prices; when a feed has no data the API returns an honest **503 `data_unavailable`**, never a guess
- **Package of practices** — 5 crops (soybean, cotton, groundnut, sunflower, safflower) transcribed **verbatim** from NFSM / ICAR-IISR / DCD publications with file+line citations; the other 11 report `pop_status: "unavailable"` with a localized explanation rather than invented numbers
- **Security** — AES-256-GCM encryption at rest (mobiles, locations), key rotation without lockouts, opt-in retention purge CLI, per-farmer data isolation
- **i18n** — `mr` / `hi` / `en`; language affects presentation only, never stored data or scoring

## Repo map

```
├── sampada-ai/
│   ├── backend/
│   │   ├── app/
│   │   │   ├── api/          # HTTP layer (one router per resource)
│   │   │   ├── services/     # domain logic (scoring, crops, i18n, soil fusion)
│   │   │   ├── repositories/ # sqlite3 persistence (stdlib, no ORM)
│   │   │   ├── core/         # config, crypto, db, retention, i18n, errors
│   │   │   ├── models/       # scoring model + factor definitions
│   │   │   ├── evaluation/   # held-out windows, promotion gates
│   │   │   ├── providers/    # weather + market feed clients
│   │   │   ├── data/         # crops.json (generated, cited), soils, sources/
│   │   │   ├── locales/      # en/mr/hi catalogs
│   │   │   ├── appui/        # farmer UI (served at /app)
│   │   │   └── console/      # dev console (served at /console)
│   │   ├── scripts/          # build_crop_table.py, promote_model.py, apply_retention.py
│   │   ├── tests/            # 237 tests
│   │   ├── ml/               # model artifacts
│   │   └── .env.example      # secrets template (real .env is git-ignored)
│   └── frontend/
│       └── voice/            # Marathi voice-to-voice assistant (Bhashini ASR/TTS)
├── docs/
│   ├── api/FRONTEND_GUIDE.md # ← start here for UI/voice work (32 routes)
│   └── spec/MVP_SPEC.md      # the spec, with open items marked
├── notes/README.md           # chronological lab notebook (research log)
└── CONTRIBUTING.md           # branch/PR workflow — read before first PR
```

## Data governance rules the API enforces

1. **Silence is never faked.** No PoP source → `pop: null` + `pop_status: "unavailable"` + localized message. No market rows → 503. No promotion → incumbent stays.
2. **Every sourced number is checkable** — verbatim quote + cached file + line numbers, in the repo.
3. **PII is ciphertext at rest** — lose the encryption key and the data is unreadable (keep `.env` safe).
4. **Retention is opt-in** — thresholds default to keep-forever; `scripts/apply_retention.py` purges only what an operator explicitly configured.

## Contributing

Feature branches + PRs only; `main` stays green. See [CONTRIBUTING.md](CONTRIBUTING.md).
Frontend and voice-assistant work: read [docs/api/FRONTEND_GUIDE.md](docs/api/FRONTEND_GUIDE.md), then branch into `sampada-ai/frontend/`. The Marathi voice assistant is at [sampada-ai/frontend/voice/](sampada-ai/frontend/voice/README.md) — run it with `npm test` (no network) and `npx serve -l 5173`.

## Open items (tracked in the lab notebook)

- PoP for the remaining 11 crops — awaiting the MPKV state publication (offline channels: university library, KVK, district agriculture office)
- Native-speaker review of mr/hi strings (including the new `pop.unavailable` drafts)
- OTP delivery (needs an SMS provider account)
- Rain-behaviour calibration (needs field observations)
