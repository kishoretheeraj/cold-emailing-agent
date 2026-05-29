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
    mocker.patch.object(monitor, "get_drafted_contacts", return_value=[])
    mocker.patch.object(monitor, "record_run")
    imap_cls = mocker.patch.object(monitor.imaplib, "IMAP4_SSL")

    monitor.run()

    imap_cls.assert_not_called()


def test_empty_contacts_skips_imap(mocker):
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[])
    mocker.patch.object(monitor, "get_drafted_contacts", return_value=[])
    mocker.patch.object(monitor, "record_run")
    imap_cls = mocker.patch.object(monitor.imaplib, "IMAP4_SSL")

    monitor.run()

    imap_cls.assert_not_called()


# ── Header-based match ─────────────────────────────────────────────────────────

def test_in_reply_to_match_classifies_and_stores(mocker):
    contact = _contact()
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[contact])
    mocker.patch.object(monitor, "get_drafted_contacts", return_value=[])
    mocker.patch.object(monitor, "record_run")
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
    """IMAP server finds nothing for our message_id — no fetch, no classification."""
    contact = _contact()
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[contact])
    mocker.patch.object(monitor, "get_drafted_contacts", return_value=[])
    mocker.patch.object(monitor, "record_run")
    mocker.patch.object(monitor, "create_gmail_label_if_not_exists")
    mocker.patch.object(monitor, "load_prompts", return_value={})

    fake = _fake_imap(msg_nums=b"", search_status="OK")
    mocker.patch.object(monitor.imaplib, "IMAP4_SSL", return_value=fake)

    mock_fetch = mocker.patch.object(monitor, "_fetch_headers")
    mock_update_cs = mocker.patch.object(monitor, "update_classifier_status")
    mocker.patch.object(monitor, "_draft_reply_responses")

    monitor.run()

    mock_fetch.assert_not_called()
    mock_update_cs.assert_not_called()


def test_imap_false_positive_skipped(mocker):
    """IMAP returns a hit (substring match) but _match_message rejects it."""
    contact = _contact()
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[contact])
    mocker.patch.object(monitor, "get_drafted_contacts", return_value=[])
    mocker.patch.object(monitor, "record_run")
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
    mocker.patch.object(monitor, "get_drafted_contacts", return_value=[])
    mocker.patch.object(monitor, "record_run")
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
    mocker.patch.object(monitor, "get_drafted_contacts", return_value=[])
    mocker.patch.object(monitor, "record_run")
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
    mocker.patch.object(monitor, "get_drafted_contacts", return_value=[])
    mocker.patch.object(monitor, "record_run")
    mocker.patch.object(monitor, "load_prompts", return_value={})
    imap_cls = mocker.patch.object(monitor.imaplib, "IMAP4_SSL")

    # Must not raise — detect_replies still runs (fast-path, no contacts)
    monitor.run()

    imap_cls.assert_not_called()


def test_per_message_exception_continues_loop(mocker):
    contact = _contact()
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[contact])
    mocker.patch.object(monitor, "get_drafted_contacts", return_value=[])
    mocker.patch.object(monitor, "record_run")
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


# ── _fetch_body_text ──────────────────────────────────────────────────────────

def _make_multipart_imap(plain_text=None, html_text=None):
    """Return a fake IMAP whose fetch() yields a minimal multipart/mixed RFC822 message."""
    import email as _email
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    outer = MIMEMultipart("mixed")
    outer["From"] = "jose@reindi.com"
    outer["To"] = "kishore@gmail.com"
    outer["Subject"] = "Re: intro"
    outer["Message-ID"] = "<reply@reindi.com>"
    outer["Date"] = "Wed, 21 May 2026 08:27:00 +0000"
    if plain_text is not None:
        outer.attach(MIMEText(plain_text, "plain"))
    if html_text is not None:
        outer.attach(MIMEText(f"<p>{html_text}</p>", "html"))

    raw = outer.as_bytes()
    fake = MagicMock()
    fake.fetch.return_value = ("OK", [(b"1 (RFC822 {%d})" % len(raw), raw)])
    return fake


def test_fetch_body_text_multipart_plain():
    fake_imap = _make_multipart_imap(plain_text="Happy to talk. Please send time slots.")
    result = monitor._fetch_body_text(fake_imap, b"1")
    assert "Happy to talk" in result


