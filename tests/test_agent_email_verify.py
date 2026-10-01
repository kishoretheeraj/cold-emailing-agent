"""Wiring tests for the email-verify pre-flight gate in agent.run()'s Phase 1 loop."""

from unittest.mock import MagicMock

import agent
import config
from email_verify import EmailVerifyResult


def _build_contact(**overrides):
    contact = {
        "id": 1, "name": "Dana", "email": "dana@example.com",
        "company": "Clearbond", "mode": "outreach", "stage": "new",
        "reply_status": "no_reply", "tier": 2,
    }
    contact.update(overrides)
    return contact


def _mock_successful_batch(mocker, contact, action, subject="subj", body="body"):
    """
    Wires up a full, successful prepare -> batch -> finalize -> draft pipeline
    so agent.run() completes cleanly without ever touching the real Anthropic
    or Gmail APIs. Mirrors tests/test_agent.py's _mock_batch_pipeline.
    """
    custom_id = f"{contact['id']}-{action}"
    mocker.patch("agent.prepare_email", return_value=("prompt", "system", {}))
    mocker.patch("agent.finalize_email", return_value=(subject, body))

    mock_result = MagicMock()
    mock_result.custom_id = custom_id
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

    from gmail import DraftResult
    mocker.patch("agent.create_draft", return_value=DraftResult("<mid@gmail.com>", None, 123))
    mocker.patch("agent.apply_label_to_latest_draft")
    mocker.patch("agent.update_contact")
    mocker.patch("agent.save_thread_info")
    mocker.patch("agent.insert_email_message")
    mocker.patch("agent.log_drafted_email")
    mocker.patch("agent.time.sleep")

    return mock_client


def test_invalid_email_skips_before_batch_is_built(mocker):
    contact = _build_contact()
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    mocker.patch("agent.email_verify.verify",
                 return_value=EmailVerifyResult("invalid", "domain has no mail route"))
    log_event = mocker.patch("agent.log_agent_event")
    prepare = mocker.patch("agent.prepare_email")
    batch_client = MagicMock()
    mocker.patch("agent.anthropic.Anthropic", return_value=batch_client)
    record_run = mocker.patch("agent.record_run")

    agent.run()

    prepare.assert_not_called()
    batch_client.messages.batches.create.assert_not_called()
    record_run.assert_called_once_with("success", 0, 1, 0, mocker.ANY)
    assert log_event.call_args.kwargs.get("status") == "blocked_invalid_email"
    assert log_event.call_args.args[0] == "email_verify"


def test_unknown_email_status_proceeds_to_batch(mocker):
    contact = _build_contact()
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    mocker.patch("agent.email_verify.verify",
                 return_value=EmailVerifyResult("unknown", "DNS timeout"))
    mock_client = _mock_successful_batch(mocker, contact, "send_first_touch")
    mocker.patch("agent.record_run")

    agent.run()

    mock_client.messages.batches.create.assert_called_once()


def test_valid_email_proceeds_to_batch(mocker):
    contact = _build_contact()
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    mocker.patch("agent.email_verify.verify",
                 return_value=EmailVerifyResult("valid", None))
    mock_client = _mock_successful_batch(mocker, contact, "send_first_touch")
    mocker.patch("agent.record_run")

    agent.run()

    mock_client.messages.batches.create.assert_called_once()


def test_followup_action_never_calls_verify(mocker):
    contact = _build_contact(stage="first_touch_sent",
                             followup_date="2020-01-01")
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    mocker.patch("agent.get_thread_info", return_value={})
    verify = mocker.patch("agent.email_verify.verify")
    _mock_successful_batch(mocker, contact, "send_followup1")
    mocker.patch("agent.record_run")

    agent.run()

    verify.assert_not_called()


def test_email_verify_disabled_skips_the_check_entirely(mocker):
    contact = _build_contact()
    mocker.patch.object(config, "EMAIL_VERIFY_ENABLED", False)
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    verify = mocker.patch("agent.email_verify.verify")
    mock_client = _mock_successful_batch(mocker, contact, "send_first_touch")
    mocker.patch("agent.record_run")

    agent.run()

    verify.assert_not_called()
    mock_client.messages.batches.create.assert_called_once()


def test_verify_raising_does_not_break_the_run(mocker):
    contact = _build_contact()
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    mocker.patch("agent.email_verify.verify", side_effect=RuntimeError("boom"))
    _mock_successful_batch(mocker, contact, "send_first_touch")
    mocker.patch("agent.record_run")

    agent.run()  # must not raise
