"""Shared test doubles."""
from __future__ import annotations

import imaplib
from datetime import datetime, timezone
from email.message import EmailMessage

from imap_client import MailMessage


def make_msg(uid: int, body: str, *, sender="noreply@example.com", received=None, subject="Hi", uv=1) -> MailMessage:
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = sender, subject, f"<m{uid}@x>"
    m.set_content(body)
    return MailMessage(uv, uid, f"<m{uid}@x>", sender, subject, received or datetime.now(timezone.utc), m)


class FakeMailbox:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.seen = []

    def fetch_since(self, after):
        return [m for m in self.messages if m.received_at >= after]

    def mark_seen(self, uv, uid):
        self.seen.append((uv, uid))


class FakeIMAP:
    """Scriptable stand-in for imaplib.IMAP4_SSL."""

    def __init__(self, login_error: str | None = None, fail_noop: bool = False):
        self.login_error, self.fail_noop = login_error, fail_noop
        self.sent, self.lines = [], []
        self.sock = None
        self.closed = False
        self.selected_readonly = None

    def login(self, user, password):
        if self.login_error:
            raise imaplib.IMAP4.error(self.login_error)
        return "OK", [b"ok"]

    def select(self, mailbox, readonly=False):
        self.selected_readonly = readonly
        return "OK", [b"1"]

    def response(self, name):
        return name, [b"42"]

    def noop(self):
        if self.fail_noop:
            raise ConnectionResetError("boom")
        return "OK", [b""]

    def shutdown(self):
        self.closed = True

    def send(self, data):
        self.sent.append(data)

    def readline(self):
        return self.lines.pop(0)
