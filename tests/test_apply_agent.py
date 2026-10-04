"""Tests mock ats_platform.classify, the per-platform fillers, and db entirely -- no real
Playwright browser, no real browser-use call, no real network. Covers: platform routing,
Workday/aggregator exclusion, the eligibility-answer lookup, and per-row failure isolation."""

from unittest.mock import MagicMock

import pytest

import apply_agent


@pytest.fixture(autouse=True)
def _lease_defaults(mocker):
    # Lease plumbing is db-backed; default every test to a successful claim, no stale leases, and
    # inert heartbeat/release so no test can reach a real Supabase client.
    mocker.patch("apply_agent.db.recover_stale_leases", return_value=0)
    mocker.patch("apply_agent.db.claim_application", return_value="lease-1")
    mocker.patch("apply_agent.db.heartbeat_application")
    mocker.patch("apply_agent.db.renew_submission_lease", return_value=True)
    mocker.patch("apply_agent.db.release_application")
    mocker.patch("apply_agent.db.set_automation_status")


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
    set_preview_mock = mocker.patch("apply_agent.db.release_application")

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
    set_blocked_mock = mocker.patch("apply_agent.db.set_automation_status")

    apply_agent.run_preview()

    launch_mock.assert_not_called()
    set_blocked_mock.assert_called_once()
    assert set_blocked_mock.call_args[0][1] == "unsupported"
    assert "workday" in set_blocked_mock.call_args[0][2].lower()


def test_run_preview_blocks_aggregator_links(mocker):
    mocker.patch("apply_agent.db.get_job_applications", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://www.indeed.com/viewjob?jk=1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"}
    ])
    mocker.patch("apply_agent.ats_platform.classify", return_value="aggregator")
    launch_mock = mocker.patch("apply_agent._launch_page")
    set_blocked_mock = mocker.patch("apply_agent.db.set_automation_status")

    apply_agent.run_preview()

    launch_mock.assert_not_called()
    set_blocked_mock.assert_called_once()
    assert set_blocked_mock.call_args[0][1] == "unsupported"


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
    mocker.patch("apply_agent.db.release_application")

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
    set_preview_mock = mocker.patch("apply_agent.db.release_application")

    apply_agent.run_preview()  # must not raise

    # row 1 failed (released failed_retryable); row 2 reached ready_for_review
    ready = [c for c in set_preview_mock.call_args_list if c[0][2] == "ready_for_review"]
    assert len(ready) == 1
    assert ready[0][0][0] == 2
    failed = [c for c in set_preview_mock.call_args_list if c[0][2] == "failed_retryable"]
    assert [c[0][0] for c in failed] == [1]


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
    mocker.patch("apply_agent.db.release_application")
    mocker.patch("apply_agent.db.set_automation_status")

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


# Merge review 2026-09-28, finding 3: this used to pass job.get("role", "") as profile_summary --
# a posting titled "Senior Product Manager" was presented back to the LLM as the candidate's own
# facts, so it could never produce the "real, factual experience" answers the prompt promises.
def test_generate_screening_answers_grounds_the_prompt_in_the_real_candidate_profile_not_the_job_role(mocker):
    page = MagicMock()
    page.get_by_text.return_value.all.return_value = [MagicMock(inner_text=lambda: "Why do you want this role?")]
    call_claude_mock = mocker.patch("apply_agent._call_claude", return_value="Grounded answer text.")
    mocker.patch("apply_agent.candidate_profile.profile_text", return_value="Associate PM at Protium Finance.")
    job = {"company": "Acme", "role": "Senior Product Manager"}

    apply_agent._generate_screening_answers(page, job)

    prompt = call_claude_mock.call_args[0][0]
    assert "Associate PM at Protium Finance." in prompt
    assert "profile_summary" not in prompt  # format() must have substituted the placeholder


