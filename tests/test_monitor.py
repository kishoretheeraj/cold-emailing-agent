"""Tests for monitor.py — new header-based reply detection, phase isolation."""

from unittest.mock import MagicMock

import pytest

import monitor


def _contact(**overrides):
    base = {
        "id": 1,
        "name": "Dana",
        "company": "Clearbond",
        "email": "dana@example.com",
        "message_id": "<orig123@gmail.com>",
        "stage": "first_touch_sent",
        "classifier_status": None,
    }
    base.update(overrides)
    return base


def _fake_imap(msg_nums=b"1", search_status="OK"):
    fake = MagicMock()
    fake.login.return_value = ("OK", [b"Logged in"])
    fake.select.return_value = ("OK", [b""])
    fake.search.return_value = (search_status, [msg_nums])
    fake.logout.return_value = ("OK", [b""])
    return fake


# ── Empty fast-path ────────────────────────────────────────────────────────────

def test_no_contacts_with_message_id_skips_imap(mocker):
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts",
                        return_value=[_contact(message_id=None)])
    imap_cls = mocker.patch.object(monitor.imaplib, "IMAP4_SSL")

    monitor.run()

    imap_cls.assert_not_called()


def test_empty_contacts_skips_imap(mocker):
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[])
    imap_cls = mocker.patch.object(monitor.imaplib, "IMAP4_SSL")

    monitor.run()

    imap_cls.assert_not_called()


# ── Header-based match ─────────────────────────────────────────────────────────

def test_in_reply_to_match_classifies_and_stores(mocker):
    contact = _contact()
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[contact])
    mocker.patch.object(monitor, "create_gmail_label_if_not_exists")
    mocker.patch.object(monitor, "load_prompts", return_value={})

    fake = _fake_imap()
    mocker.patch.object(monitor.imaplib, "IMAP4_SSL", return_value=fake)

    # _fetch_headers returns a message whose In-Reply-To matches our stored message_id
    mock_msg = MagicMock()
    mock_msg.get.side_effect = lambda h, d="": {
        "In-Reply-To": "<orig123@gmail.com>",
        "References": "",
        "Auto-Submitted": "no",
        "X-Auto-Response-Suppress": "",
        "Message-ID": "<reply456@gmail.com>",
        "Subject": "Re: intro",
        "Date": "Fri, 16 May 2026 10:00:00 +0000",
    }.get(h, d)
    mocker.patch.object(monitor, "_fetch_headers", return_value=mock_msg)
    mocker.patch.object(monitor, "_fetch_body_text", return_value="Sounds great!")
    mocker.patch.object(monitor, "_classify_reply", return_value="positive_reply")
    mock_insert = mocker.patch.object(monitor, "insert_email_message")
    mock_update_cs = mocker.patch.object(monitor, "update_classifier_status")
    mocker.patch.object(monitor, "log_agent_event")
    mocker.patch.object(monitor, "_draft_reply_responses")

    monitor.run()

    mock_insert.assert_called_once()
    mock_update_cs.assert_called_once_with(1, "positive_reply")


def test_no_header_match_skips_contact(mocker):
    contact = _contact()
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[contact])
    mocker.patch.object(monitor, "create_gmail_label_if_not_exists")
    mocker.patch.object(monitor, "load_prompts", return_value={})

    fake = _fake_imap()
    mocker.patch.object(monitor.imaplib, "IMAP4_SSL", return_value=fake)

    mock_msg = MagicMock()
    mock_msg.get.side_effect = lambda h, d="": {
        "In-Reply-To": "<unrelated@other.com>",
        "References": "",
        "Auto-Submitted": "no",
        "X-Auto-Response-Suppress": "",
    }.get(h, d)
    mocker.patch.object(monitor, "_fetch_headers", return_value=mock_msg)
    mock_update_cs = mocker.patch.object(monitor, "update_classifier_status")
    mocker.patch.object(monitor, "_draft_reply_responses")

    monitor.run()

    mock_update_cs.assert_not_called()


