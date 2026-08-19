"""Tests for Voice DNA injection into first-touch prompts."""

import pytest

import emailer


_CONTACT = {"id": 1, "name": "Jane Doe", "company": "Acme Corp",
            "role": "VP Eng", "detail": "d", "tier": 3}
_PROMPTS = {"voice_dna": "## Writing Style\nShort sentences."}


def _prep(mocker, action, prompts):
    mocker.patch.object(emailer, "_is_dartmouth", return_value=False)
    user_prompt, _system, ctx = emailer.prepare_email(_CONTACT, action, prompts)
    return user_prompt, ctx


@pytest.mark.parametrize("action", [
    "send_first_touch", "send_applied_intro", "send_networking_first_touch",
])
def test_voice_block_present_for_first_touch(mocker, action):
    user_prompt, ctx = _prep(mocker, action, _PROMPTS)
    assert "Short sentences." in user_prompt
    assert ctx["voice_block"] != ""


@pytest.mark.parametrize("action", [
    "send_followup1", "send_followup2", "send_breakup",
    "send_applied_followup", "send_networking_followup",
])
def test_voice_block_absent_for_followups(mocker, action):
    user_prompt, ctx = _prep(mocker, action, _PROMPTS)
    assert "Short sentences." not in user_prompt
    assert ctx["voice_block"] == ""


def test_no_voice_dna_row_leaves_prompt_unchanged(mocker):
    user_prompt, ctx = _prep(mocker, "send_first_touch", {})
    assert "Writing Style" not in user_prompt
    assert ctx["voice_block"] == ""


def test_blank_voice_dna_is_ignored(mocker):
    user_prompt, ctx = _prep(mocker, "send_first_touch", {"voice_dna": "   "})
    assert ctx["voice_block"] == ""


def test_finalize_email_accepts_voice_block(mocker):
    """Regression: ctx from prepare_email is splatted into finalize_email."""
    mocker.patch.object(emailer, "_is_dartmouth", return_value=False)
    # emailer imports these inside the function body, so patch at module level.
    mocker.patch("preflight.check", return_value=[])
    mocker.patch("db.log_agent_event")
    mocker.patch.object(emailer, "_generate_subject", return_value="Subject")
    _prompt, _system, ctx = emailer.prepare_email(_CONTACT, "send_first_touch", _PROMPTS)
    subject, body = emailer.finalize_email(
        _CONTACT, "send_first_touch", "A body.", None, _PROMPTS, **ctx)
    assert subject == "Subject"
    assert body == "A body."
