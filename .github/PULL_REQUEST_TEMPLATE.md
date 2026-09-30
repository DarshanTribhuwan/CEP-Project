# Pull request

**What changed and why** (link the issue/task if one exists):

-

**How to verify** (commands run, endpoints hit, what "working" looks like):

-

## Checklist — the branch is not mergeable until every box is true

- [ ] Branched from latest `main` (`git pull --rebase origin main` before starting)
- [ ] `python -m pytest -q` passes from `sampada-ai/backend/` (currently 237 tests)
- [ ] No new dependency added without a reason in `requirements.txt`
- [ ] No secrets, `.env`, or `*.db` files touched (they are git-ignored for a reason)
- [ ] Farmer-facing strings added in all three locales (`en.json`, `mr.json`, `hi.json`)
- [ ] New numbers/agronomy carry a verbatim source citation — never invented values
