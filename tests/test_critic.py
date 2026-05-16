"""Tests for emailer.py critic loop — _run_critic and critique_and_revise."""

import logging
import pytest

import emailer

# Simple critic prompt template used in _run_critic tests.
_CRITIC_TPL = (
    "Subject: {subject}\nBody: {body}\n"
    "Profile: {sender_profile}\nContext: {contact_context}"
)


def _contact(**overrides):
    base = {
        "name": "Jordan",
        "company": "Acme",
        "role": "VP Eng",
        "detail": "launched OSS infra tool",
        "tier": 1,
        "dartmouth": False,
    }
    base.update(overrides)
    return base


# ── _run_critic ───────────────────────────────────────────────────────────────


def test_run_critic_valid_json(mocker):
    mocker.patch.object(
        emailer,
        "_call_claude",
        return_value='{"score": 5, "failed_criteria": [1, 3], "feedback": "Add specifics."}',
    )
    result = emailer._run_critic(
        "Hey Jordan", "body text", _contact(), "Sender bio", _CRITIC_TPL
    )
    assert result["score"] == 5
    assert result["failed_criteria"] == [1, 3]
    assert result["feedback"] == "Add specifics."


def test_run_critic_markdown_fenced_json(mocker):
    mocker.patch.object(
        emailer,
        "_call_claude",
        return_value='```json\n{"score": 7, "failed_criteria": [], "feedback": ""}\n```',
    )
    result = emailer._run_critic(
        "Hey Jordan", "body text", _contact(), "Sender bio", _CRITIC_TPL
    )
    assert result["score"] == 7
    assert result["failed_criteria"] == []
    assert result["feedback"] == ""


def test_run_critic_malformed_json(mocker, caplog):
    mocker.patch.object(emailer, "_call_claude", return_value="not json at all")
    with caplog.at_level(logging.WARNING):
        result = emailer._run_critic(
            "Hey Jordan", "body text", _contact(), "Sender bio", _CRITIC_TPL
        )
    assert result == {"score": 7, "failed_criteria": [], "feedback": ""}
    assert any("JSON parse error" in r.message for r in caplog.records)


def test_run_critic_call_claude_raises(mocker, caplog):
    mocker.patch.object(
        emailer, "_call_claude", side_effect=Exception("API error")
    )
    with caplog.at_level(logging.WARNING):
        result = emailer._run_critic(
            "Hey Jordan", "body text", _contact(), "Sender bio", _CRITIC_TPL
        )
    assert result == {"score": 7, "failed_criteria": [], "feedback": ""}
    assert any("_call_claude error" in r.message for r in caplog.records)


def test_run_critic_format_error(mocker, caplog):
    # Template with an unknown placeholder causes KeyError during .format()
    bad_tpl = "Subject: {subject}\nBody: {body}\n{unknown_placeholder}"
    mocker.patch.object(emailer, "_call_claude", return_value='{"score": 7}')
    with caplog.at_level(logging.WARNING):
        result = emailer._run_critic(
            "Hey Jordan", "body text", _contact(), "Sender bio", bad_tpl
        )
    assert result == {"score": 7, "failed_criteria": [], "feedback": ""}
    assert any("prompt format error" in r.message for r in caplog.records)


def test_run_critic_contact_context_correct(mocker):
    captured = []

    def fake_claude(prompt):
        captured.append(prompt)
        return '{"score": 7, "failed_criteria": [], "feedback": ""}'

    mocker.patch.object(emailer, "_call_claude", side_effect=fake_claude)

    contact = _contact(dartmouth=True)
    emailer._run_critic("subj", "body", contact, "Sender bio", _CRITIC_TPL)

    assert captured, "expected _call_claude to be called"
    prompt = captured[0]
    assert "Name: Jordan" in prompt
    assert "Company: Acme" in prompt
    assert "Role: VP Eng" in prompt
    assert "Detail: launched OSS infra tool" in prompt
    assert "Tier: 1" in prompt
    assert "Dartmouth: yes" in prompt


def test_run_critic_contact_context_omits_dartmouth_when_false(mocker):
    captured = []

    def fake_claude(prompt):
        captured.append(prompt)
        return '{"score": 7, "failed_criteria": [], "feedback": ""}'

    mocker.patch.object(emailer, "_call_claude", side_effect=fake_claude)

    contact = _contact(dartmouth=False)
    emailer._run_critic("subj", "body", contact, "Sender bio", _CRITIC_TPL)

    assert captured
    assert "Dartmouth" not in captured[0]


