# IMAP patterns

- `[Gmail]/Drafts` mailbox name is double-quoted: `'"[Gmail]/Drafts"'`.
- `[Gmail]/Sent Mail` mailbox name is double-quoted: `'"[Gmail]/Sent Mail"'`.
- Label folder names are double-quoted: `f'"{label_name}"'`.
- `imap.create()` returns `('NO', [b'[ALREADYEXISTS]...'])` if the label
  exists. **This is not an exception** — `create_gmail_label_if_not_exists`
  intentionally ignores the return value.
- Always wrap IMAP calls in `try/finally imap.logout()`. Connection cleanup
  is non-negotiable.
- `create_draft()` returns `DraftResult(message_id, gmail_draft_id,
  gmail_thread_id)`, or `DraftResult(None, None, None)` if a duplicate was
  detected via `X-Cold-Email-Key`. Do not try to fetch the ID from IMAP after
  append — Gmail drafts only receive a server Message-ID when actually sent.
- `find_sent_for_thread(message_id, since_date, mode)` searches `[Gmail]/Sent Mail`
  with `readonly=True`. Use `mode="first_touch"` to match on `Message-ID`,
  `mode="followup"` to match on `In-Reply-To`. Returns the **actual Message-ID string**
  from the found sent email (not the stored one — Gmail may rewrite it on send), or
  `None` if not found. Never raises. The `message_id` search arg is double-quoted in
  the IMAP command because angle brackets are IMAP special characters.
- **Every `HEADER Message-ID` IMAP search must double-quote the value** — this
  bit `apply_label_to_latest_draft`'s `message_id`-targeted fallback once
  (shipped unquoted, silently found nothing, and fell through to a no-op
  instead of labeling the draft). `X-Cold-Email-Key` searches don't need
  quoting since that value is a plain hex string with no IMAP special chars.
- **`_fetch_body_text` uses `RFC822` + MIME walk, not `BODY[TEXT]`.** `BODY[TEXT]` on
  multipart/mixed emails (Outlook, Exchange) returns the raw MIME structure rather than
  a decoded tuple, silently producing `b""`. The function fetches the full RFC822 message,
  walks MIME parts to find `text/plain` (falling back to stripped `text/html`), and
  returns up to 2000 chars. Do not revert to `BODY[TEXT]`.
