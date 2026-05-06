import imaplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD


def create_draft(to_email, subject, body):
    """
    Create a Gmail draft via IMAP. Never sends — draft only.
    User reviews and sends manually from Gmail.
    """
    msg = MIMEMultipart()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to_email
    msg["Subject"] = subject
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
