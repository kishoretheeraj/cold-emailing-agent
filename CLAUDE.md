# Cold Email Agent — conventions for Claude

This is a Python automation that runs daily on GitHub Actions, reads contacts
from Supabase, generates personalized emails via the Claude API, and creates
Gmail drafts for review.

When working in this repo, follow the rules below. They reflect how the code
was actually written, not just style preferences.

---

## Module layout

```
agent.py
monitor.py
emailer.py
preflight.py
content_trust.py
extract_voice.py
reply_drafter.py
research.py
gmail.py
db.py
config.py
constants.py
notify_failure.py
entity_resolution.py
ingest_oflc_lca.py
ingest_uscis_datahub.py
ingest_form_d.py
visa_matching.py
visa_match_new.py
supabase/migrations/
```

Every module that touches the outside world is wrapped behind a function so
tests can mock it. Pure decision logic stays in `agent.py` (`decide_action`,
`_decide_outreach`, `_decide_applied`, `_decide_networking`, `_skip_reason`, `_parse_date`).

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

The marker is one of: `START`, `DONE`, `PAUSED`, `[OUTREACH]`, `[APPLIED]`, `[NETWORKING]`,
`[CRITIC]`, `[RESEARCH]`, `[RESEARCH-Q]`, `[RESEARCH-T]`, `[RESEARCH-F]`,
`[RESEARCH-C]`, or a level tag from a warning/error. Don't change the timestamp format — the
GitHub Actions artifacts and downstream scripts read it. Mode tags are looked up from
`agent._MODE_TAGS` / `emailer._MODE_TAGS` (two mirrored dicts, not a ternary) — add new modes
to both.