def test_generate_screening_answers_flags_for_human_review_when_no_profile_text_available(mocker):
    page = MagicMock()
    page.get_by_text.return_value.all.return_value = [MagicMock(inner_text=lambda: "Why do you want this role?")]
    call_claude_mock = mocker.patch("apply_agent._call_claude")
    mocker.patch("apply_agent.candidate_profile.profile_text", return_value="")
    job = {"company": "Acme", "role": "PM"}

    result = apply_agent._generate_screening_answers(page, job)

    assert result == {"Why do you want this role?": apply_agent._NO_PROFILE_ANSWER}
    call_claude_mock.assert_not_called()


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
    set_preview_mock = mocker.patch("apply_agent.db.release_application")

    apply_agent._process_one_preview(
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/x"}
    )

    fill_screening_mock.assert_called_once_with(page, generated)
    fill_eligibility_mock.assert_called_once_with(page, {"work_authorized_us": "Yes"})
    assert set_preview_mock.call_args[0][2] == "ready_for_review"
    preview_arg = set_preview_mock.call_args[0][3]["apply_preview"]
    assert preview_arg["screening_answers"] == generated
    assert preview_arg["eligibility_answers"] == {"work_authorized_us": "Yes"}


def test_process_one_preview_returns_lost_when_release_returns_none(mocker):
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    mocker.patch("apply_agent._launch_page", return_value=MagicMock())
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse")
    mocker.patch("apply_agent._attach_resume_and_cover_letter")
    mocker.patch("apply_agent._generate_screening_answers", return_value={})
    mocker.patch("apply_agent._fill_screening_questions")
    mocker.patch("apply_agent._fill_eligibility_answers")
    mocker.patch("apply_agent.db.load_prompts", return_value={})
    mocker.patch("apply_agent.db.release_application", return_value=None)
    job = {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/x"}
    assert apply_agent._process_one_preview(job) == "lost"


def test_run_preview_counts_lost_as_error_not_filled(mocker, caplog):
    rows = [{"id": 1, "stage": "saved", "resume_file_ref": "r", "cover_letter_file_ref": "c",
             "automation_status": "idle"}]
    mocker.patch.object(apply_agent.db, "recover_stale_leases", return_value=0)
    mocker.patch.object(apply_agent.db, "get_job_applications", return_value=rows)
    mocker.patch.object(apply_agent, "_process_one_preview", return_value="lost")
    with caplog.at_level("INFO"):
        apply_agent.run_preview()
    assert "filled=0" in caplog.text and "errors=1" in caplog.text


# Merge review 2026-09-28, finding 2: _fill_generic_via_browser_use used to catch and swallow
# every failure (a missing field_values key, or any browser-use failure), so
# _process_one_preview() always proceeded to db.set_apply_preview() and stage='ready_to_submit'
# even when the generic fill produced a blank form -- a human could approve and submit an
# incomplete application with no warning. It must now propagate, so the existing per-row
# try/except in run_preview() (and submit()'s own handler) can call db.set_apply_blocked
# instead of this being silently absorbed one level down.
def test_fill_generic_via_browser_use_propagates_a_missing_field_values_key(mocker):
    page = MagicMock()
    run_mock = mocker.patch("apply_agent._browser_use_agent_run")
    with pytest.raises(KeyError):
        apply_agent._fill_generic_via_browser_use(page, {"company": "Acme"}, {"name": "Kishore"})
    run_mock.assert_not_called()


def test_fill_generic_via_browser_use_propagates_a_browser_use_failure(mocker):
    page = MagicMock()
    mocker.patch("apply_agent._browser_use_agent_run", side_effect=RuntimeError("browser-use crashed"))
    field_values = {"name": "Kishore", "email": "k@example.com", "phone": "555", "location": "NH", "linkedin": "li"}

    with pytest.raises(RuntimeError, match="browser-use crashed"):
        apply_agent._fill_generic_via_browser_use(page, {"company": "Acme"}, field_values)


def test_run_preview_blocks_the_row_when_the_generic_browser_use_fill_fails(mocker):
    # The gap the finding-2 fix closes: a browser-use failure now actually reaches
    # db.set_apply_blocked via run_preview()'s existing per-row exception handler, and the row
    # is never marked ready_to_submit.
    mocker.patch("apply_agent.db.get_job_applications", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://careers.acme.com/apply/1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"}
    ])
    mocker.patch("apply_agent.ats_platform.classify", return_value="generic")
    mocker.patch("apply_agent._launch_page", return_value=MagicMock())
    mocker.patch("apply_agent._browser_use_agent_run", side_effect=RuntimeError("browser-use did not complete"))
    release_mock = mocker.patch("apply_agent.db.release_application")

    apply_agent.run_preview()  # must not raise -- run_preview's own per-row isolation catches it

    release_mock.assert_called_once()
    assert release_mock.call_args[0][0] == 1
    assert release_mock.call_args[0][2] == "failed_retryable"
    assert "browser-use did not complete" in release_mock.call_args[0][3]["apply_blocked_reason"]


