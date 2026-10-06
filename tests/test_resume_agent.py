"""Tests for resume_agent.py. All Claude calls and db.py calls are mocked -- no real network,
no real Supabase, no real credentials."""

import os
import datetime

import pytest

import claude_subscription
import config
import db
import resume_agent
import resume_lint


# ── _check_deadline ──────────────────────────────────────────────────────────

def test_check_deadline_true_when_no_deadline_known():
    assert resume_agent._check_deadline({"posting_snapshot": {}}) is True


def test_check_deadline_true_when_deadline_in_future():
    future = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    job = {"posting_snapshot": {"deadline": future}}
    assert resume_agent._check_deadline(job) is True


def test_check_deadline_false_when_deadline_has_passed():
    past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    job = {"posting_snapshot": {"deadline": past}}
    assert resume_agent._check_deadline(job) is False


# ── propose ────────────────────────────────────────────────────────────────────

def test_propose_raises_when_job_not_found(mocker):
    mocker.patch.object(db, "get_job_application", return_value=None)
    with pytest.raises(ValueError, match="not found"):
        resume_agent.propose(999)


def test_propose_raises_when_deadline_has_passed(mocker):
    past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    mocker.patch.object(db, "get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "posting_snapshot": {"deadline": past},
    })
    with pytest.raises(resume_agent.DeadlinePassedError):
        resume_agent.propose(1)


_USAGE = {"input_tokens": 100, "output_tokens": 50}


def test_propose_writes_strategy_to_db(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
    mocker.patch.object(db, "get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "Product Manager", "posting_snapshot": {},
    })
    mocker.patch.object(resume_agent, "_call_claude", return_value=(
        '{"section_order": ["Experience", "Projects"], "projects_included": ["Hiya"], '
        '"cover_letter_angle": "test angle", "named_gaps": ["no fintech background"]}',
        _USAGE,
    ))
    set_strategy = mocker.patch.object(db, "set_resume_strategy", return_value={"id": 1})
    track_usage = mocker.patch.object(db, "record_resume_usage", return_value={"id": 1})
    log_api_usage = mocker.patch.object(db, "log_api_usage", return_value={"id": 1})
    result = resume_agent.propose(1)
    assert result["section_order"] == ["Experience", "Projects"]
    set_strategy.assert_called_once()
    args, kwargs = set_strategy.call_args
    assert args[0] == 1
    assert args[1]["cover_letter_angle"] == "test angle"
    track_usage.assert_called_once_with(1, 100, 50, pytest.approx(0.001050))
    log_api_usage.assert_called_once_with(
        module="resume_agent", action="propose", model=config.RESUME_MODEL,
        input_tokens=100, output_tokens=50, cost_usd=pytest.approx(0.001050),
        contact_id=None, job_application_id=1, billing="api",
    )


def test_propose_raises_on_malformed_claude_response(mocker):
    mocker.patch.object(db, "get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "posting_snapshot": {},
    })
    mocker.patch.object(resume_agent, "_call_claude", return_value=("not json", _USAGE))
    mocker.patch.object(db, "record_resume_usage")
    mocker.patch.object(db, "log_api_usage")
    with pytest.raises(ValueError, match="could not parse strategy"):
        resume_agent.propose(1)


def test_propose_strips_markdown_json_fence_before_parsing(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
    # Regression test: live run against job 41 (2026-08-29) showed Claude wraps the response
    # in a ```json fence despite the prompt saying "ONLY a JSON object, no other text".
    mocker.patch.object(db, "get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "posting_snapshot": {},
    })
    mocker.patch.object(resume_agent, "_call_claude", return_value=(
        '```json\n{"section_order": ["Experience"], "projects_included": [], '
        '"cover_letter_angle": "angle", "named_gaps": []}\n```',
        _USAGE,
    ))
    mocker.patch.object(db, "set_resume_strategy", return_value={"id": 1})
    mocker.patch.object(db, "record_resume_usage", return_value={"id": 1})
    mocker.patch.object(db, "log_api_usage", return_value={"id": 1})
    result = resume_agent.propose(1)
    assert result["section_order"] == ["Experience"]


# ── _resolve_master ────────────────────────────────────────────────────────────