**Logging setup order invariant:** `logging.basicConfig` must be called before
any project-module import in every script. `agent.py` calls `basicConfig` at
module level (to claim `agent.log`); if `monitor.py` imports `agent` first,
agent's handler wins the root-logger race and all monitor output silently goes
to `agent.log`. Keep `basicConfig` + `log = getLogger(...)` at the top of each
script, above all project imports.

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
- **Modes**: `outreach`, `applied`, `networking` (enforced by a Postgres CHECK
  constraint on `contacts.mode`). `networking` is a relationship-first track —
  one first-touch + one follow-up, never a role pitch. The prompt leads with
  `contacts.connection_context` (free text) when present; when empty, the
  prompt is instructed to degrade to a genuinely low-ask cold opener rather
  than fabricate a connection. See `docs/python/db-schema.md`.

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
5. Add a `<Mode>/<Label>` formatted Gmail label — `Cold Outreach/<Label>` for
   outreach/applied actions, `Networking/<Label>` for networking actions.
   Each mode groups under its own top-level Gmail label.

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
  APPEND. It returns a `DraftResult` namedtuple `(message_id, gmail_draft_id,
  gmail_thread_id)`. `gmail_draft_id` is the Gmail API string ID (needed by
  `/api/send-draft`); `gmail_thread_id` is the X-GM-THRID integer from IMAP
  (needed by monitor's `find_sent_by_thread_id`). On duplicate detection it
  returns `DraftResult(None, None, None)`. **Do not fetch the ID from IMAP
  after append** — Gmail drafts have no server-assigned Message-ID until sent.
- `create_draft()` accepts `in_reply_to` and `references` kwargs. When
  `in_reply_to` is set, it adds `In-Reply-To` and `References` headers and
  auto-prefixes the subject with `Re: ` (unless it already starts with it).
- **Idempotency key**: when `contact_id` and `stage` are passed, `create_draft()`
  hashes `f"{contact_id}:{stage}:{date.today()}"` (SHA-256, first 16 hex chars)
  and writes it as an `X-Cold-Email-Key` custom header. Before appending, it
  searches `[Gmail]/Drafts` for that key; if found, it returns `None` without
  creating a duplicate. `agent.py` treats a `None` return as "already drafted
  today" and increments `skipped` instead of calling `update_contact()`.
- `_FIRST_TOUCH_ACTIONS = {"send_first_touch", "send_applied_intro", "send_networking_first_touch"}`
  is defined in both `agent.py` and `emailer.py`. Keep them in sync manually if
  new first-touch actions are added. Membership here also gates research
  injection and Tier-1 critic eligibility in `emailer.py` — adding an action
  to this set silently turns both on, so decide that deliberately, not by
  accident. `monitor.detect_sent_drafts()` keeps its own parallel stage-level
  set (`{"first_touch_drafted", "applied_intro_drafted", "networking_drafted"}`)
  to classify detection mode — it must list every first-touch `*_drafted`
  stage or that track silently loses subject-fallback sent-detection. See
  docs/python/sent-detection.md.
- After a first-touch draft is created, `agent.py` calls `save_thread_info()`
  to persist `message_id` and `original_subject` in Supabase.
- For all follow-up actions, `agent.py` calls `get_thread_info()` first and
  passes the stored `message_id` as `in_reply_to` to `create_draft()`.
- Before using `message_id` as `in_reply_to`, `_execute_draft` calls
  `_resolve_thread_message_id()` to verify the ID points to an actual sent
  email in Sent Mail (Gmail rewrites Message-IDs when a draft is sent, making
  the stored draft ID stale). Resolution tries `find_sent_by_thread_id` (via
  `gmail_thread_id`) then `find_sent_by_subject` (via `original_subject`). If
  the resolved ID differs, `update_message_id` is called to self-heal Supabase.
  Falls back to the stored ID if both lookups fail. Never runs for first-touch
  actions.
- **Gmail API draft creation for follow-ups**: `create_draft()` tries the Gmail
  API path first (before opening any IMAP connection) when `in_reply_to` is set
  and OAuth vars are present. The API call uses `_create_draft_via_api()`, which
  looks up the parent message's `threadId` and creates the draft inside that
  thread. This preserves `In-Reply-To` headers (Gmail silently strips them from
  IMAP APPEND). Falls back to IMAP APPEND if the API client is unavailable or
  the parent message is not found.
- **Sequential `In-Reply-To` chain**: `latest_message_id` column in `contacts`
  tracks the most recently sent email's Message-ID. `monitor.detect_sent_drafts`
  calls `update_latest_message_id` after every successful sent detection. The
  agent reads `latest_message_id` (falling back to `message_id`) when building
  the `in_reply_to` for follow-up drafts. `message_id` is kept as the
  first-touch ID and is never overwritten by follow-up detection.
- **Reply detection lookup**: `detect_replies()` builds `by_message_id` as a
  union of `message_id` AND `latest_message_id` so that replies referencing
  only the immediate parent email (common in webmail clients) are still detected.
  It also includes contacts in `*_drafted` stages (not just `*_sent`), so
  replies that arrive before the next draft has been reviewed and sent are
  captured. The `classifier_status IS NOT NULL` guard prevents double-classification.
- `apply_label_to_latest_draft(label_name, gmail_draft_id=None, message_id=None)`:
  when `gmail_draft_id` is provided and OAuth is available, uses the Gmail API
  `messages.modify` to add the label (no IMAP COPY, no duplicates). Falls back
  to IMAP COPY otherwise — targeting the draft via `HEADER Message-ID` search
  when `message_id` is passed (both current callers, `agent.py` and
  `reply_drafter.py`, always pass it), instead of blindly labeling whatever
  draft has the highest UID in `[Gmail]/Drafts` (a race with any other draft
  being created — manual or concurrent — could mislabel it). Falls back to the
  highest-UID draft only when `message_id` is omitted.
- `generate_email()` in `emailer.py` accepts `original_subject=None`. For
  follow-up actions it returns `"Re: " + original_subject` without calling
  Claude — only first-touch actions call `_generate_subject()`.
- `message_id` and `original_subject` columns are **agent-managed**. Never
  write them manually — they are set once after the first draft (and may be
  updated later by the monitor or `_resolve_thread_message_id`).
  `latest_message_id` is updated by the monitor after every sent detection.

## Visa & wage intelligence gate (Stage 1: H-1B sponsorship)

Decision-support signal (never an auto-reject) tagging each target company
with its H-1B sponsorship history from free DOL/USCIS open data. New modules:
`entity_resolution.py` (name normalization + rapidfuzz matching, no
Claude/Gmail dependency), `ingest_oflc_lca.py` / `ingest_uscis_datahub.py`
(quarterly ingestion, own workflow), `visa_matching.py` (shared
match-to-company_intel-row logic), `visa_match_new.py` (matcher with two
modes — see below — wired as a `continue-on-error: true` step in
`daily_agent.yml`, deliberately separate from `agent.py::run()`, same
reasoning as the best-effort labeling rule below). New tables
`employer_h1b_stats` and `company_intel`; `contacts.company_intel_id` is the
join column.

**Governance invariant**: Stage 1 code only ever writes `company_intel.sponsors_h1b`
as `NULL` or `true`. Setting it `false` requires an explicit human "confirmed"
decision via the contact-manager's `/visa-review` screen. A missed or
excluded entity-resolution match must always degrade to "unknown" (`NULL`),
never present as a false negative.

**`visa_match_new.py` has two modes** (`run(full_rematch=...)`, CLI `--full`):
incremental (default) only processes contacts with `company_intel_id IS NULL`;
full (`--full`, used by the quarterly workflow) re-resolves every distinct
contact company, since a company that fell to `unknown` against an earlier
corpus may match once new employer data lands. Both skip
`confirmed`/`rejected` `company_intel` rows (never overwrite a human
decision) while refreshing their denormalized stats. **Never wire the
quarterly re-match step without `--full`** — incremental mode silently
no-ops on every contact once it has any `company_intel_id`, `unknown`
included, which was a real production incident (see docs/python/db-schema.md).

**`db.get_employer_h1b_stats_corpus()` paginates via `.range()`** — PostgREST
caps a single request's rows (commonly 1000) and this table can hold up to
150,000. Don't revert to a plain `.select().execute()` here.

**Alias-group canonicalization must happen on every code path that produces
or looks up a `normalized_name`.** `entity_resolution.KNOWN_ALIAS_GROUPS`
folds known multi-legal-entity families (e.g. Amazon's `amazon com services`
/ `amazon web services` / `amazon data services`) into one canonical
`employer_h1b_stats` row at ingestion time via
`canonicalize_alias_group(normalize(name))` (`ingest_oflc_lca.py`,
`ingest_uscis_datahub.py`). `visa_matching.resolve_company()` and
`visa_match_new._normalize_for_lookup()` must call
`canonicalize_alias_group()` too — on both the match query and the
existing-row lookup — or an aliased company's contacts silently stop
resolving against the canonical corpus row, and the existing-row lookup for
a `confirmed`/`rejected` row misses too, bypassing the never-overwrite
governance check for that company. `entity_resolution.normalize()` replaces
(never deletes) punctuation for the same reason — deleting a period would
fuse `"Amazon.com"` into `"amazoncom"`, permanently unreachable from the
`"amazon com services"` alias-group member.

Full schema, entity-resolution calibration notes, and ingestion details:
see docs/python/db-schema.md.

## Form D funding signal (sub-project 4, INERT — not yet wired)

`ingest_form_d.py` turns SEC Form D exempt-offering filings into a
"recently raised" signal on `company_intel`. Same decision-support posture as the
H-1B gate: never an auto-reject, never a targeting change.

**This ships the parsing, aggregation, and download layer ONLY.** `ingest_form_d.download_quarter(url, dest_dir)`
fetches one quarterly ZIP and extracts its three tables into `dest_dir`, matching each
archive member on basename only (never `extractall()`, since SEC's internal path prefix
drifts between quarters the same way the index page's does) — this is the
`ingest_oflc_lca.py`-`download_file()` equivalent. Two pieces still do not exist and must
be built before any data can land: a **Supabase writer** (no `db.py` accessor) and a
**matcher** resolving issuer names onto `company_intel` (no `visa_match_new.py`
equivalent). There is also no `run()` orchestrator and no `__main__`.

The migration (`20260819050000_add_funding_signal_to_company_intel.sql`) **is applied**
(pushed 2026-08-23 via `supabase db push`, ahead of the writer/matcher since it's purely
additive — nullable columns, `IF NOT EXISTS`, no backfill, no risk to existing rows). The
four `company_intel` columns exist on the remote DB now but nothing writes to them yet.
There is still **no workflow step** — don't add one until the writer and matcher exist,
since a scheduled step with nothing to run is just dead CI time, not a safety issue.
Remaining build order: ~~downloader~~ → ~~apply migration~~ → writer → matcher →
workflow step.

**Governance invariant (same as the visa gate)**: no Form D match degrades to
`unknown`/NULL, never to "did not raise". Absence is not-observed, not a negative.

Two things that will silently break it if changed carelessly:
- **Link discovery matches the FILENAME only.** The SEC path prefix differs
  between quarters. Hardcoding a prefix is exactly how DOL LCA discovery broke.
- **Pooled-fund exclusion needs both signals** (`ISPOOLEDINVESTMENTFUNDTYPE` and
  `INDUSTRYGROUPTYPE`). The boolean alone misses hundreds of fund filings per
  quarter.

Verified source facts, sample output, and known limitations: docs/python/db-schema.md.

See docs/python/resilience.md for resilience patterns (Anthropic SDK, Tavily, Supabase retry, prompt validation, batch fallback).

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
  `networking_prompt`, `networking_followup_prompt`, `networking_subject_prompt`,
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
- **`system_config` table**: key-value store for runtime control flags. Currently
  one row: `key='pause_scope'`, value `'none'` | `'agent'` | `'all'`. Read at
  startup by both `agent.py` and `monitor.py` via `db.get_pause_scope()`.
  Written by the contact-manager's `/api/agent-config` route.
  `get_pause_scope()` returns `"none"` on any DB error (fail-open — never
  accidentally blocks a healthy run). Do not call `record_run` on a paused exit;
  `monitor.py` runs ~50×/day and would flood `agent_runs` otherwise.

## GitHub Actions

Three workflows live in `.github/workflows/`:

- **`daily_agent.yml`** — runs `agent.py` Mon-Fri at 4:37am EST (cron
  `37 9 * * 1-5`). Has a `check-duplicate` preflight job: if a
  `workflow_dispatch` (manual) run already succeeded today, the scheduled
  run is skipped to prevent double-drafting. The contact-manager UI has a
  "Run Agent" button (`/api/trigger-agent` route) that fires a
  `workflow_dispatch` — the dedup check prevents the scheduled run from
  duplicating it the same day. Requires `GITHUB_DISPATCH_TOKEN` env var
  (actions: write on the repo). Also runs `visa_match_new.py` as a final
  `continue-on-error: true` step (see Visa & wage intelligence gate above) —
  that step can never fail the workflow.
- **`monitor.yml`** — runs `monitor.py` every day (incl. weekends) on two schedules
  (EST = UTC-5): every 20 minutes from 8 AM–11:59 PM EST (crns `*/20 13-23 * * *`
  and `*/20 0-4 * * *`), and hourly at :30 from 12:30 AM–7:30 AM EST
  (cron `30 5-12 * * *`).
- **`visa_intel_ingest.yml`** — quarterly (`0 10 5 1,4,7,10 *`), runs
  `ingest_oflc_lca.py` → `ingest_uscis_datahub.py` → `python visa_match_new.py --full`
  (the `--full` flag is required — see Visa & wage intelligence gate above).
  `timeout-minutes: 120` (vs. 30 for the daily job) — a fresh ingest
  processes several fiscal years of DOL data. First run should be triggered
  manually via `workflow_dispatch`.

All three workflows: upload the relevant `.log` file as an artifact (30-day
retention), and run `notify_failure.py` in an `if: failure()` step.
All support `workflow_dispatch` for manual triggers.
Python version: **3.11**. Dependencies installed via `requirements.txt`.

`daily_agent.yml` and `visa_intel_ingest.yml` pass these secrets:
`ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `SUPABASE_URL`,
`SUPABASE_ANON_KEY`, plus (`daily_agent.yml` only) `TAVILY_API_KEY`,
`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`.
`monitor.yml` does **not** receive `TAVILY_API_KEY` — monitor never imports
`research`. Any new script that imports `db.py` needs at minimum
`SUPABASE_URL`/`SUPABASE_ANON_KEY` plus the other three core secrets, since
`config.py` reads all five via hard `os.environ[...]` lookups at import time
regardless of whether the script actually uses Claude/Gmail.

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

See docs/python/imap.md for IMAP patterns (mailbox quoting, DraftResult, find_sent_for_thread, _fetch_body_text).

See docs/python/sent-detection.md for sent-draft auto-detection invariants.

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
- `tests/test_agent_paused.py` — pause guard: `get_pause_scope` error handling, `agent.run()` early-exit for `scope in ("agent","all")`, `monitor.run()` early-exit for `scope=="all"` only.
- `tests/test_entity_resolution.py` — `normalize()`/`resolve()`/`classify()`, the single-token auto-band guard, exact-match override, alias consolidation.
- `tests/test_ingest_oflc_lca.py` — `resolve_columns()` cross-year drift, `CASE_STATUS`/wage-level normalization, `parse_lca_file()` against constructed `.xlsx` fixtures, materiality-filter and row-cap behavior in `build_rows_for_upsert()`.
- `tests/test_ingest_uscis_datahub.py` — CSV column resolution, approval/denial summing, and the "never targets a name outside the existing employer_h1b_stats corpus" governance test in `build_enrichment_rows()`.
- `tests/test_visa_intel_db.py` — `db.py`'s `employer_h1b_stats`/`company_intel` accessors, following `test_db_draft_history.py`'s mock pattern.
- `tests/test_visa_matching.py` — `visa_matching.resolve_company()`, including the confirmed/rejected-row-is-never-reclassified governance tests.
- `tests/test_visa_match_new.py` — parametrized never-raises sweep for the daily matcher, plus per-company failure isolation.
- `tests/test_ingest_form_d.py` — Form D date/amount parsing, the `YES`/`NO` primary-issuer flag, both pooled-fund exclusion signals, latest-filing-wins aggregation, link discovery across both observed SEC path prefixes, and a malformed-quarter never-raises sweep.

See docs/python/critic-loop.md for critic loop details (pass condition, prompts, common failures).

See docs/python/research-pipeline.md for research pipeline details (steps, log markers, failure mode, cost).

## Definition of done

Every feature or bug fix is not complete until all three of these are true:

1. **Tests pass** — `python3 -m pytest` exits green. No exceptions for "trivial" changes.
2. **CLAUDE.md updated** — if the change affects a documented behaviour (module layout, resilience pattern, decision invariant, prompt key, DB schema, test pattern), update the relevant section. Keep it concise; don't add new sections for things already derivable from the code.
3. **Memory updated** — add or update the relevant file under `~/.claude/projects/.../memory/`. One entry per shipped feature. Update `MEMORY.md` index. Delete or correct stale entries.

Do not commit until all three are done.

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

## Untrusted external content

`content_trust.scan(text)` is a pure, I/O-free scanner for prompt-injection
patterns in externally-sourced text. Two call sites, both **flag-only**:
`research.py` (curated brief, before caching) and `reply_drafter.py` (inbound
reply body, before generation). Matches land in `agent_events.metadata.trust_flags`
and log `[RESEARCH-X]` / `[REPLY-DRAFT-X]`.

**It never blocks a draft and never rewrites the text.** This is deliberate and
distinct from `preflight.py`, which blocks. Pre-flight guards our *output*;
content_trust annotates our *input*. A scanner exception degrades to "clean".

Do not move these checks into `preflight.check()` — it receives the generated
body (the research brief is already consumed by then) and its contract is
regenerate-then-hard-block, which is the wrong behaviour for this signal.

Patterns are deliberately narrow. Phrases like "forward this to your team" were
considered and excluded: they are ordinary business copy, and a guardrail that
cries wolf gets ignored.

## Voice DNA

`extract_voice.py` (manual, **not** in cron) reads recent sent mail via
`gmail.fetch_recent_sent()`, extracts a `## Writing Style` block with Claude, and
writes it to the `voice_dna` prompts row via `db.upsert_prompt`. Agent-authored
mail is excluded via the `X-Cold-Email-Key` header — never train the voice on our
own output. Degrades to a no-op (leaving any existing row untouched) on too few
samples, a Claude failure, or empty output.

`voice_dna` is injected as `voice_block` into **first-touch prompts only**
(`send_first_touch`, `send_applied_intro`, `send_networking_first_touch`),
threaded through `prepare_email` → `ctx` → `finalize_email` exactly like
`research_block`. Not applied to subject generation or the critic rubric.

**Mirrored in `contact-manager/src/lib/assembleUserMessage.ts`**
(`VOICE_INJECTION_FALLBACK` + `FIRST_TOUCH_ACTIONS`). Both sides must change
together or the Prompt Lab preview silently diverges from production. The em-dash
ban and `forbidden_phrases` still win over anything Voice DNA observes.

See docs/python/reply-pipeline.md for reply detection invariants and reply_drafter.py details.

See docs/python/db-schema.md for table schemas, new columns, reply stages, new db.py functions, and new config.py constants.

See docs/python/prompt-keys.md for the full Supabase prompts table (21 rows, sort_orders 10–63).