# Merge review 2026-09-28, finding 2: verified against the real installed browser-use==0.1.40
# API (requirements-apply.txt) -- the pinned version had no browser_use.llm module, no page=
# constructor arg, and an async-only Agent.run(), all three silently wrong in the code this
# adapter replaces. This smoke test constructs the REAL browser_use.Browser/BrowserConfig/Agent
# objects (proving the adapter's constructor kwargs -- cdp_url, _force_keep_browser_alive,
# browser=, llm= -- are real fields on this installed version) instead of mocking
# apply_agent._browser_use_agent_run itself away, which is exactly the kind of mock that let the
# original page=/run_sync() mismatch ship undetected. Only the two calls that would need a real
# browser/network (Agent.run, Browser.close) are stubbed. Skips if browser-use/langchain-anthropic
# aren't installed -- same lazy-import posture as the rest of this module; the base test
# environment doesn't include requirements-apply.txt.
def test_browser_use_agent_run_adapter_smoke_test(mocker):
    browser_use = pytest.importorskip("browser_use")
    pytest.importorskip("langchain_anthropic")

    page = MagicMock()
    apply_agent._CDP_PORTS[id(page)] = 65432

    fake_history = MagicMock()
    fake_history.is_successful.return_value = True
    fake_history.has_errors.return_value = False

    async def fake_run(self):
        return fake_history

    async def fake_close(self):
        return None

    mocker.patch.object(browser_use.Agent, "run", fake_run)
    mocker.patch.object(browser_use.Browser, "close", fake_close)

    try:
        result = apply_agent._browser_use_agent_run("fill the form", page)
    finally:
        apply_agent._CDP_PORTS.pop(id(page), None)

    assert result is fake_history


def test_browser_use_agent_run_raises_when_history_reports_failure(mocker):
    browser_use = pytest.importorskip("browser_use")
    pytest.importorskip("langchain_anthropic")

    page = MagicMock()
    apply_agent._CDP_PORTS[id(page)] = 65433

    fake_history = MagicMock()
    fake_history.is_successful.return_value = False
    fake_history.has_errors.return_value = True
    fake_history.errors.return_value = ["could not find a submit-adjacent form field"]

    async def fake_run(self):
        return fake_history

    async def fake_close(self):
        return None

    mocker.patch.object(browser_use.Agent, "run", fake_run)
    mocker.patch.object(browser_use.Browser, "close", fake_close)

    try:
        with pytest.raises(RuntimeError, match="did not complete"):
            apply_agent._browser_use_agent_run("fill the form", page)
    finally:
        apply_agent._CDP_PORTS.pop(id(page), None)


