import base64
import hashlib
import html
import imaplib
import logging
import re
import time
from collections import namedtuple
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid

from config import (
    GMAIL_ADDRESS, GMAIL_APP_PASSWORD,
    GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REFRESH_TOKEN,
)

log = logging.getLogger(__name__)

# Return type for create_draft. Callers unpack by attribute name to avoid silent
# positional bugs when the return shape changes.
DraftResult = namedtuple("DraftResult", ["message_id", "gmail_draft_id", "gmail_thread_id"])


# ── Gmail API client (optional — degrades gracefully if OAuth vars absent) ─────

def _get_gmail_api_client():
    """Return an authenticated Gmail v1 API client, or None if env vars are missing."""
    if not (GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REFRESH_TOKEN):
        return None
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        creds = Credentials(
            token=None,
            refresh_token=GOOGLE_OAUTH_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_OAUTH_CLIENT_ID,
            client_secret=GOOGLE_OAUTH_CLIENT_SECRET,
        )
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as exc:
        log.warning(f"[GMAIL-API] client init failed: {exc}")
        return None


def _lookup_gmail_draft_id(message_id):
    """
    After an IMAP APPEND, find the Gmail API draft ID that matches the given RFC822
    Message-ID. Inspects up to 20 recent drafts. Returns the draft ID string or None.
    Never raises.
    """
    client = _get_gmail_api_client()
    if client is None:
        return None
    try:
        result = client.users().drafts().list(userId="me", maxResults=20).execute()
        drafts = result.get("drafts", [])
        for draft in drafts:
            draft_id = draft.get("id")
            msg_id = draft.get("message", {}).get("id")
            if not draft_id or not msg_id:
                continue
            try:
                msg = client.users().messages().get(
                    userId="me", id=msg_id,
                    format="metadata", metadataHeaders=["Message-ID"],
                ).execute()
                headers = msg.get("payload", {}).get("headers", [])
                for h in headers:
                    if h.get("name", "").lower() == "message-id" and h.get("value") == message_id:
                        return draft_id
            except Exception:
                continue
    except Exception as exc:
        log.warning(f"[GMAIL-API] draft lookup failed: {exc}")
    return None


