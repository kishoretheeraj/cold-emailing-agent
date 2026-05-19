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
monitor.py        # Four-phase monitor: sent-detection, reply-detection, classification, reply-drafting
emailer.py        # Claude prompts and email generation (includes pre-flight integration)
preflight.py      # Six deterministic checks run before every draft; hard block on failure with one retry
reply_drafter.py  # Generates reply drafts for positive_reply/soft_yes contacts; no critic, no auto-send
research.py       # Tavily web research pipeline — query gen, curation, caching (Tier 1+2 first-touch only)
gmail.py          # IMAP draft creation + Gmail label management
db.py             # Supabase client + thin query/update wrappers
config.py         # All env-var reads + prompt templates + tier instructions
constants.py      # Stage sequences, reply statuses, TERMINAL_REPLY_STATUSES, DRAFTED_STAGES, TERMINAL_DRAFTED_STAGES
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

The marker is one of: `START`, `DONE`, `[OUTREACH]`, `[APPLIED]`, `[CRITIC]`,
`[RESEARCH]`, `[RESEARCH-Q]`, `[RESEARCH-T]`, `[RESEARCH-F]`, `[RESEARCH-C]`,
or a level tag from a warning/error. Don't change the timestamp format — the
GitHub Actions artifacts and downstream scripts read it.

## Decision logic invariants

- A contact is **skipped** the moment `reply_status` becomes anything other
  than `no_reply`. The agent must never email someone who has already replied.
- Stage progression is **strict**: `new → *_drafted → *_sent → ...`. The
  `monitor.py` sent-draft detector auto-flips `*_drafted → *_sent` when it
  finds the email in Gmail Sent Mail. The user can also flip manually.
  The agent never auto-flips.
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

`agent.DRAFTED_TO_SENT` maps every `*_drafted` stage to its `*_sent`
successor. `monitor.detect_sent_drafts()` uses this map exclusively —
do not duplicate the mapping elsewhere.

When you add a new action:
1. Add it to all four action maps (`NEXT_STAGE`, `NEXT_TEMPLATE`, `ACTION_LABEL`, `emailer.ACTION_TO_TEMPLATE`).
2. Add the `*_drafted → *_sent` entry to `agent.DRAFTED_TO_SENT`.
3. Add the action's prompt template to `config.py` if it's outreach.
4. Add a `decide_*` branch that returns the action.
5. Add `Cold Outreach/<Label>` formatted label.

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

- **Anthropic API** (`emailer._call_claude`): uses the official `anthropic` SDK
  with `max_retries=2` (3 total attempts). The SDK auto-retries 429, 529, and
  5xx; non-retryable 4xx raise immediately. Signature:
  `_call_claude(prompt, model=None, max_tokens=1000, system=None)`. When `system`
  is provided it is sent as a system-prompt block with `cache_control: ephemeral`
  for prompt caching. All five generators (`_generate_outreach`,
  `_generate_applied_intro`, `_generate_applied_followup`, `_generate_subject`,
  `_run_critic`) pass the sender profile as `system`. `research.py` passes
  `RESEARCH_QUERY_MODEL` / `RESEARCH_CURATE_MODEL` (both Haiku) with lower token
  ceilings (300 / 500) and no `system` param.
  Caching activates automatically when the system prompt reaches the 1024-token
  Sonnet minimum (currently below threshold; grows as Supabase prompts are edited).
- **Tavily** (`research.py`): `_get_client()` lazily initialises a singleton
  `TavilyClient`. All failures inside `get_research_brief` degrade to `""` —
  the function never raises. Absent `TAVILY_API_KEY` short-circuits immediately.
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
  returns `{key: value}`. Keys used by the agent: `sender_profile`,
  `outreach_prompt`, `applied_intro_prompt`, `applied_followup_prompt`,
  `subject_prompt`, `critic_prompt`, `research_query_prompt`,
  `research_curate_prompt`, `research_injection`. If the table is unreachable,
  `run()` falls back to an empty dict and each module uses its `config.py`
  defaults. Prompts can be edited live via the contact-manager's Prompts &
  Profile page; changes take effect the next run.
