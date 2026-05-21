# /replies page (Phase 2 — reply triage UI)

Two-column layout: left (320px scrollable triage list), right (focused detail + action bar).

**Data fetch**: three-step on mount + 30s auto-refresh.
1. Contacts where `classifier_status IS NOT NULL` AND `reply_status NOT IN (interested,call_scheduled,dead)` AND `deleted_at IS NULL`.
2. `draft_history` rows with `stage='reply_drafted'` AND `sent_body IS NULL`, latest per contact.
3. `email_messages` rows with `direction='incoming'`, latest per contact (for left-list snippet + timestamp).

**Sort**: client-side — positive_reply first (priority 0), soft_yes second (priority 1), others last, then created_at DESC within each group.

**Left list rows**: classifier dot+label (emerald=positive, amber=soft_yes, gray=others), name+company, stripped incoming snippet (quoted lines/headers removed, max 80 chars), relative timestamp.

**Right column**: contact header + classifier badge, ThreadView (self-contained, fetches own data per contactId), suggested reply block (subject+body, only for positive/soft_yes with draft), action bar.

**Action bar** — with draft (positive/soft_yes):
[Approve and Send] [Quick Fix] [Edit in Gmail] | [Mark interested] [Mark call scheduled] [Mark dead]

**Action bar** — without draft (hard_no, unrelated, etc.):
[Open in Gmail] | [Mark interested] [Mark call scheduled] [Mark dead]

**5-second undo**: same pattern as QueuePage. `pendingActions: Map<contact_id, PendingEntry>`. Both "Approve and Send" and "mark reply_status" changes use this pattern. Reply_status changes: Supabase PATCH fires after 5s; undo reverts optimistic removal from list.

**Important**: `reply_status` updates (i/c/D) do NOT touch `stage` — stage is managed manually via the contacts side sheet on /contacts.

**Keyboard map** (same early-return pattern as QueuePage):

| Key | Action |
|---|---|
| `j` / `↓` | Next reply |
| `k` / `↑` | Previous reply |
| `e` | Approve and Send (only when draft exists) |
| `E` | Quick Fix (only when draft exists) |
| `o` | Edit in Gmail (draft) or Open Gmail inbox (no draft) |
| `i` | Mark interested (5s undo) |
| `c` | Mark call scheduled (5s undo) |
| `D` | Mark dead (5s undo, uppercase D) |
| `?` | Keyboard shortcuts overlay |
| `Esc` | Close Quick Fix |

**ThreadView**: reused as-is — `<ThreadView contactId={focused.id} />`. It fetches its own email_messages per contactId on mount/change. No additional data plumbing needed.
