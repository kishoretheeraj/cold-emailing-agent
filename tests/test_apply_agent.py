"""Tests mock ats_platform.classify, the per-platform fillers, and db entirely -- no real
Playwright browser, no real browser-use call, no real network. Covers: platform routing,
Workday/aggregator exclusion, the eligibility-answer lookup, and per-row failure isolation."""

from unittest.mock import MagicMock

import apply_agent


def test_run_preview_routes_greenhouse_to_hand_mapped_filler(mocker):
    mocker.patch("apply_agent.db.get_job_applications", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/embed/job_app?token=1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"}
    ])
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    fill_mock = mocker.patch("apply_agent.ats_fillers.fill_greenhouse")
    mocker.patch("apply_agent._launch_page", return_value=MagicMock())
    mocker.patch("apply_agent._answer_screening_questions", return_value={})
    mocker.patch("apply_agent._attach_resume_and_cover_letter")
    mocker.patch("apply_agent.db.load_prompts", return_value={})
    set_preview_mock = mocker.patch("apply_agent.db.set_apply_preview")

    apply_agent.run_preview()

    fill_mock.assert_called_once()
    set_preview_mock.assert_called_once()


def test_run_preview_blocks_workday_without_attempting_a_fill(mocker):
    mocker.patch("apply_agent.db.get_job_applications", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://acme.wd1.myworkdayjobs.com/job/1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"}
    ])
    mocker.patch("apply_agent.ats_platform.classify", return_value="workday")
    launch_mock = mocker.patch("apply_agent._launch_page")
    set_blocked_mock = mocker.patch("apply_agent.db.set_apply_blocked")

    apply_agent.run_preview()

    launch_mock.assert_not_called()
    set_blocked_mock.assert_called_once()
    assert "workday" in set_blocked_mock.call_args[0][1].lower()


def test_run_preview_blocks_aggregator_links(mocker):
    mocker.patch("apply_agent.db.get_job_applications", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://www.indeed.com/viewjob?jk=1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"}
    ])
    mocker.patch("apply_agent.ats_platform.classify", return_value="aggregator")
    launch_mock = mocker.patch("apply_agent._launch_page")
    set_blocked_mock = mocker.patch("apply_agent.db.set_apply_blocked")

    apply_agent.run_preview()

    launch_mock.assert_not_called()
    set_blocked_mock.assert_called_once()


def test_run_preview_routes_generic_to_browser_use(mocker):
    mocker.patch("apply_agent.db.get_job_applications", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://careers.acme.com/apply/1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"}
    ])
    mocker.patch("apply_agent.ats_platform.classify", return_value="generic")
    mocker.patch("apply_agent._launch_page", return_value=MagicMock())
    browser_use_mock = mocker.patch("apply_agent._fill_generic_via_browser_use")
    mocker.patch("apply_agent._answer_screening_questions", return_value={})
    mocker.patch("apply_agent._attach_resume_and_cover_letter")
    mocker.patch("apply_agent.db.load_prompts", return_value={})
    mocker.patch("apply_agent.db.set_apply_preview")

    apply_agent.run_preview()

    browser_use_mock.assert_called_once()


def test_eligibility_answers_reads_from_prompts(mocker):
    mocker.patch("apply_agent.db.load_prompts", return_value={
        "applicant_eligibility": '{"work_authorized_us": "Yes", "requires_visa_sponsorship": "Yes"}'
    })
    result = apply_agent._eligibility_answers()
    assert result["work_authorized_us"] == "Yes"
    assert result["requires_visa_sponsorship"] == "Yes"


def test_eligibility_answers_never_raises_when_key_missing(mocker):
    mocker.patch("apply_agent.db.load_prompts", return_value={})
    result = apply_agent._eligibility_answers()
    assert result == {}


def test_run_preview_isolates_one_row_failure_from_the_rest(mocker):
    mocker.patch("apply_agent.db.get_job_applications", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/embed/job_app?token=1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"},
        {"id": 2, "company": "Beta", "role": "PM", "job_url": "https://boards.greenhouse.io/embed/job_app?token=2",
         "resume_file_ref": "resumes/2/r.pdf", "cover_letter_file_ref": "resumes/2/cl.pdf"},
    ])
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    mocker.patch("apply_agent._launch_page", side_effect=[RuntimeError("browser crashed"), MagicMock()])
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse")
    mocker.patch("apply_agent._answer_screening_questions", return_value={})
    mocker.patch("apply_agent._attach_resume_and_cover_letter")
    mocker.patch("apply_agent.db.load_prompts", return_value={})
    set_preview_mock = mocker.patch("apply_agent.db.set_apply_preview")

    apply_agent.run_preview()  # must not raise

    set_preview_mock.assert_called_once()
    assert set_preview_mock.call_args[0][0] == 2


def test_run_preview_counts_blocked_rows_separately_from_filled(mocker, caplog):
    mocker.patch("apply_agent.db.get_job_applications", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/embed/job_app?token=1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"},
        {"id": 2, "company": "Beta", "role": "PM", "job_url": "https://acme.wd1.myworkdayjobs.com/job/1",
         "resume_file_ref": "resumes/2/r.pdf", "cover_letter_file_ref": "resumes/2/cl.pdf"},
    ])
    mocker.patch("apply_agent.ats_platform.classify", side_effect=["greenhouse", "workday"])
    mocker.patch("apply_agent._launch_page", return_value=MagicMock())
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse")
    mocker.patch("apply_agent._answer_screening_questions", return_value={})
    mocker.patch("apply_agent._attach_resume_and_cover_letter")
    mocker.patch("apply_agent.db.load_prompts", return_value={})
    mocker.patch("apply_agent.db.set_apply_preview")
    mocker.patch("apply_agent.db.set_apply_blocked")

    with caplog.at_level("INFO"):
        apply_agent.run_preview()

    done_line = next(r.message for r in caplog.records if "DONE" in r.message)
    assert "filled=1" in done_line
    assert "blocked=1" in done_line
    assert "errors=0" in done_line