_CLEAN_MASTER = {
    "name": "Test Person",
    "contact": {"location": "Hanover, NH", "phone": "555-0100", "email": "test@example.com", "linkedin": None},
    "roles": [{"title": "Associate Product Manager", "company": "Acme", "descriptor": "Fintech",
               "location": "Bangalore, India", "period": "2025", "bullet_ids": ["m1"]}],
    "education": [{"institution": "Dartmouth", "location": "Hanover, NH", "program": "MEM",
                    "graduation": "2026", "coursework": None}],
    "projects": {
        "Hiya": {"descriptor": None, "period": None, "bullet_ids": ["m2"]},
        "Viant": {"descriptor": None, "period": None, "bullet_ids": ["m3"]},
    },
    "leadership": {"bullet_ids": ["m4"]},
}
_CLEAN_METRICS = [
    {"id": "m1", "role": "APM", "text": "Shipped a roadmap.", "resolved": True, "conflicting_values": []},
    {"id": "m2", "role": "Project", "text": "Protected 500M+ users.", "resolved": True, "conflicting_values": []},
    {"id": "m3", "role": "Project", "text": "Narrowed 7 vendors to 2.", "resolved": True, "conflicting_values": []},
    {"id": "m4", "role": "Personal", "text": "Mentored 500+ students.", "resolved": True, "conflicting_values": []},
]
_CONFLICTED_METRICS = [
    {"id": "m1", "role": "APM", "text": "$20K/year saved.", "resolved": None, "conflicting_values": ["$120K/year"]},
]
_CLEAN_SKILLS = {"spine": ["SQL"], "swap_pool": ["Python", "Metabase"], "banned": ["Tableau"], "flagged_unbacked": []}


def test_resolve_master_converts_bullet_ids_to_text_for_roles_and_filters_projects():
    strategy = {"projects_included": ["Hiya"]}
    resolved = resume_agent._resolve_master(_CLEAN_MASTER, _CLEAN_METRICS, strategy)
    assert resolved["roles"][0]["bullets"] == ["Shipped a roadmap."]
    assert resolved["projects"]["Hiya"]["bullets"] == ["Protected 500M+ users."]
    assert resolved["leadership_bullets"] == ["Mentored 500+ students."]
    assert resolved["name"] == "Test Person"
    assert resolved["education"] == _CLEAN_MASTER["education"]


def test_resolve_master_caps_bullets_per_role_to_configured_max():
    # Regression test: a live --build run (job 41, 2026-08-29) with 4 Protium roles at their real
    # 4-5 bullet_ids each never fit one page even at the tightest fitting-ladder rung -- real
    # historical resumes show 2-3 bullets per role, not every metric a role has bullet_ids for.
    many_bullet_metrics = [
        {"id": f"m{i}", "role": "APM", "text": f"Bullet {i}.", "resolved": True, "conflicting_values": []}
        for i in range(6)
    ]
    master = dict(_CLEAN_MASTER, roles=[dict(_CLEAN_MASTER["roles"][0], bullet_ids=[f"m{i}" for i in range(6)])])
    resolved = resume_agent._resolve_master(master, many_bullet_metrics, {"projects_included": []})
    assert len(resolved["roles"][0]["bullets"]) == config.RESUME_MAX_BULLETS_PER_ENTRY


def test_resolve_master_raises_on_fabricated_project_name():
    # Regression test: a live --propose run (job 41, 2026-08-29) had the LLM invent entirely
    # fictional project names/descriptions instead of choosing from master.json's real projects.
    # _resolve_master previously silently skipped unknown names -- now it raises.
    strategy = {"projects_included": ["A Project That Does Not Exist"]}
    with pytest.raises(ValueError, match="unknown project"):
        resume_agent._resolve_master(_CLEAN_MASTER, _CLEAN_METRICS, strategy)


# ── _check_skills_governance ───────────────────────────────────────────────────

def test_check_skills_governance_passes_valid_skills():
    strategy = {"skills_groups": [{"label": "Data & Tools", "skills": ["SQL", "Python"]}]}
    assert resume_agent._check_skills_governance(_CLEAN_SKILLS, strategy) == []


def test_check_skills_governance_flags_banned_skill():
    strategy = {"skills_groups": [{"label": "Data & Tools", "skills": ["Tableau"]}]}
    violations = resume_agent._check_skills_governance(_CLEAN_SKILLS, strategy)
    assert any("banned" in v for v in violations)


def test_check_skills_governance_flags_unbacked_skill():
    strategy = {"skills_groups": [{"label": "Data & Tools", "skills": ["Rust"]}]}
    violations = resume_agent._check_skills_governance(_CLEAN_SKILLS, strategy)
    assert any("governed skills pool" in v for v in violations)


# ── build ──────────────────────────────────────────────────────────────────────

_JOB_WITH_STRATEGY = {
    "id": 1, "company": "Acme", "role": "Product Manager", "posting_snapshot": {},
    "resume_strategy": {
        "section_order": ["Experience"], "projects_included": [],
        "skills_groups": [{"label": "Data & Tools", "skills": ["SQL"]}],
        "cover_letter_angle": "test angle", "named_gaps": [],
    },
}


