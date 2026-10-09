"""Tests mock ats_platform.classify, the per-platform fillers, and db entirely -- no real
Playwright browser, no real browser-use call, no real network. Covers: platform routing,
Workday/aggregator exclusion, the eligibility-answer lookup, and per-row failure isolation."""

from unittest.mock import MagicMock

import pytest

import apply_agent

_OK_FIELDS = {"first_name": True, "last_name": True, "email": True, "phone": True, "linkedin": True}
_OK_ATTACH = {"resume": True, "cover_letter": True}
# Captured before the autouse fixture below replaces it, for the tests of the real function.
_REAL_FORM_INVENTORY = apply_agent._form_inventory


@pytest.fixture(autouse=True)
def _lease_defaults(mocker):
    # Lease plumbing is db-backed; default every test to a successful claim, no stale leases, and
    # inert heartbeat/release so no test can reach a real Supabase client.
    mocker.patch("apply_agent.db.recover_stale_leases", return_value=0)
    mocker.patch("apply_agent.db.claim_application", return_value="lease-1")
    mocker.patch("apply_agent.db.heartbeat_application")
    mocker.patch("apply_agent.db.renew_submission_lease", return_value=True)
    mocker.patch("apply_agent.db.release_application")
    mocker.patch("apply_agent.db.mark_unsupported")
    mocker.patch("apply_agent.db.complete_preview", return_value=True)
    # The form inventory reads the live DOM; default to an empty form (nothing required) so
    # tests that aren't about questions don't trip the required-question gate.
    mocker.patch("apply_agent._form_inventory", return_value=[])
    mocker.patch("apply_agent.db.load_prompts", return_value={})
    # The approval-signature gate has its own tests (tests/test_approval_signature.py); here every
    # approved fixture row counts as validly signed.
    mocker.patch("apply_agent.approval_signature.key_configured", return_value=True)
    mocker.patch("apply_agent.approval_signature.verify", return_value=None)


def test_run_preview_routes_greenhouse_to_hand_mapped_filler(mocker):
    mocker.patch("apply_agent.db.get_preview_candidates", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/embed/job_app?token=1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"}
    ])
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    fill_mock = mocker.patch("apply_agent.ats_fillers.fill_greenhouse", return_value=dict(_OK_FIELDS))
    mocker.patch("apply_agent._launch_page", return_value=_sig_page(["text:name"]))
    mocker.patch("apply_agent._generate_screening_answers", return_value={})
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value=dict(_OK_ATTACH))
    mocker.patch("apply_agent.db.load_prompts", return_value={})
    set_preview_mock = mocker.patch("apply_agent.db.complete_preview", return_value=True)

    apply_agent.run_preview()

    fill_mock.assert_called_once()
    set_preview_mock.assert_called_once()


def test_run_preview_blocks_workday_without_attempting_a_fill(mocker):
    mocker.patch("apply_agent.db.get_preview_candidates", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://acme.wd1.myworkdayjobs.com/job/1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"}
    ])
    mocker.patch("apply_agent.ats_platform.classify", return_value="workday")
    launch_mock = mocker.patch("apply_agent._launch_page")
    set_blocked_mock = mocker.patch("apply_agent.db.mark_unsupported")

    apply_agent.run_preview()

    launch_mock.assert_not_called()
    set_blocked_mock.assert_called_once()
    assert "workday" in set_blocked_mock.call_args[0][1].lower()


def test_run_preview_blocks_aggregator_links(mocker):
    mocker.patch("apply_agent.db.get_preview_candidates", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://www.indeed.com/viewjob?jk=1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"}
    ])
    mocker.patch("apply_agent.ats_platform.classify", return_value="aggregator")
    launch_mock = mocker.patch("apply_agent._launch_page")
    set_blocked_mock = mocker.patch("apply_agent.db.mark_unsupported")

    apply_agent.run_preview()

    launch_mock.assert_not_called()
    set_blocked_mock.assert_called_once()
    assert "aggregator" in set_blocked_mock.call_args[0][1]


def test_run_preview_routes_generic_to_browser_use(mocker):
    mocker.patch("apply_agent.db.get_preview_candidates", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://careers.acme.com/apply/1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"}
    ])
    mocker.patch("apply_agent.ats_platform.classify", return_value="generic")
    mocker.patch("apply_agent._launch_page", return_value=_sig_page(["text:name"]))
    browser_use_mock = mocker.patch("apply_agent._fill_generic_via_browser_use")
    mocker.patch("apply_agent._generate_screening_answers", return_value={})
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value=dict(_OK_ATTACH))
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
    mocker.patch("apply_agent.db.get_preview_candidates", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/embed/job_app?token=1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"},
        {"id": 2, "company": "Beta", "role": "PM", "job_url": "https://boards.greenhouse.io/embed/job_app?token=2",
         "resume_file_ref": "resumes/2/r.pdf", "cover_letter_file_ref": "resumes/2/cl.pdf"},
    ])
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    mocker.patch("apply_agent._launch_page", side_effect=[RuntimeError("browser crashed"), _sig_page(["text:name"])])
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse", return_value=dict(_OK_FIELDS))
    mocker.patch("apply_agent._generate_screening_answers", return_value={})
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value=dict(_OK_ATTACH))
    mocker.patch("apply_agent.db.load_prompts", return_value={})
    complete_mock = mocker.patch("apply_agent.db.complete_preview", return_value=True)
    release_mock = mocker.patch("apply_agent.db.release_application")

    apply_agent.run_preview()  # must not raise

    # row 1 failed (released failed_retryable); row 2 reached ready_for_review
    assert [c[0][0] for c in complete_mock.call_args_list] == [2]
    failed = [c for c in release_mock.call_args_list if c[0][2] == "failed_retryable"]
    assert [c[0][0] for c in failed] == [1]


