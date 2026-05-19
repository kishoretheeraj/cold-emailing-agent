"""Tests for reply_drafter.draft_reply()."""

import pytest
import reply_drafter


def _contact(**overrides):
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


def _patch_all(mocker, body="Hi Alice, happy to chat."):
    mocker.patch.object(reply_drafter, "_generate_reply_body", return_value=body)
    mocker.patch("preflight.check", return_value=[])
    mocker.patch("reply_drafter.create_draft", return_value="<new@gmail.com>")
    mocker.patch("reply_drafter.apply_label_to_latest_draft")
    mocker.patch("reply_drafter.update_contact")
    mocker.patch("reply_drafter.insert_email_message")
    mocker.patch("reply_drafter.log_agent_event")


# ── Draft created for positive_reply ──────────────────────────────────────────

def test_draft_created_for_positive_reply(mocker):
    mock_create = mocker.patch("reply_drafter.create_draft", return_value="<new@gmail.com>")
    mocker.patch.object(reply_drafter, "_generate_reply_body", return_value="Hi Alice, happy to chat.")
    mocker.patch("preflight.check", return_value=[])
    mocker.patch("reply_drafter.apply_label_to_latest_draft")
    mocker.patch("reply_drafter.update_contact")
    mocker.patch("reply_drafter.insert_email_message")
    mocker.patch("reply_drafter.log_agent_event")

    reply_drafter.draft_reply(_contact(), "Let's chat!", {})
    assert mock_create.called


def test_draft_created_for_soft_yes(mocker):
    _patch_all(mocker)
    mock_create = mocker.patch("reply_drafter.create_draft", return_value="<new@gmail.com>")
    reply_drafter.draft_reply(_contact(classifier_status="soft_yes"), "Maybe.", {})
    assert mock_create.called


# ── Skipped for non-draftable statuses ────────────────────────────────────────

def test_skip_for_hard_no(mocker):
    mock_gen = mocker.patch.object(reply_drafter, "_generate_reply_body")
    reply_drafter.draft_reply(_contact(classifier_status="hard_no"), "No thanks.", {})
    assert not mock_gen.called


def test_skip_for_auto_reply(mocker):
    mock_gen = mocker.patch.object(reply_drafter, "_generate_reply_body")
    reply_drafter.draft_reply(_contact(classifier_status="auto_reply"), "OOO.", {})
    assert not mock_gen.called


def test_skip_if_already_reply_drafted(mocker):
    mock_gen = mocker.patch.object(reply_drafter, "_generate_reply_body")
    reply_drafter.draft_reply(
        _contact(classifier_status="positive_reply", stage="reply_drafted"),
        "Let's chat!", {},
    )
    assert not mock_gen.called


# ── Pre-flight block triggers one retry ───────────────────────────────────────

def test_preflight_block_triggers_retry(mocker):
    mocker.patch.object(reply_drafter, "_generate_reply_body", return_value="Hi Alice.")
    # Fail once, pass on retry
    mocker.patch("preflight.check", side_effect=[["first_name_missing: 'Alice'"], []])
    mock_create = mocker.patch("reply_drafter.create_draft", return_value="<new@gmail.com>")
    mocker.patch("reply_drafter.apply_label_to_latest_draft")
    mocker.patch("reply_drafter.update_contact")
    mocker.patch("reply_drafter.insert_email_message")
    mock_event = mocker.patch("reply_drafter.log_agent_event")
    mocker.patch.object(reply_drafter, "_call_claude", return_value="Hi Alice, happy to connect.")

    reply_drafter.draft_reply(_contact(), "Let's chat!", {})
    assert mock_create.called


def test_preflight_hard_block_no_draft(mocker):
    mocker.patch.object(reply_drafter, "_generate_reply_body", return_value="Hi.")
    mocker.patch("preflight.check", return_value=["first_name_missing: 'Alice'"])
    mocker.patch.object(reply_drafter, "_call_claude", return_value="Still failing.")
    mock_create = mocker.patch("reply_drafter.create_draft")
    mock_event = mocker.patch("reply_drafter.log_agent_event")

    reply_drafter.draft_reply(_contact(), "Let's chat!", {})
    assert not mock_create.called
    mock_event.assert_called_once_with(
        "draft_reply", contact_id=42,
        status="blocked_preflight", blocked_checks=["first_name_missing: 'Alice'"],
    )


# ── Critic is NOT called ───────────────────────────────────────────────────────

def test_critic_not_called(mocker):
    _patch_all(mocker)
    mock_critic = mocker.patch("reply_drafter.critique_and_revise"
                               if hasattr(reply_drafter, "critique_and_revise")
                               else "emailer.critique_and_revise")
    reply_drafter.draft_reply(_contact(), "Let's chat!", {})
    assert not mock_critic.called


# ── system=profile passed for prompt caching ──────────────────────────────────

def test_generate_reply_body_passes_system_profile(mocker):
    captured = {}

    def capture(prompt, **kwargs):
        captured.update(kwargs)
        return "Hi Alice, happy to chat."

    mocker.patch.object(reply_drafter, "_call_claude", side_effect=capture)
    prompts = {"sender_profile": "Name: Kishore\nProgram: MEM"}
    reply_drafter._generate_reply_body(_contact(), "Let's chat!", prompts)
    assert captured.get("system") == "Name: Kishore\nProgram: MEM"


def test_generate_reply_body_uses_config_profile_fallback(mocker):
    from config import SENDER_PROFILE
    captured = {}

    def capture(prompt, **kwargs):
        captured.update(kwargs)
        return "Hi Alice."

    mocker.patch.object(reply_drafter, "_call_claude", side_effect=capture)
    reply_drafter._generate_reply_body(_contact(), "Let's chat!", {})
    assert captured.get("system") == SENDER_PROFILE


# ── Duplicate draft returns None ───────────────────────────────────────────────

def test_duplicate_draft_skipped(mocker):
    mocker.patch.object(reply_drafter, "_generate_reply_body", return_value="Hi Alice.")
    mocker.patch("preflight.check", return_value=[])
    mocker.patch("reply_drafter.create_draft", return_value=None)
    mock_update = mocker.patch("reply_drafter.update_contact")
    mocker.patch("reply_drafter.insert_email_message")
    mocker.patch("reply_drafter.log_agent_event")

    reply_drafter.draft_reply(_contact(), "Let's chat!", {})
    assert not mock_update.called
