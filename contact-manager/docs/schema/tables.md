# Schema: tables and types

## New tables (Visa & wage intelligence gate, Stage 1 — 2026-08-01)

**`employer_h1b_stats`** — reference table of aggregated H-1B filing stats per
resolved employer, ingested quarterly from DOL/USCIS open data by
`ingest_oflc_lca.py`/`ingest_uscis_datahub.py`. Read-only from the frontend
(the review screen reads it to show candidate stats). RLS disabled.
- `lca_recent_2fy`, `latest_filing_fy`, `approval_rate` — the fields
  `/visa-review` denormalizes onto `company_intel` on confirm.
- Query pattern: `.select("id,display_name,lca_recent_2fy,latest_filing_fy,approval_rate").in("id", employerIds)` — the review screen fetches only the ids referenced in the current page's `top_candidates`, not the whole table.

**`company_intel`** — one row per normalized target company. Read/write from
the frontend: `ContactsList.tsx` reads it via an embedded `contacts` select
for the list badge/filter; `/visa-review` (`VisaMatchReview.tsx`) reads
`needs_review` rows directly and writes `confirmed`/`rejected` decisions.
RLS disabled.
- `match_status`: `"unknown" | "auto" | "needs_review" | "confirmed" | "rejected"`.
- `sponsors_h1b`: `boolean | null`. **Governance rule**: the frontend never
  writes `false` except via the explicit "No match" action on `/visa-review`
  (which actually sets `match_status="rejected"` and leaves `sponsors_h1b`
  `null` — Stage 1 never asserts a confirmed non-sponsor at all, since a
  rejected fuzzy-match candidate doesn't mean the company truly has zero
  H-1B history, just that this candidate wasn't them).
- `top_candidates JSONB` — `{employer_id, normalized_name, score}[]`, written
  by the Python matching jobs so `/visa-review` never needs to run
  entity-resolution client-side.
- Query pattern for the list embed: `.from("contacts").select("...,company_intel(sponsors_h1b,h1b_recent_count,match_status)")` for the unfiltered case; add `!inner` on the embed (`company_intel!inner(...)`) only when the `sponsorsH1bOnly` filter is active — a plain embed's `.eq()` filters the embedded object, not the parent row set, and silently returns every contact if `!inner` is missing.

**`contacts.company_intel_id INTEGER NULL`** — FK to `company_intel(id)`, added
by `20260801010200_add_company_intel_id_to_contacts.sql`. Included in
`LIST_COLUMNS_BASE` in `ContactsList.tsx`.

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
- `CompanyIntel` type — mirrors `company_intel` table. `Contact.company_intel` is a `Pick<CompanyIntel, "sponsors_h1b"|"h1b_recent_count"|"match_status"> | null`, populated only when the query embeds it.
- `ContactsQueryFilters.sponsorsH1bOnly: boolean` — see the `company_intel` table entry above for the `!inner`-join requirement this filter depends on.