def test_run_preview_counts_blocked_rows_separately_from_filled(mocker, caplog):
    mocker.patch("apply_agent.db.get_preview_candidates", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/embed/job_app?token=1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"},
        {"id": 2, "company": "Beta", "role": "PM", "job_url": "https://acme.wd1.myworkdayjobs.com/job/1",
         "resume_file_ref": "resumes/2/r.pdf", "cover_letter_file_ref": "resumes/2/cl.pdf"},
    ])
    mocker.patch("apply_agent.ats_platform.classify", side_effect=["greenhouse", "workday"])
    mocker.patch("apply_agent._launch_page", return_value=_sig_page(["text:name"]))
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse", return_value=dict(_OK_FIELDS))
    mocker.patch("apply_agent._generate_screening_answers", return_value={})
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value=dict(_OK_ATTACH))
    mocker.patch("apply_agent.db.load_prompts", return_value={})
    mocker.patch("apply_agent.db.release_application")
    mocker.patch("apply_agent.db.mark_unsupported")

    with caplog.at_level("INFO"):
        apply_agent.run_preview()

    done_line = next(r.message for r in caplog.records if "DONE" in r.message)
    assert "filled=1" in done_line
    assert "blocked=1" in done_line
    assert "errors=0" in done_line


def _field(label, kind="input", required=True, filled=False, options=None, selector=None,
           option_selectors=None):
    return {"key": selector or f"#{label[:8]}", "selector": selector or f"#{label[:8]}", "label": label,
            "kind": kind, "required": required, "filled": filled, "options": options or [],
            "option_selectors": option_selectors or []}


def test_generate_screening_answers_grounds_answer_in_call_claude(mocker):
    mocker.patch("apply_agent._form_inventory", return_value=[_field("Why do you want this role?", "textarea")])
    mocker.patch("apply_agent._call_claude", return_value="Grounded answer text.")
    job = {"company": "Acme", "role": "PM"}

    result = apply_agent._generate_screening_answers(MagicMock(), job)

    assert result == {"Why do you want this role?": "Grounded answer text."}


# Merge review 2026-09-28, finding 3: this used to pass job.get("role", "") as profile_summary --
# a posting titled "Senior Product Manager" was presented back to the LLM as the candidate's own
# facts, so it could never produce the "real, factual experience" answers the prompt promises.
def test_generate_screening_answers_grounds_the_prompt_in_the_real_candidate_profile_not_the_job_role(mocker):
    mocker.patch("apply_agent._form_inventory", return_value=[_field("Why do you want this role?", "textarea")])
    call_claude_mock = mocker.patch("apply_agent._call_claude", return_value="Grounded answer text.")
    mocker.patch("apply_agent.candidate_profile.profile_text", return_value="Associate PM at Protium Finance.")
    job = {"company": "Acme", "role": "Senior Product Manager"}

    apply_agent._generate_screening_answers(MagicMock(), job)

    prompt = call_claude_mock.call_args[0][0]
    assert "Associate PM at Protium Finance." in prompt
    assert "profile_summary" not in prompt  # format() must have substituted the placeholder


def test_generate_screening_answers_flags_for_human_review_when_no_profile_text_available(mocker):
    mocker.patch("apply_agent._form_inventory", return_value=[_field("Why do you want this role?", "textarea")])
    call_claude_mock = mocker.patch("apply_agent._call_claude")
    mocker.patch("apply_agent.candidate_profile.profile_text", return_value="")
    job = {"company": "Acme", "role": "PM"}

    result = apply_agent._generate_screening_answers(MagicMock(), job)

    assert result == {"Why do you want this role?": apply_agent._NO_PROFILE_ANSWER}
    call_claude_mock.assert_not_called()


def test_generate_screening_answers_skips_optional_filled_file_and_eligibility_fields(mocker):
    """Regression for the 2026-10-06 live preview: page headings ("What You'll Do?") were
    answered as questions. Only required, still-empty, non-file controls get an answer, and a
    question the applicant_eligibility answers cover is left to _fill_eligibility_answers."""
    mocker.patch("apply_agent._form_inventory", return_value=[
        _field("Why us?", "textarea"),
        _field("Anything else?", "textarea", required=False),
        _field("First Name", filled=True),
        _field("Resume/CV", "file"),
        _field("Are you legally authorized to work in the United States?", "select", options=["Yes", "No"]),
    ])
    mocker.patch("apply_agent.db.load_prompts", return_value={
        "applicant_eligibility": '{"work_authorized_us": "Yes"}'})
    call_claude_mock = mocker.patch("apply_agent._call_claude", return_value="Because.")

    result = apply_agent._generate_screening_answers(MagicMock(), {"company": "Acme"})

    assert result == {"Why us?": "Because."}
    assert call_claude_mock.call_count == 1


def test_generate_screening_answers_snaps_choice_answers_to_a_real_option(mocker):
    mocker.patch("apply_agent._form_inventory", return_value=[
        _field("How did you hear about us?", "select", options=["LinkedIn", "Company website", "Referral"])])
    call_claude_mock = mocker.patch("apply_agent._call_claude", return_value="company website")

    result = apply_agent._generate_screening_answers(MagicMock(), {"company": "Acme"})

    assert result == {"How did you hear about us?": "Company website"}
    assert "Referral" in call_claude_mock.call_args[0][0]  # options were offered in the prompt


def test_generate_screening_answers_keeps_needs_human_review(mocker):
    mocker.patch("apply_agent._form_inventory", return_value=[_field("What is your desired annual salary?")])
    mocker.patch("apply_agent._call_claude", return_value="NEEDS HUMAN REVIEW")

    result = apply_agent._generate_screening_answers(MagicMock(), {"company": "Acme"})

    assert result == {"What is your desired annual salary?": "NEEDS HUMAN REVIEW"}


def test_generate_screening_answers_survives_an_unreadable_form(mocker):
    mocker.patch("apply_agent._form_inventory", return_value=None)
    assert apply_agent._generate_screening_answers(MagicMock(), {"company": "Acme"}) == {}


# ── _set_field_by_label: the shared dropdown/radio/text fallback chain ──────────

def test_set_field_by_label_selects_a_dropdown_option(mocker):
    page = MagicMock()  # select_option succeeds on a fresh mock -- must stop there

    result = apply_agent._set_field_by_label(page, "Work authorized?", "Yes")

    assert result is True
    page.get_by_label.return_value.select_option.assert_called_with(label="Yes", timeout=apply_agent.config.APPLY_AGENT_FIELD_TIMEOUT_MS)
    page.get_by_label.return_value.fill.assert_not_called()