def _mock_clean_data(mocker):
    mocker.patch.object(resume_agent, "_load_data", side_effect=lambda name: {
        "master.json": _CLEAN_MASTER, "metrics.json": _CLEAN_METRICS, "jargon.json": {},
        "skills.json": _CLEAN_SKILLS,
    }[name])


def _mock_pdf_checks(mocker):
    mocker.patch("resume_agent.resume_scrub.read_pdf_xmp_text", return_value="")
    mocker.patch("resume_agent.resume_scrub.check_fonts", return_value=[])
    mocker.patch("resume_agent.resume_build.pdf_text", return_value="Dear team")


def test_build_raises_when_no_strategy_proposed_yet(mocker):
    mocker.patch.object(db, "get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "resume_strategy": None,
    })
    with pytest.raises(ValueError, match="propose"):
        resume_agent.build(1)


def test_build_raises_lint_failed_error_on_unresolved_metric_conflict(mocker):
    job = dict(_JOB_WITH_STRATEGY, resume_strategy=dict(
        _JOB_WITH_STRATEGY["resume_strategy"], projects_included=[],
    ))
    mocker.patch.object(db, "get_job_application", return_value=job)
    mocker.patch.object(resume_agent, "_load_data", side_effect=lambda name: {
        "master.json": {"roles": [{"title": "APM", "company": "Acme", "period": "2025",
                                    "bullet_ids": ["m1"]}], "education": [], "projects": {}},
        "metrics.json": _CONFLICTED_METRICS, "jargon.json": {}, "skills.json": _CLEAN_SKILLS,
    }[name])
    with pytest.raises(resume_agent.LintFailedError, match="unresolved metric conflict"):
        resume_agent.build(1)


def test_build_happy_path_uploads_and_writes_file_refs(mocker):
    mocker.patch.object(db, "get_job_application", return_value=_JOB_WITH_STRATEGY)
    _mock_clean_data(mocker)
    mocker.patch.object(resume_agent, "_call_claude", return_value=("A clean cover letter body.", _USAGE))
    mocker.patch.object(db, "record_resume_usage", return_value={"id": 1})
    mocker.patch.object(db, "log_api_usage", return_value={"id": 1})
    mocker.patch("resume_agent.resume_build.fit_to_one_page", return_value=("/tmp/r.pdf", "standard"))
    mocker.patch("resume_agent.resume_build.convert_to_pdf", return_value="/tmp/cl.pdf")
    mocker.patch("resume_agent.resume_scrub.scrub_pdf_metadata")
    mocker.patch("resume_agent.resume_scrub.read_pdf_metadata_text", return_value="Microsoft Word")
    mocker.patch("resume_agent.resume_scrub.verify_no_fingerprints", return_value=[])
    _mock_pdf_checks(mocker)
    mocker.patch("resume_agent.resume_build.new_document")
    mocker.patch("builtins.open", mocker.mock_open(read_data=b"pdfbytes"))
    upload = mocker.patch.object(db, "upload_resume_file", side_effect=[
        "resumes/1/resume.pdf", "resumes/1/cover_letter.pdf",
    ])
    set_files = mocker.patch.object(db, "set_resume_files", return_value={"id": 1})

    result = resume_agent.build(1)

    assert result["resume_file_ref"] == "resumes/1/resume.pdf"
    assert result["cover_letter_file_ref"] == "resumes/1/cover_letter.pdf"
    assert upload.call_count == 2
    set_files.assert_called_once()


def _mock_happy_build(mocker):
    mocker.patch.object(db, "get_job_application", return_value=_JOB_WITH_STRATEGY)
    _mock_clean_data(mocker)
    mocker.patch.object(resume_agent, "_call_claude", return_value=("A clean cover letter body.", _USAGE))
    mocker.patch.object(db, "record_resume_usage", return_value={"id": 1})
    mocker.patch.object(db, "log_api_usage", return_value={"id": 1})
    mocker.patch("resume_agent.resume_build.fit_to_one_page", return_value=("/tmp/r.pdf", "standard"))
    mocker.patch("resume_agent.resume_build.convert_to_pdf", return_value="/tmp/cl.pdf")
    mocker.patch("resume_agent.resume_scrub.scrub_pdf_metadata")
    mocker.patch("resume_agent.resume_scrub.read_pdf_metadata_text", return_value="Microsoft Word")
    mocker.patch("resume_agent.resume_scrub.verify_no_fingerprints", return_value=[])
    _mock_pdf_checks(mocker)
    mocker.patch("resume_agent.resume_build.new_document")
    mocker.patch("builtins.open", mocker.mock_open(read_data=b"pdfbytes"))


