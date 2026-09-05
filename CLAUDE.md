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
engagement_report.py
reply_drafter.py
research.py
ats.py
email_verify.py
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
job_discovery.py
jobright.py
resume_agent.py
resume_lint.py
resume_build.py
resume_scrub.py
resume/
usage_tracking.py
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
`[RESEARCH-C]`, `[RESEARCH-A]`, or a level tag from a warning/error. Don't change the timestamp format — the
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
  docs/python/sent-detection.md. `engagement_report.py` keeps a **fourth**
  copy, `_FIRST_TOUCH_DRAFTED_STAGES` — the same stage-level triple as
  monitor's. It only affects which rows the report counts, never what gets
  drafted, but a drifted copy makes the report silently undercount a whole
  track.
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

## Form D funding signal (sub-project 4 — LIVE, wired into visa_intel_ingest.yml)

`ingest_form_d.py` turns SEC Form D exempt-offering filings into a
"recently raised" signal on `company_intel`. Same decision-support posture as the
H-1B gate: never an auto-reject, never a targeting change.

**Full pipeline now exists in `ingest_form_d.py`: discover → download → parse →
aggregate → match → write → `run()` → `__main__`.**
`download_quarter(url, dest_dir)` fetches one quarterly ZIP and extracts its three
tables into `dest_dir`, matching each archive member on basename only (never
`extractall()`, since SEC's internal path prefix drifts between quarters the same way
the index page's does) — the `ingest_oflc_lca.py`-`download_file()` equivalent.
`match_funding_to_company(normalized_company_name, funding_corpus)` matches by exact
normalized name only — `entity_resolution.normalize()` + `canonicalize_alias_group()`,
same pipeline as everywhere else, but **deliberately does NOT fall back to
`entity_resolution.resolve()`/`classify()`'s fuzzy tier.** Live-verified against real
2025Q3–2026Q2 data before this was caught: querying `"scale ai"` fuzzy-auto-classified
against a corpus entry `"scale social ai"` at score 100 — `token_set_ratio` scores a
token-subset match near 100 regardless of the corpus name's extra tokens, a risk
`entity_resolution.classify()`'s own docstring already flags. For the H-1B gate that's
tolerable because a human can reject a bad match via `/visa-review`; there is no
equivalent review UI for funding claims, so exact-match-only trades missed variant-name
matches (governance-safe — degrades to unknown) for zero false positives.
`db.upsert_company_funding(rows)` batch-upserts `company_intel` on `normalized_name`,
same shape as `upsert_company_intel`; it only ever writes the columns it's handed
(PostgREST upsert semantics), so it can never touch `sponsors_h1b`/`match_status`.
`fold_issuer()` canonicalizes through `canonicalize_alias_group()` too (not just
`normalize()`) — required by the alias-group invariant above, and load-bearing now that
matching is exact-only: an aliased issuer name that only normalized (not canonicalized)
would silently never match its company_intel row's canonical key.

`last_funding_amount` is `TOTALAMOUNTSOLD` from a single Form D offering, not lifetime
funding raised — don't render it as "total raised" in any UI.

**`run()` only ever writes onto `company_intel` rows that already exist** (fetched via
`db.get_company_intel_by_normalized_names` for every distinct contact company). It never
creates a row itself — that's `visa_match_new.py`'s job exclusively; letting the Form D
matcher create rows would manufacture `company_intel` rows the H-1B matcher never
authored. `last_funding_checked_at` is written for every existing row `run()` evaluates,
matched or not (per the migration's own column comment — "whether or not a raise was
found") — this is safe specifically because the row already exists, so it's always an
UPDATE, never an INSERT.

The migration (`20260819050000_add_funding_signal_to_company_intel.sql`) **is applied**
(pushed 2026-08-23 via `supabase db push`, ahead of the writer/matcher since it's purely
additive — nullable columns, `IF NOT EXISTS`, no backfill, no risk to existing rows).

**Live-verified 2026-08-23** (same "run it manually before scheduling it" pattern as
Stage 1, which found 4 bugs on its first live run despite a green suite). Link discovery,
`download_quarter`, and `parse_form_d_quarter` all ran clean against real 2025Q3–2026Q2
SEC data on the first try. One real bug did surface — see the exact-match-only note
above — fixed before the run that actually wrote to prod. That verified run: 36 contact
companies checked, 1 real match (Shield AI, `$591,806,870` on `2026-05-01`), 0 errors;
`sponsors_h1b`/`match_status` on the matched row were confirmed untouched. Now wired as
the last step in `visa_intel_ingest.yml`, `continue-on-error: true`, after the H-1B full
re-match (see GitHub Actions below).

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
  payload plus `ats_jobs`), and `cached_at`. TTL is 7 days (`RESEARCH_CACHE_TTL_DAYS`). Caching
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

Four workflows live in `.github/workflows/`:

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
- **`visa_intel_ingest.yml`** (named "Visa & Funding Intel Ingestion") — quarterly
  (`0 10 5 1,4,7,10 *`), runs `ingest_oflc_lca.py` → `ingest_uscis_datahub.py` →
  `python visa_match_new.py --full` (the `--full` flag is required — see Visa & wage
  intelligence gate above) → `ingest_form_d.py` (Form D funding signal — runs last,
  after the H-1B full re-match, since it only writes onto `company_intel` rows that
  already exist and a fresh quarter's re-match may have just created some). All three
  steps after the first LCA ingest are `continue-on-error: true`. `timeout-minutes: 120`
  (vs. 30 for the daily job) — a fresh ingest processes several fiscal years of DOL
  data. First run should be triggered manually via `workflow_dispatch`.
- **`build-continue.yml`** — hourly (`0 * * * *`), a self-driving continuation of the
  full-fledged job-platform buildout (see
  `docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md`). Runs
  `anthropics/claude-code-action@v1` with `ANTHROPIC_API_KEY` — **never** a
  `CLAUDE_CODE_OAUTH_TOKEN`, deliberately, because that would bill against the same
  rolling 5-hour subscription window this workflow exists to work around, defeating
  the entire point. Reads the current phase's plan file under
  `docs/superpowers/plans/` (the plan's own `- [ ]`/`- [x]` checkboxes ARE the
  progress state — no separate tracking file exists), executes exactly one task per
  run, commits, and pushes straight to `main`. When a step needs a capability the
  runner doesn't have (e.g. `supabase db push` with no CI-side Supabase auth), it
  leaves that box unchecked with a `<!-- blocked: ... -->` note instead of skipping
  it silently. **Never authors the next phase's plan itself** — plugin skills
  (`superpowers:writing-plans`) aren't loadable in this action without inputs it
  doesn't set, so plan-writing stays a human, interactive-session task; the workflow
  commits a note and stops at a phase boundary instead. Installs Python deps via
  `requirements-dev.txt` (not `requirements.txt`) since pytest/pytest-mock live there.
  Disables itself (`gh workflow disable`) once every phase's plan is
  fully checked. `concurrency: { group: build-continue, cancel-in-progress: false }`
  so hourly fires queue instead of racing if one run overlaps the next. Needs
  `contents: write` + `actions: write` (self-disable) + `id-token: write` (the
  action's own auth) in addition to the standard secrets below.

All four workflows: upload the relevant `.log` file as an artifact (30-day
retention) where one exists, and run `notify_failure.py` in an `if: failure()` step.
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
- `tests/test_ats.py` — `ats.py` slug derivation, HTML stripping, provider parsers, cascade order, relevance ranking, and a never-raises sweep. All HTTP mocked at `ats._http_get_json`.
- `tests/test_research_ats.py` — ATS channel wiring in `research.py`: curation input, `ats_trust_flags` flag-not-block, cache-hit skip, never-raises.
- `tests/test_emailer_research.py` — research gating (tier/action matrix) and injection behavior in `generate_email`.
- `tests/test_agent_paused.py` — pause guard: `get_pause_scope` error handling, `agent.run()` early-exit for `scope in ("agent","all")`, `monitor.run()` early-exit for `scope=="all"` only.
- `tests/test_entity_resolution.py` — `normalize()`/`resolve()`/`classify()`, the single-token auto-band guard, exact-match override, alias consolidation.
- `tests/test_ingest_oflc_lca.py` — `resolve_columns()` cross-year drift, `CASE_STATUS`/wage-level normalization, `parse_lca_file()` against constructed `.xlsx` fixtures, materiality-filter and row-cap behavior in `build_rows_for_upsert()`.
- `tests/test_ingest_uscis_datahub.py` — CSV column resolution, approval/denial summing, and the "never targets a name outside the existing employer_h1b_stats corpus" governance test in `build_enrichment_rows()`.
- `tests/test_visa_intel_db.py` — `db.py`'s `employer_h1b_stats`/`company_intel` accessors, following `test_db_draft_history.py`'s mock pattern.
- `tests/test_visa_matching.py` — `visa_matching.resolve_company()`, including the confirmed/rejected-row-is-never-reclassified governance tests.
- `tests/test_visa_match_new.py` — parametrized never-raises sweep for the daily matcher, plus per-company failure isolation.
- `tests/test_ingest_form_d.py` — Form D date/amount parsing, the `YES`/`NO` primary-issuer flag, both pooled-fund exclusion signals, latest-filing-wins aggregation, link discovery across both observed SEC path prefixes, download-and-extract round trips (nested/flat zip layouts, missing-table raises), `match_funding_to_company`'s exact-match-only gating (including the live-discovered token-subset false-positive regression), `fold_issuer`'s alias-group canonicalization, `run()`'s never-creates-a-company_intel-row governance test, per-company error isolation, and a malformed-quarter never-raises sweep.
- `tests/test_decision_context.py` — `emailer.hash_prompt_set` determinism, key-order independence, `{}`/`None` handling, unserializable values.
- `tests/test_engagement_report.py` — the report's `db.py` accessors (following `test_db_draft_history.py`'s mock pattern), the contact join, distinct-contact grouping, NULL-renders-as-"unknown", small-`n` rate suppression, and a malformed-row never-raises sweep.

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

## ATS career-page channel

`ats.py` is a second research channel alongside Tavily: it reads a target
company's own public ATS job board so the writer knows whether they are hiring
now in the contact's function. Self-contained by design (stdlib `urllib` only,
no `db`/`gmail`/`emailer` import, no new dependency, no API key, no secret).

- **`fetch_jobs(company, role=None)` never raises.** Every provider call is
  wrapped, the cascade is wrapped, and `research._run_ats` wraps it again. Any
  failure returns `[]`. Same posture as the best-effort labeling rule and the
  visa gate's `continue-on-error` step: enrichment never costs a draft.
- **Cascade, not fan-out**: Greenhouse → Ashby → Lever, first non-empty result
  wins and short-circuits. A company lives on one ATS. `404` is the clean-miss
  signal and is swallowed silently.
- **Do not reuse `entity_resolution.normalize()` for slugs.** It replaces
  punctuation with spaces to keep visa alias groups reachable; a URL slug needs
  the opposite. `ats._slug_candidates` is separate on purpose, and coupling them
  would mean a slug tweak silently reshapes visa entity matching.
- **Scan before curation, not after.** Job-description text is externally
  controlled, so `content_trust.scan` runs on the rendered postings section in
  `get_research_brief` *before* it enters the curation prompt. Matches log
  `[RESEARCH-X]` and land on the `research` `agent_events` row under
  **`ats_trust_flags`** — a key distinct from `trust_flags`, which is the
  curated brief's. Merging them would destroy the provenance. Flag-only: the
  postings are still used.
- **No new table and no `company_intel` column.** An open req is volatile, so
  persisting it as a company attribute would create the stale-false-claim
  problem the visa gate's governance invariant exists to prevent. Postings live
  in the 7-day `research_cache` blob (`brief_json.ats_jobs`) only.
- **Truncation**: the 6000-char curation cap applies to the Tavily portion only;
  the ATS section is appended after it. With no ATS hit the curation input is
  byte-identical to the pre-channel behaviour.
- `config.ATS_ENABLED` is the off-switch for this channel alone. `TAVILY_API_KEY`
  still gates the whole research feature, ATS included.

Tests: `tests/test_ats.py` (module, all HTTP mocked at `ats._http_get_json`),
`tests/test_research_ats.py` (wiring, flag-not-block, cache, never-raises).
Details: docs/python/research-pipeline.md.

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

## Decision-context tagging

`draft_history.decision_context JSONB` records **which live prompt configuration
produced a draft**, so a prompt rewrite can later be correlated with reply rates.
`prompts` rows are edited live via the contact-manager and have no version column,
so nothing else ties a draft back to the prompt values in effect when it was
generated.

`emailer.hash_prompt_set(prompts)` is the fingerprint: SHA-256 of
`json.dumps(prompts, sort_keys=True, default=str)`, first 16 hex chars — the same
truncation `gmail.create_draft` uses for `X-Cold-Email-Key`. Pure, no I/O. It is a
**whole-snapshot** hash, not per-template: the question is "which prompt
configuration was live," and `prompts` has no version column to derive anything
finer from.

Two call sites, both of which already receive the live `prompts` dict:
`agent._execute_draft` and `reply_drafter.draft_reply`. Each computes
`{"prompt_hash": hash_prompt_set(prompts)}` and passes it to `log_drafted_email`.
Both wrap the hash computation in a `try/except` — a fingerprint bug must never
cost a `draft_history` row or block a draft (in `reply_drafter.draft_reply`
especially: an unwrapped raise there would land in the outer `except` *after* the
Gmail draft already exists, logging a real draft as failed).

**No prompt-assembly change** — `prepare_email`/`finalize_email`/`generate_email`
signatures are untouched, so unlike Voice DNA there is **no
`assembleUserMessage.ts` mirror**: nothing about what gets built changed, only
what gets recorded about it afterwards.

**Governance invariant (same posture as the visa gate and Form D):** a NULL
`decision_context` means *not instrumented*, never "no context" and never zero.
Existing rows stay NULL permanently — the prompt snapshot behind a historical
draft was never captured, so no backfill is possible. Any reader must render NULL
as "unknown".

`engagement_report.py` (manual, **not** in cron, read-only) prints the join:
first-touch `draft_history` rows → contact `name`/`company`/`classifier_status` →
`research_cache.brief_reliable`. It is **a raw join, not a stats engine** — the
per-contact table always prints, but a per-`prompt_hash` reply rate prints only at
`n >= 5` (distinct contacts, not draft rows); below that it prints the count with
"n too small for a rate" rather than a misleading percentage.

**Part A of that spec (tracer links / open-and-click tracking) is rejected, not
deferred.** Do not add a tracking pixel or a redirect domain: it would be the only
feature here that changes the wire format of an outgoing email, and it cannot be
un-sent. The reply signal it would approximate already exists via
`classifier_status` + `draft_history.edit_detected`. See
docs/superpowers/specs/2026-08-25-engagement-outcome-tracking-design.md.

## Job application tracking (full-fledged buildout, Phase 1)

`job_applications` is a new table tracking the *application* pipeline
(`saved → applied → phone_screen → onsite → offer/rejected/withdrawn/accepted`),
deliberately independent of `contacts.stage` (which tracks the outreach
relationship only, and is never touched by this feature). `contact_id` is
nullable — an application can exist with no known contact — and `INTEGER` to
match `contacts.id`'s actual type. Backend accessors live in `db.py`
(`create_job_application`, `get_job_applications`, `update_job_application_stage`,
`get_job_application`); the contact-manager exposes it via a `/applications`
page and `/api/applications` + `/api/applications/[id]` routes. This is Phase 1
of a larger 5-phase buildout (job/company discovery, resume intelligence,
interview/offer tracking, email verification) — see
docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md for the
full plan and docs/python/db-schema.md for the table schema.

## Job discovery (full-fledged buildout, Phase 2 — ATS path)

`job_discovery.py` (manual, **not** in cron) scans the public ATS boards of every company already
known via `contacts` or `company_intel` (`db.get_all_company_intel_names()` flattens
`company_intel.raw_company_names`) using `ats.fetch_jobs(company, max_jobs=config.ATS_DISCOVERY_MAX_JOBS)`
— no `role` argument, since discovery wants every open posting in source order, not `ats.py`'s
single-best-match ranking. Results are filtered against the `target_roles` prompts key (any word
overlap with any target-role line counts as a match; an empty/missing key matches everything) and
persisted via `db.create_job_application(..., source='ats_scan')` at `stage='saved'`.

`db.create_job_application` is dedup-aware: it skips (returns `None`, callers must not treat that
as an error) any `job_url` that already exists on another row, backed by a partial unique index
(`idx_job_applications_job_url_unique`, `WHERE job_url IS NOT NULL`) as the database-level
race-condition backstop.

Every per-company and per-posting operation is independently `try/except`-wrapped — one company's
ATS failure or one posting's insert failure never stops the rest of the scan, same posture as
`visa_match_new.py`/`ingest_form_d.py`. Log marker `[DISCOVERY]`, own log file (`job_discovery.log`).

The JobRight puller (a second Phase 2 source, tagged `source='jobright'`) is a separate module and
plan — see `docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md`.

## JobRight puller (full-fledged buildout, Phase 2 — JobRight source)

`jobright.py` (manual by default; also scheduled daily via `jobright_pull.yml` per an explicit
user override of the original manual-only rule) logs into JobRight.ai's unofficial internal API
using a real session-cookie login (`POST /swan/auth/login/pwd`, verified via
`GET /swan/auth/newinfo`), paginates `GET /swan/recommend/list/jobs`, and persists matches into
`job_applications` at `stage='saved'`, `source='jobright'` — the same dedup-by-`job_url` path
`job_discovery.py` uses.

`JOBRIGHT_EMAIL`/`JOBRIGHT_PASSWORD` are **soft-optional** in `config.py`
(`os.environ.get`, not `os.environ[...]`) — unlike the five core secrets, they must not become a
hard-required import-time lookup, since every other script must keep working without them set.
`fetch_recommended_jobs()` no-ops (returns `[]`) when they're absent, and never raises past its own
boundary on any other failure (login failure, session check failure, HTTP failure, malformed
response) — same "enrichment must never cost a draft" posture as `ats.py`. Log marker `[JOBRIGHT]`,
own log file (`jobright.log`).

`_job_from_result()` captures `responsibilities`/`qualifications`/`benefits` (from
`jobResult.coreResponsibilities`/`skillSummaries`/`benefitsSummaries`) alongside the existing
`description` (`jobSummary`) — found via live network reconnaissance 2026-08-29: the same
`recommend/list/jobs` response already carries the full job-detail content shown on JobRight's own
`/jobs/info/<id>` page, so no extra per-posting request is needed. All four land in
`job_applications.posting_snapshot` (JSONB, schemaless by design — see docs/python/db-schema.md).
**Deliberately not captured**: JobRight's own H1B/funding/leadership tags (would contaminate the
governance-careful Stage-1 H-1B and Form D signals documented above, which are sourced from
official DOL/USCIS/SEC data with strict never-auto-reject rules) and the "Insider Connections" panel
(surfaces real named third-party people from the user's LinkedIn/school network — a materially
different privacy category than job-posting data, not something to persist without a deliberate
separate decision).