def test_browser_use_agent_run_raises_when_no_cdp_port_recorded(mocker):
    pytest.importorskip("browser_use")
    pytest.importorskip("langchain_anthropic")

    page = MagicMock()
    apply_agent._CDP_PORTS.pop(id(page), None)

    with pytest.raises(RuntimeError, match="CDP"):
        apply_agent._browser_use_agent_run("fill the form", page)


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
        "automation_status": "submitting", "preview_revision_hash": "h1", "approved_revision_hash": "h1",
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
        "automation_status": "submitting", "preview_revision_hash": "h1", "approved_revision_hash": "h1",
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
    mocker.patch("apply_agent.db.release_application")

    apply_agent.submit(1)

    page.get_by_role.assert_called_with("button", name=apply_agent._SUBMIT_BUTTON_NAME)
    page.get_by_role.return_value.click.assert_called_once()
    record_submission_mock.assert_called_once_with(1, "greenhouse", "2026-09-24", lease_id="lease-1")


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
        "automation_status": "submitting", "preview_revision_hash": "h1", "approved_revision_hash": "h1",
    })
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    page = MagicMock()
    mocker.patch("apply_agent._launch_page", return_value=page)
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse")
    mocker.patch("apply_agent._attach_resume_and_cover_letter")
    mocker.patch("apply_agent._submission_confirmed", return_value=False)
    record_submission_mock = mocker.patch("apply_agent.db.record_submission")
    blocked_mock = mocker.patch("apply_agent.db.release_application")

    with pytest.raises(RuntimeError, match="no confirmation"):
        apply_agent.submit(1)

    page.get_by_role.return_value.click.assert_called_once()
    record_submission_mock.assert_not_called()
    # C1: a failed submit must write apply_blocked_reason before the exception propagates --
    # without this, the row stays approved_at-set/stage='ready_to_submit' forever with no UI
    # path to recover (ApplicationsPage.tsx's Try again button depends on this field).
    blocked_mock.assert_called_once()
    assert blocked_mock.call_args[0][0] == 1
    assert blocked_mock.call_args[0][2] == "needs_confirmation"
    assert "no confirmation" in blocked_mock.call_args[0][3]["apply_blocked_reason"]


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
        "automation_status": "submitting", "preview_revision_hash": "h1", "approved_revision_hash": "h1",
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
            "automation_status": "submitting", "preview_revision_hash": "h1", "approved_revision_hash": "h1",
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
        "automation_status": "submitting", "preview_revision_hash": "h1", "approved_revision_hash": "h1",
    })
    mocker.patch("apply_agent.ats_platform.classify", return_value=platform)
    launch_mock = mocker.patch("apply_agent._launch_page")
    blocked_mock = mocker.patch("apply_agent.db.set_automation_status")

    with pytest.raises(ValueError, match="permanently-excluded"):
        apply_agent.submit(1)

    launch_mock.assert_not_called()
    # C1: the platform-exclusion guard runs AFTER the approval guard has already passed, so a
    # failure here must also record apply_blocked_reason -- same as any other post-approval
    # submit failure.
    blocked_mock.assert_called_once()
    assert blocked_mock.call_args[0][0] == 1
    assert blocked_mock.call_args[0][1] == "unsupported"
    assert "permanently-excluded" in blocked_mock.call_args[0][2]


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
    apply_agent._CDP_PORTS[id(page)] = 54321

    apply_agent._close_page(page)

    page.close.assert_called_once()
    browser.close.assert_called_once()
    playwright.stop.assert_called_once()
    assert id(page) not in apply_agent._OPEN_SESSIONS
    # finding 2: _CDP_PORTS mirrors _OPEN_SESSIONS' lifecycle -- a page torn down here must not
    # leave a stale port entry another (unrelated, future) page's id could collide with.
    assert id(page) not in apply_agent._CDP_PORTS


def test_close_page_never_raises_when_teardown_fails():
    page = MagicMock()
    page.close.side_effect = RuntimeError("already closed")
    apply_agent._close_page(page)  # must not raise


def test_launch_page_records_a_cdp_debugging_port_for_the_page(mocker):
    # finding 2: _browser_use_agent_run bridges to the real browser-use library via a CDP URL,
    # not a page= constructor arg (that field doesn't exist on the installed 0.1.40 API) -- this
    # is the wiring that makes _CDP_PORTS.get(id(page)) resolvable later. Requires the real
    # playwright package (requirements-apply.txt) to patch playwright.sync_api.sync_playwright
    # at all -- skips if it isn't installed, same lazy-import posture as the rest of this module.
    pytest.importorskip("playwright")
    playwright_mock = MagicMock()
    sync_playwright_mock = MagicMock()
    sync_playwright_mock.return_value.start.return_value = playwright_mock
    mocker.patch("playwright.sync_api.sync_playwright", sync_playwright_mock)
    mocker.patch("apply_agent._free_local_port", return_value=54321)
    fake_page = MagicMock()
    playwright_mock.chromium.launch.return_value.new_page.return_value = fake_page

    page = apply_agent._launch_page("https://careers.acme.com/apply/1")

    assert page is fake_page
    launch_kwargs = playwright_mock.chromium.launch.call_args.kwargs
    assert launch_kwargs["args"] == ["--remote-debugging-port=54321"]
    assert apply_agent._CDP_PORTS[id(fake_page)] == 54321
    apply_agent._close_page(fake_page)


