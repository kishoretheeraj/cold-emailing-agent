"""Tests for gmail.py — IMAP draft creation and label management."""

from unittest.mock import MagicMock

import pytest

import gmail


# ── create_draft ─────────────────────────────────────────────────────────────


def test_create_draft_logs_in_appends_and_logs_out(mocker):
    fake_imap = MagicMock(name="imap")
    fake_imap.append.return_value = ("OK", [b"appended"])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.create_draft("dana@example.com", "subject", "body text")

    fake_imap.login.assert_called_once_with(gmail.GMAIL_ADDRESS, gmail.GMAIL_APP_PASSWORD)
    fake_imap.append.assert_called_once()
    args = fake_imap.append.call_args.args
    assert args[0] == '"[Gmail]/Drafts"'
    raw_msg = args[3]
    assert b"Subject: subject" in raw_msg
    assert b"To: dana@example.com" in raw_msg
    assert b"body text" in raw_msg
    fake_imap.logout.assert_called_once()


def test_create_draft_returns_message_id(mocker):
    fake_imap = MagicMock(name="imap")
    fake_imap.append.return_value = ("OK", [b"appended"])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    mid, thread_id = gmail.create_draft("dana@example.com", "subject", "body")

    # make_msgid always generates an RFC-compliant angle-bracket id
    assert mid.startswith("<") and mid.endswith(">")
    assert "@" in mid
    # No APPENDUID in response → thread_id is None (best-effort)
    assert thread_id is None


def test_create_draft_embeds_message_id_in_raw_bytes(mocker):
    fake_imap = MagicMock(name="imap")
    fake_imap.append.return_value = ("OK", [b"appended"])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    mid, _ = gmail.create_draft("dana@example.com", "subject", "body")

    raw_msg = fake_imap.append.call_args.args[3]
    assert mid.encode() in raw_msg


def test_create_draft_captures_gmail_thread_id(mocker):
    """When Gmail returns APPENDUID, create_draft fetches and returns X-GM-THRID."""
    fake_imap = MagicMock(name="imap")
    fake_imap.append.return_value = ("OK", [b"[APPENDUID 1234567 89012] (Success)"])
    fake_imap.fetch.return_value = ("OK", [(b"89012 (X-GM-THRID 17850200168)", None)])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    _, thread_id = gmail.create_draft("dana@example.com", "subject", "body")

    assert thread_id == 17850200168


def test_create_draft_adds_threading_headers_when_in_reply_to(mocker):
    fake_imap = MagicMock(name="imap")
    fake_imap.append.return_value = ("OK", [b"appended"])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.create_draft(
        "dana@example.com", "Re: intro", "follow-up body",
        in_reply_to="<orig@mail.gmail.com>",
    )

    raw_msg = fake_imap.append.call_args.args[3]
    assert b"In-Reply-To: <orig@mail.gmail.com>" in raw_msg
    assert b"References: <orig@mail.gmail.com>" in raw_msg


def test_create_draft_prefixes_subject_with_re(mocker):
    fake_imap = MagicMock(name="imap")
    fake_imap.append.return_value = ("OK", [b"appended"])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.create_draft(
        "dana@example.com", "quick intro", "body",
        in_reply_to="<orig@mail.gmail.com>",
    )

    raw_msg = fake_imap.append.call_args.args[3]
    assert b"Subject: Re: quick intro" in raw_msg


def test_create_draft_does_not_double_prefix_re(mocker):
    fake_imap = MagicMock(name="imap")
    fake_imap.append.return_value = ("OK", [b"appended"])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.create_draft(
        "dana@example.com", "Re: quick intro", "body",
        in_reply_to="<orig@mail.gmail.com>",
    )

    raw_msg = fake_imap.append.call_args.args[3]
    assert b"Subject: Re: quick intro" in raw_msg
    assert b"Subject: Re: Re:" not in raw_msg


def test_create_draft_raises_on_non_ok(mocker):
    fake_imap = MagicMock()
    fake_imap.append.return_value = ("NO", [b"oh no"])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    with pytest.raises(RuntimeError, match="IMAP APPEND failed"):
        gmail.create_draft("x@y", "s", "b")

    # Logout must still happen via finally.
    fake_imap.logout.assert_called_once()


def test_create_draft_skips_when_duplicate_exists(mocker):
    fake_imap = MagicMock(name="imap")
    fake_imap.select.return_value = ("OK", [b"1"])
    fake_imap.search.return_value = ("OK", [b"5"])  # existing draft found
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.create_draft("dana@example.com", "subject", "body",
                                contact_id=1, stage="new")

    assert result == (None, None)
    fake_imap.append.assert_not_called()
    fake_imap.logout.assert_called_once()