def test_build_refuses_to_upload_when_pdf_embeds_a_substitute_font(mocker):
    _mock_happy_build(mocker)
    mocker.patch("resume_agent.resume_scrub.check_fonts",
                 return_value=["embedded font 'Carlito' is not allowed"])
    upload = mocker.patch.object(db, "upload_resume_file")
    with pytest.raises(resume_agent.LintFailedError, match="Carlito"):
        resume_agent.build(1)
    upload.assert_not_called()


def test_build_uploads_nothing_when_only_the_cover_letter_pdf_fails_verification(mocker):
    _mock_happy_build(mocker)
    mocker.patch("resume_agent.resume_scrub.check_fonts", side_effect=lambda path, allowed: (
        ["embedded font 'Carlito' is not allowed"] if path == "/tmp/cl.pdf" else []))
    upload = mocker.patch.object(db, "upload_resume_file")
    with pytest.raises(resume_agent.LintFailedError, match="cover letter PDF is not clean"):
        resume_agent.build(1)
    upload.assert_not_called()


def test_build_refuses_to_upload_when_rendered_text_has_invisible_characters(mocker):
    _mock_happy_build(mocker)
    mocker.patch("resume_agent.resume_build.pdf_text", return_value="Dear\u200b team")
    upload = mocker.patch.object(db, "upload_resume_file")
    with pytest.raises(resume_agent.LintFailedError, match="invisible"):
        resume_agent.build(1)
    upload.assert_not_called()


def test_build_allows_pypdf_control_characters_in_rendered_text(mocker):
    _mock_happy_build(mocker)
    mocker.patch("resume_agent.resume_build.pdf_text", return_value="Dear team\r\nBest\x0c")
    upload = mocker.patch.object(db, "upload_resume_file", side_effect=["a", "b"])
    mocker.patch.object(db, "set_resume_files", return_value={"id": 1})
    result = resume_agent.build(1)
    assert upload.call_count == 2
    assert result == {"resume_file_ref": "a", "cover_letter_file_ref": "b"}


def test_build_refuses_to_upload_when_rendered_text_has_tool_attribution(mocker):
    _mock_happy_build(mocker)
    mocker.patch("resume_agent.resume_build.pdf_text", return_value="Written by I am Claude")
    with pytest.raises(resume_agent.LintFailedError, match="attribution"):
        resume_agent.build(1)


def _mock_build_pipeline(mocker, fit):
    mocker.patch.object(db, "get_job_application", return_value=_JOB_WITH_STRATEGY)
    _mock_clean_data(mocker)
    mocker.patch.object(resume_agent, "_call_claude", return_value=("A clean cover letter body.", _USAGE))
    mocker.patch.object(db, "record_resume_usage", return_value={"id": 1})
    mocker.patch.object(db, "log_api_usage", return_value={"id": 1})
    mocker.patch("resume_agent.resume_build.fit_to_one_page", side_effect=fit)


def test_build_uses_a_per_build_workdir_and_removes_it_on_success(mocker):
    seen = {}

    def fit(strategy, master, docx_path, out_dir):
        seen["docx"], seen["dir"] = docx_path, out_dir
        assert os.path.isdir(out_dir)
        return ("/tmp/r.pdf", "standard")

    _mock_build_pipeline(mocker, fit)
    mocker.patch("resume_agent.resume_build.convert_to_pdf", return_value="/tmp/cl.pdf")
    mocker.patch("resume_agent.resume_scrub.scrub_pdf_metadata")
    mocker.patch("resume_agent.resume_scrub.read_pdf_metadata_text", return_value="Microsoft Word")
    mocker.patch("resume_agent.resume_scrub.verify_no_fingerprints", return_value=[])
    _mock_pdf_checks(mocker)
    mocker.patch("resume_agent.resume_build.new_document")
    mocker.patch("builtins.open", mocker.mock_open(read_data=b"pdfbytes"))
    mocker.patch.object(db, "upload_resume_file", side_effect=["a", "b"])
    mocker.patch.object(db, "set_resume_files", return_value={"id": 1})
    resume_agent.build(1)
    assert not seen["docx"].startswith("/tmp/resume_")
    assert seen["docx"] == os.path.join(seen["dir"], "resume.docx")
    assert not os.path.exists(seen["dir"])


def test_build_removes_its_workdir_when_it_raises(mocker):
    seen = {}

    def fit(strategy, master, docx_path, out_dir):
        seen["dir"] = out_dir
        raise resume_agent.resume_build.StillOverflowError("too long")

    _mock_build_pipeline(mocker, fit)
    with pytest.raises(resume_agent.resume_build.StillOverflowError):
        resume_agent.build(1)
    assert not os.path.exists(seen["dir"])