def test_set_field_by_label_clicks_a_radio_option_scoped_to_the_questions_group(mocker):
    page = MagicMock()
    page.get_by_label.return_value.select_option.side_effect = Exception("not a select")

    result = apply_agent._set_field_by_label(page, "Work authorized?", "Yes")

    assert result is True
    page.get_by_role.assert_any_call("group", name="Work authorized?")
    page.get_by_role.return_value.get_by_role.assert_called_with("radio", name="Yes")
    page.get_by_role.return_value.get_by_role.return_value.click.assert_called_once_with(timeout=apply_agent.config.APPLY_AGENT_FIELD_TIMEOUT_MS)
    page.get_by_label.return_value.fill.assert_not_called()


def test_set_field_by_label_falls_back_to_a_plain_text_fill(mocker):
    page = MagicMock()
    page.get_by_label.return_value.select_option.side_effect = Exception("not a select")
    page.get_by_role.return_value.get_by_role.return_value.click.side_effect = Exception("no group")

    result = apply_agent._set_field_by_label(page, "Phone", "555-1234")

    assert result is True
    page.get_by_label.return_value.fill.assert_called_with("555-1234", timeout=apply_agent.config.APPLY_AGENT_FIELD_TIMEOUT_MS)


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


def test_fill_screening_questions_fills_an_inventory_match_by_selector(mocker):
    page = MagicMock()
    field = _field("What attracted you to this role?", "textarea")
    mocker.patch("apply_agent._form_inventory", return_value=[field])
    fill_mock = mocker.patch("apply_agent._fill_field", return_value=True)
    set_field_mock = mocker.patch("apply_agent._set_field_by_label")

    report = apply_agent._fill_screening_questions(page, {"What attracted you to this role? *": "The team."})

    fill_mock.assert_called_once_with(page, field, "The team.")
    set_field_mock.assert_not_called()
    assert report == {"What attracted you to this role? *": True}


def test_fill_screening_questions_never_types_a_needs_human_review_answer(mocker):
    mocker.patch("apply_agent._form_inventory", return_value=[_field("Desired salary")])
    fill_mock = mocker.patch("apply_agent._fill_field")

    report = apply_agent._fill_screening_questions(MagicMock(), {"Desired salary": "NEEDS HUMAN REVIEW"})

    fill_mock.assert_not_called()
    assert report == {"Desired salary": False}


def test_fill_eligibility_answers_translates_known_keys_to_real_question_patterns(mocker):
    """Regression test: the filler used to search for labels like "work_authorized_us"
    verbatim -- an internal seed-data key, not real form text. A known key matches the real
    question through _ELIGIBILITY_QUESTION_PATTERNS."""
    page = MagicMock()
    field = _field("Are you legally authorized to work in the United States?", "select", options=["Yes", "No"])
    mocker.patch("apply_agent._form_inventory", return_value=[field, _field("Phone")])
    fill_mock = mocker.patch("apply_agent._fill_field", return_value=True)

    report = apply_agent._fill_eligibility_answers(page, {"work_authorized_us": "Yes"})

    fill_mock.assert_called_once_with(page, field, "Yes")
    assert report == {field["label"]: True}


@pytest.mark.parametrize("key", list(apply_agent._ELIGIBILITY_QUESTION_PATTERNS))
def test_every_known_eligibility_key_has_a_question_pattern_that_compiles(key):
    pattern = apply_agent._ELIGIBILITY_QUESTION_PATTERNS[key]
    assert hasattr(pattern, "search")  # a compiled re.Pattern, not a bare string


@pytest.mark.parametrize("label,key", [
    ("Are you authorized to work in the United States for any employer?", "work_authorized_us"),
    ("Will you now or in the future require Morning Consult to sponsor an employment visa?",
     "requires_visa_sponsorship"),
    ("Gender", "gender"),
    ("Race", "race_ethnicity"),
    ("Veteran Status", "veteran_status"),
    ("Disability Status", "disability_status"),
    ("Do you identify as LGBTQ+?", "lgbtq_identity"),
])
def test_eligibility_patterns_match_real_live_question_wording(label, key):
    answers = {k: f"value-{k}" for k in apply_agent._ELIGIBILITY_QUESTION_PATTERNS}
    assert apply_agent._eligibility_value_for(label, answers) == f"value-{key}"


def test_user_added_eligibility_key_matches_any_label_containing_it():
    answers = {"desired salary": "150000"}
    assert apply_agent._eligibility_value_for("What is your desired annual salary?", answers) is None
    assert apply_agent._eligibility_value_for("What is your desired salary?*", answers) == "150000"
    assert apply_agent._eligibility_value_for("Desired Salary", {"desired salary": ""}) is None


def test_short_salary_key_matches_real_live_salary_questions():
    answers = {"salary": "150000"}
    for label in ("What is your desired annual salary?", "What is your desired base salary?"):
        assert apply_agent._eligibility_value_for(label, answers) == "150000"


def test_inventory_js_gives_name_only_radios_a_per_option_selector():
    """A radio with a name but no id must not share the group's selector with its siblings, or
    _fill_field would always check the first option."""
    assert "[value=" in apply_agent._FORM_INVENTORY_JS


def test_fill_eligibility_answers_skips_filled_fields_and_unmatched_keys(mocker):
    mocker.patch("apply_agent._form_inventory", return_value=[
        _field("Gender", "select", filled=True, options=["Male", "Female"])])
    fill_mock = mocker.patch("apply_agent._fill_field")

    assert apply_agent._fill_eligibility_answers(MagicMock(), {"gender": "Male", "veteran_status": "No"}) == {}
    fill_mock.assert_not_called()


def test_fill_eligibility_answers_never_raises_on_an_unreadable_form(mocker):
    mocker.patch("apply_agent._form_inventory", return_value=None)
    assert apply_agent._fill_eligibility_answers(MagicMock(), {"work_authorized_us": "Yes"}) == {}


# ── Form inventory and field filling ───────────────────────────────────────────