def test_auto_reply_header_bypasses_claude(mocker):
    contact = _contact()
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[contact])
    mocker.patch.object(monitor, "create_gmail_label_if_not_exists")
    mocker.patch.object(monitor, "load_prompts", return_value={})

    fake = _fake_imap()
    mocker.patch.object(monitor.imaplib, "IMAP4_SSL", return_value=fake)

    mock_msg = MagicMock()
    mock_msg.get.side_effect = lambda h, d="": {
        "In-Reply-To": "<orig123@gmail.com>",
        "References": "",
        "Auto-Submitted": "auto-replied",
        "X-Auto-Response-Suppress": "",
        "Message-ID": "<autoreply@gmail.com>",
        "Subject": "Out of office",
        "Date": "Fri, 16 May 2026 10:00:00 +0000",
    }.get(h, d)
    mocker.patch.object(monitor, "_fetch_headers", return_value=mock_msg)
    mock_classify = mocker.patch.object(monitor, "_classify_reply")
    mocker.patch.object(monitor, "insert_email_message")
    mock_update_cs = mocker.patch.object(monitor, "update_classifier_status")
    mocker.patch.object(monitor, "log_agent_event")
    mocker.patch.object(monitor, "_draft_reply_responses")

    monitor.run()

    mock_classify.assert_not_called()
    mock_update_cs.assert_called_once_with(1, "auto_reply")


def test_already_classified_contact_skipped(mocker):
    contact = _contact(classifier_status="positive_reply")
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[contact])
    mocker.patch.object(monitor, "create_gmail_label_if_not_exists")
    mocker.patch.object(monitor, "load_prompts", return_value={})

    fake = _fake_imap()
    mocker.patch.object(monitor.imaplib, "IMAP4_SSL", return_value=fake)

    mock_msg = MagicMock()
    mock_msg.get.side_effect = lambda h, d="": {
        "In-Reply-To": "<orig123@gmail.com>",
        "References": "",
        "Auto-Submitted": "no",
        "X-Auto-Response-Suppress": "",
    }.get(h, d)
    mocker.patch.object(monitor, "_fetch_headers", return_value=mock_msg)
    mock_update_cs = mocker.patch.object(monitor, "update_classifier_status")
    mocker.patch.object(monitor, "_draft_reply_responses")

    monitor.run()

    mock_update_cs.assert_not_called()


# ── Phase isolation ────────────────────────────────────────────────────────────

def test_sent_detection_failure_does_not_block_reply_detection(mocker):
    mocker.patch.object(monitor, "detect_sent_drafts",
                        side_effect=RuntimeError("sent detection exploded"))
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[])
    mocker.patch.object(monitor, "load_prompts", return_value={})
    imap_cls = mocker.patch.object(monitor.imaplib, "IMAP4_SSL")

    # Must not raise — detect_replies still runs (fast-path, no contacts)
    monitor.run()

    imap_cls.assert_not_called()


def test_per_message_exception_continues_loop(mocker):
    contact = _contact()
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[contact])
    mocker.patch.object(monitor, "create_gmail_label_if_not_exists")
    mocker.patch.object(monitor, "load_prompts", return_value={})

    fake = _fake_imap(msg_nums=b"1 2")
    mocker.patch.object(monitor.imaplib, "IMAP4_SSL", return_value=fake)

    # First message raises; second should still be processed
    mocker.patch.object(monitor, "_fetch_headers",
                        side_effect=[RuntimeError("fetch error"), MagicMock()])
    mocker.patch.object(monitor, "_draft_reply_responses")

    # Must not raise
    monitor.run()


# ── Constant invariant ─────────────────────────────────────────────────────────

def test_replied_label_format():
    assert monitor.REPLIED_LABEL.startswith("Cold Outreach/")