def test_build_raises_on_cover_letter_lint_violation_after_one_retry(mocker):
    mocker.patch.object(db, "get_job_application", return_value=_JOB_WITH_STRATEGY)
    _mock_clean_data(mocker)
    mocker.patch("resume_agent.resume_build.fit_to_one_page", return_value=("/tmp/r.pdf", "standard"))
    mocker.patch("resume_agent.resume_scrub.scrub_pdf_metadata")
    mocker.patch("resume_agent.resume_scrub.read_pdf_metadata_text", return_value="Microsoft Word")
    mocker.patch("resume_agent.resume_scrub.verify_no_fingerprints", return_value=[])
    _mock_pdf_checks(mocker)
    mocker.patch.object(db, "record_resume_usage", return_value={"id": 1})
    mocker.patch.object(db, "log_api_usage", return_value={"id": 1})
    # Cover letter always contains an em dash -- lint keeps failing across the one retry.
    mocker.patch.object(resume_agent, "_call_claude", return_value=("Bad letter — with an em dash.", _USAGE))
    with pytest.raises(resume_agent.LintFailedError):
        resume_agent.build(1)


def test_build_tracks_usage_for_the_cover_letter_call(mocker):
    mocker.patch.object(db, "get_job_application", return_value=_JOB_WITH_STRATEGY)
    _mock_clean_data(mocker)
    mocker.patch.object(resume_agent, "_call_claude", return_value=("A clean cover letter body.", _USAGE))
    track_usage = mocker.patch.object(db, "record_resume_usage", return_value={"id": 1})
    log_api_usage = mocker.patch.object(db, "log_api_usage", return_value={"id": 1})
    mocker.patch("resume_agent.resume_build.fit_to_one_page", return_value=("/tmp/r.pdf", "standard"))
    mocker.patch("resume_agent.resume_build.convert_to_pdf", return_value="/tmp/cl.pdf")
    mocker.patch("resume_agent.resume_scrub.scrub_pdf_metadata")
    mocker.patch("resume_agent.resume_scrub.read_pdf_metadata_text", return_value="Microsoft Word")
    mocker.patch("resume_agent.resume_scrub.verify_no_fingerprints", return_value=[])
    _mock_pdf_checks(mocker)
    mocker.patch("resume_agent.resume_build.new_document")
    mocker.patch("builtins.open", mocker.mock_open(read_data=b"pdfbytes"))
    mocker.patch.object(db, "upload_resume_file", side_effect=[
        "resumes/1/resume.pdf", "resumes/1/cover_letter.pdf",
    ])
    mocker.patch.object(db, "set_resume_files", return_value={"id": 1})

    resume_agent.build(1)

    track_usage.assert_called_once_with(1, 100, 50, pytest.approx(0.001050))
    log_api_usage.assert_called_once_with(
        module="resume_agent", action="cover_letter", model=config.RESUME_MODEL,
        input_tokens=100, output_tokens=50, cost_usd=pytest.approx(0.001050),
        contact_id=None, job_application_id=1, billing="api",
    )


def test_build_raises_lint_failed_error_when_resume_pdf_metadata_still_has_fingerprints(mocker):
    mocker.patch.object(db, "get_job_application", return_value=_JOB_WITH_STRATEGY)
    _mock_clean_data(mocker)
    mocker.patch("resume_agent.resume_build.fit_to_one_page", return_value=("/tmp/r.pdf", "standard"))
    mocker.patch("resume_agent.resume_scrub.scrub_pdf_metadata")
    mocker.patch("resume_agent.resume_scrub.read_pdf_metadata_text", return_value="Producer: LibreOffice 24.2")
    _mock_pdf_checks(mocker)
    with pytest.raises(resume_agent.LintFailedError, match="fingerprint"):
        resume_agent.build(1)


def test_build_still_overflow_error_propagates_after_retry(mocker):
    mocker.patch.object(db, "get_job_application", return_value=_JOB_WITH_STRATEGY)
    _mock_clean_data(mocker)
    mocker.patch(
        "resume_agent.resume_build.fit_to_one_page",
        side_effect=resume_agent.resume_build.StillOverflowError("still too long"),
    )
    with pytest.raises(resume_agent.resume_build.StillOverflowError):
        resume_agent.build(1)


# ── Backend dispatch ───────────────────────────────────────────────────────────

def test_call_claude_subscription_backend_uses_claude_subscription(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "subscription")
    complete = mocker.patch.object(claude_subscription, "complete", return_value=("txt", _USAGE))
    assert resume_agent._call_claude("p", system="s") == ("txt", _USAGE)
    complete.assert_called_once_with("p", system="s", model=config.RESUME_MODEL)


