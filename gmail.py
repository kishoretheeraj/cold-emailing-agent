import hashlib
import html
import imaplib
import logging
import time
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid

from config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD

log = logging.getLogger(__name__)


def _body_to_html(text):
    """Convert normalized plain-text body to HTML so Gmail renders at full column width."""
    parts = []
    for para in text.split("\n\n"):
        escaped = html.escape(para).replace("\n", "<br>\n")
        parts.append(f"<p>{escaped}</p>")
    return "\n".join(parts)


def create_draft(to_email, subject, body, in_reply_to=None, references=None,
                 subject_prefix=True, contact_id=None, stage=None):
    """
    Create a Gmail draft via IMAP. Never sends — draft only.
    Generates and sets a Message-ID on the draft so follow-ups can reference
    it for threading. Returns the Message-ID string, or None if a duplicate
    draft already exists for this contact_id/stage/date combination.
    When in_reply_to is provided, adds In-Reply-To/References headers and
    prefixes subject with 'Re: ' (unless already set or subject_prefix=False).
    """
    if in_reply_to and subject_prefix and not subject.startswith("Re: "):
        subject = "Re: " + subject

    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)

        # Idempotency: if contact_id and stage are given, check whether a draft
        # with the same key was already created today and skip if so.
        key = None
        if contact_id is not None and stage is not None:
            key = hashlib.sha256(
                f"{contact_id}:{stage}:{date.today()}".encode()
            ).hexdigest()[:16]
            imap.select('"[Gmail]/Drafts"')
            status, data = imap.search(None, "HEADER", "X-Cold-Email-Key", key)
            if status == "OK" and data[0]:
                log.info(f"draft already exists | key={key} | contact={contact_id}")
                return None

        # Generate before append so we own the ID and can return it immediately.
        # Gmail honours a pre-set Message-ID when the user clicks Send.
        mid = make_msgid(domain="gmail.com")

        msg = MIMEMultipart()
        msg["Message-ID"] = mid
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = to_email
        msg["Subject"] = subject
        if key:
            msg["X-Cold-Email-Key"] = key
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = references or in_reply_to
        msg.attach(MIMEText(_body_to_html(body), "html"))

        status, data = imap.append(
            '"[Gmail]/Drafts"',
            "",
            imaplib.Time2Internaldate(time.time()),
            msg.as_bytes(),
        )
        if status != "OK":
            raise RuntimeError(f"IMAP APPEND failed: {status} {data}")
        return mid
    finally:
        imap.logout()


def create_gmail_label_if_not_exists(imap, label_name):
    """
    Create a Gmail label via IMAP CREATE. No-op if the label already exists.
    Nested labels (e.g. 'Cold Outreach/First Touch') are created in one call —
    Gmail creates the parent automatically.
    """
    imap.create(f'"{label_name}"')
    # Returns ('OK', ...) when created, ('NO', [b'[ALREADYEXISTS]...']) when
    # it already exists. imaplib does not raise on NO — both outcomes are fine.


def apply_label_to_latest_draft(label_name):
    """
    Open a fresh IMAP connection, ensure the label exists, then copy the
    most-recently-appended draft to that label folder (which adds the label
    in Gmail without creating a storage duplicate).
    """
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        create_gmail_label_if_not_exists(imap, label_name)
        imap.select('"[Gmail]/Drafts"')
        status, data = imap.search(None, "ALL")
        if status != "OK" or not data[0]:
            return
        nums = data[0].split()
        if nums:
            imap.copy(nums[-1].decode(), f'"{label_name}"')
    finally:
        imap.logout()


# ── Sent-mail search ───────────────────────────────────────────────────────────