def _create_draft_via_api(client, to_email, subject, body, in_reply_to, mid):
    """
    Create a follow-up draft via Gmail API so In-Reply-To and threadId are
    preserved (Gmail strips In-Reply-To from IMAP-appended drafts).

    Looks up the threadId of in_reply_to by searching Gmail, then creates
    the draft inside that thread. Returns (gmail_draft_id, gmail_thread_id_int)
    on success, or None if the parent message cannot be found.
    Never raises — failures fall back to IMAP APPEND.
    """
    try:
        q = f"rfc822msgid:{in_reply_to.strip('<>')}"
        result = client.users().messages().list(userId="me", q=q, maxResults=1).execute()
        messages = result.get("messages", [])
        if not messages:
            log.info(f"[GMAIL-API] in_reply_to not found in Gmail — falling back to IMAP: {in_reply_to}")
            return None
        thread_id = messages[0]["threadId"]

        msg = MIMEMultipart("alternative")
        msg["Message-ID"]  = mid
        msg["From"]        = GMAIL_ADDRESS
        msg["To"]          = to_email
        msg["Subject"]     = subject
        msg["In-Reply-To"] = in_reply_to
        msg["References"]  = in_reply_to
        msg.attach(MIMEText(_body_to_html(body), "html"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        draft = client.users().drafts().create(
            userId="me",
            body={"message": {"raw": raw, "threadId": thread_id}},
        ).execute()

        gmail_draft_id = draft["id"]
        return (gmail_draft_id, int(thread_id, 16))
    except Exception as exc:
        log.warning(f"[GMAIL-API] _create_draft_via_api failed — falling back to IMAP: {exc}")
        return None


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
    Create a Gmail draft. Never sends — draft only.
    For follow-up drafts (in_reply_to set): uses Gmail API so In-Reply-To and
    threadId are preserved (Gmail silently strips In-Reply-To from IMAP APPEND).
    Falls back to IMAP APPEND when OAuth is unavailable or the parent message
    cannot be found.
    Returns DraftResult(message_id, gmail_draft_id, gmail_thread_id).
    gmail_thread_id is X-GM-THRID (int) — primary key for monitor sent detection.
    gmail_draft_id is the Gmail API string ID — needed by /api/send-draft.
    Returns DraftResult(None, None, None) if duplicate detected for this
    contact_id/stage/date combination.
    """
    if in_reply_to and subject_prefix and not subject.startswith("Re: "):
        subject = "Re: " + subject

    mid = make_msgid(domain="gmail.com")

    # For follow-ups, try Gmail API first — before opening any IMAP connection.
    # Gmail strips In-Reply-To from IMAP-appended messages; the API preserves
    # headers and accepts threadId to force correct thread placement.
    if in_reply_to:
        api_client = _get_gmail_api_client()
        if api_client:
            api_result = _create_draft_via_api(api_client, to_email, subject, body, in_reply_to, mid)
            if api_result:
                gmail_draft_id, gmail_thread_id = api_result
                return DraftResult(mid, gmail_draft_id, gmail_thread_id)

    # IMAP fallback: idempotency check + APPEND (first-touch drafts and API failures).
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
                return DraftResult(None, None, None)

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

        # Capture Gmail's X-GM-THRID via the APPENDUID the server returns.
        # X-GM-THRID survives Gmail rewriting the Message-ID on send and is
        # the primary key for reliable sent detection.
        gmail_thread_id = None
        if data and data[0]:
            uid_match = re.search(rb'APPENDUID\s+\d+\s+(\d+)', data[0])
            if uid_match:
                append_uid = uid_match.group(1).decode()
                try:
                    imap.select('"[Gmail]/Drafts"', readonly=True)
                    _, thrid_data = imap.uid("FETCH", append_uid, "(X-GM-THRID)")
                    thrid_match = re.search(r'X-GM-THRID\s+(\d+)', str(thrid_data))
                    if thrid_match:
                        gmail_thread_id = int(thrid_match.group(1))
                except Exception:
                    pass  # Best-effort; detection falls back to message_id search

        gmail_draft_id = _lookup_gmail_draft_id(mid)
        return DraftResult(mid, gmail_draft_id, gmail_thread_id)
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


def apply_label_to_latest_draft(label_name, gmail_draft_id=None):
    """
    Add label_name to a Gmail draft. When gmail_draft_id is provided and OAuth
    is available, uses the Gmail API (no IMAP COPY duplicate). Falls back to
    IMAP COPY otherwise.
    """
    if gmail_draft_id:
        client = _get_gmail_api_client()
        if client:
            try:
                labels = client.users().labels().list(userId="me").execute()
                label = next(
                    (l for l in labels.get("labels", [])
                     if l.get("name", "").lower() == label_name.lower()),
                    None,
                )
                if label:
                    msg_id = client.users().drafts().get(
                        userId="me", id=gmail_draft_id
                    ).execute()["message"]["id"]
                    client.users().messages().modify(
                        userId="me", id=msg_id,
                        body={"addLabelIds": [label["id"]]},
                    ).execute()
                    return
            except Exception as exc:
                log.warning(f"[GMAIL-API] apply_label_to_latest_draft failed — falling back to IMAP: {exc}")

    # IMAP COPY fallback
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


def find_sent_by_subject(subject, since_date, to_email):
    """
    Fallback: search [Gmail]/Sent Mail by subject + recipient when the Message-ID
    search finds nothing (e.g. Gmail rewrote the draft ID on send).
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
        status, data = imap.search(None, "SINCE", since_str, "TO", to_email, "SUBJECT", f'"{term}"')
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


def find_sent_by_thread_id(gmail_thread_id, since_date):
    """
    Primary sent-detection path: search [Gmail]/Sent Mail by Gmail's X-GM-THRID.
    This survives Gmail rewriting the Message-ID on send. Returns the actual
    Message-ID of the found email, or None.
    """
    since_str = since_date.strftime("%d-%b-%Y")
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        imap.select('"[Gmail]/Sent Mail"', readonly=True)
        status, data = imap.search(None, "X-GM-THRID", str(gmail_thread_id), "SINCE", since_str)
        if status != "OK" or not data[0]:
            log.info(f"[SENT-CHECK-THRID] | {gmail_thread_id} | found=False")
            return None
        nums = data[0].split()
        actual_mid = None
        status2, msg_data = imap.fetch(nums[0], "(BODY[HEADER.FIELDS (MESSAGE-ID)])")
        if status2 == "OK" and msg_data and msg_data[0]:
            raw = msg_data[0][1]
            if isinstance(raw, bytes):
                raw = raw.decode(errors="replace")
            for line in raw.splitlines():
                if line.lower().startswith("message-id:"):
                    actual_mid = line.split(":", 1)[1].strip()
                    break
        log.info(f"[SENT-CHECK-THRID] | {gmail_thread_id} | found={actual_mid is not None} | mid={actual_mid}")
        return actual_mid
    except Exception as exc:
        log.warning(f"[SENT-CHECK-THRID] | IMAP error | {gmail_thread_id} | {exc}")
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