def test_call_claude_api_backend_sanitizes(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
    resp = mocker.MagicMock()
    resp.content = [mocker.MagicMock(text="Dear\u200b Ana")]
    resp.usage.input_tokens, resp.usage.output_tokens = 10, 5
    mocker.patch.object(resume_agent._claude.messages, "create", return_value=resp)
    assert resume_agent._call_claude("p") == ("Dear Ana", {"input_tokens": 10, "output_tokens": 5})


def test_call_claude_rejects_unknown_backend(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "carrier-pigeon")
    with pytest.raises(ValueError, match="RESUME_CLAUDE_BACKEND"):
        resume_agent._call_claude("p")


def test_track_usage_subscription_records_zero_cost_and_billing(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "subscription")
    record = mocker.patch.object(db, "record_resume_usage")
    log_api = mocker.patch.object(db, "log_api_usage")
    assert resume_agent._track_usage(9, {"input_tokens": 100, "output_tokens": 50}, "propose") == 0.0
    record.assert_called_once_with(9, 100, 50, 0.0)
    assert log_api.call_args.kwargs["billing"] == "subscription"
    assert log_api.call_args.kwargs["cost_usd"] == 0.0


# ── Attribution scan ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Dear team,\nI built things.\nGenerated with Claude",
    "Dear team,\nCo-Authored-By: Claude <noreply@anthropic.com>",
    "Dear team,\nAs an AI, I cannot attend interviews.",
    "Dear team,\nAs a language model I lack opinions.",
    "Here's a cover letter tailored to the role:\nDear team,",
    "Sure! Below is the letter.\nDear team,",
    "Dear team,\nI shipped a thing.\nLet me know if you'd like any changes!",
    "Dear team,\nThis letter was generated by ChatGPT.",
])
def test_check_attribution_flags(text):
    assert resume_agent._check_attribution(text)


@pytest.mark.parametrize("text", [
    "Dear Anthropic hiring team,\nI shipped Claude Code workflows and the Claude Vision API.\nBest,\nKishore",
    "Dear team,\nMy work on large language model evaluation cut costs 30%.\nBest,\nKishore",
    "Dear team,\nRevenue generated with outbound grew 30%.\nBest,\nKishore",
    "Dear team,\nI was an aide to the CFO.\nBest,\nKishore",
    "Dear team,\nHe has an aim.\nBest,\nKishore",
])
def test_check_attribution_allows_operator_facts(text):
    assert resume_agent._check_attribution(text) == []


def test_operator_resume_data_never_trips_the_attribution_scan():
    import json
    import os
    data_dir = os.path.join(os.path.dirname(resume_agent.__file__), "resume", "data")
    for name in os.listdir(data_dir):
        if name.endswith(".json"):
            with open(os.path.join(data_dir, name)) as f:
                text = json.dumps(json.load(f))
            assert resume_agent._check_attribution(text) == [], name