`config.JOBRIGHT_SORT_CONDITION` (default `0`) controls the `sortCondition` query param on
`recommend/list/jobs` — confirmed via live reconnaissance 2026-08-29 to have exactly three modes:
`0` Recommended (JobRight's blended default), `1` Most Recent, `2` Top Matched; any other value
silently falls back to `0` server-side. **Location/title/seniority/date-range/H1B-only filtering is
NOT a request param on this endpoint** — it's read implicitly from the account's saved filter object
(`POST /swan/filter/get/filter`, e.g. `daysAgo`, `isH1BOnly`, `jobTypes`, `seniority`, `locations`),
so `jobright.py` always pulls whatever filter is currently configured in the JobRight web UI itself.
Changing that filter from code would mean writing to the account's saved settings (a real account
mutation, not read-only recon) and was deliberately not built without the user explicitly asking for
it.

**Never log, print, persist, or commit the credential values, session cookies, or any response
field containing them.** See
`docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md` for the full decision
record, including the live-reconnaissance findings that grounded the real endpoint shapes used
here (not guessed) — the account authenticates via a native email+password login
(`/swan/auth/login/pwd`), a separate path from the Google Sign-In flow discovered first during
reconnaissance.

## Resume intelligence (full-fledged buildout, Phase 3)

