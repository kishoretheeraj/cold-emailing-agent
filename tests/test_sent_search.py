"""Tests for gmail.find_sent_for_thread — searches Gmail Sent Mail for a thread."""

from datetime import date
from unittest.mock import MagicMock

import pytest

import gmail


SINCE = date(2026, 5, 1)
MID = "<test-message-id@mail.gmail.com>"


def _make_imap(search_status="OK", search_data=b"5"):
    fake_imap = MagicMock(name="imap")
    fake_imap.select.return_value = ("OK", [b"1"])
    fake_imap.search.return_value = (search_status, [search_data])
    return fake_imap


def test_first_touch_searches_message_id_header(mocker):
    fake_imap = _make_imap()
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.find_sent_for_thread(MID, SINCE, mode="first_touch")

    args = fake_imap.search.call_args.args
    assert "Message-ID" in args
    assert MID in args


def test_followup_searches_in_reply_to_header(mocker):
    fake_imap = _make_imap()
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.find_sent_for_thread(MID, SINCE, mode="followup")

    args = fake_imap.search.call_args.args
    assert "In-Reply-To" in args
    assert MID in args


def test_returns_true_when_search_finds_message(mocker):
    fake_imap = _make_imap(search_status="OK", search_data=b"5")
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.find_sent_for_thread(MID, SINCE, mode="first_touch")

    assert result is True


def test_returns_false_when_search_is_empty(mocker):
    fake_imap = _make_imap(search_status="OK", search_data=b"")
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.find_sent_for_thread(MID, SINCE, mode="first_touch")

    assert result is False


def test_returns_false_on_imap_exception(mocker):
    fake_imap = MagicMock()
    fake_imap.login.side_effect = Exception("imap is down")
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.find_sent_for_thread(MID, SINCE, mode="first_touch")

    assert result is False


def test_logout_called_even_when_search_raises(mocker):
    fake_imap = MagicMock()
    fake_imap.select.return_value = ("OK", [b"1"])
    fake_imap.search.side_effect = Exception("network timeout")
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.find_sent_for_thread(MID, SINCE, mode="followup")

    fake_imap.logout.assert_called_once()


def test_select_uses_readonly(mocker):
    fake_imap = _make_imap()
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.find_sent_for_thread(MID, SINCE, mode="first_touch")

    fake_imap.select.assert_called_once_with('"[Gmail]/Sent Mail"', readonly=True)


def test_since_date_formatted_correctly(mocker):
    fake_imap = _make_imap()
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.find_sent_for_thread(MID, date(2026, 5, 1), mode="first_touch")

    args = fake_imap.search.call_args.args
    assert "01-May-2026" in args
