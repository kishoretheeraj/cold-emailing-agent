"""Tests for gmail.py — DraftResult namedtuple and Gmail API draft ID lookup."""

from unittest.mock import MagicMock

import pytest

import gmail
from gmail import DraftResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_imap(append_data=b"appended"):
    fake = MagicMock(name="imap")
    fake.append.return_value = ("OK", [append_data])
    # Default: no idempotency hit
    fake.search.return_value = ("OK", [b""])
    return fake


def _patch_imap(mocker, append_data=b"appended"):
    fake = _fake_imap(append_data)
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake)
    return fake


# ── Return type ───────────────────────────────────────────────────────────────

def test_create_draft_returns_draft_result_namedtuple(mocker):
    """create_draft must return a DraftResult, not a plain tuple."""
    _patch_imap(mocker)
    mocker.patch.object(gmail, "_lookup_gmail_draft_id", return_value=None)

    result = gmail.create_draft("a@b.com", "subj", "body")

    assert isinstance(result, DraftResult)
    assert result.message_id is not None
    assert result.gmail_draft_id is None
    assert result.gmail_thread_id is None


def test_create_draft_message_id_attribute(mocker):
    """message_id must be a valid RFC822 angle-bracket ID."""
    _patch_imap(mocker)
    mocker.patch.object(gmail, "_lookup_gmail_draft_id", return_value=None)

    result = gmail.create_draft("a@b.com", "subj", "body")

    assert result.message_id.startswith("<")
    assert result.message_id.endswith(">")
    assert "@" in result.message_id


def test_create_draft_gmail_draft_id_set_when_lookup_succeeds(mocker):
    """gmail_draft_id is the value returned by _lookup_gmail_draft_id."""
    _patch_imap(mocker)
    mocker.patch.object(gmail, "_lookup_gmail_draft_id", return_value="r123abc")

    result = gmail.create_draft("a@b.com", "subj", "body")

    assert result.gmail_draft_id == "r123abc"


def test_create_draft_gmail_draft_id_none_when_oauth_absent(mocker):
    """gmail_draft_id is None when OAuth env vars are not set."""
    _patch_imap(mocker)
    mocker.patch.object(gmail, "_get_gmail_api_client", return_value=None)

    result = gmail.create_draft("a@b.com", "subj", "body")

    assert result.gmail_draft_id is None


# ── Headers ───────────────────────────────────────────────────────────────────

def test_create_draft_headers_in_raw_bytes(mocker):
    """To, Subject, From, and Message-ID headers must be in the appended bytes."""
    fake = _patch_imap(mocker)
    mocker.patch.object(gmail, "_lookup_gmail_draft_id", return_value=None)

    result = gmail.create_draft("dana@example.com", "My Subject", "body")

    raw = fake.append.call_args.args[3]
    assert b"To: dana@example.com" in raw
    assert b"Subject: My Subject" in raw
    assert b"From: " in raw
    assert result.message_id.encode() in raw


def test_create_draft_threading_headers_when_in_reply_to(mocker):
    """In-Reply-To and References headers are set when in_reply_to is provided."""
    fake = _patch_imap(mocker)
    mocker.patch.object(gmail, "_lookup_gmail_draft_id", return_value=None)

    gmail.create_draft(
        "dana@example.com", "Re: intro", "body",
        in_reply_to="<orig@mail.gmail.com>",
    )

    raw = fake.append.call_args.args[3]
    assert b"In-Reply-To: <orig@mail.gmail.com>" in raw
    assert b"References:" in raw


# ── Error handling ────────────────────────────────────────────────────────────

def test_create_draft_raises_on_imap_append_failure(mocker):
    """IMAP APPEND failure must propagate as RuntimeError."""
    fake = MagicMock(name="imap")
    fake.append.return_value = ("NO", [b"[LIMIT] quota exceeded"])
    fake.search.return_value = ("OK", [b""])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake)
    mocker.patch.object(gmail, "_lookup_gmail_draft_id", return_value=None)

    with pytest.raises(RuntimeError, match="IMAP APPEND failed"):
        gmail.create_draft("a@b.com", "s", "b")