# ── Leases, revision-bound approval, and the clicked boundary ──────────────────

@pytest.fixture
def approved_job():
    return {"id": 9, "company": "Acme", "job_url": "https://boards.greenhouse.io/acme/jobs/1",
            "stage": "ready_to_submit", "apply_preview": {"platform": "greenhouse",
            "field_values": {}, "screening_answers": {}, "eligibility_answers": {}},
            "approved_at": "2026-10-01T00:00:00Z", "automation_status": "submitting",
            "preview_revision_hash": "h1", "approved_revision_hash": "h1",
            "resume_file_ref": "r", "cover_letter_file_ref": "c"}


def _arm_submit(mocker, job, confirmed=True, click_raises=None):
    mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": "1"})
    mocker.patch.object(apply_agent.db, "recover_stale_leases", return_value=0)
    mocker.patch.object(apply_agent.db, "get_job_application", return_value=job)
    mocker.patch.object(apply_agent.db, "claim_application", return_value="lease-1")
    release = mocker.patch.object(apply_agent.db, "release_application")
    record = mocker.patch.object(apply_agent.db, "record_submission")
    page = MagicMock()
    if click_raises:
        page.get_by_role.return_value.click.side_effect = click_raises
    mocker.patch.object(apply_agent, "_launch_page", return_value=page)
    mocker.patch.object(apply_agent, "_close_page")
    mocker.patch.object(apply_agent.ats_fillers, "fill_greenhouse")
    mocker.patch.object(apply_agent, "_attach_resume_and_cover_letter")
    mocker.patch.object(apply_agent, "_fill_screening_questions")
    mocker.patch.object(apply_agent, "_fill_eligibility_answers")
    mocker.patch.object(apply_agent, "_submission_confirmed", return_value=confirmed)
    return page, release, record


def test_submit_happy_path_records_with_lease(mocker, approved_job):
    page, release, record = _arm_submit(mocker, approved_job)
    apply_agent.submit(9)
    record.assert_called_once()
    assert record.call_args.kwargs.get("lease_id") == "lease-1"
    release.assert_not_called()


@pytest.mark.parametrize("failure", [False, RuntimeError("database unavailable")])
def test_submit_stops_if_final_lease_or_revision_check_fails(mocker, approved_job, failure):
    page, release, record = _arm_submit(mocker, approved_job)
    guard = apply_agent.db.renew_submission_lease
    if isinstance(failure, Exception):
        guard.side_effect = failure
    else:
        guard.return_value = failure
    with pytest.raises(RuntimeError):
        apply_agent.submit(9)
    page.get_by_role.return_value.click.assert_not_called()
    record.assert_not_called()
    assert release.call_args[0][2] == "failed_retryable"


def test_final_guard_runs_after_filling_and_before_submit(mocker, approved_job):
    page, _, _ = _arm_submit(mocker, approved_job)
    events = []
    apply_agent._fill_eligibility_answers.side_effect = lambda *args: events.append("filled")
    apply_agent.db.renew_submission_lease.side_effect = lambda *args: events.append("checked") or True
    page.get_by_role.return_value.click.side_effect = lambda: events.append("clicked")
    apply_agent.submit(9)
    assert events == ["filled", "checked", "clicked"]
    apply_agent.db.renew_submission_lease.assert_called_once_with(9, "lease-1", "h1")


@pytest.mark.parametrize("confirmed,click_raises", [
    (False, None),                              # clicked, no confirmation on page
    (True, RuntimeError("navigation crashed")), # exception raised by .click() itself
])
def test_any_failure_from_the_click_onward_is_needs_confirmation(mocker, approved_job, confirmed, click_raises):
    _, release, _ = _arm_submit(mocker, approved_job, confirmed=confirmed, click_raises=click_raises)
    with pytest.raises(Exception):
        apply_agent.submit(9)
    assert release.call_args[0][2] == "needs_confirmation"


def test_record_submission_failure_after_confirmed_click_is_needs_confirmation(mocker, approved_job):
    _, release, record = _arm_submit(mocker, approved_job)
    record.side_effect = RuntimeError("supabase down")
    with pytest.raises(RuntimeError):
        apply_agent.submit(9)
    assert release.call_args[0][2] == "needs_confirmation"


