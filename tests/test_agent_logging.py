"""Tests for agent._execute_draft and reply_drafter logging to draft_history."""

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

import agent
import reply_drafter
from gmail import DraftResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_contact(**overrides):
    base = {
        "id": 1,
        "name": "Dana",
        "email": "dana@example.com",
        "company": "Acme",
        "role": "VP Eng",
        "mode": "outreach",
        "tier": 1,
        "stage": "new",
        "reply_status": "no_reply",
        "dartmouth": False,
        "followup_date": None,
        "last_emailed": None,
        "message_id": None,
        "original_subject": None,
        "detail": "built a thing",
        "notes": None,
        "job_title": None,
        "job_description": None,
        "applied_date": None,
        "company_applied": None,
        "gmail_thread_id": None,
    }
    base.update(overrides)
    return base


def _mock_batch_pipeline(mocker, contact, action, subject, body,
                         finalize_side_effect=None):
    """Wire up the batch pipeline so run() calls _execute_draft once."""
    mock_prepare = mocker.patch("agent.prepare_email", return_value=("prompt", "system", {}))
    mock_finalize = mocker.patch(
        "agent.finalize_email",
        return_value=(subject, body),
        side_effect=finalize_side_effect,
    )
    mock_result = MagicMock()
    mock_result.custom_id = f"{contact['id']}-{action}"
    mock_result.result.type = "succeeded"
    mock_result.result.message.content = [MagicMock(text=body)]

    mock_batch = MagicMock()
    mock_batch.id = "batch-test"
    mock_batch.processing_status = "ended"
    mock_batch.request_counts = MagicMock()

    mock_client = MagicMock()
    mock_client.messages.batches.create.return_value = mock_batch
    mock_client.messages.batches.retrieve.return_value = mock_batch
    mock_client.messages.batches.results.return_value = [mock_result]
    mocker.patch("agent.anthropic.Anthropic", return_value=mock_client)
    return mock_prepare, mock_finalize, mock_client


# ── _execute_draft calls log_drafted_email ────────────────────────────────────

def test_execute_draft_calls_log_drafted_email(mocker):
    """agent._execute_draft must call log_drafted_email with message_id and gmail_draft_id."""
    contact = _build_contact()
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    _mock_batch_pipeline(mocker, contact, "send_first_touch", "subj", "body")
    mocker.patch("agent.create_draft",
                 return_value=DraftResult("<mid@gmail.com>", "r-draft99", 178502168))
    mocker.patch("agent.apply_label_to_latest_draft")
    mocker.patch("agent.update_contact")
    mocker.patch("agent.save_thread_info")
    mocker.patch("agent.insert_email_message")
    mock_log_draft = mocker.patch("agent.log_drafted_email")
    mocker.patch("agent.time.sleep")

    agent.run()

    mock_log_draft.assert_called_once()
    _, kwargs = mock_log_draft.call_args
    assert kwargs["message_id"] == "<mid@gmail.com>"
    assert kwargs["gmail_draft_id"] == "r-draft99"


def test_execute_draft_passes_x_gm_thrid_to_save_thread_info(mocker):
    """X-GM-THRID must still go to save_thread_info, not gmail_draft_id."""
    contact = _build_contact()
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    _mock_batch_pipeline(mocker, contact, "send_first_touch", "subj", "body")
    mocker.patch("agent.create_draft",
                 return_value=DraftResult("<mid@gmail.com>", "r-draft99", 17850200168))
    mocker.patch("agent.apply_label_to_latest_draft")
    mocker.patch("agent.update_contact")
    save_thread_info = mocker.patch("agent.save_thread_info")
    mocker.patch("agent.insert_email_message")
    mocker.patch("agent.log_drafted_email")
    mocker.patch("agent.time.sleep")

    agent.run()

    # save_thread_info must receive the integer X-GM-THRID, not the string draft ID
    save_thread_info.assert_called_once_with(
        1, "<mid@gmail.com>", "subj", gmail_thread_id=17850200168
    )


def test_execute_draft_skips_log_when_duplicate(mocker):
    """log_drafted_email must NOT be called when create_draft returns (None, None, None)."""
    contact = _build_contact()
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    _mock_batch_pipeline(mocker, contact, "send_first_touch", "subj", "body")
    mocker.patch("agent.create_draft", return_value=DraftResult(None, None, None))
    mocker.patch("agent.update_contact")
    mocker.patch("agent.save_thread_info")
    mock_log_draft = mocker.patch("agent.log_drafted_email")
    mocker.patch("agent.time.sleep")

    agent.run()

    mock_log_draft.assert_not_called()


# ── reply_drafter calls log_drafted_email ─────────────────────────────────────

def _contact_for_reply(**overrides):
    base = {
        "id": 42,
        "name": "Alice",
        "email": "alice@acme.com",
        "company": "Acme",
        "role": "VP Product",
        "stage": "first_touch_sent",
        "classifier_status": "positive_reply",
        "original_subject": "quick intro",
        "message_id": "<orig@gmail.com>",
    }
    base.update(overrides)
    return base


def test_reply_drafter_calls_log_drafted_email(mocker):
    """reply_drafter.draft_reply must call log_drafted_email with message_id and gmail_draft_id."""
    mocker.patch.object(reply_drafter, "_generate_reply_body", return_value="Hi Alice.")
    mocker.patch("preflight.check", return_value=[])
    mocker.patch("reply_drafter.create_draft",
                 return_value=DraftResult("<reply@gmail.com>", "r-reply77", None))
    mocker.patch("reply_drafter.apply_label_to_latest_draft")
    mocker.patch("reply_drafter.update_contact")
    mocker.patch("reply_drafter.insert_email_message")
    mocker.patch("reply_drafter.log_agent_event")
    mock_log_draft = mocker.patch("reply_drafter.log_drafted_email")

    reply_drafter.draft_reply(_contact_for_reply(), "Let's talk!", {})

    mock_log_draft.assert_called_once()
    _, kwargs = mock_log_draft.call_args
    assert kwargs["message_id"] == "<reply@gmail.com>"
    assert kwargs["gmail_draft_id"] == "r-reply77"


def test_reply_drafter_skips_log_when_duplicate(mocker):
    """log_drafted_email must NOT be called when create_draft returns a null result."""
    mocker.patch.object(reply_drafter, "_generate_reply_body", return_value="Hi Alice.")
    mocker.patch("preflight.check", return_value=[])
    mocker.patch("reply_drafter.create_draft", return_value=DraftResult(None, None, None))
    mocker.patch("reply_drafter.update_contact")
    mocker.patch("reply_drafter.insert_email_message")
    mocker.patch("reply_drafter.log_agent_event")
    mock_log_draft = mocker.patch("reply_drafter.log_drafted_email")

    reply_drafter.draft_reply(_contact_for_reply(), "Let's talk!", {})

    mock_log_draft.assert_not_called()
