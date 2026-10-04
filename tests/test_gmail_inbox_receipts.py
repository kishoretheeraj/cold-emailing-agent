"""Tests for gmail.fetch_inbox_since: read-only, PEEK-only scan of the receipt mailbox."""

from unittest.mock import MagicMock

import gmail

_HDR = (
    b"From: \"Acme Recruiting\" <no-reply@us.greenhouse-mail.io>\r\n"
    b"Subject: Thank you for applying to Acme\r\n"
    b"Date: Sat, 04 Oct 2026 10:00:00 +0000\r\n"
    b"Message-ID: <m1@greenhouse>\r\n\r\n"
)
_HDR_REPLY = (
    b"From: Bob <bob@acme.com>\r\nSubject: Re: hello\r\n"
    b"Date: Sat, 04 Oct 2026 11:00:00 +0000\r\nMessage-ID: <m2@x>\r\n"
    b"In-Reply-To: <zzz@x>\r\n\r\n"
)
_HDR_BAD_DATE = b"From: a@b.com\r\nSubject: x\r\nDate: not a date\r\nMessage-ID: <m3@x>\r\n\r\n"
_BODY = b"From: a@b.com\r\nSubject: s\r\nContent-Type: text/plain\r\n\r\nWe have received your application."


def _fake_imap(mocker, headers, bodies=None):
    imap = MagicMock()
    imap.search.return_value = ("OK", [b" ".join(str(i + 1).encode() for i in range(len(headers)))])
    fetches = []

    def fetch(num, spec):
        fetches.append((num, spec))
        if "HEADER" in spec:
            return ("OK", [(b"1 (BODY[HEADER] {1}", headers[int(num) - 1]), b")"])
        return ("OK", [(b"1 (BODY[] {1}", (bodies or {}).get(int(num), _BODY)), b")"])

    imap.fetch.side_effect = fetch
    imap._fetches = fetches
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=imap)
    return imap


def _since():
    from datetime import date
    return date(2026, 10, 1)


def test_selects_all_mail_readonly_and_excludes_own_address(mocker):
    imap = _fake_imap(mocker, [_HDR])
    gmail.fetch_inbox_since(_since(), "me@gmail.com", "pw")
    imap.login.assert_called_once_with("me@gmail.com", "pw")
    imap.select.assert_called_once_with('"[Gmail]/All Mail"', readonly=True)
    args = imap.search.call_args[0]
    assert "SINCE" in args and "01-Oct-2026" in args
    assert "NOT" in args and "FROM" in args and '"me@gmail.com"' in args
    imap.logout.assert_called_once()


def test_every_fetch_uses_peek(mocker):
    imap = _fake_imap(mocker, [_HDR])
    gmail.fetch_inbox_since(_since(), "me@gmail.com", "pw", want_body=lambda m: True)
    assert imap._fetches
    assert all("PEEK" in spec for _, spec in imap._fetches)


def test_parses_headers_and_reply_flag(mocker):
    _fake_imap(mocker, [_HDR, _HDR_REPLY])
    msgs = gmail.fetch_inbox_since(_since(), "me@gmail.com", "pw")
    assert msgs[0]["message_id"] == "<m1@greenhouse>"
    assert msgs[0]["subject"] == "Thank you for applying to Acme"
    assert msgs[0]["is_reply"] is False
    assert msgs[0]["date"].utcoffset().total_seconds() == 0
    assert msgs[1]["is_reply"] is True


def test_unparseable_date_is_skipped(mocker):
    _fake_imap(mocker, [_HDR_BAD_DATE, _HDR])
    msgs = gmail.fetch_inbox_since(_since(), "me@gmail.com", "pw")
    assert [m["message_id"] for m in msgs] == ["<m1@greenhouse>"]


def test_body_fetched_only_when_callback_approves(mocker):
    imap = _fake_imap(mocker, [_HDR, _HDR_REPLY])
    msgs = gmail.fetch_inbox_since(
        _since(), "me@gmail.com", "pw", want_body=lambda m: not m["is_reply"])
    assert "received your application" in msgs[0]["body"]
    assert "body" not in msgs[1]
    assert len([f for f in imap._fetches if "HEADER" not in f[1]]) == 1


def test_body_is_truncated(mocker):
    big = b"From: a@b.com\r\nSubject: s\r\nContent-Type: text/plain\r\n\r\n" + b"x" * 9000
    _fake_imap(mocker, [_HDR], bodies={1: big})
    msgs = gmail.fetch_inbox_since(_since(), "me@gmail.com", "pw", want_body=lambda m: True)
    assert len(msgs[0]["body"]) == 4000


def test_logs_out_even_when_search_fails(mocker):
    imap = _fake_imap(mocker, [_HDR])
    imap.search.side_effect = RuntimeError("boom")
    try:
        gmail.fetch_inbox_since(_since(), "me@gmail.com", "pw")
    except RuntimeError:
        pass
    imap.logout.assert_called_once()
