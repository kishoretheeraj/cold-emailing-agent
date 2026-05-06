# Cold Email Agent — conventions for Claude

This is a Python automation that runs daily on GitHub Actions, reads contacts
from Supabase, generates personalized emails via the Claude API, and creates
Gmail drafts for review.

When working in this repo, follow the rules below. They reflect how the code
was actually written, not just style preferences.

---

## Module layout

```
agent.py     # Daily run: pick contacts, draft, label, update Supabase
monitor.py   # Reply detector — runs every 2 hours, sets reply_status=replied
emailer.py   # Claude prompts and email generation
gmail.py     # IMAP draft creation + Gmail label management
db.py        # Supabase client + thin query/update wrappers
config.py    # All env-var reads + prompt templates + tier instructions
```

Every module that touches the outside world is wrapped behind a function so
tests can mock it. Pure decision logic stays in `agent.py` (`decide_action`,
`_decide_outreach`, `_decide_applied`, `_skip_reason`, `_parse_date`).

## Code style

- **No type annotations.** The codebase is plain Python. Don't introduce
  `typing` imports or function signatures with types unless a function is
  genuinely public-API.
- **Section banners.** Files use `# ── Section name ──...` (16+ box-drawing
  chars) to separate logical groups. Keep this convention when adding code.
- **No docstrings on private helpers.** Only public functions like
  `decide_action`, `generate_email`, `create_draft`, `run` get a short
  docstring; underscore-prefixed helpers don't.
- **No em dashes inside email copy.** This rule is enforced in the prompt
  templates themselves (`config.py`). Don't add em dashes to generated text.
- **Use `f""` for log messages** with the pipe-separated format
  `prefix | name | company | event | extra` so logs stay easy to grep.

## Logging format

Every script logs to its own file (`agent.log`, `monitor.log`) with the line
format:

```
2026-04-21 08:00 EST | <event marker> | <details>
```

The marker is one of: `START`, `DONE`, `[OUTREACH]`, `[APPLIED]`, or a level
tag from a warning/error. Don't change the timestamp format — the GitHub
Actions artifacts and downstream scripts read it.

## Decision logic invariants

- A contact is **skipped** the moment `reply_status` becomes anything other
  than `no_reply`. The agent must never email someone who has already replied.
- Stage progression is **strict**: `new → *_drafted → *_sent → ...`. The
  user is the one who flips `*_drafted` to `*_sent` (after they hit Send in
  Gmail). The agent never auto-flips.
- `breakup_sent` and `applied_followup_sent` are **terminal**.
- `followup_date` is checked with `<=`, not `<`, so a follow-up due "today"
  is sent today.
- `_parse_date` accepts: `None`, `""`, a `date` object, an ISO date string,
  or a string with a date prefix (the function takes the first 10 chars).
  Anything else returns `None`.

## Stage / template / label maps

`agent.NEXT_STAGE`, `agent.NEXT_TEMPLATE`, `agent.ACTION_LABEL`, and
`emailer.ACTION_TO_TEMPLATE` must keep matching keys. There's a test
(`test_action_maps_have_consistent_keys`) that fails if they drift.

When you add a new action:
1. Add it to all four maps.
2. Add the action's prompt template to `config.py` if it's outreach.
3. Add a `decide_*` branch that returns the action.
4. Add `Cold Outreach/<Label>` formatted label.

## Best-effort labeling rule

Gmail labeling is **best-effort** in both `agent.py` and `monitor.py`. A
label failure logs a warning but never raises. Drafts and reply detection
are more important than visual organization in Gmail.

When wiring up new label calls, always wrap them:

```python
try:
    apply_label_to_latest_draft(label)
except Exception as exc:
    log.warning(f"{mode_tag} {name} | {company} | label warning: {exc}")
```

## Supabase patterns

- All queries go through `get_client()` (cached singleton).
- Filtering uses the chain pattern: `.table().select().eq().like().execute()`.
- Updates always set `last_emailed = str(date.today())` alongside the stage
  change (except for `update_reply_status`, which only changes that field).
- The Supabase Python client validates API keys against a JWT regex. The
  monkey-patch in `db.py` widens it to also accept `sb_publishable_*` keys.
  **Do not remove this patch** — it is the only reason the publishable key
  format works.

## IMAP patterns

- `[Gmail]/Drafts` mailbox name is double-quoted: `'"[Gmail]/Drafts"'`.
- Label folder names are double-quoted: `f'"{label_name}"'`.
- `imap.create()` returns `('NO', [b'[ALREADYEXISTS]...'])` if the label
  exists. **This is not an exception** — `create_gmail_label_if_not_exists`
  intentionally ignores the return value.
- Always wrap IMAP calls in `try/finally imap.logout()`. Connection cleanup
  is non-negotiable.

## Tests

- Run tests with `python3 -m pytest`.
- Tests live in `tests/`. `conftest.py` sets fake env vars before any
  module is imported (because `config.py` reads `os.environ` at import time
  and would otherwise raise `KeyError`).
- All outbound calls (Anthropic, Supabase, IMAP) **must be mocked**. Tests
  never travel.
- Use `pytest-mock`'s `mocker` fixture for patching. Use `mocker.patch.object`
  to replace functions on a module under test.
- Decision-logic tests use `pytest.mark.parametrize` to enumerate stages
  and replies. Match this pattern when adding cases.

## When changing things

- **Don't refactor `agent.py` decision logic** without updating the
  parametrized tests. The dispatch table for stages is part of the
  contract with the user's Supabase rows.
- **Don't add an SMTP/send path.** This system *only* creates drafts. The
  user reviews and sends manually. If asked to "send automatically", surface
  the manual-review constraint and wait for confirmation.
- **Don't store `ANTHROPIC_API_KEY` or `GMAIL_APP_PASSWORD` anywhere except
  GitHub Actions secrets and `.env`.** `.env` is in `.gitignore`.

## Style: comments and docs

Don't write comments that restate the code. Do write a short comment when:
- You're documenting a non-obvious workaround (e.g. the supabase regex patch).
- You're flagging a best-effort vs. blocking distinction (the label calls).

Don't add docstrings to private (`_prefixed`) helpers.

## File header banners (visual)

Use this banner shape for top-level sections inside files:

```python
# ── Section title ──────────────────────────────────────────────────────────────
```

Replace the title; keep the trailing dashes consistent.