def test_form_inventory_returns_none_when_the_page_cannot_be_read():
    page = MagicMock()
    page.evaluate.side_effect = RuntimeError("page closed")
    assert _REAL_FORM_INVENTORY(page) is None
    page.evaluate.side_effect = None
    page.evaluate.return_value = "not a list"
    assert _REAL_FORM_INVENTORY(page) is None


def test_form_inventory_drops_entries_without_a_label():
    page = MagicMock()
    page.evaluate.return_value = [{"label": ""}, {"label": "Email", "kind": "input"}, "junk"]
    assert _REAL_FORM_INVENTORY(page) == [{"label": "Email", "kind": "input"}]


def test_required_unfilled_lists_required_empty_fields_and_fails_closed():
    inventory = [_field("Salary"), _field("Gender", required=False), _field("Email", filled=True)]
    assert apply_agent._required_unfilled(inventory) == ["Salary"]
    assert apply_agent._required_unfilled([]) == []
    assert apply_agent._required_unfilled(None) == ["form questions (the form could not be read)"]


@pytest.mark.parametrize("options,value,expected", [
    (["Male", "Female", "Decline"], "Male", "Male"),
    (["Female", "Male"], "male", "Male"),
    (["Yes, I am authorized", "No"], "Yes", "Yes, I am authorized"),
    (["Yes", "No"], "Yes - I am authorized", "Yes"),
    (["LinkedIn", "Other"], "Referral", None),
    (["Yes", "No"], "", None),
    (["Male", "Female", "Decline To Self Identify"], "decline to self-identify", "Decline To Self Identify"),
    (["Yes", "No", "I don't wish to answer"], "decline to self-identify", "I don't wish to answer"),
    (["Yes", "No"], "decline to self-identify", None),
])
def test_pick_option(options, value, expected):
    assert apply_agent._pick_option(options, value) == expected


def test_fill_field_text_uses_the_selector_and_field_timeout():
    page = MagicMock()
    assert apply_agent._fill_field(page, _field("Employer", selector="#question_1"), "Protium") is True
    page.locator.assert_called_once_with("#question_1")
    page.locator.return_value.first.fill.assert_called_once_with(
        "Protium", timeout=apply_agent.config.APPLY_AGENT_FIELD_TIMEOUT_MS)


def test_fill_field_select_picks_the_matching_option_or_gives_up():
    page = MagicMock()
    field = _field("Gender", "select", options=["Female", "Male"], selector="#g")
    assert apply_agent._fill_field(page, field, "male") is True
    page.locator.return_value.first.select_option.assert_called_once_with(
        label="Male", timeout=apply_agent.config.APPLY_AGENT_FIELD_TIMEOUT_MS)
    assert apply_agent._fill_field(MagicMock(), field, "Nonbinary") is False


def test_fill_field_radio_checks_the_chosen_options_own_input():
    page = MagicMock()
    field = _field("Sponsorship?", "radio", options=["Yes", "No"], option_selectors=["#s_yes", "#s_no"])
    assert apply_agent._fill_field(page, field, "No") is True
    page.locator.assert_called_once_with("#s_no")
    page.locator.return_value.first.check.assert_called_once()


def test_fill_field_single_checkbox_only_checks_on_agreement():
    field = _field("I agree to the privacy policy", "checkbox", options=["I agree"], option_selectors=["#ok"])
    page = MagicMock()
    assert apply_agent._fill_field(page, field, "Yes") is True
    page.locator.assert_called_once_with("#ok")
    assert apply_agent._fill_field(MagicMock(), field, "No") is False


def test_fill_field_combobox_types_and_presses_enter():
    page = MagicMock()
    assert apply_agent._fill_field(page, _field("Country", "combobox", selector="#c"), "United States") is True
    box = page.locator.return_value.first
    box.click.assert_called_once()
    box.fill.assert_called_once_with("United States", timeout=apply_agent.config.APPLY_AGENT_FIELD_TIMEOUT_MS)
    page.keyboard.press.assert_called_once_with("Enter")


def test_fill_field_never_raises_and_skips_files():
    page = MagicMock()
    page.locator.return_value.first.fill.side_effect = RuntimeError("Timeout 3000ms exceeded")
    assert apply_agent._fill_field(page, _field("Employer"), "x") is False
    assert apply_agent._fill_field(MagicMock(), _field("Resume", "file"), "x") is False


@pytest.mark.parametrize("url,expected", [
    ("https://jobs.ashbyhq.com/neighborly-software/23712769-a9c6-4840-ac07-7116ca45d79a",
     "https://jobs.ashbyhq.com/neighborly-software/23712769-a9c6-4840-ac07-7116ca45d79a/application"),
    ("https://jobs.ashbyhq.com/kalshi/a2e482ee-e520-4182-b383-3be4f9ca8155/application",
     "https://jobs.ashbyhq.com/kalshi/a2e482ee-e520-4182-b383-3be4f9ca8155/application"),
    # Tracking parameters are dropped (job_identity), real ones would be kept.
    ("https://jobs.ashbyhq.com/x/23712769-a9c6-4840-ac07-7116ca45d79a/?src=jr",
     "https://jobs.ashbyhq.com/x/23712769-a9c6-4840-ac07-7116ca45d79a/application"),
    ("https://www.ixl.com/company/jobs?gh_jid=8862211002",
     "https://boards.greenhouse.io/embed/job_app?token=8862211002"),
    ("https://boards.greenhouse.io/figma/jobs/6180116004?gh_src=x",
     "https://job-boards.greenhouse.io/figma/jobs/6180116004"),
    ("https://jobs.lever.co/neighbor/aa8a58c7-9a82-4127-b060-28d168bcd3fc",
     "https://jobs.lever.co/neighbor/aa8a58c7-9a82-4127-b060-28d168bcd3fc/apply"),
    ("https://jobs.lever.co/neighbor/aa8a58c7-9a82-4127-b060-28d168bcd3fc/apply",
     "https://jobs.lever.co/neighbor/aa8a58c7-9a82-4127-b060-28d168bcd3fc/apply"),
    ("https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=7737124",
     "https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=7737124"),
    (None, ""),
])
def test_application_url(url, expected):
    assert apply_agent._application_url(url) == expected


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
        result.filter.return_value = result   # _submission_state counts visible text only
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
    page.get_by_text.return_value.filter.return_value = page.get_by_text.return_value

    assert apply_agent._submission_confirmed(page) is False
    assert page.get_by_text.call_count == 1