# ── Idempotency ───────────────────────────────────────────────────────────────

def test_create_draft_duplicate_returns_none_result(mocker):
    """Duplicate detection returns DraftResult(None, None, None)."""
    fake = MagicMock(name="imap")
    fake.search.return_value = ("OK", [b"1"])  # key found → duplicate
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake)

    result = gmail.create_draft("a@b.com", "s", "b", contact_id=1, stage="first_touch_drafted")

    assert result == DraftResult(None, None, None)
    fake.append.assert_not_called()


# ── _lookup_gmail_draft_id ────────────────────────────────────────────────────

def test_lookup_gmail_draft_id_returns_none_when_no_client(mocker):
    """Returns None gracefully when OAuth client is unavailable."""
    mocker.patch.object(gmail, "_get_gmail_api_client", return_value=None)
    assert gmail._lookup_gmail_draft_id("<test@gmail.com>") is None


def test_lookup_gmail_draft_id_returns_none_on_api_error(mocker):
    """Never raises — returns None on any API exception."""
    mock_client = MagicMock()
    mock_client.users.return_value.messages.return_value.list.return_value.execute.side_effect = Exception("quota")
    mocker.patch.object(gmail, "_get_gmail_api_client", return_value=mock_client)

    result = gmail._lookup_gmail_draft_id("<test@gmail.com>")
    assert result is None


def test_lookup_gmail_draft_id_matches_by_message_id(mocker):
    """Returns draft ID when messages.list finds the RFC822 message and drafts.list maps it."""
    mock_client = MagicMock()
    target_mid = "<abc123@gmail.com>"
    target_msg_id = "msg-hex-abc"
    # messages.list search returns the target message
    mock_client.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": target_msg_id}]
    }
    # drafts.list returns one draft with matching message.id
    mock_client.users.return_value.drafts.return_value.list.return_value.execute.return_value = {
        "drafts": [{"id": "draft99", "message": {"id": target_msg_id}}]
    }
    mocker.patch.object(gmail, "_get_gmail_api_client", return_value=mock_client)

    result = gmail._lookup_gmail_draft_id(target_mid)
    assert result == "draft99"


def test_lookup_gmail_draft_id_no_match(mocker):
    """Returns None when messages.list finds no message for the given Message-ID."""
    mock_client = MagicMock()
    mock_client.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": []
    }
    mocker.patch.object(gmail, "_get_gmail_api_client", return_value=mock_client)

    result = gmail._lookup_gmail_draft_id("<abc123@gmail.com>")
    assert result is None


# ── X-GM-THRID still captured ─────────────────────────────────────────────────

def test_create_draft_captures_gmail_thread_id(mocker):
    """X-GM-THRID is still captured from IMAP APPENDUID when available."""
    fake = MagicMock(name="imap")
    fake.append.return_value = ("OK", [b"[APPENDUID 1234567 89012] (Success)"])
    fake.search.return_value = ("OK", [b""])
    fake.uid.return_value = ("OK", [b"1 (X-GM-THRID 17850200168 UID 89012)"])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake)
    mocker.patch.object(gmail, "_lookup_gmail_draft_id", return_value=None)

    result = gmail.create_draft("a@b.com", "s", "b")

    assert result.gmail_thread_id == 17850200168


# ── Gmail API path for follow-up drafts ───────────────────────────────────────

def _mock_api_client(thread_id="19e16fae44291d1f", draft_id="r123abc"):
    """Return a minimal mock Gmail API client for follow-up draft creation."""
    client = MagicMock()
    # messages().list() → finds the in_reply_to message
    client.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": "msg001", "threadId": thread_id}]
    }
    # drafts().create() → returns the new draft
    client.users.return_value.drafts.return_value.create.return_value.execute.return_value = {
        "id": draft_id,
        "message": {"id": "msg002", "threadId": thread_id},
    }
    return client