def test_record_submission_returning_none_logs_stale_lease_warning_without_raising(mocker, approved_job, caplog):
    _, release, record = _arm_submit(mocker, approved_job)
    record.return_value = None
    with caplog.at_level("WARNING"):
        apply_agent.submit(9)
    assert any("needs_confirmation by lease recovery" in r.message for r in caplog.records)
    release.assert_not_called()


def test_failure_before_click_is_failed_retryable(mocker, approved_job):
    _, release, _ = _arm_submit(mocker, approved_job)
    apply_agent.ats_fillers.fill_greenhouse.side_effect = RuntimeError("selector missing")
    with pytest.raises(RuntimeError):
        apply_agent.submit(9)
    assert release.call_args[0][2] == "failed_retryable"


def test_release_failure_does_not_mask_the_original_exception(mocker, approved_job):
    _, release, _ = _arm_submit(mocker, approved_job, click_raises=RuntimeError("original"))
    release.side_effect = RuntimeError("release also failed")
    with pytest.raises(RuntimeError, match="original"):
        apply_agent.submit(9)


def test_unarmed_submit_releases_back_to_approved_without_clicking(mocker, approved_job):
    page, release, record = _arm_submit(mocker, approved_job)
    mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": ""})
    apply_agent.submit(9)
    page.get_by_role.return_value.click.assert_not_called()
    assert release.call_args[0][2] == "approved"
    record.assert_not_called()


def test_unclaimable_row_raises_and_never_launches_browser(mocker, approved_job):
    _arm_submit(mocker, approved_job)
    apply_agent.db.claim_application.return_value = None
    with pytest.raises(ValueError):
        apply_agent.submit(9)
    apply_agent._launch_page.assert_not_called()
    apply_agent.db.release_application.assert_not_called()


@pytest.mark.parametrize("override", [
    {"approved_revision_hash": "stale"},       # preview edited after approval
    {"approved_revision_hash": None},
    {"automation_status": "approved"},         # re-read shows we don't actually hold it
    {"approved_at": None},
    {"apply_preview": None},
    {"stage": "saved"},
])
def test_gate_on_reread_row_blocks_before_browser(mocker, approved_job, override):
    _, release, _ = _arm_submit(mocker, {**approved_job, **override})
    with pytest.raises(ValueError):
        apply_agent.submit(9)
    apply_agent._launch_page.assert_not_called()
    assert release.call_args[0][2] == "failed_retryable"


def test_preview_skips_rows_another_worker_claimed(mocker):
    job = {"id": 3, "job_url": "https://boards.greenhouse.io/x/jobs/1", "automation_status": "idle",
           "resume_file_ref": "r", "cover_letter_file_ref": "c", "stage": "saved"}
    mocker.patch.object(apply_agent.db, "claim_application", return_value=None)
    launch = mocker.patch.object(apply_agent, "_launch_page")
    assert apply_agent._process_one_preview(job) == "skipped"
    launch.assert_not_called()


def test_preview_marks_workday_unsupported_without_claiming(mocker):
    job = {"id": 3, "job_url": "https://acme.wd5.myworkdayjobs.com/x", "stage": "saved"}
    status = mocker.patch.object(apply_agent.db, "set_automation_status")
    claim = mocker.patch.object(apply_agent.db, "claim_application")
    assert apply_agent._process_one_preview(job) == "blocked"
    assert status.call_args[0][1] == "unsupported"
    claim.assert_not_called()


def test_run_preview_ignores_rows_not_in_eligible_statuses(mocker):
    rows = [{"id": i, "stage": "saved", "resume_file_ref": "r", "cover_letter_file_ref": "c",
             "automation_status": s} for i, s in enumerate(
             ["idle", "failed_retryable", "unsupported", "ready_for_review", "preparing"])]
    mocker.patch.object(apply_agent.db, "recover_stale_leases", return_value=0)
    mocker.patch.object(apply_agent.db, "get_job_applications", return_value=rows)
    proc = mocker.patch.object(apply_agent, "_process_one_preview", return_value="filled")
    apply_agent.run_preview()
    assert [c.args[0]["id"] for c in proc.call_args_list] == [0, 1]