- **`research_cache` table**: keyed by `"{name_lower}|{company_lower}"`. Stores
  `brief_text` (may be `""` for no-footprint contacts), `brief_json` (raw Tavily
  payload), and `cached_at`. TTL is 7 days (`RESEARCH_CACHE_TTL_DAYS`). Caching
  empty briefs is intentional — prevents re-querying Tavily for contacts with no
  public footprint every run. `db.get_research_cache` / `db.set_research_cache`
  are the only accessors; both use `db._retry`.

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

`daily_agent.yml` requires these secrets: `ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`,
`GMAIL_APP_PASSWORD`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `TAVILY_API_KEY`.
`monitor.yml` does **not** receive `TAVILY_API_KEY` — monitor never imports
`research`.

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
- `[Gmail]/Sent Mail` mailbox name is double-quoted: `'"[Gmail]/Sent Mail"'`.
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
- `find_sent_for_thread(message_id, since_date, mode)` searches `[Gmail]/Sent Mail`
  with `readonly=True`. Use `mode="first_touch"` to match on `Message-ID`,
  `mode="followup"` to match on `In-Reply-To`. Returns the **actual Message-ID string**
  from the found sent email (not the stored one — Gmail may rewrite it on send), or
  `None` if not found. Never raises. The `message_id` search arg is double-quoted in
  the IMAP command because angle brackets are IMAP special characters.

## Sent-draft auto-detection

`monitor.detect_sent_drafts()` runs at the start of every monitor cycle,
before reply detection. It flips contacts from `*_drafted` to `*_sent`
automatically when the user sends a draft from Gmail.

Key invariants:
- **Best-effort, per-contact**: any per-contact failure (IMAP error, Supabase
  error, missing message_id) logs a warning and continues to the next contact.
  A single failure never aborts the loop or blocks reply detection.
- **`message_id` required**: contacts with `message_id=None` are skipped with
  an info log. The agent populates `message_id` when it creates the first draft.
- **Cadence reuse**: `followup_date` is set using `FOLLOWUP_DAYS[action]` from
  `config.py` — the exact same values the agent uses. Look up the action via
  the inverse of `NEXT_STAGE`. Do not hardcode day counts here.
- **Terminal stages**: `breakup_drafted` and `applied_followup_drafted` are in
  `TERMINAL_DRAFTED_STAGES`. When flipped, `followup_date` is cleared to `None`
  (via `update_contact(..., clear_followup_date=True)`).
- **`update_contact` change**: `db.update_contact` now accepts `clear_followup_date=False`.
  When `True`, it explicitly sets `followup_date=None` in the Supabase update.
  All existing callers are unaffected (default is `False`).
- **`detect_replies()` independence**: `run()` wraps `detect_sent_drafts()` in
  `try/except` so a catastrophic failure there never blocks reply detection.
- **v1 limitation**: depends on Gmail preserving the draft's `Message-ID` on
  send. Scheduled-send drafts and heavily-edited first-touch drafts may not be
  detected. If the monitor log shows `found=False` after sending, Gmail may
  have rewritten the Message-ID — a subject+recipient fallback would be v2.

## Tests

- **Every code change must ship with tests.** New functions get a test file or new cases in the nearest existing test file. Bug fixes get a regression test that would have caught the bug. No exceptions for "trivial" changes — if it's worth changing, it's worth a test.
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
- `tests/test_sent_detection.py` — parametrized tests for `detect_sent_drafts()`.
- `tests/test_sent_search.py` — tests for `gmail.find_sent_for_thread()`.
- `tests/test_critic.py` — tests for `_run_critic` and `critique_and_revise`.
- `tests/test_emailer_tier1.py` — parametrized gating + end-to-end retry tests for the Tier 1 critic loop.
- `tests/test_research_queries.py` — `_generate_queries` unit tests.
- `tests/test_research_tavily.py` — `_run_tavily` and `_run_hardcoded_fallback` unit tests.
- `tests/test_research_curate.py` — `_curate_brief` unit tests (includes disambiguation guard).
- `tests/test_research_brief.py` — `get_research_brief` integration tests (cache TTL, pipeline, never-raises parametrize).
- `tests/test_emailer_research.py` — research gating (tier/action matrix) and injection behavior in `generate_email`.

## Critic loop (v1)

