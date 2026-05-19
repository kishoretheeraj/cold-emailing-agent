"""Tests for gmail.find_sent_for_thread — searches Gmail Sent Mail for a thread."""

from datetime import date
from unittest.mock import MagicMock

import pytest

import gmail


SINCE = date(2026, 5, 1)
MID = "<test-message-id@mail.gmail.com>"
FETCH_RESPONSE = (
    "OK",
    [(None, b"Message-ID: <test-message-id@mail.gmail.com>\r\n")],
)


def _make_imap(search_status="OK", search_data=b"5"):
    fake_imap = MagicMock(name="imap")
    fake_imap.select.return_value = ("OK", [b"1"])
    fake_imap.search.return_value = (search_status, [search_data])
    fake_imap.fetch.return_value = FETCH_RESPONSE
    return fake_imap


def test_first_touch_searches_message_id_header(mocker):
    fake_imap = _make_imap()
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.find_sent_for_thread(MID, SINCE, mode="first_touch")

    args = fake_imap.search.call_args.args
    assert "Message-ID" in args
    assert any(MID in a for a in args if isinstance(a, str))


def test_followup_searches_in_reply_to_header(mocker):
    fake_imap = _make_imap()
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.find_sent_for_thread(MID, SINCE, mode="followup")

    args = fake_imap.search.call_args.args
    assert "In-Reply-To" in args
    assert any(MID in a for a in args if isinstance(a, str))


def test_message_id_is_quoted_in_search(mocker):
    fake_imap = _make_imap()
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.find_sent_for_thread(MID, SINCE, mode="first_touch")

    args = fake_imap.search.call_args.args
    assert f'"{MID}"' in args


def test_returns_actual_mid_when_found(mocker):
    fake_imap = _make_imap(search_status="OK", search_data=b"5")
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.find_sent_for_thread(MID, SINCE, mode="first_touch")

    assert result == MID


def test_returns_rewritten_mid_when_gmail_changes_it(mocker):
    rewritten = "<rewritten-by-gmail@mail.gmail.com>"
    fake_imap = _make_imap(search_status="OK", search_data=b"5")
    fake_imap.fetch.return_value = (
        "OK",
        [(None, f"Message-ID: {rewritten}\r\n".encode())],
    )
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.find_sent_for_thread(MID, SINCE, mode="first_touch")

    assert result == rewritten


def test_returns_none_when_search_is_empty(mocker):
    fake_imap = _make_imap(search_status="OK", search_data=b"")
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.find_sent_for_thread(MID, SINCE, mode="first_touch")

    assert result is None


def test_returns_none_on_imap_exception(mocker):
    fake_imap = MagicMock()
    fake_imap.login.side_effect = Exception("imap is down")
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.find_sent_for_thread(MID, SINCE, mode="first_touch")

    assert result is None


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


# ── find_sent_by_thread_id ────────────────────────────────────────────────────

THRID = 17850200168


def _make_thrid_imap(search_status="OK", search_data=b"5"):
    fake_imap = MagicMock(name="imap")
    fake_imap.select.return_value = ("OK", [b"1"])
    fake_imap.search.return_value = (search_status, [search_data])
    fake_imap.fetch.return_value = FETCH_RESPONSE
    return fake_imap


def test_thread_id_search_uses_xgm_thrid(mocker):
    fake_imap = _make_thrid_imap()
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.find_sent_by_thread_id(THRID, SINCE)

    args = fake_imap.search.call_args.args
    assert "X-GM-THRID" in args
    assert str(THRID) in args


def test_thread_id_search_returns_actual_mid_when_found(mocker):
    fake_imap = _make_thrid_imap()
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.find_sent_by_thread_id(THRID, SINCE)

    assert result == MID


def test_thread_id_search_returns_none_when_not_found(mocker):
    fake_imap = _make_thrid_imap(search_status="OK", search_data=b"")
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.find_sent_by_thread_id(THRID, SINCE)

    assert result is None