`resume_agent.py` (manual only, two-command CLI: `--propose` then `--build`) generates a tailored
resume + cover letter for a specific `job_applications` row, distilled from the user's own 30-session
corpus spec (`RESUME_AGENT_SPEC.md`). `--propose` runs JD diagnosis/research/strategy and writes
`job_applications.resume_strategy` (JSONB) -- nothing is built yet. `--build` only proceeds if a
strategy already exists (i.e. a human reviewed it), then builds the DOCX, converts to PDF via
LibreOffice (`soffice`, external system binary), lints, scrubs metadata, and uploads to the
`resumes` Supabase Storage bucket. This mirrors the Gmail-draft pattern (propose, human reviews,
human acts), not the critic-loop pattern -- strategy correctness isn't something a rubric score can
validate.

Reference data lives in git-versioned `resume/data/*.json` (`master.json`, `metrics.json`,
`jargon.json`, `projects.json`, `skills.json`, `moments.json`), transcribed directly from the
corpus spec, not fabricated. `metrics.json` entries carry `resolved: null` for the three flagged
metric conflicts -- `resume_lint.check_metrics_whitelist` hard-fails a build that uses any of those
numbers until the user resolves them by hand.

`resume_lint.py`, `resume_build.py`, and `resume_scrub.py` are pure/deterministic modules that
**raise on failure rather than swallowing it** -- unlike `ats.py`/`jobright.py`'s best-effort
posture (built for unattended background runs), this pipeline is manual and interactive, so a
failure should surface immediately.

