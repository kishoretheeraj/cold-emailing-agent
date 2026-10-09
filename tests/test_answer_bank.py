"""The answer bank: short factual answers the operator already approved are reused for the same
question before a model is asked, so the same question gets the same answer everywhere.
Company-specific questions and long answers are never reused."""

from unittest.mock import MagicMock

import pytest

import apply_agent
import db


def _row(company, answers, status="submitted"):
    return {"company": company, "automation_status": status, "approved_at": "2026-10-01T00:00:00Z",
            "apply_preview": {"screening_answers": answers}}


# ── db.get_answer_bank ─────────────────────────────────────────────────────────

def test_answer_bank_reads_only_approved_previews_newest_first(mocker):
    client = MagicMock()
    chain = client.table.return_value.select.return_value.not_.is_.return_value
    chain.order.return_value.limit.return_value.execute.return_value.data = [
        _row("Acme", {"How did you hear about us?": "LinkedIn"}),
        _row("Beta", {"How did you hear about us?": "Company website"}),
    ]
    mocker.patch.object(db, "get_client", return_value=client)
    rows = db.get_answer_bank_rows()
    assert [r["company"] for r in rows] == ["Acme", "Beta"]
    client.table.return_value.select.return_value.not_.is_.assert_called_once_with("approved_at", "null")
    chain.order.assert_called_once_with("approved_at", desc=True)


# ── apply_agent._answer_bank ───────────────────────────────────────────────────

def test_bank_keeps_the_newest_short_factual_answer(mocker):
    mocker.patch.object(apply_agent.db, "get_answer_bank_rows", return_value=[
        _row("Acme", {"How did you hear about us? *": "LinkedIn", "Earliest start date": "Two weeks after an offer"}),
        _row("Beta", {"How did you hear about us?": "Company website"}),
    ])
    bank = apply_agent._answer_bank()
    assert bank["how did you hear about us?"] == "LinkedIn"
    assert bank["earliest start date"] == "Two weeks after an offer"


@pytest.mark.parametrize("question,answer", [
    ("Why do you want to work at Acme?", "Because Acme builds lending tools."),       # names its company
    ("What interests you about this role?", "x" * 200),                              # long, written for one job
    ("Describe a product you launched", "NEEDS HUMAN REVIEW: not in the facts"),     # never approved as an answer
])
def test_bank_skips_company_specific_long_or_unanswered(mocker, question, answer):
    mocker.patch.object(apply_agent.db, "get_answer_bank_rows", return_value=[_row("Acme", {question: answer})])
    assert apply_agent._norm_label(question) not in apply_agent._answer_bank()


def test_bank_never_reuses_an_answer_that_names_its_company(mocker):
    mocker.patch.object(apply_agent.db, "get_answer_bank_rows", return_value=[
        _row("Acme", {"Anything else we should know?": "I love Acme's product."})])
    assert apply_agent._answer_bank() == {}


def test_bank_failure_is_an_empty_bank(mocker):
    mocker.patch.object(apply_agent.db, "get_answer_bank_rows", side_effect=RuntimeError("db down"))
    assert apply_agent._answer_bank() == {}


# ── use in screening generation ────────────────────────────────────────────────

def _field(label, options=None, kind="input"):
    return {"key": "#f", "selector": "#f", "label": label, "kind": kind, "required": True,
            "filled": False, "options": options or [], "option_selectors": []}


def test_a_banked_answer_is_used_instead_of_a_model_call(mocker):
    mocker.patch.object(apply_agent, "_form_inventory", return_value=[
        _field("How did you hear about us?", options=["LinkedIn", "Referral", "Other"], kind="select"),
        _field("Why us?", kind="textarea")])
    mocker.patch.object(apply_agent, "_answer_bank", return_value={"how did you hear about us?": "LinkedIn"})
    mocker.patch.object(apply_agent.candidate_profile, "profile_text", return_value="Associate PM.")
    mocker.patch.object(apply_agent.db, "load_prompts", return_value={})
    model = mocker.patch.object(apply_agent, "_screening_completion", return_value="Because.")
    answers = apply_agent._generate_screening_answers(MagicMock(), {"id": 1, "company": "Gamma"})
    assert answers == {"How did you hear about us?": "LinkedIn", "Why us?": "Because."}
    assert model.call_count == 1  # only the question the bank could not answer


def test_a_banked_answer_that_is_not_an_option_here_falls_back_to_the_model(mocker):
    mocker.patch.object(apply_agent, "_form_inventory", return_value=[
        _field("How did you hear about us?", options=["Job board", "Referral"], kind="select")])
    mocker.patch.object(apply_agent, "_answer_bank", return_value={"how did you hear about us?": "LinkedIn"})
    mocker.patch.object(apply_agent.candidate_profile, "profile_text", return_value="Associate PM.")
    mocker.patch.object(apply_agent.db, "load_prompts", return_value={})
    model = mocker.patch.object(apply_agent, "_screening_completion", return_value="Job board")
    assert apply_agent._generate_screening_answers(MagicMock(), {"id": 1}) == {"How did you hear about us?": "Job board"}
    model.assert_called_once()
