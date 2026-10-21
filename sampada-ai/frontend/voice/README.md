# Marathi voice-to-voice assistant

A spoken interface for SAMPADA AI: the farmer talks, the assistant answers aloud
in Marathi, and can be interrupted mid-sentence.

```
mic → STT → brain → sentence splitter → TTS → speaker
                                          ↑ barge-in
```

## Run it

```bash
cd sampada-ai/frontend/voice
npm test          # 72 assertions, no network
npx serve -l 5173 # then open http://127.0.0.1:5173
```

The backend should be running for the grounded path (`python -m uvicorn
app.main:app --reload --port 8000` from `sampada-ai/backend`). The LLM path does
not need it.

Open **⚙ सेटिंग्ज** and paste the keys. With none set, the page still works and
tells you so:

| Missing key | What happens instead |
|---|---|
| Bhashini | Browser `SpeechRecognition` + `speechSynthesis`. Chrome's Marathi support is poor, so treat this as a smoke test. |
| LLM | Answers come from the backend's own endpoints (`/crops`, `/weather`, `/market/prices`, `/recommendations`) with their confidence levels, citations and honest-unavailable states. |

The status line above the log always shows which provider is actually live
(`ऐकणे: … · बोलणे: … · विचार: …`), so a keyless run never looks like a working
one.

## Files

| File | Role |
|---|---|
| `js/app.js` | wiring: the turn loop, barge-in monitor, UI |
| `js/stt.js` | mic capture, 16 kHz mono WAV encode, Bhashini ASR |
| `js/brain.js` | streaming LLM, or the grounded backend |
| `js/sentences.js` | buffers token deltas into speakable sentences |
| `js/tts.js` | Bhashini TTS, or the browser synthesiser |
| `js/speaker.js` | playback queue, session counter, interruption |
| `js/grounded.js` | backend queries and the spoken answers built from them |
| `js/config.js` | keys, endpoints, capability reporting |
| `test/*.test.mjs` | 72 assertions over the five units that can fail silently |

Test files are plain `node` scripts, not a framework: each prints `PASS`/`FAIL`
per case and exits non-zero on failure, so `npm test` works with no install step.

## Details worth knowing

**Bhashini endpoints.** `POST https://api.bhashini.ai/v1/asr` (multipart `file`
+ `language`) and `POST https://api.bhashini.ai/v1/synthesize` (JSON `text`,
`language`, `voiceName`), authenticated with an `X-API-KEY` header. Both were
confirmed reachable on 2026-10-01 — the ASR route returns 415 without a
multipart body and TTS returns 401 without a key. The `language` field takes a
display name (`"Marathi"`), not the ISO code, on these two routes.

**Interruption is a session counter, not a flag.** Bumping `speaker.session`
invalidates every in-flight synthesis and queued item at once. The reference
implementation's flag-only approach leaks audio when a barge-in lands during
TTS — there is a test for exactly that case in `test/speaker.test.mjs`.

**Audio is re-encoded before upload.** Browsers record webm/opus; Bhashini's ASR
models expect 16 kHz mono wav, so the clip is decoded and resampled through an
`OfflineAudioContext` before upload. Recording is capped at 15 s so a silent
room cannot produce a huge upload.

**Sentence splitting is Marathi-aware.** The danda (`।`) is treated as a real
terminator, a full stop between digits is not (`55.5` stays intact), and a
terminator with no following space is held rather than guessed at, so text is
never split mid-word.

**Crop names are matched on their consonant skeleton.** Marathi inflects them —
`कांदा` (onion) becomes `कांद्याची`, `सोयाबीन` becomes `सोयाबीनचा` — so a plain
substring test misses ordinary questions. `js/grounded.js` strips the matras and
viramas and compares what is left, which reduces all three forms to `कद`. Without
this, "कांद्याची बियाणे किती" matched no crop and fell through to the first row of
the table, answering a question about onion with soybean figures. It now asks
which crop instead: when no crop is named, the assistant says so rather than
guessing.

**The grounded path will not read an operator message aloud.** When the market
feed is unconfigured the API honestly returns 503 with
`environment variable SAMPADA_DATA_GOV_IN_KEY is not set`. That text is correct
and belongs in the log, where the UI shows it, but speaking an env-var name to a
farmer is noise. The spoken line is the farmer-facing one; the reason stays
visible on screen.

## Known limits

- Keys live in the browser and in `localStorage`. That is a prototype trade-off
  for iteration speed. Before this ships, the keys belong in the backend and
  these modules become a thin client of `/api/v1/voice/*` — see the note at the
  top of `js/config.js`.
- The LLM is instructed to defer factual questions to the backend, but that is
  a prompt, not a guarantee. It can still answer a price from memory, and
  nothing it says about a number is cited. Treat the LLM path as a conversation
  layer over the grounded path, not a replacement for it.
- One turn at a time. There is no conversation history across turns, so
  follow-ups like "आणि बियाणे किती?" lose their referent. This is the most
  noticeable gap: Marathi conversation leans heavily on pronouns.
- Intent matching is keyword-based, not a classifier. It covers the six
  questions above and refuses everything else, which is the safe direction to
  fail, but "माती कोणती" and "पाण्याची सोय" will not route correctly yet.
- The soil answer needs a soil type to say anything. With no evidence the
  endpoint returns `top_soil_type: null`, so the assistant asks for rain
  behaviour or a lab test instead of guessing — it cannot yet hold a two-turn
  soil conversation to collect that evidence.
- Chrome-only in practice: the `X-API-KEY` header, ES modules and
  `OfflineAudioContext` all assume a current Chromium or Firefox.
