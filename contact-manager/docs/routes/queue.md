# /queue page (Phase 1 — bulk-send UI)

Three-column layout: left rail (filters), center (scrollable draft list), right (focused detail + action bar).

**Data fetch**: two-step on mount + 30s auto-refresh.
1. Contacts `IN QUEUE_STAGES` (from `cadence.ts`) AND `deleted_at IS NULL`, sorted tier ASC + created_at DESC.
2. draft_history rows for those contacts with `sent_body IS NULL`, latest per contact.

**Focus tracking**: focus is tracked by `contact_id`, not index. On auto-refresh, if the focused contact is still present its position is restored. If it disappeared, focus resets to index 0.

**5-second undo pattern** (used for both Approve and Send and Mark Dead):
- API fires AFTER the 5-second delay. Optimistically remove from list → show toast with Undo button → start `setTimeout(5000)`.
- Undo: `clearTimeout`, re-insert at `originalIndex`, toast.info("Send canceled"). API never called.
- Timer fires: POST `/api/send-draft` (or Supabase PATCH for dead). On error: re-add to list + error toast.
- Unmount: all pending timers are cleared — navigating away cancels pending sends (deliberate trade-off, documented in comment).
- Concurrent: multiple contacts can have pending timers simultaneously; each is tracked separately in `pendingSends` Map.

**Quick Fix mode** (`E` key): replaces subject/body in right column with editable inputs. "Save and Send" calls `/api/update-draft` first, then enters the 5s undo flow.

**Keyboard map** (document-level listener; early-returns when input/textarea is focused):

| Key | Action |
|---|---|
| `j` / `↓` | Next draft |
| `k` / `↑` | Previous draft |
| `g g` | Jump to top (500ms window between presses) |
| `G` | Jump to bottom |
| `e` | Approve and Send (5s undo) |
| `E` | Open Quick Fix |
| `o` | Edit in Gmail (new tab, `/u/0/#drafts?compose=<id>`) |
| `x` | Skip (session-only, survives 30s auto-refresh) |
| `D` | Mark dead (uppercase D — 5s undo) |
| `1`/`2`/`3` | Toggle tier filter |
| `?` | Show keyboard shortcuts overlay |
| `Esc` | Close Quick Fix → clear filters |

**Signals in right column**:
- Critic: queryable from `agent_events` (event_type='critic', metadata.score/verdict/retried). T2+ shows "n/a (T2+)".
- Pre-flight: shows "✓ passed" inferred from draft existence (no logged event for passing preflight — only blocked events exist).
- Edited in Gmail: always "—" in v1 (`draft_history.edit_detected` is null until send; per-row API call too expensive).

## Location label and timezone display (2026-05-23)

**Row label**: when `contact.state` is set, a `<p>` below the contact name/company line shows `"NY · 4:00 PM"`. Null state renders nothing — no placeholder, no extra padding.

**Header line**: the left rail shows `"Your time: 4:00 PM ET · 2 ET"` — sender's local time + a distribution of contacts-with-state grouped by tz label. Only rendered when at least one contact has a state set. Updates every 60s via a separate `setInterval` (independent of the 30s data-refresh interval).

**Timezone derivation**: state → IANA zone via `STATE_TO_TIMEZONE` in `src/lib/timezone.ts` → short label via `ZONE_TO_LABEL`. Split-zone states use the majority zone (e.g. TX → CT, FL → ET). AZ is special (no DST → "AZ" label). All derivation is read-time; no `timezone` column exists.

## Mirrored cadence constants

`src/lib/cadence.ts` mirrors `agent/config.py::FOLLOWUP_DAYS`. If the agent cadence
changes (days between emails), update **both** files. Same pattern as `REPLY_STAGES`
in `types.ts` mirroring `constants.py`. The `STAGE_TRANSITIONS` map in `cadence.ts`
is the authoritative transition table for all `/api/send-draft` stage flips.
`QUEUE_STAGES` (in `cadence.ts`) is used by `/queue` page to filter contacts for bulk approval.

## Networking mode (added 2026-08-01)

`networking_drafted` and `networking_followup_drafted` are in `QUEUE_STAGES`, so
networking drafts appear in `/queue` alongside outreach/applied drafts — no separate
page. `CADENCE.NETWORKING_TO_FOLLOWUP_DAYS = 6` is the only cadence value (the
follow-up is terminal — `networking_followup_drafted`'s `cadenceKey` is `null`, same
shape as `applied_followup_drafted`, so no further follow-up is ever scheduled).
`STAGE_TO_LABEL` in `src/app/api/send-draft/route.ts` applies `Networking/Intro` and
`Networking/Follow-up` Gmail labels for these stages (a separate top-level Gmail label
from `Cold Outreach/...`).
