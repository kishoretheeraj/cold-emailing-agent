"""Tests mock ats_platform.classify, the per-platform fillers, and db entirely -- no real
Playwright browser, no real browser-use call, no real network. Covers: platform routing,
Workday/aggregator exclusion, the eligibility-answer lookup, and per-row failure isolation."""

from unittest.mock import MagicMock

import pytest

import apply_agent


def test_run_preview_routes_greenhouse_to_hand_mapped_filler(mocker):
    mocker.patch("apply_agent.db.get_job_applications", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/embed/job_app?token=1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"}
    ])
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    fill_mock = mocker.patch("apply_agent.ats_fillers.fill_greenhouse")
    mocker.patch("apply_agent._launch_page", return_value=MagicMock())
    mocker.patch("apply_agent._generate_screening_answers", return_value={})
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
    mocker.patch("apply_agent._generate_screening_answers", return_value={})
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
    mocker.patch("apply_agent._generate_screening_answers", return_value={})
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
    mocker.patch("apply_agent._generate_screening_answers", return_value={})
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


def test_attach_resume_and_cover_letter_downloads_and_sets_input_files(mocker):
    page = MagicMock()
    mocker.patch("apply_agent.db.get_client", return_value=MagicMock(
        storage=MagicMock(from_=MagicMock(return_value=MagicMock(
            download=MagicMock(side_effect=[b"resume-bytes", b"cover-letter-bytes"]))))
    ))
    job = {"resume_file_ref": "resumes/1/resume.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"}

    apply_agent._attach_resume_and_cover_letter(page, job)

    assert page.get_by_label.return_value.set_input_files.call_count >= 1


def test_generate_screening_answers_grounds_answer_in_call_claude(mocker):
    page = MagicMock()
    page.get_by_text.return_value.all.return_value = [MagicMock(inner_text=lambda: "Why do you want this role?")]
    mocker.patch("apply_agent._call_claude", return_value="Grounded answer text.")
    job = {"company": "Acme", "role": "PM"}

    result = apply_agent._generate_screening_answers(page, job)

    assert result == {"Why do you want this role?": "Grounded answer text."}


# ── _set_field_by_label: the shared dropdown/radio/text fallback chain ──────────

def test_set_field_by_label_selects_a_dropdown_option(mocker):
    page = MagicMock()  # select_option succeeds on a fresh mock -- must stop there

    result = apply_agent._set_field_by_label(page, "Work authorized?", "Yes")

    assert result is True
    page.get_by_label.return_value.select_option.assert_called_with(label="Yes")
    page.get_by_label.return_value.fill.assert_not_called()


def test_set_field_by_label_clicks_a_radio_option_scoped_to_the_questions_group(mocker):
    page = MagicMock()
    page.get_by_label.return_value.select_option.side_effect = Exception("not a select")

    result = apply_agent._set_field_by_label(page, "Work authorized?", "Yes")

    assert result is True
    page.get_by_role.assert_any_call("group", name="Work authorized?")
    page.get_by_role.return_value.get_by_role.assert_called_with("radio", name="Yes")
    page.get_by_role.return_value.get_by_role.return_value.click.assert_called_once()
    page.get_by_label.return_value.fill.assert_not_called()


def test_set_field_by_label_falls_back_to_a_plain_text_fill(mocker):
    page = MagicMock()
    page.get_by_label.return_value.select_option.side_effect = Exception("not a select")
    page.get_by_role.return_value.get_by_role.return_value.click.side_effect = Exception("no group")

    result = apply_agent._set_field_by_label(page, "Phone", "555-1234")

    assert result is True
    page.get_by_label.return_value.fill.assert_called_with("555-1234")


def test_set_field_by_label_returns_false_when_no_strategy_works(mocker):
    page = MagicMock()
    page.get_by_label.return_value.select_option.side_effect = Exception("not a select")
    page.get_by_role.return_value.get_by_role.return_value.click.side_effect = Exception("no group")
    page.get_by_label.return_value.fill.side_effect = Exception("not fillable either")

    assert apply_agent._set_field_by_label(page, "Q", "A") is False