`resume_build.py`'s fitting ladder (`_FIT_RUNGS`) cumulatively tightens header/bullet/entry-line
spacing, margins, and body font size (10pt down to 9pt) across 5 rungs; the content-editing rungs
(orphan-word trims, section folding, bullet drops) are handled by `resume_agent.py --build`'s
one-retry regeneration loop instead (same pattern as `preflight.py`'s regenerate-with-error-list
retry), since those are content decisions, not formatting. The fitting ladder starts at
`build_docx`'s own `"standard"` margin preset, not the looser `"comfortable"` preset at index 0 of
`_MARGIN_LADDER` -- it only ever tightens from the normal baseline. Every paragraph is built via
`_new_paragraph()`, which zeroes python-docx's default template spacing (1.15x line height + 10pt
`space_after` on every paragraph unless overridden) to an explicit single-spaced baseline -- found
live: left at the default, that alone was enough extra height across ~20 paragraphs to push a
one-page resume onto a second page even at the tightest rung. `config.RESUME_MAX_BULLETS_PER_ENTRY`
(3) caps bullets per role/project/leadership entry to the first N of `bullet_ids`' own curated
order -- real historical resumes show 2-3 bullets per role, not every metric a role has bullet_ids
for; rendering all of them for a multi-role tenure never fit one page even at the floor rung.