def test_submission_confirmed_false_when_no_confirmation_text(mocker):
    page = MagicMock()
    page.get_by_text.return_value.count.return_value = 0
    page.get_by_text.return_value.filter.return_value = page.get_by_text.return_value

    assert apply_agent._submission_confirmed(page) is False


def test_submission_confirmed_false_when_page_check_raises(mocker):
    page = MagicMock()
    page.get_by_text.side_effect = RuntimeError("page closed")

    assert apply_agent._submission_confirmed(page) is False  # must not raise


def test_process_one_preview_fills_and_stores_screening_and_eligibility_answers(mocker):
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    page = _sig_page(["text:name"])
    mocker.patch("apply_agent._launch_page", return_value=page)
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse", return_value=dict(_OK_FIELDS))
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value=dict(_OK_ATTACH))
    generated = {"Why this role?": "Because reasons."}
    mocker.patch("apply_agent._generate_screening_answers", return_value=generated)
    fill_screening_mock = mocker.patch("apply_agent._fill_screening_questions")
    mocker.patch("apply_agent.db.load_prompts", return_value={
        "applicant_eligibility": '{"work_authorized_us": "Yes"}'
    })
    fill_eligibility_mock = mocker.patch("apply_agent._fill_eligibility_answers")
    set_preview_mock = mocker.patch("apply_agent.db.complete_preview", return_value=True)

    apply_agent._process_one_preview(
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/x"}
    )

    fill_screening_mock.assert_called_once_with(page, generated)
    fill_eligibility_mock.assert_called_once_with(page, {"work_authorized_us": "Yes"})
    preview_arg = set_preview_mock.call_args[0][2]
    assert preview_arg["screening_answers"] == generated
    assert preview_arg["eligibility_answers"] == {"work_authorized_us": "Yes"}


def test_process_one_preview_returns_lost_when_release_returns_none(mocker):
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    mocker.patch("apply_agent._launch_page", return_value=_sig_page(["text:name"]))
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse", return_value=dict(_OK_FIELDS))
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value=dict(_OK_ATTACH))
    mocker.patch("apply_agent._generate_screening_answers", return_value={})
    mocker.patch("apply_agent._fill_screening_questions")
    mocker.patch("apply_agent._fill_eligibility_answers")
    mocker.patch("apply_agent.db.load_prompts", return_value={})
    mocker.patch("apply_agent.db.complete_preview", return_value=False)
    job = {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/x"}
    assert apply_agent._process_one_preview(job) == "lost"


def test_run_preview_counts_lost_as_error_not_filled(mocker, caplog):
    rows = [{"id": 1, "stage": "saved", "resume_file_ref": "r", "cover_letter_file_ref": "c",
             "automation_status": "idle"}]
    mocker.patch.object(apply_agent.db, "recover_stale_leases", return_value=0)
    mocker.patch.object(apply_agent.db, "get_preview_candidates", return_value=rows)
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
    mocker.patch("apply_agent.db.get_preview_candidates", return_value=[
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://careers.acme.com/apply/1",
         "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf"}
    ])
    mocker.patch("apply_agent.ats_platform.classify", return_value="generic")
    mocker.patch("apply_agent._launch_page", return_value=_sig_page(["text:name"]))
    mocker.patch("apply_agent._browser_use_agent_run", side_effect=RuntimeError("browser-use did not complete"))
    release_mock = mocker.patch("apply_agent.db.release_application")

    apply_agent.run_preview()  # must not raise -- run_preview's own per-row isolation catches it

    release_mock.assert_called_once()
    assert release_mock.call_args[0][0] == 1
    assert release_mock.call_args[0][2] == "failed_retryable"
    assert "browser-use did not complete" in release_mock.call_args[0][3]


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
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse", return_value=dict(_OK_FIELDS))
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value=dict(_OK_ATTACH))
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
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse", return_value=dict(_OK_FIELDS))
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value=dict(_OK_ATTACH))
    mocker.patch("apply_agent._submission_confirmed", return_value=True)
    fake_date = mocker.patch("apply_agent.date")
    fake_date.today.return_value.isoformat.return_value = "2026-09-24"
    record_submission_mock = mocker.patch("apply_agent.db.record_submission")
    mocker.patch("apply_agent.db.release_application")

    apply_agent.submit(1)

    page.get_by_role.assert_called_with("button", name=apply_agent._SUBMIT_BUTTON_NAME)
    page.get_by_role.return_value.click.assert_called_once()
    record_submission_mock.assert_called_once_with(1, "lease-1", "greenhouse", "2026-09-24")


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
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse", return_value=dict(_OK_FIELDS))
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value=dict(_OK_ATTACH))
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
    assert "no confirmation" in blocked_mock.call_args[0][3]


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
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse", return_value=dict(_OK_FIELDS))
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value=dict(_OK_ATTACH))
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
        mocker.patch("apply_agent.ats_fillers.fill_greenhouse", return_value=dict(_OK_FIELDS))
        mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value=dict(_OK_ATTACH))
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
    blocked_mock = mocker.patch("apply_agent.db.mark_unsupported")

    with pytest.raises(ValueError, match="permanently-excluded"):
        apply_agent.submit(1)

    launch_mock.assert_not_called()
    # C1: the platform-exclusion guard runs AFTER the approval guard has already passed, so a
    # failure here must also record apply_blocked_reason -- same as any other post-approval
    # submit failure.
    blocked_mock.assert_called_once()
    assert blocked_mock.call_args[0][0] == 1
    assert "permanently-excluded" in blocked_mock.call_args[0][1]


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
    page = _sig_page(["text:name"])
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
    mocker.patch.object(apply_agent.ats_fillers, "fill_greenhouse", return_value=dict(_OK_FIELDS))
    mocker.patch.object(apply_agent, "_attach_resume_and_cover_letter", return_value=dict(_OK_ATTACH))
    mocker.patch.object(apply_agent, "_fill_screening_questions")
    mocker.patch.object(apply_agent, "_fill_eligibility_answers")
    mocker.patch.object(apply_agent, "_submission_confirmed", return_value=confirmed)
    return page, release, record


