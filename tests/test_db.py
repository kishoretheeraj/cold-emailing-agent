"""Tests for db.py — Supabase wrapper functions and lazy client construction."""

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

import db


@pytest.fixture
def fake_client(mocker):
    """Replace the cached supabase client with a chainable MagicMock."""
    client = MagicMock(name="supabase_client")
    mocker.patch.object(db, "_client", client)
    mocker.patch.object(db, "get_client", return_value=client)
    return client


def _execute_returns(client, data):
    """Make `client.table(..).select(..).execute()` (and similar chains) return `data`."""
    client.table.return_value.select.return_value.execute.return_value.data = data
    # Chain with eq() for filtered selects
    client.table.return_value.select.return_value.eq.return_value.like.return_value.execute.return_value.data = (
        data
    )


# ── get_all_contacts ─────────────────────────────────────────────────────────


def test_get_all_contacts_returns_data(fake_client):
    _execute_returns(fake_client, [{"id": 1, "name": "X"}])
    assert db.get_all_contacts() == [{"id": 1, "name": "X"}]
    fake_client.table.assert_called_with("contacts")


def test_get_all_contacts_returns_empty_when_none(fake_client):
    _execute_returns(fake_client, None)
    assert db.get_all_contacts() == []


# ── update_contact ───────────────────────────────────────────────────────────


def test_update_contact_minimal(fake_client):
    db.update_contact(7, "first_touch_drafted")

    update_call = fake_client.table.return_value.update.call_args
    payload = update_call.args[0]
    assert payload["stage"] == "first_touch_drafted"
    assert payload["last_emailed"] == str(date.today())
    assert "followup_date" not in payload
    assert "template_current" not in payload
    fake_client.table.return_value.update.return_value.eq.assert_called_with("id", 7)


def test_update_contact_with_followup_and_template(fake_client):
    db.update_contact(3, "first_touch_drafted", followup_days=5, template="cold_intro")
    payload = fake_client.table.return_value.update.call_args.args[0]

    assert payload["followup_date"] == str(date.today() + timedelta(days=5))
    assert payload["template_current"] == "cold_intro"


def test_update_contact_omits_followup_when_none(fake_client):
    # followup_days=None → no followup_date written (used for terminal stages).
    db.update_contact(3, "breakup_drafted", followup_days=None)
    payload = fake_client.table.return_value.update.call_args.args[0]
    assert "followup_date" not in payload


def test_update_contact_conditional_adds_stage_filter(fake_client):
    db.update_contact(7, "first_touch_drafted", expected_stage="new")

    # First .eq("id", 7) is called on the update chain
    eq_mock = fake_client.table.return_value.update.return_value.eq
    eq_mock.assert_called_with("id", 7)
    # Second .eq("stage", "new") is chained on the first eq's return value
    eq_mock.return_value.eq.assert_called_with("stage", "new")


def test_update_contact_logs_warning_on_stage_mismatch(fake_client, mocker):
    # Simulate 0 rows updated (stage was changed externally).
    exec_result = MagicMock()
    exec_result.data = []
    (
        fake_client.table.return_value.update.return_value.eq.return_value.eq
        .return_value.execute.return_value
    ) = exec_result

    mock_log = mocker.patch.object(db, "log")
    db.update_contact(7, "first_touch_drafted", expected_stage="new")

    mock_log.warning.assert_called_once()
    assert "7" in mock_log.warning.call_args.args[0]


# ── close_contact ────────────────────────────────────────────────────────────


def test_close_contact_sets_closed(fake_client):
    db.close_contact(11)
    payload = fake_client.table.return_value.update.call_args.args[0]
    assert payload == {"stage": "closed", "last_emailed": str(date.today())}
    fake_client.table.return_value.update.return_value.eq.assert_called_with("id", 11)


# ── get_sent_contacts ────────────────────────────────────────────────────────


