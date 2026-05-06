import imaplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid

from config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD


def create_draft(to_email, subject, body, in_reply_to=None, references=None, subject_prefix=True):
    """
    Create a Gmail draft via IMAP. Never sends — draft only.
    Generates and sets a Message-ID on the draft so follow-ups can reference
    it for threading. Returns the Message-ID string.
    When in_reply_to is provided, adds In-Reply-To/References headers and
    prefixes subject with 'Re: ' (unless already set or subject_prefix=False).
    """
    if in_reply_to and subject_prefix and not subject.startswith("Re: "):
        subject = "Re: " + subject

    # Generate before append so we own the ID and can return it immediately.
    # Gmail honours a pre-set Message-ID when the user clicks Send.
    mid = make_msgid(domain="gmail.com")

    msg = MIMEMultipart()
    msg["Message-ID"] = mid
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to_email
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    msg.attach(MIMEText(body, "plain"))

    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
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
