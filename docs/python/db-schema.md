# Database schema additions

## New Supabase tables

**`draft_history`** — lifecycle of every Gmail draft created by the agent (Phase 0, 2026-05-20).
- `contact_id INTEGER FK→contacts(id) ON DELETE CASCADE`
- `stage TEXT NOT NULL` — the drafted stage at time of creation
- `subject TEXT, body TEXT` — draft content; updated by `/api/update-draft` on Quick Fix edits
- `message_id TEXT` — RFC822 Message-ID from IMAP APPEND
- `gmail_draft_id TEXT` — Gmail API draft ID; required by `/api/send-draft`
- `drafted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `sent_subject TEXT, sent_body TEXT, sent_at TIMESTAMPTZ` — populated by `/api/send-draft`
- `edit_detected BOOLEAN` — true when user edited the draft in Gmail before sending
- RLS disabled. Written by `db.log_drafted_email()` (called from `agent._execute_draft` and `reply_drafter.draft_reply`).
- Migration: `supabase/migrations/20260520000001_create_draft_history.sql`

**`email_messages`** — durable copy of every sent/received email per contact.
- `contact_id INTEGER FK→contacts(id) ON DELETE CASCADE`
- `direction TEXT` ('outgoing'|'incoming')
- `UNIQUE(message_id) WHERE message_id IS NOT NULL` — idempotency index
- Index on `(contact_id, sent_at DESC)`
- RLS disabled. Access via `db.insert_email_message()` and `db.get_email_messages(contact_id)`.

**`agent_events`** — per-action audit log (preflight, classify_reply, draft_reply, critic, sent_detected, research).
- `run_id INTEGER FK→agent_runs(id) ON DELETE SET NULL` (nullable)
- `contact_name TEXT nullable` — denormalized contact name; stored at write time so /runs page never needs a join
- `status TEXT` ('running'|'success'|'failed'|'blocked_preflight')
- `metadata JSONB nullable` — structured per-event data; shape varies by `event_type`:
  - `preflight`: `{"blocked_checks": ["check_name", ...]}`
  - `critic`: `{"score": N, "verdict": "PASS"|"FAIL", "rewrite_required": bool, "killed_by": [...], "failed_soft": [...], "retried": bool}`
  - `sent_detected`: `{"method": "thrid"|"mid"|"subject", "new_stage": "first_touch_sent"|...}`
  - `classify_reply`: `{"classifier_status": "positive_reply"|...}`
  - `research`: `{"cache_hit": bool, "queries_generated": N, "tavily_results": N, "brief_reliable": bool, "brief_length": N}` (cache hits also include `"cache_age_days": N`)
- Indexes on `(started_at DESC)`, `status`, `contact_id`
- RLS disabled. Access via `db.log_agent_event()` (best-effort, never raises) and `db.get_agent_events(limit=100)`.

**`agent_runs`** gains `source TEXT DEFAULT 'agent'` — values: `'agent'` (daily agent.py run) or `'monitor'` (monitor.py run). `monitor.run()` calls `record_run(source='monitor')` at the end of every cycle.

**`research_cache`** gains `queries_generated INT` and `brief_reliable BOOLEAN` — populated by `db.set_research_cache()`. Allows querying which contacts had no reliable brief without unpacking `brief_json`.

**`prompts_history`** — append-only audit log of every prompt value change. Populated automatically via a Supabase BEFORE UPDATE trigger on the `prompts` table (no application code needed). Columns: `id`, `key`, `old_value`, `new_value`, `changed_at`.

**`employer_h1b_stats`** and **`company_intel`** — added 2026-08-01 for the
visa & wage intelligence gate (Stage 1: H-1B sponsorship signal). See the
dedicated section below.

## New contacts column

**`contacts.classifier_status TEXT nullable`** — written by monitor's reply classifier; never by user or agent. User manages `reply_status` separately. "Needs response" filter: `classifier_status IN ('positive_reply','soft_yes') AND reply_status NOT IN ('interested','call_scheduled','dead')`.

**`contacts.connection_context TEXT nullable`** (added 2026-08-01, migration
`20260801005138_add_networking_mode.sql`) — free-text networking connection
hook (Dartmouth affiliation, mutual contact, shared background). Sheet-only
(not in `LIST_COLUMNS` on either side). Consumed by `emailer._connection_context_instruction()`
to build `{connection_context_instruction}` for `networking_prompt`; empty
means "no hook was asserted" and the prompt is instructed to degrade rather
than fabricate one. User-editable placeholder text (not an auto-written
value) suggests Dartmouth-flavored phrasing in the UI when `dartmouth=true`.

## Networking mode (added 2026-08-01)

Same migration widened `contacts.mode`'s CHECK constraint to
`IN ('outreach', 'applied', 'networking')` and seeded three prompt rows
(`networking_prompt`, `networking_followup_prompt`, `networking_subject_prompt`
— see `docs/python/prompt-keys.md`). `NETWORKING_STAGES` in `constants.py`:
`new → networking_drafted → networking_sent → networking_followup_drafted →
networking_followup_sent → closed` — one send + one follow-up nudge, no
further cadence. Reuses the generic reply pipeline unchanged (`REPLY_STAGES`,
`reply_status`) — no networking-specific reply stage or classifier category
exists. The migration's constraint widening uses a `pg_constraint` lookup
rather than a hardcoded constraint name, since the live schema had already
drifted from `setup_supabase.sql` before this change (see the migration file
for details) — don't assume `setup_supabase.sql` fully describes the live
schema.

## Reply stages

`REPLY_STAGES = ["reply_drafted", "reply_sent"]` in `constants.py`. These are included in `DRAFTED_STAGES` (comprehension extended to include `REPLY_STAGES`). `reply_drafted` is in `TERMINAL_DRAFTED_STAGES`. `DRAFTED_TO_SENT` in `agent.py` includes `"reply_drafted": "reply_sent"`.

**`REPLY_STAGES` is a mirrored constant** — update both `constants.py` (Python) and `types.ts` (TypeScript) if adding new reply stages.

## Visa & wage intelligence gate — Stage 1 (H-1B sponsorship), added 2026-08-01

Decision-support signal, never an auto-reject: tags each target company with
its H-1B sponsorship history from free DOL/USCIS open data. See
`entity_resolution.py`, `ingest_oflc_lca.py`, `ingest_uscis_datahub.py`,
`visa_matching.py`, `visa_match_new.py`.

**`employer_h1b_stats`** — reference table, aggregated per resolved/consolidated
employer identity from DOL OFLC LCA disclosure files (+ USCIS Data Hub
enrichment). Materiality-filtered at ingestion (`lca_recent_2fy >= 1`, hard row
cap) — an employer absent from this table means "unknown", never "confirmed
non-sponsor". Columns: `normalized_name` (unique), `display_name`, `aliases[]`,
`lca_total`, `lca_recent_2fy`, `distinct_socs`, `latest_filing_fy`,
`worksite_states[]`, `wage_level_dist JSONB` (raw counts, Stage 2 extension
point), `uscis_approvals`, `uscis_denials`, `approval_rate`, `naics_code`,
`source_vintages JSONB`. Migration: `20260801010000_create_employer_h1b_stats.sql`.

**`company_intel`** — one row per normalized target company (a `contacts.company`
value), joined via `contacts.company_intel_id`. Governance rule (enforced in
`visa_matching.resolve_company`, not the DB): code only ever writes
`sponsors_h1b` as `NULL` or `true`; `false` requires an explicit human
`confirmed` decision via `/visa-review`. `match_status` is one of `unknown`,
`auto`, `needs_review`, `confirmed`, `rejected`. `top_candidates JSONB` stores
the top-3 fuzzy-match candidates (employer id + score) so the review screen
never needs to re-run rapidfuzz client-side. `typical_wage_level` and
`cap_exempt_likely` are Stage 2/3 extension points, always NULL in Stage 1.
Migration: `20260801010100_create_company_intel.sql`.

**`contacts.company_intel_id`** — nullable FK to `company_intel(id)`, added by
`20260801010200_add_company_intel_id_to_contacts.sql`. NULL is the permanent
"not yet matched" state.

**Entity resolution** (`entity_resolution.py`): `normalize()` strips legal
suffixes/punctuation; `resolve()`/`classify()` use `rapidfuzz.fuzz.token_set_ratio`
with `AUTO_THRESHOLD = 93` / `REVIEW_FLOOR = 80` (provisional, calibrate against
real data after first ingest). An exact match after normalization always
auto-classifies; a fuzzy single-token match never does (`token_set_ratio`'s
known false-positive mode). `KNOWN_ALIAS_GROUPS` is a small curated dict for
multi-legal-entity employers (e.g. Amazon subsidiaries) — not solved
algorithmically in Stage 1.

**Ingestion** (`ingest_oflc_lca.py`, `ingest_uscis_datahub.py`): stream-parse
with `openpyxl`/`csv`, no DataFrame materialization. `COLUMN_ALIASES` maps
canonical fields to known raw header spellings per FY vintage — `employer_name`
is the only required field (unresolvable → abort that file, log, continue);
everything else degrades gracefully. First ingest covers the last ~4 fiscal
years (recency is what the signal needs, not full FY2008+ history). USCIS
enrichment only updates existing `employer_h1b_stats` rows and only applies
`auto`-confidence matches — never creates new rows.

**Matching** (`visa_match_new.py`): fully separate from `agent.py::run()` —
two modes via `run(full_rematch=...)`, CLI flag `--full`:
- **Incremental (default, daily)**: wired as a `continue-on-error: true` step
  in `daily_agent.yml`. Only links contacts with `company_intel_id IS NULL`
  against the already-materialized `employer_h1b_stats` corpus — a company
  already linked (even with `match_status="unknown"`) is skipped.
- **Full (`--full`, quarterly)**: re-resolves **every** distinct contact
  company, not just unmatched ones. Required after `employer_h1b_stats`
  refreshes, since a company that resolved to `unknown` against an earlier
  corpus may now match. **Do not run the quarterly workflow's re-match step
  without `--full`** — the incremental mode silently no-ops on every contact
  that already has a `company_intel_id`, which is the common case after the
  first ingest (a real production incident: the first full ingestion linked
  every contact to `unknown` rows off a truncated 1000-row corpus page — see
  the pagination note below — and a plain incremental re-match afterward
  skipped all of them, since they were already "linked").

Both modes enforce the same governance via `visa_matching.resolve_company`:
`confirmed`/`rejected` `company_intel` rows are never reclassified, only
their denormalized stats (`h1b_recent_count`, `latest_filing_fy`,
`approval_rate`) refresh from the linked employer — see
`visa_matching._refresh_confirmed_row`. Records its own run status via
`db.record_run(source="visa_match")`.

**Quarterly ingestion workflow**: `.github/workflows/visa_intel_ingest.yml`,
cron `0 10 5 1,4,7,10 *`. Runs `ingest_oflc_lca.py` → `ingest_uscis_datahub.py`
→ `python visa_match_new.py --full`.

**`db.get_employer_h1b_stats_corpus()` pagination**: PostgREST caps a single
request's rows (commonly 1000, confirmed against the live production
project). This table holds up to `MAX_EMPLOYER_ROWS` (150,000) after a real
ingest — the accessor paginates via `.range()` until a short page confirms
the end. A plain `.select().execute()` here will silently truncate and
starve entity resolution of most of the real corpus; don't revert to it.

## New db.py functions

- `log_agent_event(event_type, contact_id, contact_name, status, metadata, ...)` — best-effort insert to agent_events; never raises. `metadata` replaces the old `blocked_checks` param — pass a dict, not a list.
- `get_agent_events(limit=100)` — ordered by started_at DESC; used by /runs page
- `update_classifier_status(contact_id, value)` — sets classifier_status; used by monitor
- `insert_email_message(contact_id, direction, sent_at, ...)` — upsert on message_id; used by agent (outgoing) and monitor (incoming)
- `get_email_messages(contact_id)` — ordered by sent_at ASC; used by thread view in contact sheet
- `update_message_id(contact_id, message_id)` — updates only `message_id`; called by `detect_sent_drafts` when Gmail rewrites the ID on send (threading fix)
- `record_run(status, drafted, skipped, errors, elapsed, failure_reason, source)` — `source` defaults to `'agent'`; pass `source='monitor'` from monitor.py
- `set_research_cache(..., queries_generated, brief_reliable)` — two new optional params populate the analytics columns
- `log_drafted_email(contact_id, stage, subject, body, message_id=None, gmail_draft_id=None)` — best-effort insert to draft_history; never raises. Called from `agent._execute_draft` and `reply_drafter.draft_reply` after IMAP APPEND.
- `get_employer_h1b_stats_corpus()` — fetches id/normalized_name/lca_recent_2fy/latest_filing_fy/approval_rate for every cached employer; raises on failure (caller must have a fresh corpus to match against).
- `upsert_employer_h1b_stats(rows)` — best-effort batch upsert on `normalized_name`; never raises.
- `get_company_intel_by_normalized_names(names)` — raises on failure (used to check confirmed/rejected status before a re-match, so a silent failure would risk overwriting a human decision).
- `upsert_company_intel(rows)` — best-effort batch upsert on `normalized_name`; never raises.
- `update_contact_company_intel_id(contact_id, company_intel_id)` — best-effort; never raises.

## New config.py constants

- `REPLY_CLASSIFICATION_MODEL = "claude-haiku-4-5-20251001"` — Haiku for reply classification
- `REPLY_RESPONSE_MODEL = "claude-sonnet-4-6"` — Sonnet for reply body generation
- `REPLY_CLASSIFICATION_DEFAULT` — fallback classification prompt
- `REPLY_RESPONSE_DEFAULT` — fallback reply response template

## Form D funding signal (added 2026-08-19, migration applied 2026-08-23)

`company_intel` gains four nullable columns via
`20260819050000_add_funding_signal_to_company_intel.sql`:
`last_funding_date DATE`, `last_funding_amount BIGINT`,
`last_funding_source TEXT` (currently always `'sec_form_d'`), and
`last_funding_checked_at TIMESTAMPTZ`. Plus a partial index on
`last_funding_date DESC WHERE last_funding_date IS NOT NULL`.

**The migration is applied on the remote DB** (pushed via `supabase db push` ahead
of the writer/matcher — it's purely additive, `IF NOT EXISTS`, no backfill, no risk
to existing rows). The columns exist but nothing writes to them yet:
`ingest_form_d.py` ships only the parsing/aggregation/download layer.
`download_quarter(url, dest_dir)` fetches and extracts one quarterly ZIP (cf.
`ingest_oflc_lca.download_file`), matching archive members by basename so the
extraction doesn't depend on SEC's internal path layout. Still to be built before
data can land: a `db.py` upsert accessor, a matcher onto `company_intel`, and a
`run()` orchestrator. Never add a scheduled workflow step until the writer and
matcher exist — a step with nothing to run is dead CI time, not a safety issue,
but also not worth scheduling yet.

**Governance — identical to the H-1B column**: NULL means *not observed*, never
"did not raise". A company may raise through a route that does not file Form D,
or file under a legal entity name that differs from `contacts.company`. The UI
must never render NULL as a negative claim.

**Source facts, verified against the real 2025Q4 dataset** (re-verify if
ingestion coverage looks thin — SEC publishes no manifest):
- The index page's ZIP path prefix **drifts between quarters**
  (`/files/datastandardsinnovation/` for the newest, `/files/structureddata/`
  for older). `ingest_form_d` matches on the FILENAME only. Never hardcode a
  prefix — this is the failure mode that broke DOL LCA discovery (`611723b`).
- Three TSVs join on `ACCESSIONNUMBER`: `FORMDSUBMISSION`, `ISSUERS`, `OFFERING`.
- `IS_PRIMARYISSUER_FLAG` is `YES`/`NO`, **not** `Y`/`N`.
- `FILING_DATE` is `DD-MON-YYYY` (`31-DEC-2025`), not ISO.
- Pooled investment funds are 65% of filings and must be excluded by **two**
  signals: `ISPOOLEDINVESTMENTFUNDTYPE == "true"` *and*
  `INDUSTRYGROUPTYPE == "Pooled Investment Fund"`. In 2025Q4, 222 rows set only
  the latter.
- **Known limitation**: some VC funds (e.g. "SpringTime Ventures Fund III LP")
  set neither signal and leak through. Deliberately not over-filtered — tighter
  heuristics risk excluding real operating companies, and a fund only ever
  surfaces if a contact actually works there.