def test_get_sent_contacts_filters_correctly(fake_client):
    chain = (
        fake_client.table.return_value.select.return_value.eq.return_value.like.return_value
    )
    chain.execute.return_value.data = [{"id": 1}]

    rows = db.get_sent_contacts()

    fake_client.table.assert_called_with("contacts")
    # Verify the eq + like chain was used with the right args
    fake_client.table.return_value.select.return_value.eq.assert_called_with(
        "reply_status", "no_reply"
    )
    fake_client.table.return_value.select.return_value.eq.return_value.like.assert_called_with(
        "stage", "%_sent%"
    )
    assert rows == [{"id": 1}]


def test_get_sent_contacts_returns_empty(fake_client):
    fake_client.table.return_value.select.return_value.eq.return_value.like.return_value.execute.return_value.data = (
        None
    )
    assert db.get_sent_contacts() == []


# ── update_reply_status ──────────────────────────────────────────────────────


def test_update_reply_status_writes_field(fake_client):
    db.update_reply_status(42, "replied")
    payload = fake_client.table.return_value.update.call_args.args[0]
    assert payload == {"reply_status": "replied"}
    fake_client.table.return_value.update.return_value.eq.assert_called_with("id", 42)


# ── save_thread_info ─────────────────────────────────────────────────────────


def test_save_thread_info_writes_message_id_and_subject(fake_client):
    db.save_thread_info(5, "<mid@gmail.com>", "quick intro")

    payload = fake_client.table.return_value.update.call_args.args[0]
    assert payload["message_id"] == "<mid@gmail.com>"
    assert payload["original_subject"] == "quick intro"
    fake_client.table.return_value.update.return_value.eq.assert_called_with("id", 5)


# ── get_thread_info ───────────────────────────────────────────────────────────


def test_get_thread_info_returns_row(fake_client):
    chain = fake_client.table.return_value.select.return_value.eq.return_value
    chain.execute.return_value.data = [
        {"message_id": "<mid@gmail.com>", "original_subject": "quick intro"}
    ]

    result = db.get_thread_info(5)

    fake_client.table.return_value.select.assert_called_with("message_id, original_subject")
    fake_client.table.return_value.select.return_value.eq.assert_called_with("id", 5)
    assert result == {"message_id": "<mid@gmail.com>", "original_subject": "quick intro"}


def test_get_thread_info_returns_empty_dict_when_no_row(fake_client):
    chain = fake_client.table.return_value.select.return_value.eq.return_value
    chain.execute.return_value.data = []

    assert db.get_thread_info(99) == {}


# ── get_client lazy caching ──────────────────────────────────────────────────


# ── load_prompts ─────────────────────────────────────────────────────────────


def test_load_prompts_returns_key_value_dict(fake_client):
    chain = fake_client.table.return_value.select.return_value
    chain.execute.return_value.data = [
        {"key": "outreach_prompt", "value": "Be a great emailer"},
        {"key": "sender_profile", "value": "Name: Kishore"},
    ]
    result = db.load_prompts()
    assert result == {
        "outreach_prompt": "Be a great emailer",
        "sender_profile": "Name: Kishore",
    }
    fake_client.table.assert_called_with("prompts")


def test_load_prompts_returns_empty_dict_when_no_rows(fake_client):
    chain = fake_client.table.return_value.select.return_value
    chain.execute.return_value.data = []
    assert db.load_prompts() == {}


# ── get_client lazy caching ──────────────────────────────────────────────────


def test_get_client_caches_instance(monkeypatch):
    # Clear the cached singleton, then verify _create_patched is called once.
    monkeypatch.setattr(db, "_client", None)
    calls = {"n": 0}

    def fake_create(url, key, options=None):
        calls["n"] += 1
        return MagicMock(name="created")

    monkeypatch.setattr(db, "_create_patched", fake_create)

    a = db.get_client()
    b = db.get_client()

    assert a is b
    assert calls["n"] == 1
