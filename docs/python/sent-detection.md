# Sent-draft auto-detection

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
