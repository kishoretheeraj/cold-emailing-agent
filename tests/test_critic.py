"""Tests for emailer.py critic loop — _run_critic and critique_and_revise."""

import logging
import pytest

import emailer

# Simple critic prompt template used in _run_critic tests.
_CRITIC_TPL = (
    "Subject: {subject}\nBody: {body}\n"
    "Profile: {sender_profile}\nContext: {contact_context}"
)

_PASS_RESULT = {
    "verdict": "PASS", "score": 16, "rewrite_required": False,
    "killed_by": [], "failed_soft_criteria": [],
    "banned_phrases_found": [], "ai_tells_found": [], "feedback": "",
}
_FAIL_RESULT = {
    "verdict": "FAIL", "score": 12, "rewrite_required": True,
    "killed_by": ["K3"], "failed_soft_criteria": ["S2"],
    "banned_phrases_found": [], "ai_tells_found": [], "feedback": "Hook is generic.",
}
_FALLBACK = {
    "verdict": "PASS", "score": 16, "rewrite_required": False,
    "killed_by": [], "failed_soft_criteria": [],
    "banned_phrases_found": [], "ai_tells_found": [], "feedback": "",
}


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
        return_value=(
            '{"verdict":"FAIL","score":10,"rewrite_required":true,'
            '"killed_by":["K1"],"failed_soft_criteria":["S3"],'
            '"banned_phrases_found":[],"ai_tells_found":[],"feedback":"Too generic."}'
        ),
    )
    result = emailer._run_critic(
        "Hey Jordan", "body text", _contact(), "Sender bio", _CRITIC_TPL
    )
    assert result["verdict"] == "FAIL"
    assert result["score"] == 10
    assert result["rewrite_required"] is True
    assert result["killed_by"] == ["K1"]
    assert result["feedback"] == "Too generic."


def test_run_critic_markdown_fenced_json(mocker):
    json_str = (
        '{"verdict":"PASS","score":16,"rewrite_required":false,'
        '"killed_by":[],"failed_soft_criteria":[],'
        '"banned_phrases_found":[],"ai_tells_found":[],"feedback":""}'
    )
    mocker.patch.object(
        emailer, "_call_claude", return_value=f"```json\n{json_str}\n```"
    )
    result = emailer._run_critic(
        "Hey Jordan", "body text", _contact(), "Sender bio", _CRITIC_TPL
    )
    assert result["verdict"] == "PASS"
    assert result["rewrite_required"] is False
    assert result["score"] == 16


def test_run_critic_malformed_json(mocker, caplog):
    mocker.patch.object(emailer, "_call_claude", return_value="not json at all")
    with caplog.at_level(logging.WARNING):
        result = emailer._run_critic(
            "Hey Jordan", "body text", _contact(), "Sender bio", _CRITIC_TPL
        )
    assert result == _FALLBACK
    assert any("JSON parse error" in r.message for r in caplog.records)


def test_run_critic_call_claude_raises(mocker, caplog):
    mocker.patch.object(
        emailer, "_call_claude", side_effect=Exception("API error")
    )
    with caplog.at_level(logging.WARNING):
        result = emailer._run_critic(
            "Hey Jordan", "body text", _contact(), "Sender bio", _CRITIC_TPL
        )
    assert result == _FALLBACK
    assert any("_call_claude error" in r.message for r in caplog.records)


def test_run_critic_format_error(mocker, caplog):
    # Template with an unknown placeholder causes KeyError during .format()
    bad_tpl = "Subject: {subject}\nBody: {body}\n{unknown_placeholder}"
    mocker.patch.object(emailer, "_call_claude", return_value='{"verdict":"PASS"}')
    with caplog.at_level(logging.WARNING):
        result = emailer._run_critic(
            "Hey Jordan", "body text", _contact(), "Sender bio", bad_tpl
        )
    assert result == _FALLBACK
    assert any("prompt format error" in r.message for r in caplog.records)


def test_run_critic_contact_context_correct(mocker):
    captured = []

    def fake_claude(prompt, **kwargs):
        captured.append(prompt)
        import json
        return json.dumps(_PASS_RESULT)

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

    def fake_claude(prompt, **kwargs):
        captured.append(prompt)
        import json
        return json.dumps(_PASS_RESULT)

    mocker.patch.object(emailer, "_call_claude", side_effect=fake_claude)

    contact = _contact(dartmouth=False)
    emailer._run_critic("subj", "body", contact, "Sender bio", _CRITIC_TPL)

    assert captured
    assert "Dartmouth" not in captured[0]


def test_run_critic_contact_context_omits_none_fields(mocker):
    captured = []

    def fake_claude(prompt, **kwargs):
        captured.append(prompt)
        import json
        return json.dumps(_PASS_RESULT)

    mocker.patch.object(emailer, "_call_claude", side_effect=fake_claude)

    contact = _contact(role=None, detail="")
    emailer._run_critic("subj", "body", contact, "Sender bio", _CRITIC_TPL)

    assert captured
    prompt = captured[0]
    assert "Role:" not in prompt
    assert "Detail:" not in prompt


# ── critique_and_revise ───────────────────────────────────────────────────────


def test_critique_and_revise_pass_returns_unchanged(mocker):
    mocker.patch.object(emailer, "_run_critic", return_value=_PASS_RESULT)
    regenerate_fn = mocker.MagicMock()

    subject, body = emailer.critique_and_revise(
        "Original Subject", "Original body.",
        _contact(), "Sender bio", _CRITIC_TPL, regenerate_fn,
    )

    assert subject == "Original Subject"
    assert body == "Original body."
    regenerate_fn.assert_not_called()


def test_critique_and_revise_rewrite_required_triggers_retry(mocker):
    mocker.patch.object(emailer, "_run_critic", return_value=_FAIL_RESULT)
    regenerate_fn = mocker.MagicMock(return_value=("New Subject", "New body."))

    subject, body = emailer.critique_and_revise(
        "Original Subject", "Original body.",
        _contact(), "Sender bio", _CRITIC_TPL, regenerate_fn,
    )

    assert subject == "New Subject"
    assert body == "New body."
    regenerate_fn.assert_called_once_with("Hook is generic.")


def test_critique_and_revise_regenerate_raises_returns_original(mocker, caplog):
    mocker.patch.object(emailer, "_run_critic", return_value=_FAIL_RESULT)
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
    mocker.patch.object(emailer, "_run_critic", return_value=_PASS_RESULT)
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
    mocker.patch.object(emailer, "_run_critic", return_value=_FAIL_RESULT)
    regenerate_fn = mocker.MagicMock(return_value=("New Subject", "New body."))

    with caplog.at_level(logging.INFO):
        emailer.critique_and_revise(
            "Subject", "Body.",
            _contact(), "Sender bio", _CRITIC_TPL, regenerate_fn,
        )

    combined = " ".join(r.message for r in caplog.records)
    assert "[CRITIC]" in combined
    assert "retried=True" in combined
