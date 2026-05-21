# Reply pipeline

## monitor.py — detect_replies

`monitor.run()` now has four sequential phases, each wrapped in `try/except` so failure in one never blocks the next:
1. `detect_sent_drafts()` — unchanged
2. `detect_replies(prompts)` — targeted per-contact IMAP HEADER search; returns list of newly-classified contacts
3. (called inside detect_replies) `_classify_reply()` — Claude Haiku classifies reply body → `classifier_status`
4. `_draft_reply_responses(classified, prompts)` — calls `reply_drafter.draft_reply()` for positive_reply/soft_yes contacts

**Reply detection invariants:**
- Iterates over contacts with a stored `message_id`; issues two targeted server-side IMAP searches per contact: `HEADER "In-Reply-To" "<mid>"` and `HEADER "References" "<mid>"`. No full inbox scan. No FROM fallback.
- False-positive guard: IMAP `HEADER` search does substring matching, so each hit is re-verified with `_match_message()` before classification.
- `seen_nums` set deduplicates message UIDs across contacts (one message can reference multiple threads).
- Skip contact if `classifier_status` is already set (idempotent across 2-hour runs).
- Auto-reply bypass: if `Auto-Submitted` header is not "no" or `X-Auto-Response-Suppress` is present, classify as `auto_reply` without calling Claude.
- Notification-sender filter: `_is_notification_sender(from_header)` checks the FROM domain against `_NOTIFICATION_SENDER_DOMAINS` (a frozenset of known tracking-service domains: mailsuite.com, mailtrack.io, streak.com, etc.). Matched emails are skipped with no `classifier_status` write, keeping the contact checkable for future real replies. Real human replies (delegated person, assistant, any non-blocklisted domain) are not affected.
- `email_messages` insert is upsert on `message_id` (ON CONFLICT DO NOTHING).
- One IMAP connection for all contacts. `readonly=True` at open; label copy re-selects INBOX read-write.
- **Empty-body guard**: if `_fetch_body_text` returns `""` for a non-auto reply, the contact is skipped with a warning and `classifier_status` is NOT written. This keeps the contact eligible for retry on the next run. Without this guard, the Anthropic API returns 400 (empty content) and the error fallback silently sets `classifier_status=unrelated`, permanently locking the contact out of future detection.

## reply_drafter.py — draft_reply

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
- Prompt fallback: uses `prompts.get("reply_response_prompt") or REPLY_RESPONSE_DEFAULT` (not `.get(key, default)`) so an empty string stored in Supabase correctly falls back to the hardcoded default.
- **Threading**: `draft_reply()` accepts `in_reply_to_mid` (the incoming reply's message-id). The draft sets `In-Reply-To` to this value and `References` to `"<first_touch_mid> <in_reply_to_mid>"` so it appears after the recipient's reply in the thread, not after the original first-touch email. `_draft_reply_responses` in `monitor.py` passes `incoming[-1]["message_id"]`.