def test_set_field_by_label_never_raises_when_get_by_label_itself_raises(mocker):
    page = MagicMock()
    page.get_by_label.side_effect = RuntimeError("strict mode violation: 2 elements match")
    page.get_by_role.return_value.get_by_role.return_value.click.side_effect = Exception("no group")

    assert apply_agent._set_field_by_label(page, "Q", "A") is False  # must not raise


# ── _fill_screening_questions / _fill_eligibility_answers: orchestration only ───

def test_fill_screening_questions_calls_set_field_by_label_for_each_answer(mocker):
    page = MagicMock()
    set_field_mock = mocker.patch("apply_agent._set_field_by_label", return_value=True)

    apply_agent._fill_screening_questions(page, {"Why this role?": "Because reasons."})

    set_field_mock.assert_called_once_with(page, "Why this role?", "Because reasons.")


def test_fill_screening_questions_never_raises_when_field_not_fillable(mocker):
    page = MagicMock()
    mocker.patch("apply_agent._set_field_by_label", return_value=False)

    apply_agent._fill_screening_questions(page, {"Q": "A"})  # must not raise


def test_fill_screening_questions_handles_none_and_empty(mocker):
    page = MagicMock()
    set_field_mock = mocker.patch("apply_agent._set_field_by_label")

    apply_agent._fill_screening_questions(page, None)  # must not raise
    apply_agent._fill_screening_questions(page, {})

    set_field_mock.assert_not_called()


def test_fill_eligibility_answers_translates_known_keys_to_real_question_patterns(mocker):
    """Regression test for the reported bug: the filler used to search for labels like
    "work_authorized_us" verbatim -- an internal seed-data key, not real form text. Every
    known applicant_eligibility key must resolve through _ELIGIBILITY_QUESTION_PATTERNS
    before reaching the page, never the raw key."""
    page = MagicMock()
    set_field_mock = mocker.patch("apply_agent._set_field_by_label", return_value=True)

    apply_agent._fill_eligibility_answers(page, {"work_authorized_us": "Yes"})

    set_field_mock.assert_called_once_with(
        page, apply_agent._ELIGIBILITY_QUESTION_PATTERNS["work_authorized_us"], "Yes"
    )
    assert "work_authorized_us" not in [c.args[1] for c in set_field_mock.call_args_list]


@pytest.mark.parametrize("key", list(apply_agent._ELIGIBILITY_QUESTION_PATTERNS))
def test_every_known_eligibility_key_has_a_question_pattern_that_compiles(key):
    pattern = apply_agent._ELIGIBILITY_QUESTION_PATTERNS[key]
    assert hasattr(pattern, "search")  # a compiled re.Pattern, not a bare string


def test_fill_eligibility_answers_falls_back_to_the_raw_key_for_unrecognized_keys(mocker):
    page = MagicMock()
    set_field_mock = mocker.patch("apply_agent._set_field_by_label", return_value=True)

    apply_agent._fill_eligibility_answers(page, {"some_future_custom_key": "Yes"})

    set_field_mock.assert_called_once_with(page, "some_future_custom_key", "Yes")


def test_fill_eligibility_answers_never_raises_when_field_not_fillable(mocker):
    page = MagicMock()
    mocker.patch("apply_agent._set_field_by_label", return_value=False)

    apply_agent._fill_eligibility_answers(page, {"work_authorized_us": "Yes"})  # must not raise


# ── _submission_confirmed: rejection copy must never read as confirmed ──────────

@pytest.mark.parametrize("rejection_text", [
    "Your application is incomplete",
    "Your application is invalid",
    "Please correct the highlighted fields",
    "This field is required",
    "Something went wrong while submitting your application",
])
def test_rejection_pattern_matches_real_validation_copy(rejection_text):
    assert apply_agent._REJECTION_TEXT_PATTERN.search(rejection_text)