`emailer.critique_and_revise()` runs on Tier 1 first-touch emails only
(`send_first_touch` and `send_applied_intro` when `contact["tier"] == 1`).
All other tiers and all follow-up actions skip it entirely.

- **Threshold**: `CRITIC_PASS_THRESHOLD = 6` (in `config.py`). Drafts scoring
  `>= 6` are returned unchanged. Drafts scoring `< 6` trigger one regeneration
  via `extra_instruction` kwarg on the body generator. Max 2 generation
  attempts total — never loop.
- **Prompt**: `critic_prompt` key in the Supabase `prompts` table
  (`sort_order=25`, between Outreach Email and Applied Intro on `/prompts`).
  `CRITIC_PROMPT_DEFAULT` in `config.py` is the fallback.
- **Failure safety**: any error inside `_run_critic` (format error, Claude
  error, JSON parse failure) returns the pass-through fallback
  `{"score": 7, "failed_criteria": [], "feedback": ""}` and logs a warning.
  Critic failures never block draft creation.
- **Log marker**: `[CRITIC] | name | company | score=N | failed=[...] | retried=<bool>`
  appears exactly once per Tier 1 first-touch draft.
- **Cost**: adds 1 critic Claude call per Tier 1 first-touch, plus 1 optional
  regeneration call. Subject is also regenerated on retry.

## Research pipeline (v1)

`research.get_research_brief(contact, sender_profile, prompts)` runs for Tier 1
and Tier 2 first-touch actions only (`send_first_touch`, `send_applied_intro`).
Follow-ups and Tier 3 skip research entirely. The brief is injected into the
outreach/applied-intro prompt before body generation and persists through any
critic retry.

**Pipeline steps:**
1. **Cache check** — `research_cache` keyed by `name_lower|company_lower`. Hit
   within 7 days: return `brief_text` immediately, no Tavily or Claude calls.
2. **Query generation** — Haiku (`RESEARCH_QUERY_MODEL`) generates 1-5 Tavily
   search queries, person-first (every query includes the company name to
   disambiguate). Returns JSON array. Hard-capped at `RESEARCH_MAX_QUERIES=5`,
   each query truncated to `RESEARCH_MAX_QUERY_LEN=80` chars.
3. **Tavily execution** — `search_depth="basic"`, `max_results=3` per query.
   Per-query failures skip silently. If query gen returns `[]`, the hardcoded
   fallback `"{company} news 2026"` fires.
4. **Brief curation** — Haiku (`RESEARCH_CURATE_MODEL`) synthesises raw results
   into a short markdown brief. Applies a strict disambiguation rule: any fact
   that could refer to a different person with the same name is excluded. Returns
   `NO_RELIABLE_BRIEF` (mapped to `""`) if results are ambiguous or off-topic.
5. **Cache write** — `brief_text` cached even when `""` to prevent re-querying
   no-footprint contacts on the next run.

**Log markers (all in `agent.log`):**
- `[RESEARCH-Q]` — queries generated by Haiku
- `[RESEARCH-T]` — Tavily execution results
- `[RESEARCH-F]` — hardcoded fallback fired (query gen returned `[]`)
- `[RESEARCH-C]` — curator output (`reliable=True/False`)
- `[RESEARCH]` — top-level summary (`path=cache|fresh`)
- `[OUTREACH] | name | company | tier=N | has_brief=True/False` — emitted from
  `emailer.generate_email` for every first-touch, showing whether a brief was
  injected

**Worst-case failure mode**: Tavily returns facts about a different person with
the same name. The curation step is the guard against this — if you see wrong
facts in a draft, tighten `research_curate_prompt` in `/prompts` and add a
failure example. Never trust a confident-sounding brief without verifying a
specific fact against a real source before sending.

**Cost** (approximate): ~1-2 USD/month extra Anthropic spend at typical volume
(two Haiku calls per researched contact). Tavily free tier: 1000 credits/month;
~3 queries per contact = ~30 credits per 10 first-touches.

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

## Pre-flight checks (preflight.py)

Six deterministic checks run on every generated body after Claude returns, before the critic and before any Gmail draft is created. On failure, one automatic regeneration with the error list as extra instruction. If the retry also fails: log to `agent_events` with `status="blocked_preflight"` and raise `ValueError` — no draft is created.