def test_submit_happy_path_records_with_lease(mocker, approved_job):
    page, release, record = _arm_submit(mocker, approved_job)
    apply_agent.submit(9)
    record.assert_called_once()
    assert record.call_args[0][:2] == (9, "lease-1")
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
    status = mocker.patch.object(apply_agent.db, "mark_unsupported")
    claim = mocker.patch.object(apply_agent.db, "claim_application")
    assert apply_agent._process_one_preview(job) == "blocked"
    assert "workday" in status.call_args[0][1]
    claim.assert_not_called()


def test_run_preview_reads_only_eligible_statuses_from_the_database(mocker):
    # The status filter runs in the query (fifty-a-day F3); tests/test_stress_local.py runs it
    # against Postgres.
    client = MagicMock()
    chain = client.table.return_value
    for name in ("select", "eq", "not_", "is_", "in_", "lt", "or_", "order", "limit"):
        getattr(chain, name).return_value = chain
    chain.not_ = chain
    chain.execute.return_value = MagicMock(data=[])
    mocker.patch.object(apply_agent.db, "get_client", return_value=client)
    apply_agent.db.get_preview_candidates(10)
    chain.in_.assert_called_once_with("automation_status", list(apply_agent.config.APPLY_AGENT_PREVIEW_ELIGIBLE_STATUSES))
    chain.lt.assert_called_once_with("prepare_attempts", apply_agent.config.APPLY_PREPARE_MAX_ATTEMPTS)
    chain.limit.assert_called_once_with(10)


def test_standard_field_values_uses_application_email():
    # Receipts land in this mailbox; the reconciler's RECEIPT_IMAP_ADDRESS must match it.
    assert apply_agent._standard_field_values({})["email"] == "kishoretheerajvj@gmail.com"


# ── Form signature ─────────────────────────────────────────────────────────────

def _sig_page(idents=None, evaluate_raises=None, wait_raises=None):
    page = MagicMock()
    if wait_raises:
        page.wait_for_selector.side_effect = wait_raises
    if evaluate_raises:
        page.evaluate.side_effect = evaluate_raises
    else:
        page.evaluate.return_value = idents
    return page


def test_form_signature_waits_for_fields_before_evaluating():
    page = _sig_page(["text:name"])
    apply_agent._form_signature(page)
    page.wait_for_selector.assert_called_once_with("input:visible, select:visible, textarea:visible", timeout=15000)


def test_form_signature_ignores_order_case_and_whitespace():
    a = apply_agent._form_signature(_sig_page(["text:First Name", "email:Email  Address"]))
    b = apply_agent._form_signature(_sig_page(["email:email address", "text:first   name"]))
    assert a and a == b


def test_form_signature_differs_when_a_field_is_added():
    a = apply_agent._form_signature(_sig_page(["text:name"]))
    b = apply_agent._form_signature(_sig_page(["text:name", "text:phone"]))
    assert a != b


def test_form_field_js_never_reads_element_id_and_skips_captcha():
    js = apply_agent._FORM_FIELDS_JS
    assert ".id" not in js
    assert "/captcha/i" in js


@pytest.mark.parametrize("page", [
    _sig_page([]),
    _sig_page(None),
    _sig_page(evaluate_raises=RuntimeError("page closed")),
    _sig_page(wait_raises=RuntimeError("timeout")),
])
def test_form_signature_returns_none_when_empty_or_failing(page):
    assert apply_agent._form_signature(page) is None