@pytest.mark.parametrize("rejection_text", [
    "Your application is incomplete",
    "Your application is invalid",
])
def test_confirmation_pattern_does_not_match_rejection_copy(rejection_text):
    """Regression test for the exact bug PR review found: the old
    `your application (is|has been) (complete|in)` clause let bare "in" match as an
    unanchored prefix of "incomplete"/"invalid", so both of these read as confirmed."""
    assert not apply_agent._CONFIRMATION_TEXT_PATTERN.search(rejection_text)


@pytest.mark.parametrize("confirmation_text", [
    "Your application has been submitted",
    "Application received",
    "Thank you for applying",
    "Thank you for your application",
    "Your application was successfully submitted",
])
def test_confirmation_pattern_matches_real_confirmation_copy(confirmation_text):
    assert apply_agent._CONFIRMATION_TEXT_PATTERN.search(confirmation_text)


def _get_by_text_matching(matching_pattern):
    def get_by_text(pattern):
        result = MagicMock()
        result.count.return_value = 1 if pattern is matching_pattern else 0
        return result
    return get_by_text


def test_submission_confirmed_true_when_confirmation_text_present(mocker):
    page = MagicMock()
    page.get_by_text.side_effect = _get_by_text_matching(apply_agent._CONFIRMATION_TEXT_PATTERN)

    assert apply_agent._submission_confirmed(page) is True


def test_submission_confirmed_false_when_rejection_text_present_even_if_it_also_looks_confirmed(mocker):
    """The rejection check runs first and short-circuits -- get_by_text must be called
    exactly once (for the rejection pattern), never reaching the confirmation check at all."""
    page = MagicMock()
    page.get_by_text.return_value.count.return_value = 1

    assert apply_agent._submission_confirmed(page) is False
    assert page.get_by_text.call_count == 1


def test_submission_confirmed_false_when_no_confirmation_text(mocker):
    page = MagicMock()
    page.get_by_text.return_value.count.return_value = 0

    assert apply_agent._submission_confirmed(page) is False


def test_submission_confirmed_false_when_page_check_raises(mocker):
    page = MagicMock()
    page.get_by_text.side_effect = RuntimeError("page closed")

    assert apply_agent._submission_confirmed(page) is False  # must not raise


def test_process_one_preview_fills_and_stores_screening_and_eligibility_answers(mocker):
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    page = MagicMock()
    mocker.patch("apply_agent._launch_page", return_value=page)
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse")
    mocker.patch("apply_agent._attach_resume_and_cover_letter")
    generated = {"Why this role?": "Because reasons."}
    mocker.patch("apply_agent._generate_screening_answers", return_value=generated)
    fill_screening_mock = mocker.patch("apply_agent._fill_screening_questions")
    mocker.patch("apply_agent.db.load_prompts", return_value={
        "applicant_eligibility": '{"work_authorized_us": "Yes"}'
    })
    fill_eligibility_mock = mocker.patch("apply_agent._fill_eligibility_answers")
    set_preview_mock = mocker.patch("apply_agent.db.set_apply_preview")

    apply_agent._process_one_preview(
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/x"}
    )

    fill_screening_mock.assert_called_once_with(page, generated)
    fill_eligibility_mock.assert_called_once_with(page, {"work_authorized_us": "Yes"})
    preview_arg = set_preview_mock.call_args[0][1]
    assert preview_arg["screening_answers"] == generated
    assert preview_arg["eligibility_answers"] == {"work_authorized_us": "Yes"}


def test_fill_generic_via_browser_use_never_raises_on_library_failure(mocker):
    page = MagicMock()
    mocker.patch("apply_agent._browser_use_agent_run", side_effect=RuntimeError("browser-use crashed"))
    apply_agent._fill_generic_via_browser_use(page, {"company": "Acme"}, {"name": "Kishore"})  # must not raise


