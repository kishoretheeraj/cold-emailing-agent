"""Tests for _resolve_thread_message_id and the threading fix in _execute_draft."""

from datetime import date
from unittest.mock import MagicMock

import pytest

import agent
from gmail import DraftResult

DRAFT_MID = "<draft-123@gmail.com>"
ACTUAL_MID = "<sent-abc@mail.gmail.com>"


def _contact(**overrides):
    base = {
        "id": 7,
        "name": "Avery",
        "email": "avery@coverdash.com",
        "company": "Coverdash",
        "mode": "outreach",
        "stage": "first_touch_sent",
        "reply_status": "no_reply",
        "gmail_thread_id": 99001122,
        "original_subject": "built insurance infra at scale",
        "last_emailed": "2026-05-10",
    }
    base.update(overrides)
    return base


# ── _resolve_thread_message_id unit tests ─────────────────────────────────────

def test_resolve_thrid_hit(mocker):
    mocker.patch("agent.find_sent_by_thread_id", return_value=ACTUAL_MID)
    mocker.patch("agent.find_sent_by_subject", return_value=None)
    result = agent._resolve_thread_message_id(_contact(), DRAFT_MID)
    assert result == ACTUAL_MID


def test_resolve_thrid_miss_subject_hit(mocker):
    mocker.patch("agent.find_sent_by_thread_id", return_value=None)
    mocker.patch("agent.find_sent_by_subject", return_value=ACTUAL_MID)
    result = agent._resolve_thread_message_id(_contact(), DRAFT_MID)
    assert result == ACTUAL_MID


def test_resolve_both_miss_returns_original(mocker):
    mocker.patch("agent.find_sent_by_thread_id", return_value=None)
    mocker.patch("agent.find_sent_by_subject", return_value=None)
    result = agent._resolve_thread_message_id(_contact(), DRAFT_MID)
    assert result == DRAFT_MID


def test_resolve_no_thrid_falls_through_to_subject(mocker):
    thrid_fn = mocker.patch("agent.find_sent_by_thread_id")
    subj_fn = mocker.patch("agent.find_sent_by_subject", return_value=ACTUAL_MID)
    contact = _contact(gmail_thread_id=None)
    result = agent._resolve_thread_message_id(contact, DRAFT_MID)
    thrid_fn.assert_not_called()
    subj_fn.assert_called_once()
    assert result == ACTUAL_MID


def test_resolve_no_subject_thrid_fails_returns_original(mocker):
    mocker.patch("agent.find_sent_by_thread_id", return_value=None)
    subj_fn = mocker.patch("agent.find_sent_by_subject")
    contact = _contact(original_subject=None)
    result = agent._resolve_thread_message_id(contact, DRAFT_MID)
    subj_fn.assert_not_called()
    assert result == DRAFT_MID


def test_resolve_thrid_exception_falls_through(mocker):
    mocker.patch("agent.find_sent_by_thread_id", side_effect=RuntimeError("imap down"))
    mocker.patch("agent.find_sent_by_subject", return_value=ACTUAL_MID)
    result = agent._resolve_thread_message_id(_contact(), DRAFT_MID)
    assert result == ACTUAL_MID


# ── _execute_draft integration: resolution updates Supabase and create_draft ──

def _build_followup_contact(**overrides):
    base = {
        "id": 7,
        "name": "Avery",
        "email": "avery@coverdash.com",
        "company": "Coverdash",
        "mode": "outreach",
        "stage": "first_touch_sent",
        "reply_status": "no_reply",
        "gmail_thread_id": 99001122,
        "original_subject": "built insurance infra at scale",
        "latest_message_id": ACTUAL_MID,
        "last_emailed": "2026-05-10",
        "tier": 2,
    }
    base.update(overrides)
    return base


def test_execute_draft_resolves_stale_message_id_for_followup(mocker):
    """When _resolve_thread_message_id returns a different ID for a follow-up,
    update_message_id is called and create_draft receives the resolved ID."""
    contact = _build_followup_contact()

    mocker.patch("agent._resolve_thread_message_id", return_value=ACTUAL_MID)
    update_mid = mocker.patch("agent.update_message_id")
    create_draft_mock = mocker.patch(
        "agent.create_draft",
        return_value=DraftResult(ACTUAL_MID, "draft-id-xyz", None),
    )
    mocker.patch("agent.apply_label_to_latest_draft")
    mocker.patch("agent.update_contact")
    mocker.patch("agent.save_thread_info")
    mocker.patch("agent.insert_email_message")
    mocker.patch("agent.log_drafted_email")

    agent._execute_draft(
        contact=contact,
        action="send_followup1",
        subject="Re: built insurance infra at scale",
        body="Hi Avery, following up...",
        thread_message_id=DRAFT_MID,
        mode_tag="[OUTREACH]",
        today=date(2026, 5, 22),
        prompts={},
    )

    update_mid.assert_called_once_with(7, ACTUAL_MID)
    _, kwargs = create_draft_mock.call_args
    assert kwargs.get("in_reply_to") == ACTUAL_MID


def test_execute_draft_no_resolution_when_ids_match(mocker):
    """When resolved ID equals the stored ID, update_message_id is not called."""
    contact = _build_followup_contact()

    mocker.patch("agent._resolve_thread_message_id", return_value=DRAFT_MID)
    update_mid = mocker.patch("agent.update_message_id")
    mocker.patch(
        "agent.create_draft",
        return_value=DraftResult(DRAFT_MID, "draft-id-xyz", None),
    )
    mocker.patch("agent.apply_label_to_latest_draft")
    mocker.patch("agent.update_contact")
    mocker.patch("agent.save_thread_info")
    mocker.patch("agent.insert_email_message")
    mocker.patch("agent.log_drafted_email")

    agent._execute_draft(
        contact=contact,
        action="send_followup1",
        subject="Re: built insurance infra at scale",
        body="Hi Avery, following up...",
        thread_message_id=DRAFT_MID,
        mode_tag="[OUTREACH]",
        today=date(2026, 5, 22),
        prompts={},
    )

    update_mid.assert_not_called()


def test_execute_draft_skips_resolution_for_first_touch(mocker):
    """First-touch actions never call _resolve_thread_message_id."""
    contact = _build_followup_contact(stage="new")
    resolve_fn = mocker.patch("agent._resolve_thread_message_id")
    mocker.patch(
        "agent.create_draft",
        return_value=DraftResult("<new-mid@gmail.com>", None, 12345),
    )
    mocker.patch("agent.apply_label_to_latest_draft")
    mocker.patch("agent.update_contact")
    mocker.patch("agent.save_thread_info")
    mocker.patch("agent.insert_email_message")
    mocker.patch("agent.log_drafted_email")
    mocker.patch("agent.update_message_id")

    agent._execute_draft(
        contact=contact,
        action="send_first_touch",
        subject="built insurance infra at scale",
        body="Hi Avery...",
        thread_message_id=None,
        mode_tag="[OUTREACH]",
        today=date(2026, 5, 22),
        prompts={},
    )

    resolve_fn.assert_not_called()
