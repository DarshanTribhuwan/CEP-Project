# Documentation drift audit — SAMPADA AI

**Date:** 2026-09-17
**Method:** extracted all text from the five `.docx` files in
`SAMPADA_AI_800_Page_Documentation/` with `python-docx`, then compared their
content against `chat_extracted.txt` (the full text of `Chat.pdf`, 6,342 lines,
read end to end).

**Verdict:** the documents' **chapter titles are faithful** to the design
conversation. Their **bodies are not**. They are a length-optimised shell, not a
specification, and should not be handed to a coding agent as-is.

---

## 1. Measured evidence

| Signal | Measured |
|---|---|
| Paragraphs per part | exactly **1504** in all four parts |
| Unique paragraphs, Part 3 | **630 of 1504** — 58% of the text is duplication |
| Repeated strings | the identical sentence *"The objectives are to provide a reliable, measurable, maintainable, and scalable implementation…"* appears **20×**, once per chapter; same for scope, system overview and functional requirements |
| Total length | ~27,000 words/part, ~108,000 words total → **~215 pages at 500 words/page**, labelled "800 pages" |
| Tables | **0** across all five files, despite the source conversation being dominated by tables |
| The word "confidence" | exactly 100 / 100 / 104 / 100 occurrences across Master / Part 1 / Part 2 / Part 3 / Part 4 — uniform repetition, not per-chapter analysis |

## 2. The decisive test: does the content carry the design?

Searching every document for the concepts the conversation actually settled on:

| Concept agreed in the chat | Occurrences in all five docs |
|---|---|
| Pilot plot mode (test on 10% of land) | **0** |
| Crop Saturation Index | **0** |
| Crisis mode (switch objective to minimise loss) | **0** |
| Cold start / three-level learning | **0** |
| Black swan event handling | **0** |
| GPS, Agmarknet, Soil Health Card, market arrivals | **0** |
| Multi-agent framing, resilience scoring | **0** |
| "controlled random" / "oversupply" / "net return" | **4 each**, all in Part 3 |
| Marathi question set | **4**, all in Part 1 |

### Chapter 9 of Part 3 is the clearest single example

Its title is *"Controlled Random Allocation & Oversupply Prevention"* — the
central research contribution of the entire project. Its body contains none of
it: no saturation formula, no allocation procedure, no fairness policy. It is the
same 9.1–9.7 template used by every other chapter, with generic bullets
("Inputs shall be validated before processing.", "Outputs shall use standardized
SAMPADA AI structures.").

## 3. What is actually correct in the docs

The chapter *list* is a genuinely good table of contents and maps closely onto
the design conversation. Part 3's chapters include Community-Aware
Recommendation, Controlled Random Allocation & Oversupply Prevention, Market
Selection & Net Return Optimization, Alternative Income Recommendation Engine,
Explainable AI & Evidence Engine. Part 1's include Multilingual UX and Farmer
Onboarding.

**Use the docs for structure. Do not use them for substance.**

## 4. Code drift

`sampada-ai/backend/app/main.py` (the pre-existing stub) imported
`app.core.db`, `app.api.farmer`, `app.api.recommendation`, `app.api.soil`,
`app.api.weather`, `app.api.market` and `app.api.admin`. **None of those modules
existed.** There was no `requirements.txt`, no database, no tests and no
frontend, so the backend could not start.

`config.toml` also contained two values that appear nowhere in the source
conversation and were undocumented:

- `min_confidence_for_recommendation = 0.45`
- `community_oversupply_threshold = 0.85`

Both are retained, now documented as **unvalidated defaults** and configurable,
rather than silently acting as if they were derived.

## 5. Recommendation

1. Keep the five `.docx` files as **reference structure** only.
2. Build from `docs/spec/MVP_SPEC.md`, which is derived from the source
   conversation and matches the code that exists.
3. Before any field pilot, replace the agronomic values in
   `app/data/crops.json` with cited values — they are marked `UNVERIFIED` in the
   file itself (see §6).
4. Have the Marathi and Hindi strings reviewed by a native speaker; the
   `/api/v1/i18n/status` endpoint reports `reviewed_by_native_speaker: false`.

## 6. Provenance statements now carried in the code

Rather than relying on this document being read, the limitations are asserted in
the artefacts themselves:

- `app/data/crops.json` → `_meta.verification: "UNVERIFIED"` with a warning
  naming ICAR / MPKV Rahuri as the sources that must replace it.
- `app/data/soil_types.json` → soil names are standard classification; the
  rain-behaviour likelihoods are explicitly labelled as our own estimates, not
  published values.
- `app/data/sample/market_sample.json` → `_meta.verification: "SYNTHETIC"`,
  with the sentence *"THIS IS INVENTED DATA."*
- `/api/v1/providers/status` → reports `not_implemented` for the photo soil
  model, news/policy monitoring and transport logistics.