**Format extracted from the user's own real resume corpus** (77 `.docx` files under
`~/Downloads/Career/Resumes/`, analyzed programmatically 2026-08-29 after the user found the
generated output "looks like a normal document," not a resume): name centered/bold/13pt, contact
line centered below it, section headers ALL CAPS/bold with a bottom-border rule
(`_add_bottom_border` -- present in every file checked, both the dominant ALL-CAPS/Calibri
pattern and a single more-recent Title-Case/Garamond variant; Calibri was picked as
`config.RESUME_FONT_NAME` since it's ~76/77 files vs. Garamond's one), right-tab-stopped
dates/locations at the content-width edge (`_add_right_tab_stop`), bold company/institution +
plain descriptor on one line and bold+italic title/program + italic date on the next
(`_add_two_line_entry`; projects use a one-line variant, `_add_one_line_entry`), real Word bulleted
lists (`"List Bullet"` style, not manual "•" characters), and Skills/Leadership as bold
`"Label: "` + inline comma-joined text, not bulleted. Consecutive roles at the same company
(`_add_experience_section`'s `last_company_key` tracking) share one company header line instead of
repeating it per promotion -- otherwise a 4-role tenure reads as 4 separate jobs.

**Two governance bugs found on the first live `--build` run, both silent-drop failure modes**:
(1) `strategy["section_order"]` was unconstrained, so the LLM invented section labels
("Selected Projects", "Core Competencies") that `build_docx`'s lookup silently dropped -- whole
sections vanished from the built resume with no error. (2) `strategy["projects_included"]` was
similarly unconstrained, so the LLM invented entirely fictional project names/descriptions instead
of choosing from `master.json`'s real projects -- `_resolve_master` silently produced an empty
Projects section rather than raising. Both are now fixed at two layers: the `_STRATEGY_PROMPT`
lists the real allowed set (`config.RESUME_ALLOWED_SECTIONS`) and the real project names
(`master.json`'s own keys) so the LLM is constrained upstream, and `build_docx`/`_resolve_master`
now raise `ValueError` on any name outside those sets as an enforcement backstop -- matching this
module's "raise on failure, never silently skip" Global Constraint, which the original code
violated in exactly the two places that mattered most. The same governance pattern
(`_check_skills_governance`, mirroring `resume_lint.check_metrics_whitelist`) validates
`strategy["skills_groups"]` against `skills.json`'s spine/swap_pool, raising `LintFailedError` on a
banned or fabricated skill.

`master.json` gained `name`, `contact` (location/phone/email/linkedin), per-role/per-project
`descriptor`/`location`/`period`, `education` as a list (was a single object -- the user has two
degrees), and a `leadership` bucket resolving four previously-orphaned personal metrics
(`personal_portfolio_return`, `personal_pinnacle_app`, `personal_madras_defense_lms`,
`personal_unschool_mentoring`) that no section builder had ever referenced. All values are the
user's own real facts, extracted from their real `.docx` files, not fabricated.

**Claude sometimes wraps a JSON response in a ` ```json ` markdown fence** despite the strategy
prompt saying "ONLY a JSON object, no other text" -- caught on the first real `--propose` run
against production data. `_strip_json_fence()` handles it before `json.loads`, the same pattern
already used in `research.py`'s `_generate_queries`.

**Token/cost tracking**: `_call_claude()` returns `(text, usage)` where `usage` is the real API
response's `{"input_tokens", "output_tokens"}`. Every call site passes `usage` to `_track_usage()`,
which computes cost from `config.RESUME_MODEL_COST_PER_MTOK_INPUT`/`_OUTPUT` (Claude Sonnet 4.6
pricing, checked 2026-08-29 -- update if `RESUME_MODEL` or Anthropic's pricing changes) and calls
`db.record_resume_usage()` to accumulate onto `job_applications.resume_tokens_input` /
`resume_tokens_output` / `resume_cost_usd`. These are running totals across every call for that
row (the strategy call in `--propose`, the cover-letter call in `--build`, and any lint-failure
retry) -- read-then-write, not atomic, which is fine for this manual single-user CLI.

**AI-content-detection evasion was explicitly declined** -- see
docs/superpowers/specs/2026-08-29-phase3-resume-intelligence-design.md's "Rejected, not deferred"
section. The humanizer lint pass (em dashes, jargon) and the PDF metadata scrub (tool-fingerprint
removal, realistic timestamps) both shipped; a dedicated AI-detector-evasion layer did not, and no
third-party tool was fetched or integrated for that purpose.

No auto-submit exists in this phase -- that is Phase 2.5 (auto-apply agent), a separate future
design gated behind its own explicit opt-in.

## System-wide Claude API cost tracking

`api_usage_log` is an append-only ledger covering **every** Claude call in the codebase, not just
resume_agent.py's per-application accumulator columns on `job_applications`. `usage_tracking.py`
(`calculate_cost(model, input_tokens, output_tokens)`, pure, raises `KeyError` for a model with no
entry in `config.MODEL_PRICING`; `log_usage(module, action, model, usage, contact_id=None,
job_application_id=None)`, best-effort, never raises) is the single shared entry point. Every real
number is a fact, not an estimate: `usage` always comes from the real Anthropic API response's
`.usage.input_tokens`/`.output_tokens`, and `config.MODEL_PRICING` prices (currently
`claude-sonnet-4-6` and `claude-haiku-4-5-20251001` -- the only two model strings any config
constant resolves to) were verified against platform.claude.com/docs/en/about-claude/pricing, not
guessed.

`emailer._call_claude` -- the shared function `agent.py`, `monitor.py`, `reply_drafter.py`,
`research.py`, `extract_voice.py`, and `reclassify_unrelated.py` all call -- gained optional
`module`/`action`/`contact_id` kwargs. Passing them logs a row after the real API call completes;
omitting them (any pre-existing call site that isn't updated) just skips logging for that call, it
never changes `_call_claude`'s return value or raises. Every real call site in this repo now
passes them: `emailer.py`'s own outreach/applied/networking/subject/critic calls (`module=
"emailer"`, `action=<the email action or "critic"/"subject">`, `contact_id` from the contact dict),
`monitor.py`'s reply classification, `reply_drafter.py`'s reply generation (+ its preflight
retry), `research.py`'s query generation and curation, `extract_voice.py`'s voice extraction
(no `contact_id` -- it summarizes a batch of sent mail, not one contact),
`reclassify_unrelated.py`'s retrospective reclassification. `resume_agent.py` keeps its own
independent Anthropic client (manual, never-cron posture) but its `_track_usage` now writes to
**both** `db.record_resume_usage` (the existing per-application running total) and
`usage_tracking.log_usage` (this central ledger, `action="propose"` or `"cover_letter"`).

`contact_id` and `job_application_id` are both nullable on `api_usage_log` and mutually exclusive
in practice -- a call is either about a contact-based flow or a job_applications resume flow,
never both.

## Email verification pre-flight (full-fledged buildout, Phase 5)

`email_verify.py` is a bounce-risk gate on `contact["email"]`, run once per contact
**before** a first-touch draft is even generated -- unlike `preflight.py`'s checks, which
run on the generated body and retry via regeneration, a bad email address is a property of
the contact record that no body rewrite can fix, so this is its own small gate rather than
a `preflight.check()` entry (the Phase 5 stub in the buildout spec explicitly left that
choice open).

Self-contained, in the shape of `content_trust.py`/`ats.py`: no `db`/`gmail`/`emailer`
import. Its only outside dependency is `dnspython` (new `requirements.txt` entry -- the
stdlib has no MX-record lookup). Public surface: `verify(email) -> EmailVerifyResult(status,
reason)`, a namedtuple, and it **never raises**.

`status` is one of three values -- syntax check first (a regex failure short-circuits
before any DNS call), then an MX lookup with an A/AAAA fallback per RFC 5321 when no MX is
published:
- `"invalid"` -- a deterministic negative: malformed syntax, or the domain has neither an
  MX nor a fallback A/AAAA record (`NXDOMAIN`, or `NoAnswer` on both lookups). The only
  status that blocks a draft.
- `"unknown"` -- the DNS lookup itself failed (`Timeout`, `NoNameservers`, any unexpected
  exception). **Never** treated as a block -- same governance shape as the visa gate's
  NULL-never-a-false-negative rule, applied to a blocking gate instead of a tagging one.
- `"valid"` -- syntax passes and the domain resolves.

Wired into `agent.run()`'s Phase 1 loop, gated to `_FIRST_TOUCH_ACTIONS` only (same set
Voice DNA and Tier-1 critic eligibility already gate on) and to **before** the batch
request is built -- the whole point is avoiding a wasted Claude call and draft on an
address that will bounce, so the check has to happen earlier than `preflight.py`'s checks
ever could. Only `result.status == "invalid"` skips the contact; `skipped` increments and
`update_contact` is never called, exactly like an existing `decide_action == "skip"`
outcome, so a bad address is re-checked (and re-skipped) on every run until the user fixes
it in Supabase -- there is no new table to remember the flag, deliberately, since today's
DNS failure can be tomorrow's success and vice versa (same never-persist-a-volatile-signal
restraint as the ATS channel and JobRight puller).

New marker `[EMAIL-VERIFY]`, and a new `agent_events` row (`event_type="email_verify"`,
`status="blocked_invalid_email"`, `metadata={"email", "reason"}`) via the existing
best-effort `db.log_agent_event`. `config.EMAIL_VERIFY_ENABLED` is the independent
off-switch, matching `ATS_ENABLED`'s role -- flipping it off restores Phase 1's behavior
byte-for-byte. `monitor.yml` is unaffected; it never imports `agent.py`'s draft-generation
path.

Design record: docs/superpowers/specs/2026-09-05-email-verification-preflight-design.md.

See docs/python/reply-pipeline.md for reply detection invariants and reply_drafter.py details.

See docs/python/db-schema.md for table schemas, new columns, reply stages, new db.py functions, and new config.py constants.

See docs/python/prompt-keys.md for the full Supabase prompts table (25 rows, sort_orders 10–65).
