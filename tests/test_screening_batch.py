"""Screening answers for a whole form in one model call (spec 2026-10-08 fifty-a-day §3.7): an
eight-question form used to cost eight subscription calls. Anything the batch leaves unanswered
falls back to one call per question, so a bad batch never costs an answer."""

import json

import pytest

import apply_agent
import claude_subscription


def _field(label, options=None, kind="input"):
    return {"label": label, "kind": kind, "required": True, "filled": False, "options": options or [],
            "selector": f"[name='{label}']"}


@pytest.fixture
def form(mocker):
    mocker.patch.object(apply_agent, "_form_inventory", return_value=[
        _field("Why this company?"), _field("Years of SQL experience?"),
        _field("Are you open to relocation?", options=["Yes", "No"], kind="select")])
    mocker.patch.object(apply_agent, "_eligibility_answers", return_value={})
    mocker.patch.object(apply_agent, "_answer_bank", return_value={})
    mocker.patch.object(apply_agent.candidate_profile, "profile_text", return_value="APM at Protium Finance.")


def test_one_call_answers_every_question(mocker, form):
    model = mocker.patch.object(apply_agent, "_screening_completion", return_value=json.dumps([
        {"id": 0, "answer": "Because of its lending products."}, {"id": 1, "answer": "3 years"},
        {"id": 2, "answer": "yes"}]))
    answers = apply_agent._generate_screening_answers(None, {"id": 9})
    assert model.call_count == 1
    assert answers == {"Why this company?": "Because of its lending products.",
                       "Years of SQL experience?": "3 years", "Are you open to relocation?": "Yes"}
    prompt = model.call_args.args[0]
    assert "[2] Are you open to relocation? (choices: Yes | No)" in prompt and "APM at Protium Finance." in prompt


def test_questions_the_batch_skipped_are_asked_one_by_one(mocker, form):
    model = mocker.patch.object(apply_agent, "_screening_completion", side_effect=[
        "```json\n" + json.dumps([{"id": 0, "answer": "Lending."}, {"id": 1, "answer": ""}]) + "\n```",
        "2 years", "No"])
    answers = apply_agent._generate_screening_answers(None, {"id": 9})
    assert model.call_count == 3
    assert answers["Why this company?"] == "Lending."
    assert answers["Years of SQL experience?"] == "2 years"
    assert answers["Are you open to relocation?"] == "No"


@pytest.mark.parametrize("raw", ["Sure! Here are the answers.", "{}", "[{\"id\": \"x\"}]"])
def test_an_unreadable_batch_falls_back_to_single_calls(mocker, form, raw):
    model = mocker.patch.object(apply_agent, "_screening_completion", side_effect=[raw, "A", "B", "Yes"])
    answers = apply_agent._generate_screening_answers(None, {"id": 9})
    assert model.call_count == 4
    assert list(answers.values()) == ["A", "B", "Yes"]


def test_a_subscription_failure_stops_the_form(mocker, form):
    mocker.patch.object(apply_agent, "_screening_completion",
                        side_effect=claude_subscription.ClaudeUsageLimitError("limit"))
    with pytest.raises(claude_subscription.ClaudeUsageLimitError):
        apply_agent._generate_screening_answers(None, {"id": 9})


def test_a_single_question_is_asked_directly(mocker):
    mocker.patch.object(apply_agent, "_form_inventory", return_value=[_field("Why us?")])
    mocker.patch.object(apply_agent, "_eligibility_answers", return_value={})
    mocker.patch.object(apply_agent, "_answer_bank", return_value={})
    mocker.patch.object(apply_agent.candidate_profile, "profile_text", return_value="facts")
    model = mocker.patch.object(apply_agent, "_screening_completion", return_value="Because.")
    assert apply_agent._generate_screening_answers(None, {"id": 9}) == {"Why us?": "Because."}
    assert "Question: Why us?" in model.call_args.args[0]