def test_fetch_body_text_multipart_html_fallback():
    fake_imap = _make_multipart_imap(html_text="Happy to connect with you!")
    result = monitor._fetch_body_text(fake_imap, b"1")
    assert "Happy to connect" in result


def test_fetch_body_text_empty_returns_empty():
    fake = MagicMock()
    fake.fetch.return_value = ("OK", [None])
    assert monitor._fetch_body_text(fake, b"1") == ""


def test_empty_body_skips_classification(mocker):
    """When _fetch_body_text returns empty string, classifier_status must NOT be written."""
    contact = _contact()
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[contact])
    mocker.patch.object(monitor, "get_drafted_contacts", return_value=[])
    mocker.patch.object(monitor, "record_run")
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
        "Message-ID": "<reply456@gmail.com>",
        "Subject": "Re: intro",
        "Date": "Wed, 21 May 2026 08:27:00 +0000",
    }.get(h, d)
    mocker.patch.object(monitor, "_fetch_headers", return_value=mock_msg)
    mocker.patch.object(monitor, "_fetch_body_text", return_value="")  # body fetch failed
    mock_classify = mocker.patch.object(monitor, "_classify_reply")
    mock_update_cs = mocker.patch.object(monitor, "update_classifier_status")
    mocker.patch.object(monitor, "_draft_reply_responses")

    monitor.run()

    mock_classify.assert_not_called()
    mock_update_cs.assert_not_called()


# ── latest_message_id lookup (Nikki WW regression) ───────────────────────────

def test_reply_to_latest_message_id_is_detected(mocker):
    """Reply whose In-Reply-To = latest_message_id (not message_id) must be matched.

    Regression for: by_message_id only contained message_id, missing
    latest_message_id. Replies to follow-up emails (where the email client
    doesn't propagate the full References chain) were never detected.
    """
    contact = _contact(
        message_id="<first@gmail.com>",
        latest_message_id="<followup@gmail.com>",
    )
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[contact])
    mocker.patch.object(monitor, "get_drafted_contacts", return_value=[])
    mocker.patch.object(monitor, "record_run")
    mocker.patch.object(monitor, "create_gmail_label_if_not_exists")
    mocker.patch.object(monitor, "load_prompts", return_value={})

    # IMAP only finds message #1 when searching for the follow-up ID, not the
    # first-touch ID — this mirrors real behaviour where the reply references
    # only the immediate parent email.
    fake = MagicMock()
    fake.login.return_value = ("OK", [b"Logged in"])
    fake.select.return_value = ("OK", [b""])
    fake.logout.return_value = ("OK", [b""])

    def search_side_effect(_, criteria):
        return ("OK", [b"1"]) if "followup@gmail.com" in criteria else ("OK", [b""])

    fake.search.side_effect = search_side_effect
    mocker.patch.object(monitor.imaplib, "IMAP4_SSL", return_value=fake)

    mock_msg = MagicMock()
    mock_msg.get.side_effect = lambda h, d="": {
        "In-Reply-To": "<followup@gmail.com>",   # only latest_message_id, not first
        "References": "<followup@gmail.com>",
        "Auto-Submitted": "no",
        "X-Auto-Response-Suppress": "",
        "Message-ID": "<nikki-reply@gmail.com>",
        "Subject": "Re: intro",
        "Date": "Wed, 27 May 2026 15:43:00 +0000",
    }.get(h, d)
    mocker.patch.object(monitor, "_fetch_headers", return_value=mock_msg)
    mocker.patch.object(monitor, "_fetch_body_text", return_value="I retired from KP.")
    mocker.patch.object(monitor, "_classify_reply", return_value="out_of_office")
    mocker.patch.object(monitor, "insert_email_message")
    mock_update_cs = mocker.patch.object(monitor, "update_classifier_status")
    mocker.patch.object(monitor, "log_agent_event")
    mocker.patch.object(monitor, "_draft_reply_responses")

    monitor.run()

    mock_update_cs.assert_called_once_with(contact["id"], "out_of_office")