def test_create_draft_proceeds_when_no_duplicate(mocker):
    fake_imap = MagicMock(name="imap")
    fake_imap.select.return_value = ("OK", [b"1"])
    fake_imap.search.return_value = ("OK", [b""])  # no existing draft
    fake_imap.append.return_value = ("OK", [b"appended"])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.create_draft("dana@example.com", "subject", "body",
                                contact_id=1, stage="new")

    assert result[0] is not None
    fake_imap.append.assert_called_once()


def test_create_draft_adds_idempotency_header_when_key_provided(mocker):
    fake_imap = MagicMock(name="imap")
    fake_imap.select.return_value = ("OK", [b"1"])
    fake_imap.search.return_value = ("OK", [b""])
    fake_imap.append.return_value = ("OK", [b"appended"])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.create_draft("dana@example.com", "subject", "body",
                       contact_id=42, stage="first_touch_drafted")

    raw_msg = fake_imap.append.call_args.args[3]
    assert b"X-Cold-Email-Key:" in raw_msg


def test_create_draft_without_contact_id_skips_idempotency_check(mocker):
    fake_imap = MagicMock(name="imap")
    fake_imap.append.return_value = ("OK", [b"appended"])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    result = gmail.create_draft("dana@example.com", "subject", "body")

    # No search was performed — idempotency check is opt-in
    fake_imap.search.assert_not_called()
    assert result[0] is not None


# ── create_gmail_label_if_not_exists ─────────────────────────────────────────


def test_create_gmail_label_calls_create_with_quoted_name():
    fake_imap = MagicMock()
    fake_imap.create.return_value = ("OK", [b"created"])

    gmail.create_gmail_label_if_not_exists(fake_imap, "Cold Outreach/First Touch")

    fake_imap.create.assert_called_once_with('"Cold Outreach/First Touch"')


def test_create_gmail_label_swallows_already_exists():
    fake_imap = MagicMock()
    fake_imap.create.return_value = ("NO", [b"[ALREADYEXISTS] Mailbox exists."])

    # Must not raise — Gmail returns NO when the label already exists.
    gmail.create_gmail_label_if_not_exists(fake_imap, "Cold Outreach/Replied")


# ── apply_label_to_latest_draft ──────────────────────────────────────────────


def test_apply_label_copies_latest_message(mocker):
    fake_imap = MagicMock()
    fake_imap.search.return_value = ("OK", [b"1 2 3"])
    fake_imap.create.return_value = ("OK", [b"ok"])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.apply_label_to_latest_draft("Cold Outreach/First Touch")

    fake_imap.login.assert_called_once_with(gmail.GMAIL_ADDRESS, gmail.GMAIL_APP_PASSWORD)
    fake_imap.create.assert_called_once_with('"Cold Outreach/First Touch"')
    fake_imap.select.assert_called_once_with('"[Gmail]/Drafts"')
    fake_imap.search.assert_called_once_with(None, "ALL")
    # Copies the LAST UID — not the first
    fake_imap.copy.assert_called_once_with("3", '"Cold Outreach/First Touch"')
    fake_imap.logout.assert_called_once()


def test_apply_label_no_messages_skips_copy(mocker):
    fake_imap = MagicMock()
    fake_imap.search.return_value = ("OK", [b""])
    fake_imap.create.return_value = ("OK", [b"ok"])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.apply_label_to_latest_draft("Cold Outreach/Break-up")

    fake_imap.copy.assert_not_called()
    fake_imap.logout.assert_called_once()


def test_apply_label_search_failure_skips(mocker):
    fake_imap = MagicMock()
    fake_imap.search.return_value = ("NO", [None])
    fake_imap.create.return_value = ("OK", [b"ok"])
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    gmail.apply_label_to_latest_draft("Cold Outreach/First Touch")

    fake_imap.copy.assert_not_called()
    fake_imap.logout.assert_called_once()


def test_apply_label_logs_out_even_on_failure(mocker):
    fake_imap = MagicMock()
    fake_imap.create.side_effect = RuntimeError("imap is down")
    mocker.patch.object(gmail.imaplib, "IMAP4_SSL", return_value=fake_imap)

    with pytest.raises(RuntimeError):
        gmail.apply_label_to_latest_draft("Cold Outreach/First Touch")

    fake_imap.logout.assert_called_once()
