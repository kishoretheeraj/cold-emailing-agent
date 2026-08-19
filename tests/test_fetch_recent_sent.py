"""Tests for gmail.fetch_recent_sent -- listing recent human-written sent mail."""

import pytest

import gmail


def _msg(body, key_header=False):
    headers = "Subject: Hi\r\n"
    if key_header:
        headers += "X-Cold-Email-Key: abc123\r\n"
    return f"{headers}\r\n{body}".encode()


def _fake_imap(mocker, messages):
    """messages[0] is the oldest (IMAP seq 1); the last is the newest."""
    imap = mocker.MagicMock()
    nums = [str(i + 1).encode() for i in range(len(messages))]
    imap.search.return_value = ("OK", [b" ".join(nums)])
    lookup = dict(zip(nums, messages))

    def _fetch(num, spec):
        return ("OK", [(b"", lookup[num])])

    imap.fetch.side_effect = _fetch
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=imap)
    return imap


def test_returns_bodies_newest_first(mocker):
    _fake_imap(mocker, [_msg("older body"), _msg("newer body")])
    assert gmail.fetch_recent_sent(limit=10) == ["newer body", "older body"]


def test_excludes_agent_authored_mail(mocker):
    _fake_imap(mocker, [_msg("human body"), _msg("agent body", key_header=True)])
    assert gmail.fetch_recent_sent(limit=10) == ["human body"]


def test_respects_limit(mocker):
    _fake_imap(mocker, [_msg("a"), _msg("b"), _msg("c")])
    assert len(gmail.fetch_recent_sent(limit=2)) == 2


def test_returns_empty_on_imap_error(mocker):
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", side_effect=OSError("network down"))
    assert gmail.fetch_recent_sent(limit=10) == []


def test_returns_empty_when_search_finds_nothing(mocker):
    imap = mocker.MagicMock()
    imap.search.return_value = ("OK", [b""])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=imap)
    assert gmail.fetch_recent_sent(limit=10) == []