def test_latest_message_id_same_as_message_id_no_duplicate_key(mocker):
    """When latest_message_id == message_id (first-touch only), only one dict entry."""
    contact = _contact(
        message_id="<orig123@gmail.com>",
        latest_message_id="<orig123@gmail.com>",  # same as message_id
    )
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[contact])
    mocker.patch.object(monitor, "get_drafted_contacts", return_value=[])
    mocker.patch.object(monitor, "record_run")
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
        "Message-ID": "<reply@gmail.com>",
        "Subject": "Re: intro",
        "Date": "Wed, 27 May 2026 10:00:00 +0000",
    }.get(h, d)
    mocker.patch.object(monitor, "_fetch_headers", return_value=mock_msg)
    mocker.patch.object(monitor, "_fetch_body_text", return_value="Happy to chat.")
    mocker.patch.object(monitor, "_classify_reply", return_value="positive_reply")
    mocker.patch.object(monitor, "insert_email_message")
    mock_update_cs = mocker.patch.object(monitor, "update_classifier_status")
    mocker.patch.object(monitor, "log_agent_event")
    mocker.patch.object(monitor, "_draft_reply_responses")

    monitor.run()

    # Contact found once (no double-processing from duplicate dict key)
    mock_update_cs.assert_called_once_with(contact["id"], "positive_reply")


# ── Drafted contacts included in reply detection (George/John regression) ─────

def test_drafted_contact_included_in_reply_detection(mocker):
    """A contact in *_drafted stage is included in reply detection.

    Regression for: detect_replies() only called get_sent_contacts(), excluding
    contacts whose current draft hadn't been sent yet but who replied to an
    earlier email.
    """
    sent_contact = _contact(id=1, message_id="<sent@gmail.com>")
    drafted_contact = _contact(
        id=2, message_id="<first-touch@gmail.com>",
        stage="followup1_drafted", classifier_status=None,
    )
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[sent_contact])
    mocker.patch.object(monitor, "get_drafted_contacts", return_value=[drafted_contact])
    mocker.patch.object(monitor, "record_run")
    mocker.patch.object(monitor, "create_gmail_label_if_not_exists")
    mocker.patch.object(monitor, "load_prompts", return_value={})

    # IMAP returns msg num 1 for sent_contact's mid, msg num 2 for drafted_contact's mid
    fake = MagicMock()
    fake.login.return_value = ("OK", [b"Logged in"])
    fake.select.return_value = ("OK", [b""])
    fake.logout.return_value = ("OK", [b""])

    def search_side_effect(_, criteria):
        if "sent@gmail.com" in criteria:
            return ("OK", [b"1"])
        if "first-touch@gmail.com" in criteria:
            return ("OK", [b"2"])
        return ("OK", [b""])

    fake.search.side_effect = search_side_effect
    mocker.patch.object(monitor.imaplib, "IMAP4_SSL", return_value=fake)

    def make_msg(in_reply_to):
        m = MagicMock()
        m.get.side_effect = lambda h, d="": {
            "In-Reply-To": in_reply_to,
            "References": in_reply_to,
            "Auto-Submitted": "no",
            "X-Auto-Response-Suppress": "",
            "Message-ID": f"<reply-for-{in_reply_to.strip('<>')}@gmail.com>",
            "Subject": "Re: intro",
            "Date": "Wed, 27 May 2026 10:00:00 +0000",
        }.get(h, d)
        return m

    fetch_calls = []

    def fetch_headers_side(_, num):
        n = num if isinstance(num, bytes) else num.encode()
        if n == b"1":
            return make_msg("<sent@gmail.com>")
        return make_msg("<first-touch@gmail.com>")

    mocker.patch.object(monitor, "_fetch_headers", side_effect=fetch_headers_side)
    mocker.patch.object(monitor, "_fetch_body_text", return_value="Happy to talk!")
    mocker.patch.object(monitor, "_classify_reply", return_value="positive_reply")
    mocker.patch.object(monitor, "insert_email_message")
    mock_update_cs = mocker.patch.object(monitor, "update_classifier_status")
    mocker.patch.object(monitor, "log_agent_event")
    mocker.patch.object(monitor, "_draft_reply_responses")

    monitor.run()

    # Both contacts' replies should be classified
    classified_ids = {call.args[0] for call in mock_update_cs.call_args_list}
    assert 1 in classified_ids, "sent contact reply not classified"
    assert 2 in classified_ids, "drafted contact reply not classified"


# ── Constant invariant ─────────────────────────────────────────────────────────

def test_replied_label_format():
    assert monitor.REPLIED_LABEL.startswith("Cold Outreach/")
