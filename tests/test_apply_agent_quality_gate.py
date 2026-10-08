"""The quality gate in apply_agent's preview pass: a failing document stops the row in needs_input
before any browser opens; a passing one records keyword coverage on the preview."""

from unittest.mock import MagicMock

import pytest

import apply_agent
import config


@pytest.fixture(autouse=True)
def _defaults(mocker):
    for name, value in (("claim_application", "lease-1"), ("heartbeat_application", None),
                        ("release_application", True), ("complete_preview", True),
                        ("mark_unsupported", True), ("load_prompts", {})):
        mocker.patch.object(apply_agent.db, name, return_value=value)
    mocker.patch.object(apply_agent, "_form_inventory", return_value=[])
    mocker.patch.object(apply_agent, "_form_signature", return_value="sig")
    mocker.patch.object(apply_agent, "_close_page")
    mocker.patch.object(apply_agent, "_generate_screening_answers", return_value={})
    mocker.patch.object(apply_agent, "_fill_screening_questions", return_value={})
    mocker.patch.object(apply_agent, "_fill_eligibility_answers")
    mocker.patch.object(apply_agent, "_attach_resume_and_cover_letter", return_value={"resume": True, "cover_letter": True})
    mocker.patch.object(apply_agent.ats_fillers, "fill_greenhouse",
                        return_value={"first_name": True, "last_name": True, "email": True})


def _job():
    return {"id": 5, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/acme/jobs/1",
            "stage": "saved", "automation_status": "idle", "resume_file_ref": "r.pdf", "cover_letter_file_ref": "c.pdf"}


def test_failing_documents_stop_the_row_before_a_browser_opens(mocker):
    mocker.patch.object(apply_agent.application_quality, "evaluate", return_value={
        "problems": ["Resume: 2 pages; the resume must fit on one page", "Cover letter: does not name Acme"],
        "coverage": {"covered": [], "missing": []}})
    launch = mocker.patch.object(apply_agent, "_launch_page")
    assert apply_agent._process_one_preview(_job()) == "blocked"
    launch.assert_not_called()
    args = apply_agent.db.release_application.call_args[0]
    assert args[:3] == (5, "lease-1", "needs_input")
    assert args[3].startswith("Quality check: Resume: 2 pages")
    assert "does not name Acme" in args[3]
    apply_agent.db.complete_preview.assert_not_called()


def test_passing_documents_record_keyword_coverage_on_the_preview(mocker):
    coverage = {"covered": ["SQL"], "missing": ["Python"]}
    mocker.patch.object(apply_agent.application_quality, "evaluate", return_value={"problems": [], "coverage": coverage})
    mocker.patch.object(apply_agent, "_launch_page", return_value=MagicMock())
    assert apply_agent._process_one_preview(_job()) == "filled"
    preview = apply_agent.db.complete_preview.call_args[0][2]
    assert preview["keyword_coverage"] == coverage


def test_a_storage_failure_is_retryable_not_a_quality_verdict(mocker):
    mocker.patch.object(apply_agent.application_quality, "evaluate", side_effect=RuntimeError("storage down"))
    with pytest.raises(RuntimeError):
        apply_agent._process_one_preview(_job())
    assert apply_agent.db.release_application.call_args[0][2] == "failed_retryable"


def test_the_gate_can_be_switched_off(mocker):
    mocker.patch.object(config, "APPLY_QUALITY_GATE", False)
    evaluate = mocker.patch.object(apply_agent.application_quality, "evaluate")
    mocker.patch.object(apply_agent, "_launch_page", return_value=MagicMock())
    assert apply_agent._process_one_preview(_job()) == "filled"
    evaluate.assert_not_called()