def test_preview_computes_signature_before_any_fill_and_stores_it(mocker):
    order = mocker.Mock()
    page = MagicMock()
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    mocker.patch("apply_agent._launch_page", return_value=page)
    mocker.patch("apply_agent._form_signature", side_effect=order.sig)
    order.sig.return_value = "sig-1"
    order.fill.return_value = dict(_OK_FIELDS)
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse", side_effect=order.fill)
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value=dict(_OK_ATTACH))
    mocker.patch("apply_agent._generate_screening_answers", return_value={})
    mocker.patch("apply_agent._fill_screening_questions")
    mocker.patch("apply_agent._fill_eligibility_answers")
    mocker.patch("apply_agent.db.load_prompts", return_value={})
    complete = mocker.patch("apply_agent.db.complete_preview", return_value=True)

    apply_agent._process_one_preview(
        {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/x"})

    assert [c[0] for c in order.mock_calls[:2]] == ["sig", "fill"]
    assert complete.call_args[0][3] == "sig-1"


def test_submit_form_drift_releases_needs_input_and_never_fills_or_clicks(mocker, approved_job):
    page, release, record = _arm_submit(mocker, {**approved_job, "form_signature": "old"})
    mocker.patch.object(apply_agent, "_form_signature", return_value="new")
    with pytest.raises(apply_agent.FormChangedError):
        apply_agent.submit(9)
    apply_agent.ats_fillers.fill_greenhouse.assert_not_called()
    page.get_by_role.return_value.click.assert_not_called()
    release.assert_called_once_with(9, "lease-1", "needs_input", "Form changed after approval")
    record.assert_not_called()


def test_submit_without_stored_signature_skips_drift_check_with_warning(mocker, approved_job, caplog):
    page, release, record = _arm_submit(mocker, {**approved_job, "form_signature": None})
    sig = mocker.patch.object(apply_agent, "_form_signature")
    with caplog.at_level("WARNING"):
        apply_agent.submit(9)
    sig.assert_not_called()
    assert any("drift check skipped" in r.message for r in caplog.records)
    page.get_by_role.return_value.click.assert_called_once()


def test_submit_with_matching_signature_proceeds(mocker, approved_job):
    page, release, record = _arm_submit(mocker, {**approved_job, "form_signature": "same"})
    mocker.patch.object(apply_agent, "_form_signature", return_value="same")
    apply_agent.submit(9)
    page.get_by_role.return_value.click.assert_called_once()
    record.assert_called_once()


def test_new_preview_without_fingerprint_cannot_be_approved(mocker):
    page = _sig_page([])
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    mocker.patch("apply_agent._launch_page", return_value=page)
    fill = mocker.patch("apply_agent.ats_fillers.fill_greenhouse", return_value=dict(_OK_FIELDS))
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value=dict(_OK_ATTACH))
    mocker.patch("apply_agent._generate_screening_answers", return_value={})
    mocker.patch("apply_agent._fill_screening_questions")
    mocker.patch("apply_agent._fill_eligibility_answers")
    mocker.patch("apply_agent.db.load_prompts", return_value={})
    with pytest.raises(ValueError, match="fingerprint"):
        apply_agent._process_one_preview({"id": 1, "job_url": "https://boards.greenhouse.io/x"})
    apply_agent.db.complete_preview.assert_not_called()
    fill.assert_not_called()
    assert apply_agent.db.release_application.call_args.args[2] == "failed_retryable"


# ── Fill report / required-field gate ──────────────────────────────────────────

class _FileLocator:
    def __init__(self, present):
        self.present = present
        self.set_calls = []

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.present else 0

    def set_input_files(self, path, timeout=None):
        self.set_calls.append((path, timeout))


class _FilePage:
    def __init__(self, present):
        self.present = set(present)
        self.locs = {}

    def locator(self, sel):
        return self.locs.setdefault(sel, _FileLocator(sel in self.present))


def _storage(mocker):
    mocker.patch("apply_agent.db.get_client", return_value=MagicMock(
        storage=MagicMock(from_=MagicMock(return_value=MagicMock(download=MagicMock(return_value=b"x"))))))


def test_attach_finds_hidden_file_input_by_id(mocker):
    _storage(mocker)
    page = _FilePage({"#resume", "#cover_letter"})
    job = {"resume_file_ref": "r.pdf", "cover_letter_file_ref": "c.pdf"}
    assert apply_agent._attach_resume_and_cover_letter(page, job) == {"resume": True, "cover_letter": True}
    assert page.locs["#resume"].set_calls[0][1] == apply_agent.config.APPLY_AGENT_FIELD_TIMEOUT_MS


def test_attachments_carry_the_candidates_name_and_leave_no_temp_files(mocker, tmp_path):
    # fifty-a-day F11: uploads used to be named tmpXXXX.pdf (what a recruiter sees) and the temp
    # files were never deleted.
    _storage(mocker)
    mocker.patch("tempfile.tempdir", str(tmp_path))
    page = _FilePage({"#resume", "#cover_letter"})
    apply_agent._attach_resume_and_cover_letter(page, {"resume_file_ref": "r.pdf", "cover_letter_file_ref": "c.pdf"})
    resume = page.locs["#resume"].set_calls[0][0]
    cover = page.locs["#cover_letter"].set_calls[0][0]
    assert resume == {"name": "Kishore_Theeraj_Vasudevan_Jaya_Resume.pdf", "mimeType": "application/pdf", "buffer": b"x"}
    assert cover["name"] == "Kishore_Theeraj_Vasudevan_Jaya_Cover_Letter.pdf"
    assert list(tmp_path.iterdir()) == []


def test_attach_resume_falls_back_to_first_file_input_and_cover_letter_none_without_field(mocker):
    _storage(mocker)
    page = _FilePage({"input[type='file']:not([id*='cover' i]):not([name*='cover' i])"})
    job = {"resume_file_ref": "r.pdf", "cover_letter_file_ref": "c.pdf"}
    assert apply_agent._attach_resume_and_cover_letter(page, job) == {"resume": True, "cover_letter": None}


def test_attach_none_when_no_file_ref_and_false_on_set_failure(mocker):
    _storage(mocker)
    page = _FilePage({"#resume"})
    page.locator("#resume").set_input_files = MagicMock(side_effect=RuntimeError("bad"))
    assert apply_agent._attach_resume_and_cover_letter(page, {"resume_file_ref": "r.pdf"}) == {
        "resume": False, "cover_letter": None}
    assert apply_agent._attach_resume_and_cover_letter(_FilePage(set()), {}) == {
        "resume": None, "cover_letter": None}


@pytest.mark.parametrize("fields,attach,expected_missing", [
    ({"first_name": True, "last_name": True, "email": True}, {"resume": True}, []),
    ({"name": True, "email": True}, {"resume": True}, []),
    ({"first_name": True, "last_name": False, "email": True}, {"resume": True}, ["name"]),
    ({"name": True, "email": False}, {"resume": True}, ["email"]),
    ({"name": True, "email": True}, {"resume": None}, ["resume"]),
    ({}, {}, ["name", "email", "resume"]),
])
def test_missing_required(fields, attach, expected_missing):
    missing = apply_agent._missing_required({"fields": fields, "attachments": attach})
    assert missing == expected_missing


def _preview_job():
    return {"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/x"}


def _preview_mocks(mocker, attach):
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    mocker.patch("apply_agent._launch_page", return_value=_sig_page(["text:name"]))
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse", return_value=dict(_OK_FIELDS))
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value=attach)
    mocker.patch("apply_agent._generate_screening_answers", return_value={})
    mocker.patch("apply_agent._fill_screening_questions", return_value={})
    mocker.patch("apply_agent._fill_eligibility_answers")
    mocker.patch("apply_agent.db.load_prompts", return_value={})


def test_preview_missing_resume_releases_needs_input_without_complete_preview(mocker):
    _preview_mocks(mocker, {"resume": False, "cover_letter": None})
    complete = mocker.patch("apply_agent.db.complete_preview", return_value=True)
    release = mocker.patch("apply_agent.db.release_application")

    assert apply_agent._process_one_preview(_preview_job()) == "blocked"

    complete.assert_not_called()
    args = release.call_args[0]
    assert args[2] == "needs_input"
    assert args[3] == "Preview couldn't fill required fields: resume"


def test_preview_complete_stores_fill_report(mocker):
    _preview_mocks(mocker, dict(_OK_ATTACH))
    complete = mocker.patch("apply_agent.db.complete_preview", return_value=True)

    assert apply_agent._process_one_preview(_preview_job()) == "filled"

    assert complete.call_args[0][2]["fill_report"] == {
        "fields": _OK_FIELDS, "attachments": _OK_ATTACH, "questions": {}, "required_unfilled": []}


