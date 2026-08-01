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

## New config.py constants

- `REPLY_CLASSIFICATION_MODEL = "claude-haiku-4-5-20251001"` — Haiku for reply classification
- `REPLY_RESPONSE_MODEL = "claude-sonnet-4-6"` — Sonnet for reply body generation
- `REPLY_CLASSIFICATION_DEFAULT` — fallback classification prompt
- `REPLY_RESPONSE_DEFAULT` — fallback reply response template
