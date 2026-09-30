# Contributing — SAMPADA AI

Two-person team, one rule: **`main` is always green and always runnable.**
All work happens on feature branches and lands through pull requests.

## 1. One-time setup

```bash
git clone https://github.com/DarshanTribhuwan/CEP-Project.git
cd CEP-Project/sampada-ai/backend

python -m venv .venv                       # Windows: py -m venv .venv
source .venv/Scripts/activate              # Windows Git Bash (`.venv\Scripts\activate` in PowerShell)
pip install -r requirements.txt

cp .env.example .env                       # then paste YOUR OWN generated key
python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"
```

Never copy someone else's `.env` — each machine gets its own secrets.

## 2. Every work session

```bash
git switch main
git pull --rebase origin main              # start from green
git switch -c feat/<what-you-are-doing>    # e.g. feat/voice-pop-readout, fix/market-503-copy
```

Branch name prefixes: `feat/`, `fix/`, `docs/`, `data/` (PoP transcriptions), `exp/` (may be broken, never merged).

## 3. Before opening a PR

```bash
python -m pytest -q                        # from sampada-ai/backend — must be green
git pull --rebase origin main              # replay your work onto latest main
git push -u origin feat/<branch>
```

Then open the PR on GitHub (the template's checklist appears automatically).
A teammate review is one "LGTM + I ran the tests" — don't skip it.

## 4. Merging

**Squash and merge** — keeps `main` one-commit-per-feature, easy to revert.
Delete the branch after merge (button appears on the PR).

## 5. If a rebase goes sideways

```bash
git rebase --abort                         # undo, back to safety
```
Ask before force-pushing anything. `git push --force` to `main` is forbidden.