def test_fill_generic_via_browser_use_never_raises_when_library_call_itself_fails(mocker):
    """Additive test (not from the brief's verbatim three) -- exercises the _browser_use_agent_run
    failure path with a complete field_values dict, since a missing key now raises before that
    call is ever reached (see test above)."""
    page = MagicMock()
    run_mock = mocker.patch("apply_agent._browser_use_agent_run", side_effect=RuntimeError("browser-use crashed"))
    field_values = {"name": "Kishore", "email": "k@example.com", "phone": "555", "location": "NH", "linkedin": "li"}

    apply_agent._fill_generic_via_browser_use(page, {"company": "Acme"}, field_values)  # must not raise

    run_mock.assert_called_once()


import os


def test_submit_does_not_click_submit_when_not_armed(mocker):
    mocker.patch.dict(os.environ, {}, clear=False)
    if "APPLY_AGENT_ARMED" in os.environ:
        del os.environ["APPLY_AGENT_ARMED"]
    mocker.patch("apply_agent.db.get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/embed/job_app?token=1",
        "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf",
        "stage": "ready_to_submit", "apply_preview": {"platform": "greenhouse"},
        "approved_at": "2026-09-20T00:00:00Z",
    })
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    page = MagicMock()
    mocker.patch("apply_agent._launch_page", return_value=page)
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse")
    mocker.patch("apply_agent._attach_resume_and_cover_letter")
    record_submission_mock = mocker.patch("apply_agent.db.record_submission")

    apply_agent.submit(1)

    page.get_by_role.return_value.click.assert_not_called()
    record_submission_mock.assert_not_called()


def test_submit_clicks_submit_and_flips_stage_when_armed(mocker):
    mocker.patch.dict(os.environ, {"APPLY_AGENT_ARMED": "1"})
    mocker.patch("apply_agent.db.get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/embed/job_app?token=1",
        "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf",
        "stage": "ready_to_submit", "apply_preview": {"platform": "greenhouse"},
        "approved_at": "2026-09-20T00:00:00Z",
    })
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    page = MagicMock()
    mocker.patch("apply_agent._launch_page", return_value=page)
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse")
    mocker.patch("apply_agent._attach_resume_and_cover_letter")
    mocker.patch("apply_agent._submission_confirmed", return_value=True)
    fake_date = mocker.patch("apply_agent.date")
    fake_date.today.return_value.isoformat.return_value = "2026-09-24"
    record_submission_mock = mocker.patch("apply_agent.db.record_submission")
    mocker.patch("apply_agent.db.set_apply_preview")

    apply_agent.submit(1)

    page.get_by_role.assert_called_with("button", name=apply_agent._SUBMIT_BUTTON_NAME)
    page.get_by_role.return_value.click.assert_called_once()
    record_submission_mock.assert_called_once_with(1, "greenhouse", "2026-09-24")


def test_submit_raises_and_leaves_stage_unchanged_when_confirmation_is_missing(mocker):
    """The click succeeding is not proof the application landed -- a client-side validation
    error can leave the Submit button's click handler a no-op. Regression test: without the
    confirmation check, this row would be silently marked "applied" even though nothing was
    actually submitted. Reported by external PR review of PR #8."""
    mocker.patch.dict(os.environ, {"APPLY_AGENT_ARMED": "1"})
    mocker.patch("apply_agent.db.get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/embed/job_app?token=1",
        "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf",
        "stage": "ready_to_submit", "apply_preview": {"platform": "greenhouse"},
        "approved_at": "2026-09-20T00:00:00Z",
    })
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    page = MagicMock()
    mocker.patch("apply_agent._launch_page", return_value=page)
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse")
    mocker.patch("apply_agent._attach_resume_and_cover_letter")
    mocker.patch("apply_agent._submission_confirmed", return_value=False)
    record_submission_mock = mocker.patch("apply_agent.db.record_submission")

    with pytest.raises(RuntimeError, match="no confirmation"):
        apply_agent.submit(1)

    page.get_by_role.return_value.click.assert_called_once()
    record_submission_mock.assert_not_called()