def test_preview_releases_needs_input_when_a_required_question_stays_empty(mocker):
    """Regression for the 2026-10-06 live preview: 7 forms reached ready_for_review with every
    required screening question blank, because only name/email/resume were checked."""
    _preview_mocks(mocker, dict(_OK_ATTACH))
    mocker.patch("apply_agent._form_inventory", return_value=[
        _field("What is your desired annual salary?"), _field("Email", filled=True)])
    complete = mocker.patch("apply_agent.db.complete_preview", return_value=True)
    release = mocker.patch("apply_agent.db.release_application", return_value=True)

    assert apply_agent._process_one_preview(_preview_job()) == "blocked"

    complete.assert_not_called()
    args = release.call_args[0]
    assert args[2] == "needs_input"
    assert args[3].startswith("Preview couldn't fill required questions: What is your desired annual salary?. ")
    assert "applicant_eligibility" in args[3]


def test_preview_releases_needs_input_when_the_form_cannot_be_read(mocker):
    _preview_mocks(mocker, dict(_OK_ATTACH))
    mocker.patch("apply_agent._form_inventory", return_value=None)
    complete = mocker.patch("apply_agent.db.complete_preview", return_value=True)
    release = mocker.patch("apply_agent.db.release_application", return_value=True)

    assert apply_agent._process_one_preview(_preview_job()) == "blocked"
    complete.assert_not_called()
    assert release.call_args[0][2] == "needs_input"


def test_preview_logs_the_form_inventory_without_values(mocker, caplog):
    _preview_mocks(mocker, dict(_OK_ATTACH))
    mocker.patch("apply_agent._form_inventory", return_value=[
        dict(_field("Email", filled=True), value="secret@example.com")])
    caplog.set_level("INFO")

    apply_agent._process_one_preview(_preview_job())

    line = next(r.getMessage() for r in caplog.records if "[APPLY-FORM]" in r.getMessage())
    assert '"label": "Email"' in line
    assert "secret@example.com" not in line


def test_preview_opens_the_application_url_not_the_posting(mocker):
    _preview_mocks(mocker, dict(_OK_ATTACH))
    mocker.patch("apply_agent.ats_platform.classify", return_value="ashby")
    mocker.patch("apply_agent.ats_fillers.fill_ashby", return_value={"name": True, "email": True})
    launch = apply_agent._launch_page
    job = dict(_preview_job(), job_url="https://jobs.ashbyhq.com/n/23712769-a9c6-4840-ac07-7116ca45d79a")

    apply_agent._process_one_preview(job)

    launch.assert_called_once_with("https://jobs.ashbyhq.com/n/23712769-a9c6-4840-ac07-7116ca45d79a/application")


def test_submit_refuses_before_clicking_when_a_required_question_is_empty(mocker, approved_job):
    page, release, record = _arm_submit(mocker, approved_job)
    mocker.patch("apply_agent._form_inventory", return_value=[_field("Desired salary")])

    with pytest.raises(ValueError, match="required fields not filled: Desired salary"):
        apply_agent.submit(9)

    page.get_by_role.return_value.click.assert_not_called()
    record.assert_not_called()
    assert release.call_args[0][2] == "failed_retryable"


def test_submit_refuses_before_clicking_when_required_fields_missing(mocker, approved_job):
    page, release, record = _arm_submit(mocker, approved_job)
    apply_agent._attach_resume_and_cover_letter.return_value = {"resume": False, "cover_letter": None}

    with pytest.raises(ValueError, match="Refusing to submit: required fields not filled: resume"):
        apply_agent.submit(9)

    page.get_by_role.return_value.click.assert_not_called()
    apply_agent.db.renew_submission_lease.assert_not_called()
    record.assert_not_called()
    assert release.call_args[0][2] == "failed_retryable"


def test_attach_resume_does_not_take_a_cover_letter_only_file_input(mocker):
    _storage(mocker)
    page = _FilePage({"#cover_letter", "input[type='file']"})
    job = {"resume_file_ref": "r.pdf", "cover_letter_file_ref": "c.pdf"}
    report = apply_agent._attach_resume_and_cover_letter(page, job)
    assert report == {"resume": None, "cover_letter": True}
    assert "input[type='file']" not in page.locs or not page.locs["input[type='file']"].set_calls
    assert apply_agent._missing_required(
        {"fields": {"name": True, "email": True}, "attachments": report}) == ["resume"]
    assert "input[type='file']:not([id*='cover' i]):not([name*='cover' i])" in page.locs


def test_attach_does_not_download_when_no_matching_file_input(mocker):
    _storage(mocker)
    client = apply_agent.db.get_client()
    apply_agent._attach_resume_and_cover_letter(_FilePage(set()), {"resume_file_ref": "r.pdf"})
    client.storage.from_.return_value.download.assert_not_called()


def test_generic_platform_with_no_resume_input_still_completes_preview(mocker):
    _preview_mocks(mocker, {"resume": None, "cover_letter": None})
    mocker.patch("apply_agent.ats_platform.classify", return_value="generic")
    mocker.patch("apply_agent._fill_generic_via_browser_use")
    complete = mocker.patch("apply_agent.db.complete_preview", return_value=True)
    assert apply_agent._process_one_preview(_preview_job()) == "filled"
    complete.assert_called_once()
    assert "fill_report" not in complete.call_args[0][2]


def test_preview_returns_lost_when_needs_input_release_returns_none(mocker):
    _preview_mocks(mocker, {"resume": None, "cover_letter": None})
    mocker.patch("apply_agent.db.release_application", return_value=None)
    assert apply_agent._process_one_preview(_preview_job()) == "lost"


def test_refuse_on_failed_attachments_raises_only_for_failed():
    with pytest.raises(ValueError, match="attachments failed: resume"):
        apply_agent._refuse_on_failed_attachments({"resume": False, "cover_letter": True})
    # None means "no file input on this step" — not a failure.
    apply_agent._refuse_on_failed_attachments({"resume": None, "cover_letter": None})
    apply_agent._refuse_on_failed_attachments({"resume": True})
    apply_agent._refuse_on_failed_attachments({})
    apply_agent._refuse_on_failed_attachments(None)