def test_thread_id_search_returns_none_on_imap_error(mocker):
    fake_imap = MagicMock()
    fake_imap.login.side_effect = Exception("imap is down")
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.find_sent_by_thread_id(THRID, SINCE)

    assert result is None


def test_thread_id_search_logout_on_error(mocker):
    fake_imap = MagicMock()
    fake_imap.select.return_value = ("OK", [b"1"])
    fake_imap.search.side_effect = Exception("timeout")
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.find_sent_by_thread_id(THRID, SINCE)

    fake_imap.logout.assert_called_once()


# ── find_sent_by_subject ──────────────────────────────────────────────────────

TO = "contact@example.com"
SUBJECT = "Fellow Dartmouth alum reaching out"
SUBJ_FETCH = (
    "OK",
    [(None, b"Message-ID: <found-mid@mail.gmail.com>\r\n")],
)


def _make_subj_imap(search_status="OK", search_data=b"3"):
    fake_imap = MagicMock(name="imap")
    fake_imap.select.return_value = ("OK", [b"1"])
    fake_imap.search.return_value = (search_status, [search_data])
    fake_imap.fetch.return_value = SUBJ_FETCH
    return fake_imap


def test_subject_search_includes_to_filter(mocker):
    fake_imap = _make_subj_imap()
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.find_sent_by_subject(SUBJECT, SINCE, to_email=TO)

    args = fake_imap.search.call_args.args
    assert "TO" in args
    assert TO in args


def test_subject_search_includes_subject_filter(mocker):
    fake_imap = _make_subj_imap()
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.find_sent_by_subject(SUBJECT, SINCE, to_email=TO)

    args = fake_imap.search.call_args.args
    assert "SUBJECT" in args
    # Fragment must appear quoted somewhere in the args
    assert any("Fellow Dartmouth alum" in a for a in args if isinstance(a, str))


def test_subject_search_returns_message_id_when_found(mocker):
    fake_imap = _make_subj_imap()
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.find_sent_by_subject(SUBJECT, SINCE, to_email=TO)

    assert result == "<found-mid@mail.gmail.com>"


def test_subject_search_returns_none_when_no_match(mocker):
    fake_imap = _make_subj_imap(search_status="OK", search_data=b"")
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.find_sent_by_subject(SUBJECT, SINCE, to_email=TO)

    assert result is None


def test_subject_search_returns_none_on_imap_error(mocker):
    fake_imap = MagicMock()
    fake_imap.login.side_effect = Exception("imap is down")
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.find_sent_by_subject(SUBJECT, SINCE, to_email=TO)

    assert result is None


def test_subject_search_logout_on_error(mocker):
    fake_imap = MagicMock()
    fake_imap.select.return_value = ("OK", [b"1"])
    fake_imap.search.side_effect = Exception("timeout")
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.find_sent_by_subject(SUBJECT, SINCE, to_email=TO)

    fake_imap.logout.assert_called_once()


def test_subject_search_different_recipients_get_different_results(mocker):
    """Two contacts with the same subject but different recipients must not collide."""
    fake_imap_snow = _make_subj_imap(search_data=b"7")
    fake_imap_snow.fetch.return_value = ("OK", [(None, b"Message-ID: <snow-mid@mail.gmail.com>\r\n")])

    fake_imap_adams = _make_subj_imap(search_data=b"9")
    fake_imap_adams.fetch.return_value = ("OK", [(None, b"Message-ID: <adams-mid@mail.gmail.com>\r\n")])

    imap_instances = [fake_imap_snow, fake_imap_adams]
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", side_effect=imap_instances)

    result_snow = gmail.find_sent_by_subject(SUBJECT, SINCE, to_email="george@keybank.com")
    result_adams = gmail.find_sent_by_subject(SUBJECT, SINCE, to_email="claytonadams@mascoma.com")

    assert result_snow == "<snow-mid@mail.gmail.com>"
    assert result_adams == "<adams-mid@mail.gmail.com>"
    assert result_snow != result_adams
