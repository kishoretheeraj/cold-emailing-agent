# Cold Email Agent — conventions for Claude

This is a Python automation that runs daily on GitHub Actions, reads contacts
from Supabase, generates personalized emails via the Claude API, and creates
Gmail drafts for review.

When working in this repo, follow the rules below. They reflect how the code
was actually written, not just style preferences.

---

## Module layout

```
agent.py          # Daily run: pick contacts, draft, label, update Supabase
monitor.py        # Reply detector — runs every 2 hours, sets reply_status=replied
emailer.py        # Claude prompts and email generation
gmail.py          # IMAP draft creation + Gmail label management
db.py             # Supabase client + thin query/update wrappers
config.py         # All env-var reads + prompt templates + tier instructions
constants.py      # Stage sequences, reply statuses, TERMINAL_REPLY_STATUSES, DRAFTED_STAGES
notify_failure.py # Emailed to GMAIL_ADDRESS when a workflow step fails
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

## Threading invariants

Follow-up emails must land in the same Gmail thread as the original.

- `create_draft()` in `gmail.py` generates a `Message-ID` via
  `email.utils.make_msgid()` and embeds it in the MIME message before IMAP
  APPEND. It returns the Message-ID string, or `None` if a duplicate draft
  already exists for this `contact_id`/`stage`/`date` combination. **Do not
  fetch the ID from IMAP after append** — Gmail drafts have no server-assigned
  Message-ID until sent.
- `create_draft()` accepts `in_reply_to` and `references` kwargs. When
  `in_reply_to` is set, it adds `In-Reply-To` and `References` headers and
  auto-prefixes the subject with `Re: ` (unless it already starts with it).
- **Idempotency key**: when `contact_id` and `stage` are passed, `create_draft()`
  hashes `f"{contact_id}:{stage}:{date.today()}"` (SHA-256, first 16 hex chars)
  and writes it as an `X-Cold-Email-Key` custom header. Before appending, it
  searches `[Gmail]/Drafts` for that key; if found, it returns `None` without
  creating a duplicate. `agent.py` treats a `None` return as "already drafted
  today" and increments `skipped` instead of calling `update_contact()`.
- `_FIRST_TOUCH_ACTIONS = {"send_first_touch", "send_applied_intro"}` is
  defined in both `agent.py` and `emailer.py`. Keep them in sync manually if
  new first-touch actions are added.
- After a first-touch draft is created, `agent.py` calls `save_thread_info()`
  to persist `message_id` and `original_subject` in Supabase.
- For all follow-up actions, `agent.py` calls `get_thread_info()` first and
  passes the stored `message_id` as `in_reply_to` to `create_draft()`.
- `generate_email()` in `emailer.py` accepts `original_subject=None`. For
  follow-up actions it returns `"Re: " + original_subject` without calling
  Claude — only first-touch actions call `_generate_subject()`.
- `message_id` and `original_subject` columns are **agent-managed**. Never
  write them manually — they are set once after the first draft.

## Resilience patterns

- **Anthropic API** (`emailer._call_claude`): retries on HTTP 429, 529, any
  5xx, and `urllib.error.URLError`, up to 3 attempts with 2 s / 4 s backoff.
  Other 4xx (auth, bad request) raise immediately.
- **Supabase** (`db._retry`): every query/update is wrapped in a 3-attempt
  retry with the same 2 s / 4 s backoff. Catches broad `Exception` — Supabase
  blips are transient; the retry budget is small.
- **Failure notification** (`notify_failure.py`): both workflows have an
  `if: failure()` step that runs this script. It emails `GMAIL_ADDRESS` via
  Gmail SMTP using `GMAIL_APP_PASSWORD` — no new secrets required.

## Supabase patterns

- All queries go through `get_client()` (cached singleton).
- Filtering uses the chain pattern: `.table().select().eq().like().execute()`.
- Updates always set `last_emailed = str(date.today())` alongside the stage
  change (except for `update_reply_status` and `save_thread_info`, which only
  touch their own fields).
- The Supabase Python client validates API keys against a JWT regex. The
  monkey-patch in `db.py` widens it to also accept `sb_publishable_*` keys.
  **Do not remove this patch** — it is the only reason the publishable key
  format works.
- **`prompts` table**: `db.load_prompts()` reads all rows at agent startup and
  returns `{key: value}`. Keys used: `sender_profile`, `outreach_prompt`,
  `applied_intro_prompt`, `applied_followup_prompt`, `subject_prompt`. If the
  table is unreachable, `run()` falls back to an empty dict and `emailer.py`
  uses the `config.py` defaults. Prompts can be edited live via the
  contact-manager's Prompts & Profile page; changes take effect the next run.

## GitHub Actions

Two workflows live in `.github/workflows/`:

- **`daily_agent.yml`** — runs `agent.py` Mon-Fri at 5:37am EST (cron
  `37 10 * * 1-5`). Has a `check-duplicate` preflight job: if a
  `workflow_dispatch` (manual) run already succeeded today, the scheduled
  run is skipped to prevent double-drafting.
- **`monitor.yml`** — runs `monitor.py` every 2 hours Mon-Fri at :23
  (cron `23 */2 * * 1-5`).

Both workflows: upload the relevant `.log` file as an artifact (30-day
retention), and run `notify_failure.py` in an `if: failure()` step.
Both support `workflow_dispatch` for manual triggers.
Python version: **3.11**. Dependencies installed via `requirements.txt`.

## Agent run tracking

`agent_runs` is a separate table (not `contacts`) that records every run:

```
id, ran_at, status ('success'|'failure'), drafted, skipped, errors,
elapsed_seconds, failure_reason (TEXT, nullable)
```

`db.record_run()` is called at the end of `run()` — before `sys.exit(1)` —
so it captures per-contact error counts as `failure`. The `__main__` block
also catches catastrophic exceptions (Supabase down, auth failure) and records
them with the exception message as `failure_reason`. Both callsites are
best-effort: a failed `record_run` logs a warning and never masks the original
error or exit code.

The dashboard's "Last run" reads `ran_at` from the most recent `agent_runs`
row, not `MAX(last_emailed)` from contacts. This means the date updates on
every run regardless of whether any drafts were created.

## IMAP patterns

- `[Gmail]/Drafts` mailbox name is double-quoted: `'"[Gmail]/Drafts"'`.
- Label folder names are double-quoted: `f'"{label_name}"'`.
- `imap.create()` returns `('NO', [b'[ALREADYEXISTS]...'])` if the label
  exists. **This is not an exception** — `create_gmail_label_if_not_exists`
  intentionally ignores the return value.
- Always wrap IMAP calls in `try/finally imap.logout()`. Connection cleanup
  is non-negotiable.
- `create_draft()` returns the `Message-ID` it generated, or `None` if a
  duplicate was detected via `X-Cold-Email-Key`. Do not try to fetch the ID
  from IMAP after append — Gmail drafts only receive a server Message-ID when
  actually sent.

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
