# Schema: tables and types

## New tables (Phase 0 — 2026-05-20)

**`draft_history`** — lifecycle of every Gmail draft created by the agent.
- `gmail_draft_id TEXT` — Gmail API draft ID, required by `/api/send-draft`.
- `subject, body` — draft content as generated. Updated by `/api/update-draft` on Quick Fix edits.
- `sent_subject, sent_body, sent_at, edit_detected` — populated by `/api/send-draft` after send.
- `edit_detected = true` when the sent body differs from the draft body (user edited in Gmail).
- RLS disabled. Access via `/api/send-draft` and `/api/update-draft`; read by `/queue` page.
- Written by Python `db.log_drafted_email()` — called from `agent._execute_draft` and `reply_drafter.draft_reply`.

## New tables (Sprint 2 — 2026-05-16)

Two new Supabase tables are readable by the frontend. Both have RLS disabled.

**`email_messages`** — outgoing and incoming emails per contact, written by the Python agent and monitor.
- `direction`: `"outgoing"` | `"incoming"`
- `sent_at`: timestamptz, used for chronological ordering
- Used by `ThreadView.tsx` in the Vaul side sheet.
- Query pattern: `.from("email_messages").select("*").eq("contact_id", id).order("sent_at", { ascending: true })` — no `.limit()`, `.order()` is terminal.

**`agent_events`** — per-action audit log written by the Python agent/monitor (preflight blocks, reply classification, draft creation).
- `status`: `"success"` | `"failed"` | `"blocked_preflight"` | `"running"`
- `event_type`: `"preflight"` | `"classify_reply"` | `"draft_reply"` | `"critic"` | `"sent_detection"`
- Used by `/runs` page (`app/runs/page.tsx`).
- Query pattern: `.from("agent_events").select("*").order("started_at", { ascending: false }).limit(100)` — `.limit()` is terminal.
- Badge query (7-day failures): `.select("id", { count: "exact", head: true }).in("status", [...]).gte("started_at", since)` — this returns a thenable, not a limit-terminated chain.

## New columns (2026-05-23)

**`contacts.state TEXT NULL`** — two-letter US state code (e.g. `"NY"`), or `null` if unknown/non-US.
- Migration: `supabase/migrations/20260523000000_add_state_to_contacts.sql`
- Populated by: `/api/extract` (Claude extraction), SmartInput preview dropdown, StructuredForm dropdown, ContactsList side sheet dropdown.
- Displayed in: `/queue` row labels (`"NY · 4:00 PM"`), `/queue` header distribution (`"2 ET"`), `/replies` row labels.
- Timezone is **always derived at read time** via `src/lib/timezone.ts` — no `timezone` column ever.
- Radix Select sentinel: `value="_none"` maps to `null` on save. Do not use `value=""` — Radix forbids empty string on `<SelectItem>`.

## New types (types.ts)

- `Contact.classifier_status: string | null` — auto-set by monitor; never by user. Distinct from `reply_status` (user-managed).
- `REPLY_STAGES = ["reply_drafted", "reply_sent"]` — **mirrored constant**: must also be updated in Python `constants.py` if changed.
- `ContactsQueryFilters.needsResponseOnly: boolean` — filter: `classifier_status IN (positive_reply, soft_yes) AND reply_status NOT IN (interested, call_scheduled, dead)`.
- `EmailMessage` type — mirrors `email_messages` table.
- `AgentEvent` type — mirrors `agent_events` table.