def test_create_draft_uses_api_for_followup(mocker):
    """When in_reply_to is set and OAuth is available, Gmail API is used."""
    mock_client = _mock_api_client(thread_id="19e16fae44291d1f", draft_id="r-api-draft")
    mocker.patch.object(gmail, "_get_gmail_api_client", return_value=mock_client)
    imap_mock = mocker.patch.object(gmail.imaplib, "IMAP4_SSL")

    result = gmail.create_draft(
        "a@b.com", "Re: intro", "body",
        in_reply_to="<orig@mail.gmail.com>",
    )

    mock_client.users.return_value.drafts.return_value.create.assert_called_once()
    imap_mock.assert_not_called()
    assert result.gmail_draft_id == "r-api-draft"
    assert result.gmail_thread_id == int("19e16fae44291d1f", 16)
    assert result.message_id.startswith("<")


def test_create_draft_api_thread_id_is_decimal_int(mocker):
    """gmail_thread_id in DraftResult is the decimal conversion of the hex threadId."""
    mocker.patch.object(gmail, "_get_gmail_api_client", return_value=_mock_api_client("000000000000000a"))
    result = gmail.create_draft("a@b.com", "Re: s", "b", in_reply_to="<x@y.com>")
    assert result.gmail_thread_id == 10  # 0xa == 10


def test_create_draft_falls_back_to_imap_when_message_not_found(mocker):
    """Falls back to IMAP APPEND when Gmail API can't find the in_reply_to message."""
    client = MagicMock()
    client.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": []  # not found
    }
    mocker.patch.object(gmail, "_get_gmail_api_client", return_value=client)
    fake_imap = _patch_imap(mocker)
    mocker.patch.object(gmail, "_lookup_gmail_draft_id", return_value=None)

    result = gmail.create_draft("a@b.com", "Re: s", "b", in_reply_to="<notfound@x.com>")

    fake_imap.append.assert_called_once()
    assert result.message_id is not None


def test_create_draft_falls_back_to_imap_when_api_unavailable(mocker):
    """Falls back to IMAP APPEND when OAuth vars are absent."""
    mocker.patch.object(gmail, "_get_gmail_api_client", return_value=None)
    fake_imap = _patch_imap(mocker)
    mocker.patch.object(gmail, "_lookup_gmail_draft_id", return_value=None)

    result = gmail.create_draft("a@b.com", "Re: s", "b", in_reply_to="<orig@x.com>")

    fake_imap.append.assert_called_once()
    assert result.message_id is not None


def test_create_draft_no_api_call_for_first_touch(mocker):
    """First-touch drafts (no in_reply_to) never call the Gmail API."""
    api_spy = mocker.patch.object(gmail, "_get_gmail_api_client", return_value=MagicMock())
    _patch_imap(mocker)
    mocker.patch.object(gmail, "_lookup_gmail_draft_id", return_value=None)

    gmail.create_draft("a@b.com", "intro", "body")  # no in_reply_to

    api_spy.assert_not_called()


# ── apply_label_to_latest_draft via Gmail API ─────────────────────────────────

def test_apply_label_uses_api_when_draft_id_provided(mocker):
    """Gmail API messages.modify is called when gmail_draft_id is given."""
    client = MagicMock()
    client.users.return_value.labels.return_value.list.return_value.execute.return_value = {
        "labels": [{"id": "Label_1", "name": "Cold Outreach/Follow-up #1"}]
    }
    client.users.return_value.drafts.return_value.get.return_value.execute.return_value = {
        "message": {"id": "msg999"}
    }
    mocker.patch.object(gmail, "_get_gmail_api_client", return_value=client)
    imap_spy = mocker.patch.object(gmail.imaplib, "IMAP4_SSL")

    gmail.apply_label_to_latest_draft("Cold Outreach/Follow-up #1", gmail_draft_id="r-draft")

    client.users.return_value.messages.return_value.modify.assert_called_once()
    imap_spy.assert_not_called()


def test_apply_label_falls_back_to_imap_without_draft_id(mocker):
    """IMAP COPY fallback is used when no gmail_draft_id is provided."""
    mocker.patch.object(gmail, "_get_gmail_api_client", return_value=MagicMock())
    fake_imap = MagicMock()
    fake_imap.search.return_value = ("OK", [b"1 2 3"])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.apply_label_to_latest_draft("Cold Outreach/Follow-up #1")  # no gmail_draft_id

    fake_imap.copy.assert_called_once()
