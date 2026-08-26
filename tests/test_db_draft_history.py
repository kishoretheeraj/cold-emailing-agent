"""Tests for db.log_drafted_email — draft_history inserts."""

from unittest.mock import MagicMock

import pytest

import db


@pytest.fixture
def fake_client(mocker):
    client = MagicMock(name="supabase_client")
    mocker.patch.object(db, "_client", client)
    mocker.patch.object(db, "get_client", return_value=client)
    return client


def test_log_drafted_email_with_all_params(fake_client):
    """Full insert: all params stored including gmail_draft_id."""
    db.log_drafted_email(
        contact_id=7,
        stage="first_touch_drafted",
        subject="Hello there",
        body="Body text",
        message_id="<abc@gmail.com>",
        gmail_draft_id="r123",
    )

    fake_client.table.assert_called_with("draft_history")
    payload = fake_client.table.return_value.insert.call_args.args[0]
    assert payload["contact_id"] == 7
    assert payload["stage"] == "first_touch_drafted"
    assert payload["subject"] == "Hello there"
    assert payload["body"] == "Body text"
    assert payload["message_id"] == "<abc@gmail.com>"
    assert payload["gmail_draft_id"] == "r123"
    assert "drafted_at" in payload


def test_log_drafted_email_without_gmail_draft_id(fake_client):
    """Backward compat: gmail_draft_id omitted → not in payload (stores NULL)."""
    db.log_drafted_email(7, "first_touch_drafted", "subj", "body",
                         message_id="<abc@gmail.com>")

    payload = fake_client.table.return_value.insert.call_args.args[0]
    assert "gmail_draft_id" not in payload


def test_log_drafted_email_minimal(fake_client):
    """Minimal call: only contact_id and stage required."""
    db.log_drafted_email(3, "reply_drafted", None, None)

    payload = fake_client.table.return_value.insert.call_args.args[0]
    assert payload["contact_id"] == 3
    assert payload["stage"] == "reply_drafted"
    assert "subject" not in payload
    assert "body" not in payload


def test_log_drafted_email_with_decision_context(fake_client):
    """decision_context passed → stored verbatim in the insert payload."""
    db.log_drafted_email(
        contact_id=7,
        stage="first_touch_drafted",
        subject="Hello there",
        body="Body text",
        message_id="<abc@gmail.com>",
        gmail_draft_id="r123",
        decision_context={"prompt_hash": "3f9a1c2b7e0d4f6a"},
    )

    payload = fake_client.table.return_value.insert.call_args.args[0]
    assert payload["decision_context"] == {"prompt_hash": "3f9a1c2b7e0d4f6a"}


def test_log_drafted_email_without_decision_context(fake_client):
    """Backward compat: omitted → not in payload (stores NULL = not instrumented)."""
    db.log_drafted_email(7, "first_touch_drafted", "subj", "body",
                         message_id="<abc@gmail.com>")

    payload = fake_client.table.return_value.insert.call_args.args[0]
    assert "decision_context" not in payload


def test_log_drafted_email_never_raises_on_supabase_error(mocker):
    """Supabase failure is swallowed — function must not raise."""
    mocker.patch.object(db, "get_client", side_effect=RuntimeError("db down"))

    # Should not raise
    db.log_drafted_email(1, "first_touch_drafted", "s", "b")


def test_log_drafted_email_logs_warning_on_error(mocker, caplog):
    """Supabase failure emits a warning log."""
    import logging
    mocker.patch.object(db, "get_client", side_effect=RuntimeError("db down"))

    with caplog.at_level(logging.WARNING):
        db.log_drafted_email(1, "first_touch_drafted", "s", "b")

    assert any("draft_history" in r.message for r in caplog.records)