Checks (all six are separate functions with distinct error messages):
1. `check_placeholder_braces` — flags `{UPPER_CASE}` tokens
2. `check_unfilled_brackets` — flags `[Title Case]` tokens
3. `check_first_name_presence` — contact's first name must appear in the body
4. `check_wrong_company` — flags watchlist companies in body that don't match contact's company (schema-driven: `guardrail_company_list` prompt key, newline-delimited)
5. `check_stale_year` — flags `year < current_year` within 50-char window of future-tense phrases
6. `check_forbidden_phrases` — substring match against `forbidden_phrases` prompt key, newline-delimited

Pre-flight runs on ALL actions (outreach, follow-up, reply). Critic runs only on Tier 1 first-touch. Pre-flight is the inner gate; critic is the outer gate.

In tests: `mocker.patch("preflight.check", return_value=[])` and `mocker.patch("db.log_agent_event")` are required in any test that calls `generate_email()`.

## Reply pipeline (monitor.py)

`monitor.run()` now has four sequential phases, each wrapped in `try/except` so failure in one never blocks the next:
1. `detect_sent_drafts()` — unchanged
2. `detect_replies(prompts)` — header-based IMAP INBOX scan; returns list of newly-classified contacts
3. (called inside detect_replies) `_classify_reply()` — Claude Haiku classifies reply body → `classifier_status`
4. `_draft_reply_responses(classified, prompts)` — calls `reply_drafter.draft_reply()` for positive_reply/soft_yes contacts

**Reply detection invariants:**
- Matches via `In-Reply-To` header first, then walks `References` chain. No FROM fallback.
- Skip message if no header matches any stored `contacts.message_id`.
- Auto-reply bypass: if `Auto-Submitted` header is not "no" or `X-Auto-Response-Suppress` is present, classify as `auto_reply` without calling Claude.
- Skip contact if `classifier_status` is already set (idempotent across 2-hour runs).
- `email_messages` insert is upsert on `message_id` (ON CONFLICT DO NOTHING).
- One IMAP connection for all contacts in the INBOX phase. `readonly=True` for the SINCE search; switches to read-write only for label copy.

## Reply drafting (reply_drafter.py)

`draft_reply(contact, reply_body_text, prompts)`:
- Generates reply body via `REPLY_RESPONSE_MODEL` (Sonnet).
- Runs pre-flight; one retry on failure; hard block with `agent_events` log if still failing.
- **Never calls the critic loop.**
- Creates Gmail draft via `create_draft()` with `in_reply_to=contact.message_id`.
- Stores draft in `email_messages` as `direction="outgoing"`.
- Applies label `Cold Outreach/Reply` (best-effort).
- Updates stage to `reply_drafted` via `update_contact(clear_followup_date=True)` — reply is terminal.
- Skips silently if `classifier_status` not in `{"positive_reply", "soft_yes"}`.
- Skips if already in `reply_drafted` or `reply_sent`.

## New Supabase tables

**`email_messages`** — durable copy of every sent/received email per contact.
- `contact_id INTEGER FK→contacts(id) ON DELETE CASCADE`
- `direction TEXT` ('outgoing'|'incoming')
- `UNIQUE(message_id) WHERE message_id IS NOT NULL` — idempotency index
- Index on `(contact_id, sent_at DESC)`
- RLS disabled. Access via `db.insert_email_message()` and `db.get_email_messages(contact_id)`.

**`agent_events`** — per-action audit log (preflight, classify_reply, draft_reply, etc.).
- `run_id INTEGER FK→agent_runs(id) ON DELETE SET NULL` (nullable — events outside cron runs still log)
- `status TEXT` ('running'|'success'|'failed'|'blocked_preflight')
- `blocked_checks JSONB` — list of failed pre-flight check strings
- Indexes on `(started_at DESC)`, `status`, `contact_id`
- RLS disabled. Access via `db.log_agent_event()` (best-effort, never raises) and `db.get_agent_events(limit=100)`.

## New contacts column

**`contacts.classifier_status TEXT nullable`** — written by monitor's reply classifier; never by user or agent. User manages `reply_status` separately. "Needs response" filter: `classifier_status IN ('positive_reply','soft_yes') AND reply_status NOT IN ('interested','call_scheduled','dead')`.

