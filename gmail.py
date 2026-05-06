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
        imap.select('"[Gmail]/Drafts"')
        imap.append(
            '"[Gmail]/Drafts"',
            "",
            imaplib.Time2Internaldate(time.time()),
            msg.as_bytes(),
        )
    finally:
        imap.logout()