def test_submit_fills_screening_and_eligibility_from_the_stored_preview_not_regenerated(mocker):
    """Regression test: submit() used to call the LLM-generation function a second time and
    discard the result, so the human-reviewed preview answers were never actually used.
    Reported by external PR review of PR #8."""
    generate_mock = mocker.patch("apply_agent._generate_screening_answers")
    fill_screening_mock = mocker.patch("apply_agent._fill_screening_questions")
    fill_eligibility_mock = mocker.patch("apply_agent._fill_eligibility_answers")
    mocker.patch.dict(os.environ, {"APPLY_AGENT_ARMED": "1"})
    stored_preview = {
        "platform": "greenhouse",
        "screening_answers": {"Why this role?": "Because reasons."},
        "eligibility_answers": {"work_authorized_us": "Yes"},
    }
    mocker.patch("apply_agent.db.get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/embed/job_app?token=1",
        "stage": "ready_to_submit", "apply_preview": stored_preview,
        "approved_at": "2026-09-20T00:00:00Z",
    })
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    page = MagicMock()
    mocker.patch("apply_agent._launch_page", return_value=page)
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse")
    mocker.patch("apply_agent._attach_resume_and_cover_letter")
    mocker.patch("apply_agent._submission_confirmed", return_value=True)
    mocker.patch("apply_agent.db.record_submission")

    apply_agent.submit(1)

    generate_mock.assert_not_called()
    fill_screening_mock.assert_called_once_with(page, stored_preview["screening_answers"])
    fill_eligibility_mock.assert_called_once_with(page, stored_preview["eligibility_answers"])


def test_submit_never_arms_from_a_missing_or_falsy_env_value(mocker):
    # Includes both falsy-looking strings ("0", "false", "", "no") AND truthy-looking non-"1"
    # strings ("true", "yes", "TRUE") -- the gate is exact-string-equality-to-"1", not a
    # truthy/falsy interpretation, so a widened accept list (e.g. accepting "true"/"yes" too) must
    # also fail this test. A probe set of only falsy-ish values cannot catch that drift direction.
    for falsy_value in ("0", "false", "", "no", "true", "yes", "TRUE"):
        mocker.patch.dict(os.environ, {"APPLY_AGENT_ARMED": falsy_value})
        mocker.patch("apply_agent.db.get_job_application", return_value={
            "id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/embed/job_app?token=1",
            "resume_file_ref": "r.pdf", "cover_letter_file_ref": "cl.pdf",
            "stage": "ready_to_submit", "apply_preview": {"platform": "greenhouse"},
            "approved_at": "2026-09-20T00:00:00Z",
        })
        mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
        page = MagicMock()
        mocker.patch("apply_agent._launch_page", return_value=page)
        mocker.patch("apply_agent.ats_fillers.fill_greenhouse")
        mocker.patch("apply_agent._attach_resume_and_cover_letter")
        update_stage_mock = mocker.patch("apply_agent.db.update_job_application_stage")

        apply_agent.submit(1)

        page.get_by_role.return_value.click.assert_not_called()
        update_stage_mock.assert_not_called()