## Reply stages

`REPLY_STAGES = ["reply_drafted", "reply_sent"]` in `constants.py`. These are included in `DRAFTED_STAGES` (comprehension extended to include `REPLY_STAGES`). `reply_drafted` is in `TERMINAL_DRAFTED_STAGES`. `DRAFTED_TO_SENT` in `agent.py` includes `"reply_drafted": "reply_sent"`.

**`REPLY_STAGES` is a mirrored constant** — update both `constants.py` (Python) and `types.ts` (TypeScript) if adding new reply stages.

## Prompt keys (Supabase prompts table — 21 rows)

Loaded at startup by `db.load_prompts()` → `{key: value}`. Absent keys fall back to
`config.py` constants; `emailer.py` logs `[WARN] prompt key X not in DB — using fallback`.
Instruction-level keys (sort_orders 11–18) use `get_tier_instruction()`,
`get_template_instruction()`, `get_dartmouth_instruction()` in `emailer.py`.

| key | sort_order | purpose |
|-----|-----------|---------|
| `sender_profile` | 10 | Sender bio; injected as `{profile}` into every email template |
| `outreach_first_touch_instruction` | 11 | `{template_instruction}` for `cold_intro` emails |
| `outreach_followup1_instruction` | 12 | `{template_instruction}` for `follow_up_1` emails |
| `outreach_followup2_instruction` | 13 | `{template_instruction}` for `follow_up_2` emails |
| `outreach_breakup_instruction` | 14 | `{template_instruction}` for `breakup` emails |
| `tier_1_instruction` | 15 | `{tier_instruction}` for Tier 1 contacts |
| `tier_2_instruction` | 16 | `{tier_instruction}` for Tier 2 contacts |
| `tier_3_instruction` | 17 | `{tier_instruction}` for Tier 3 contacts |
| `dartmouth_instruction` | 18 | `{dartmouth_instruction}` when alumnus detected; used in outreach AND applied |
| `outreach_prompt` | 20 | Cold outreach email body (all 4 templates) |
| `critic_prompt` | 25 | Critic editor prompt; Tier 1 first-touch only |
| `research_query_prompt` | 26 | Haiku generates 1–5 Tavily queries |
| `research_curate_prompt` | 27 | Haiku synthesises Tavily results into a brief |
| `research_injection` | 28 | Wraps curated brief before appending to outreach/applied-intro |
| `applied_intro_prompt` | 30 | Applied intro email with 3 bullets |
| `applied_followup_prompt` | 40 | Applied follow-up; short, adds one new value |
| `subject_prompt` | 50 | Subject line; called once per first-touch email |
| `reply_classification_prompt` | 60 | Claude Haiku classifier; returns `{"classifier_status": "..."}` JSON |
| `reply_response_prompt` | 61 | Reply body template for `reply_drafter.py` |
| `forbidden_phrases` | 62 | Newline-delimited banned substrings for pre-flight check 6 |
| `guardrail_company_list` | 63 | Newline-delimited company watchwords for pre-flight check 4 |

**Locked:** `/api/extract` prompt is hardcoded — bound to `ExtractedContact` JSON schema.

## New db.py functions

- `log_agent_event(event_type, contact_id, status, ...)` — best-effort insert to agent_events; never raises
- `get_agent_events(limit=100)` — ordered by started_at DESC; used by /runs page
- `update_classifier_status(contact_id, value)` — sets classifier_status; used by monitor
- `insert_email_message(contact_id, direction, sent_at, ...)` — upsert on message_id; used by agent (outgoing) and monitor (incoming)
- `get_email_messages(contact_id)` — ordered by sent_at ASC; used by thread view in contact sheet
- `update_message_id(contact_id, message_id)` — updates only `message_id`; called by `detect_sent_drafts` when Gmail rewrites the ID on send (threading fix)

## New config.py constants

- `REPLY_CLASSIFICATION_MODEL = "claude-haiku-4-5-20251001"` — Haiku for reply classification
- `REPLY_RESPONSE_MODEL = "claude-sonnet-4-6"` — Sonnet for reply body generation
- `REPLY_CLASSIFICATION_DEFAULT` — fallback classification prompt
- `REPLY_RESPONSE_DEFAULT` — fallback reply response template
