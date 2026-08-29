"""Tests for resume_agent.py. All Claude calls and db.py calls are mocked -- no real network,
no real Supabase, no real credentials."""

import datetime

import pytest

import config
import db
import resume_agent


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
    result = resume_agent.propose(1)
    assert result["section_order"] == ["Experience", "Projects"]
    set_strategy.assert_called_once()
    args, kwargs = set_strategy.call_args
    assert args[0] == 1
    assert args[1]["cover_letter_angle"] == "test angle"
    track_usage.assert_called_once_with(1, 100, 50, pytest.approx(0.001050))


def test_propose_raises_on_malformed_claude_response(mocker):
    mocker.patch.object(db, "get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "posting_snapshot": {},
    })
    mocker.patch.object(resume_agent, "_call_claude", return_value=("not json", _USAGE))
    with pytest.raises(ValueError, match="could not parse strategy"):
        resume_agent.propose(1)


def test_propose_strips_markdown_json_fence_before_parsing(mocker):
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
    mocker.patch("resume_agent.resume_build.fit_to_one_page", return_value=("/tmp/r.pdf", "standard"))
    mocker.patch("resume_agent.resume_build.convert_to_pdf", return_value="/tmp/cl.pdf")
    mocker.patch("resume_agent.resume_scrub.scrub_pdf_metadata")
    mocker.patch("resume_agent.resume_scrub.read_pdf_metadata_text", return_value="Microsoft Word")
    mocker.patch("resume_agent.resume_scrub.verify_no_fingerprints", return_value=[])
    mocker.patch("resume_agent.Document")
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


def test_build_raises_on_cover_letter_lint_violation_after_one_retry(mocker):
    mocker.patch.object(db, "get_job_application", return_value=_JOB_WITH_STRATEGY)
    _mock_clean_data(mocker)
    mocker.patch("resume_agent.resume_build.fit_to_one_page", return_value=("/tmp/r.pdf", "standard"))
    mocker.patch("resume_agent.resume_scrub.scrub_pdf_metadata")
    mocker.patch("resume_agent.resume_scrub.read_pdf_metadata_text", return_value="Microsoft Word")
    mocker.patch("resume_agent.resume_scrub.verify_no_fingerprints", return_value=[])
    mocker.patch.object(db, "record_resume_usage", return_value={"id": 1})
    # Cover letter always contains an em dash -- lint keeps failing across the one retry.
    mocker.patch.object(resume_agent, "_call_claude", return_value=("Bad letter — with an em dash.", _USAGE))
    with pytest.raises(resume_agent.LintFailedError):
        resume_agent.build(1)


def test_build_tracks_usage_for_the_cover_letter_call(mocker):
    mocker.patch.object(db, "get_job_application", return_value=_JOB_WITH_STRATEGY)
    _mock_clean_data(mocker)
    mocker.patch.object(resume_agent, "_call_claude", return_value=("A clean cover letter body.", _USAGE))
    track_usage = mocker.patch.object(db, "record_resume_usage", return_value={"id": 1})
    mocker.patch("resume_agent.resume_build.fit_to_one_page", return_value=("/tmp/r.pdf", "standard"))
    mocker.patch("resume_agent.resume_build.convert_to_pdf", return_value="/tmp/cl.pdf")
    mocker.patch("resume_agent.resume_scrub.scrub_pdf_metadata")
    mocker.patch("resume_agent.resume_scrub.read_pdf_metadata_text", return_value="Microsoft Word")
    mocker.patch("resume_agent.resume_scrub.verify_no_fingerprints", return_value=[])
    mocker.patch("resume_agent.Document")
    mocker.patch("builtins.open", mocker.mock_open(read_data=b"pdfbytes"))
    mocker.patch.object(db, "upload_resume_file", side_effect=[
        "resumes/1/resume.pdf", "resumes/1/cover_letter.pdf",
    ])
    mocker.patch.object(db, "set_resume_files", return_value={"id": 1})

    resume_agent.build(1)

    track_usage.assert_called_once_with(1, 100, 50, pytest.approx(0.001050))


def test_build_raises_lint_failed_error_when_resume_pdf_metadata_still_has_fingerprints(mocker):
    mocker.patch.object(db, "get_job_application", return_value=_JOB_WITH_STRATEGY)
    _mock_clean_data(mocker)
    mocker.patch("resume_agent.resume_build.fit_to_one_page", return_value=("/tmp/r.pdf", "standard"))
    mocker.patch("resume_agent.resume_scrub.scrub_pdf_metadata")
    mocker.patch("resume_agent.resume_scrub.read_pdf_metadata_text", return_value="Producer: LibreOffice 24.2")
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