def test_propose_raises_on_attribution_in_strategy(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
    mocker.patch.object(db, "get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "posting_snapshot": {}})
    mocker.patch.object(resume_agent, "_call_claude", return_value=(
        '{"section_order": ["Experience"], "cover_letter_angle": "Generated with Claude"}', _USAGE))
    mocker.patch.object(db, "record_resume_usage")
    mocker.patch.object(db, "log_api_usage")
    set_strategy = mocker.patch.object(db, "set_resume_strategy")
    with pytest.raises(resume_agent.LintFailedError, match="generated with"):
        resume_agent.propose(1)
    set_strategy.assert_not_called()


def test_lint_cover_letter_flags_chat_preamble_so_build_regenerates(mocker):
    mocker.patch.object(resume_lint, "check_cover_letter", return_value=[])
    violations = resume_agent._lint_cover_letter("Here's your cover letter:\nDear team,", "resume text")
    assert any("preamble" in v for v in violations)


@pytest.mark.parametrize("label,ok", [
    ("Data & Tools", True),
    ("Product, Analytics", True),
    ("AI/ML Platforms", True),
    ("Strategic Leadership And Vision Setting", False),
    ("Tools I'm great at!", False),
    ("Generated with Claude", False),
])
def test_skills_group_labels_are_short_plain_and_unattributed(label, ok):
    skills = {"spine": ["SQL"], "swap_pool": [], "banned": []}
    strategy = {"skills_groups": [{"label": label, "skills": ["SQL"]}]}
    assert (resume_agent._check_skills_governance(skills, strategy) == []) is ok


# ── drain ──────────────────────────────────────────────────────────────────────

def _drain_ready(mocker):
    mocker.patch.object(resume_agent, "_worker_preflight", return_value=[])
    mocker.patch.object(resume_agent, "_check_deadline", return_value=True)


def test_drain_runs_propose_then_build_per_row(mocker):
    _drain_ready(mocker)
    mocker.patch.object(db, "get_strong_applications_without_resume",
                        return_value=[{"id": 1, "company": "A"}, {"id": 2, "company": "B", "resume_strategy": {"x": 1}}])
    propose = mocker.patch.object(resume_agent, "propose")
    build = mocker.patch.object(resume_agent, "build")
    set_error = mocker.patch.object(db, "set_resume_error")
    assert resume_agent.drain(limit=3) == 0
    propose.assert_called_once_with(1)
    assert [c.args[0] for c in build.call_args_list] == [1, 2]
    set_error.assert_not_called()


def test_drain_records_error_and_continues(mocker):
    _drain_ready(mocker)
    mocker.patch.object(db, "get_strong_applications_without_resume",
                        return_value=[{"id": 1, "company": "A"}, {"id": 2, "company": "B"}])
    mocker.patch.object(resume_agent, "propose", side_effect=[ValueError("bad json"), None])
    build = mocker.patch.object(resume_agent, "build")
    set_error = mocker.patch.object(db, "set_resume_error")
    assert resume_agent.drain() == 1
    set_error.assert_called_once()
    assert set_error.call_args.args[0] == 1 and "bad json" in set_error.call_args.args[1]
    build.assert_called_once_with(2)


def test_drain_marks_deadline_passed_rows(mocker):
    _drain_ready(mocker)
    mocker.patch.object(db, "get_strong_applications_without_resume", return_value=[{"id": 5, "company": "A"}])
    mocker.patch.object(resume_agent, "propose", side_effect=resume_agent.DeadlinePassedError("passed"))
    set_error = mocker.patch.object(db, "set_resume_error")
    assert resume_agent.drain() == 1
    set_error.assert_called_once()


def test_drain_stops_on_usage_limit_without_marking(mocker):
    _drain_ready(mocker)
    mocker.patch.object(db, "get_strong_applications_without_resume",
                        return_value=[{"id": 1, "company": "A"}, {"id": 2, "company": "B"}])
    propose = mocker.patch.object(resume_agent, "propose",
                                  side_effect=claude_subscription.ClaudeUsageLimitError("usage limit reached"))
    set_error = mocker.patch.object(db, "set_resume_error")
    assert resume_agent.drain() == 0
    propose.assert_called_once_with(1)
    set_error.assert_not_called()


def test_drain_survives_a_failing_error_write(mocker):
    _drain_ready(mocker)
    mocker.patch.object(db, "get_strong_applications_without_resume", return_value=[{"id": 1, "company": "A"}])
    mocker.patch.object(resume_agent, "propose", side_effect=ValueError("x"))
    mocker.patch.object(db, "set_resume_error", side_effect=RuntimeError("db down"))
    assert resume_agent.drain() == 1


def test_drain_uses_configured_batch_by_default(mocker):
    mocker.patch.object(resume_agent, "_worker_preflight", return_value=[])
    get = mocker.patch.object(db, "get_strong_applications_without_resume", return_value=[])
    resume_agent.drain()
    get.assert_called_once_with(config.RESUME_WORKER_BATCH)


def test_drain_preflight_failure_touches_no_rows(mocker):
    mocker.patch.object(resume_agent, "_worker_preflight", return_value=["CLAUDE_CODE_OAUTH_TOKEN is not set"])
    get = mocker.patch.object(db, "get_strong_applications_without_resume")
    set_error = mocker.patch.object(db, "set_resume_error")
    assert resume_agent.drain() == 1
    get.assert_not_called()
    set_error.assert_not_called()


def test_drain_transport_failure_stops_the_run_without_marking(mocker):
    _drain_ready(mocker)
    mocker.patch.object(db, "get_strong_applications_without_resume",
                        return_value=[{"id": 1, "company": "A"}, {"id": 2, "company": "B"}])
    propose = mocker.patch.object(resume_agent, "propose",
                                  side_effect=claude_subscription.ClaudeSubscriptionError("claude CLI failed (exit 1): auth"))
    set_error = mocker.patch.object(db, "set_resume_error")
    assert resume_agent.drain() == 1
    propose.assert_called_once_with(1)
    set_error.assert_not_called()


def test_drain_checks_the_deadline_even_when_a_strategy_exists(mocker):
    mocker.patch.object(resume_agent, "_worker_preflight", return_value=[])
    mocker.patch.object(db, "get_strong_applications_without_resume",
                        return_value=[{"id": 4, "company": "A", "resume_strategy": {"x": 1}}])
    mocker.patch.object(resume_agent, "_check_deadline", return_value=False)
    build = mocker.patch.object(resume_agent, "build")
    set_error = mocker.patch.object(db, "set_resume_error")
    assert resume_agent.drain() == 1
    build.assert_not_called()
    assert "deadline" in set_error.call_args.args[1].lower()


def test_worker_preflight_reports_missing_prereqs(mocker, monkeypatch):
    monkeypatch.setattr(config, "RESUME_CLAUDE_BACKEND", "subscription")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    mocker.patch("resume_agent.shutil.which", return_value=None)
    problems = resume_agent._worker_preflight()
    assert any("CLAUDE_CODE_OAUTH_TOKEN" in p for p in problems)
    assert any("soffice" in p for p in problems)


# ── propose: governance before persisting, usage before validating ─────────────

def _propose_with(mocker, strategy_json):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
    mocker.patch.object(db, "get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "posting_snapshot": {}})
    mocker.patch.object(resume_agent, "_call_claude", return_value=(strategy_json, _USAGE))
    mocker.patch.object(db, "record_resume_usage")
    mocker.patch.object(db, "log_api_usage")
    return mocker.patch.object(db, "set_resume_strategy")


@pytest.mark.parametrize("strategy_json", [
    '{"section_order": ["Experience"], "projects_included": ["A Project That Does Not Exist"]}',
    '{"section_order": ["Core Competencies"], "projects_included": []}',
    '{"section_order": ["Experience"], "projects_included": [], '
    '"skills_groups": [{"label": "Data & Tools", "skills": ["Definitely Not A Skill"]}]}',
])
def test_propose_rejects_ungoverned_strategies_without_persisting(mocker, strategy_json):
    set_strategy = _propose_with(mocker, strategy_json)
    with pytest.raises(resume_agent.LintFailedError, match="governance"):
        resume_agent.propose(1)
    set_strategy.assert_not_called()


def test_propose_rejects_a_bad_skills_label_without_persisting(mocker):
    set_strategy = _propose_with(mocker, '{"section_order": ["Experience"], "projects_included": [], '
                                         '"skills_groups": [{"label": "one two three four five", "skills": []}]}')
    with pytest.raises(resume_agent.LintFailedError, match="governance"):
        resume_agent.propose(1)
    set_strategy.assert_not_called()


def test_propose_records_usage_even_when_the_response_is_unparseable(mocker):
    _propose_with(mocker, "not json")
    track = mocker.patch.object(resume_agent, "_track_usage", return_value=0.0)
    with pytest.raises(ValueError):
        resume_agent.propose(1)
    track.assert_called_once_with(1, _USAGE, "propose")


# ── _worker_preflight canary ───────────────────────────────────────────────────

def _preflight_env(mocker, monkeypatch, backend="api"):
    monkeypatch.setattr(config, "RESUME_CLAUDE_BACKEND", backend)
    mocker.patch("resume_agent.shutil.which", return_value="/usr/bin/soffice")
    mocker.patch("resume_agent.resume_build.new_document")
    return mocker.patch("resume_agent.resume_build.convert_to_pdf", return_value="/tmp/canary.pdf")


def test_preflight_canary_success_reports_no_problem(mocker, monkeypatch):
    _preflight_env(mocker, monkeypatch)
    mocker.patch("resume_agent.resume_scrub.check_fonts", return_value=[])
    assert resume_agent._worker_preflight() == []


def test_preflight_canary_font_violation_is_a_problem(mocker, monkeypatch):
    _preflight_env(mocker, monkeypatch)
    mocker.patch("resume_agent.resume_scrub.check_fonts",
                 return_value=["embedded font 'Carlito' is not allowed"])
    problems = resume_agent._worker_preflight()
    assert any("PDF canary failed" in p and "Carlito" in p for p in problems)


def test_preflight_canary_conversion_failure_is_a_problem(mocker, monkeypatch):
    convert = _preflight_env(mocker, monkeypatch)
    convert.side_effect = RuntimeError("soffice exploded")
    problems = resume_agent._worker_preflight()
    assert any("PDF canary failed" in p and "soffice exploded" in p for p in problems)


def test_preflight_rejects_an_unknown_backend(mocker, monkeypatch):
    _preflight_env(mocker, monkeypatch, backend="bogus")
    mocker.patch("resume_agent.resume_scrub.check_fonts", return_value=[])
    assert any("RESUME_CLAUDE_BACKEND" in p for p in resume_agent._worker_preflight())