@pytest.mark.parametrize("platform", ["workday", "aggregator"])
def test_submit_raises_on_permanently_excluded_platform(mocker, platform):
    """Regression test for the workday/aggregator guard in submit() -- _process_one_preview
    (the preview pass) permanently blocks these platforms before any fill attempt, and submit()
    must too. Without this guard, an armed submit against a workday/aggregator row would launch
    the generic browser-use filler against a platform this codebase treats as permanently
    excluded. Confirmed live: deleting the guard leaves this test failing (no ValueError raised)."""
    # Must be an otherwise-approved row, or the approval guard (which runs first) would
    # raise instead and this would pass without exercising the platform guard at all.
    mocker.patch("apply_agent.db.get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "job_url": "https://example.com/job/1",
        "stage": "ready_to_submit", "apply_preview": {"platform": platform},
        "approved_at": "2026-09-20T00:00:00Z",
    })
    mocker.patch("apply_agent.ats_platform.classify", return_value=platform)
    launch_mock = mocker.patch("apply_agent._launch_page")

    with pytest.raises(ValueError, match="permanently-excluded"):
        apply_agent.submit(1)

    launch_mock.assert_not_called()


# ── The approval guard: ARMED proves a human tapped, this proves they tapped THIS row ──

@pytest.mark.parametrize("job,reason", [
    ({"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/x",
      "stage": "saved", "apply_preview": {"platform": "greenhouse"}}, "never previewed"),
    ({"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/x",
      "stage": "applied", "apply_preview": {"platform": "greenhouse"}}, "already submitted"),
    ({"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/x",
      "stage": "ready_to_submit", "apply_preview": None}, "no preview blob"),
    ({"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/x",
      "stage": "ready_to_submit", "apply_preview": {"platform": "greenhouse"},
      "approved_at": None}, "not yet approved"),
])
def test_submit_raises_on_unapproved_row(mocker, job, reason):
    """The ARMED gate only proves a human tapped *something*. Without this guard any id
    reaching the workflow gets submitted -- a stale id, a mistyped manual workflow_dispatch,
    or a row that was never previewed (no eligibility/screening answers, possibly no resume)
    would go to a real employer. Armed here on purpose: the guard must hold even when armed."""
    mocker.patch.dict(os.environ, {"APPLY_AGENT_ARMED": "1"})
    mocker.patch("apply_agent.db.get_job_application", return_value=job)
    launch_mock = mocker.patch("apply_agent._launch_page")
    update_stage_mock = mocker.patch("apply_agent.db.update_job_application_stage")

    with pytest.raises(ValueError, match="unapproved row"):
        apply_agent.submit(1)

    launch_mock.assert_not_called()
    update_stage_mock.assert_not_called()


def test_submit_raises_on_nonexistent_row(mocker):
    mocker.patch.dict(os.environ, {"APPLY_AGENT_ARMED": "1"})
    mocker.patch("apply_agent.db.get_job_application", return_value=None)
    launch_mock = mocker.patch("apply_agent._launch_page")

    with pytest.raises(ValueError, match="nonexistent"):
        apply_agent.submit(1)

    launch_mock.assert_not_called()


# ── Browser teardown: run_preview loops one launch per row; leaking them OOMs a batch ──

def test_process_one_preview_closes_the_page_even_when_filling_raises(mocker):
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    page = MagicMock()
    mocker.patch("apply_agent._launch_page", return_value=page)
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse", side_effect=RuntimeError("boom"))
    close_mock = mocker.patch("apply_agent._close_page")

    with pytest.raises(RuntimeError):
        apply_agent._process_one_preview({"id": 1, "company": "Acme", "job_url": "https://boards.greenhouse.io/x"})

    close_mock.assert_called_once_with(page)


def test_close_page_tears_down_browser_and_driver():
    page, browser, playwright = MagicMock(), MagicMock(), MagicMock()
    apply_agent._OPEN_SESSIONS[id(page)] = (browser, playwright)

    apply_agent._close_page(page)

    page.close.assert_called_once()
    browser.close.assert_called_once()
    playwright.stop.assert_called_once()
    assert id(page) not in apply_agent._OPEN_SESSIONS


def test_close_page_never_raises_when_teardown_fails():
    page = MagicMock()
    page.close.side_effect = RuntimeError("already closed")
    apply_agent._close_page(page)  # must not raise