def _ascii_subject_fragment(subject):
    """
    Return a short ASCII-safe substring suitable for IMAP SUBJECT search.
    Stops at the first non-ASCII character (e.g. em dash) so the fragment
    matches as a substring in the actual sent email subject regardless of
    how Gmail normalised the special char.
    """
    import re
    s = re.sub(r'^(Re:|Fwd:)\s*', '', subject, flags=re.IGNORECASE).strip()
    # Take only the prefix up to the first non-ASCII character
    ascii_prefix = ''
    for c in s:
        if ord(c) >= 128:
            break
        ascii_prefix += c
    s = ascii_prefix.strip()
    # Trim to 40 chars at a word boundary
    if len(s) > 40:
        s = s[:40].rsplit(' ', 1)[0]
    return s.strip()


def find_sent_by_subject(subject, since_date):
    """
    Fallback: search [Gmail]/Sent Mail by subject when the Message-ID search
    finds nothing (e.g. Gmail rewrote the draft ID on send).
    Returns the actual Message-ID of the earliest matching sent email, or None.
    """
    term = _ascii_subject_fragment(subject)
    if not term:
        return None
    since_str = since_date.strftime("%d-%b-%Y")
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        imap.select('"[Gmail]/Sent Mail"', readonly=True)
        status, data = imap.search(None, "SINCE", since_str, "SUBJECT", f'"{term}"')
        if status != "OK" or not data[0]:
            log.info(f"[SENT-CHECK-SUBJ] | {term!r} | found=False")
            return None
        nums = data[0].split()
        # Use the earliest result (nums[0]) — most likely the first-touch, not a follow-up.
        status2, msg_data = imap.fetch(nums[0], "(BODY[HEADER.FIELDS (MESSAGE-ID)])")
        actual_mid = None
        if status2 == "OK" and msg_data and msg_data[0]:
            raw = msg_data[0][1]
            if isinstance(raw, bytes):
                raw = raw.decode(errors="replace")
            for line in raw.splitlines():
                if line.lower().startswith("message-id:"):
                    actual_mid = line.split(":", 1)[1].strip()
                    break
        log.info(f"[SENT-CHECK-SUBJ] | {term!r} | found={actual_mid is not None} | mid={actual_mid}")
        return actual_mid
    except Exception as exc:
        log.warning(f"[SENT-CHECK-SUBJ] | IMAP error | {term!r} | {exc}")
        return None
    finally:
        imap.logout()


def find_sent_for_thread(message_id, since_date, mode):
    """
    Returns the actual Message-ID of the found sent email, or None if not found.
    Searches [Gmail]/Sent Mail for a message with the given Message-ID (first_touch)
    or In-Reply-To (followup), on or after since_date. The returned ID may differ
    from message_id if Gmail rewrote it when the draft was sent.
    """
    since_str = since_date.strftime("%d-%b-%Y")
    header = "Message-ID" if mode == "first_touch" else "In-Reply-To"
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        imap.select('"[Gmail]/Sent Mail"', readonly=True)
        # message_id contains angle brackets (<abc@gmail.com>) which are IMAP
        # special chars — must be double-quoted so the server parses them correctly.
        status, data = imap.search(None, "SINCE", since_str, "HEADER", header, f'"{message_id}"')
        if status != "OK" or not data[0]:
            log.info(f"[SENT-CHECK] | {message_id} | {mode} | found=False")
            return None
        nums = data[0].split()
        # Fetch the actual Message-ID — Gmail may rewrite it when sending a draft.
        actual_mid = message_id
        status2, msg_data = imap.fetch(nums[0], "(BODY[HEADER.FIELDS (MESSAGE-ID)])")
        if status2 == "OK" and msg_data and msg_data[0]:
            raw = msg_data[0][1]
            if isinstance(raw, bytes):
                raw = raw.decode(errors="replace")
            for line in raw.splitlines():
                if line.lower().startswith("message-id:"):
                    actual_mid = line.split(":", 1)[1].strip()
                    break
        log.info(f"[SENT-CHECK] | {message_id} | {mode} | found=True | actual={actual_mid}")
        return actual_mid
    except Exception as exc:
        log.warning(f"[SENT-CHECK] | IMAP error | {message_id} | {exc}")
        return None
    finally:
        imap.logout()