def test_run_critic_contact_context_omits_none_fields(mocker):
    captured = []

    def fake_claude(prompt):
        captured.append(prompt)
        return '{"score": 7, "failed_criteria": [], "feedback": ""}'

    mocker.patch.object(emailer, "_call_claude", side_effect=fake_claude)

    contact = _contact(role=None, detail="")
    emailer._run_critic("subj", "body", contact, "Sender bio", _CRITIC_TPL)

    assert captured
    prompt = captured[0]
    assert "Role:" not in prompt
    assert "Detail:" not in prompt


# ── critique_and_revise ───────────────────────────────────────────────────────


def test_critique_and_revise_score_7_passes_unchanged(mocker):
    mocker.patch.object(
        emailer,
        "_run_critic",
        return_value={"score": 7, "failed_criteria": [], "feedback": ""},
    )
    regenerate_fn = mocker.MagicMock()

    subject, body = emailer.critique_and_revise(
        "Original Subject", "Original body.",
        _contact(), "Sender bio", _CRITIC_TPL, regenerate_fn,
    )

    assert subject == "Original Subject"
    assert body == "Original body."
    regenerate_fn.assert_not_called()


def test_critique_and_revise_score_6_passes_unchanged(mocker):
    mocker.patch.object(
        emailer,
        "_run_critic",
        return_value={"score": 6, "failed_criteria": [3], "feedback": "Minor tweak."},
    )
    regenerate_fn = mocker.MagicMock()

    subject, body = emailer.critique_and_revise(
        "Original Subject", "Original body.",
        _contact(), "Sender bio", _CRITIC_TPL, regenerate_fn,
    )

    assert subject == "Original Subject"
    assert body == "Original body."
    regenerate_fn.assert_not_called()


def test_critique_and_revise_score_5_triggers_retry(mocker):
    mocker.patch.object(
        emailer,
        "_run_critic",
        return_value={"score": 5, "failed_criteria": [1, 4], "feedback": "Fix the ask."},
    )
    regenerate_fn = mocker.MagicMock(return_value=("New Subject", "New body."))

    subject, body = emailer.critique_and_revise(
        "Original Subject", "Original body.",
        _contact(), "Sender bio", _CRITIC_TPL, regenerate_fn,
    )

    assert subject == "New Subject"
    assert body == "New body."
    regenerate_fn.assert_called_once_with("Fix the ask.")


def test_critique_and_revise_regenerate_raises_returns_original(mocker, caplog):
    mocker.patch.object(
        emailer,
        "_run_critic",
        return_value={"score": 3, "failed_criteria": [1, 2, 3, 4], "feedback": "Too generic."},
    )
    regenerate_fn = mocker.MagicMock(side_effect=Exception("boom"))

    with caplog.at_level(logging.WARNING):
        subject, body = emailer.critique_and_revise(
            "Original Subject", "Original body.",
            _contact(), "Sender bio", _CRITIC_TPL, regenerate_fn,
        )

    assert subject == "Original Subject"
    assert body == "Original body."
    assert any("regenerate error" in r.message for r in caplog.records)


def test_critique_and_revise_logs_critic_line_pass(mocker, caplog):
    mocker.patch.object(
        emailer,
        "_run_critic",
        return_value={"score": 7, "failed_criteria": [], "feedback": ""},
    )
    regenerate_fn = mocker.MagicMock()

    with caplog.at_level(logging.INFO):
        emailer.critique_and_revise(
            "Subject", "Body.",
            _contact(), "Sender bio", _CRITIC_TPL, regenerate_fn,
        )

    combined = " ".join(r.message for r in caplog.records)
    assert "[CRITIC]" in combined
    assert "retried=False" in combined


def test_critique_and_revise_logs_critic_line_retry(mocker, caplog):
    mocker.patch.object(
        emailer,
        "_run_critic",
        return_value={"score": 4, "failed_criteria": [2, 5], "feedback": "Needs work."},
    )
    regenerate_fn = mocker.MagicMock(return_value=("New Subject", "New body."))

    with caplog.at_level(logging.INFO):
        emailer.critique_and_revise(
            "Subject", "Body.",
            _contact(), "Sender bio", _CRITIC_TPL, regenerate_fn,
        )

    combined = " ".join(r.message for r in caplog.records)
    assert "[CRITIC]" in combined
    assert "retried=True" in combined
