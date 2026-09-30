# SAMPADA AI — Feynman research project

> **Goal:** use Feynman for all research work on SAMPADA AI.
> **Backup repo:** https://github.com/DarshanTheRuler/cep-project-ai
> **Local repo:** `sampada-ai/`
> **Feynman install:** `feynman-local/node_modules/@advaitpaliwal/feynman`
> **Run locally:** `node feynman-local/node_modules/@advaitpaliwal/feynman/bin/feynman.js --help`

## What Feynman is

Feynman is an open source AI research agent CLI. Main capabilities:

- `feynman help`
- `feynman setup`
- `feynman model set ...`
- `feynman search set ...`
- `feynman rank "topic"`
- `feynman paper <doi|arxiv-id|pmid|title>`
- `feynman alpha search "query"`
- `feynman deepresearch "topic"`
- `feynman lit "topic"`
- `feynman serve`

Feynman is built on Pi and alphaXiv. It uses skills under `skills/` and prompts under `prompts/`.

## How we use Feynman for this project

- Research only. Do not use it as the main app coding agent unless the task is explicitly a research task.
- Research topics:
  - Maharashtra crop price prediction datasets and methods
  - soil classification from images in agriculture
  - community-aware recommendation and oversupply mitigation
  - explainable agricultural decision support
  - open weather and market data sources for India/Maharashtra
- Save research artifacts under `docs/research/` and `outputs/`.
- Use slugs for filenames, for example `crop-price-maharashtra-brief.md`.

## Local run convention

Feynman does not run cleanly from bash here because the npm bin shims are CMD/PowerShell wrappers.

Use this local runner:

- `node feynman-local/node_modules/@advaitpaliwal/feynman/bin/feynman.js --help`
- `node feynman-local/node_modules/@advaitpaliwal/feynman/bin/feynman.js setup`
- `node feynman-local/node_modules/@advaitpaliwal/feynman/bin/feynman.js model list`

If a command wants a model or web search provider configured, run `feynman setup` first or set the provider/API key in the Feynman settings.

## Repo-level conventions

- English for project docs and code.
- Marathi only for farmer-facing app strings.
- Keep sample/mock data separate from production data.
- Do not fabricate APIs, datasets, or accuracy numbers.
- Every research claim should link to a source when possible.
