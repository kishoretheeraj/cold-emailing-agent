"""Tests for monitor.py — reply detection loop with mocked IMAP and Supabase."""

from unittest.mock import MagicMock

import pytest

import monitor


def _contact(**overrides):
    base = {
        "id": 1,
        "name": "Dana",
        "company": "Clearbond",
        "email": "dana@example.com",
    }
    base.update(overrides)
    return base


# ── Empty fast path ──────────────────────────────────────────────────────────


def test_run_with_no_contacts_does_not_open_imap(mocker, capsys):
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[])
    imap_factory = mocker.patch.object(monitor.imaplib, "IMAP4_SSL")

    monitor.run()

    imap_factory.assert_not_called()
    captured = capsys.readouterr().out
    assert "0 replies found, 0 contacts checked" in captured


# ── Reply detected path ──────────────────────────────────────────────────────


def test_run_detects_reply_updates_status_and_labels(mocker, capsys):
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[_contact()])
    update_status = mocker.patch.object(monitor, "update_reply_status")
    create_label = mocker.patch.object(monitor, "create_gmail_label_if_not_exists")

    fake_imap = MagicMock()
    fake_imap.search.return_value = ("OK", [b"5 7"])
    mocker.patch.object(monitor.imaplib, "IMAP4_SSL", return_value=fake_imap)

    monitor.run()

    fake_imap.login.assert_called_once_with(
        monitor.GMAIL_ADDRESS, monitor.GMAIL_APP_PASSWORD
    )
    fake_imap.select.assert_called_once_with("INBOX")
    create_label.assert_called_once_with(fake_imap, monitor.REPLIED_LABEL)
    fake_imap.search.assert_called_once_with(None, 'FROM "dana@example.com"')

    update_status.assert_called_once_with(1, "replied")
    # Labeled both UIDs returned by SEARCH
    assert fake_imap.copy.call_args_list[0].args == ("5", '"Cold Outreach/Replied"')
    assert fake_imap.copy.call_args_list[1].args == ("7", '"Cold Outreach/Replied"')

    fake_imap.logout.assert_called_once()
    out = capsys.readouterr().out
    assert "1 replies found, 1 contacts checked" in out


# ── No reply path ────────────────────────────────────────────────────────────


def test_run_no_reply_does_not_update(mocker, capsys):
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[_contact()])
    update_status = mocker.patch.object(monitor, "update_reply_status")
    mocker.patch.object(monitor, "create_gmail_label_if_not_exists")

    fake_imap = MagicMock()
    fake_imap.search.return_value = ("OK", [b""])
    mocker.patch.object(monitor.imaplib, "IMAP4_SSL", return_value=fake_imap)

    monitor.run()

    update_status.assert_not_called()
    fake_imap.copy.assert_not_called()
    out = capsys.readouterr().out
    assert "0 replies found, 1 contacts checked" in out


# ── Label-failure resilience ─────────────────────────────────────────────────


def test_run_continues_when_individual_label_copy_fails(mocker):
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[_contact()])
    update_status = mocker.patch.object(monitor, "update_reply_status")
    mocker.patch.object(monitor, "create_gmail_label_if_not_exists")

    fake_imap = MagicMock()
    fake_imap.search.return_value = ("OK", [b"1"])
    fake_imap.copy.side_effect = RuntimeError("transient gmail glitch")
    mocker.patch.object(monitor.imaplib, "IMAP4_SSL", return_value=fake_imap)

    # Must not raise — labeling individual messages is best-effort.
    monitor.run()

    update_status.assert_called_once_with(1, "replied")


# ── Always logs out ──────────────────────────────────────────────────────────


def test_run_logs_out_even_on_search_error(mocker):
    mocker.patch.object(monitor, "detect_sent_drafts")
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[_contact()])
    mocker.patch.object(monitor, "create_gmail_label_if_not_exists")

    fake_imap = MagicMock()
    fake_imap.search.side_effect = RuntimeError("network down")
    mocker.patch.object(monitor.imaplib, "IMAP4_SSL", return_value=fake_imap)

    with pytest.raises(RuntimeError):
        monitor.run()

    fake_imap.logout.assert_called_once()


# ── Independence: detect_sent_drafts failure must not block detect_replies ───


def test_run_continues_to_reply_detection_if_sent_detection_raises(mocker, capsys):
    mocker.patch.object(monitor, "detect_sent_drafts",
                        side_effect=RuntimeError("detect_sent_drafts exploded"))
    mocker.patch.object(monitor, "get_sent_contacts", return_value=[])
    imap_factory = mocker.patch.object(monitor.imaplib, "IMAP4_SSL")

    # Must not raise; detect_replies still runs (fast-path with 0 contacts)
    monitor.run()

    out = capsys.readouterr().out
    assert "0 replies found, 0 contacts checked" in out


# ── Constant invariants ──────────────────────────────────────────────────────


def test_replied_label_matches_format():
    assert monitor.REPLIED_LABEL.startswith("Cold Outreach/")
