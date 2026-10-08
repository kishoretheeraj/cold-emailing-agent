"""Tests for scripts/apply_status.py -- the read-only pipeline snapshot printed by the
apply_status workflow. Privacy is the load-bearing property: GitHub Actions logs are readable by
anyone when the repo is public, so per-row detail (company, role, reasons) and eligibility
answer values must never print unless the repo is confirmed private."""

import json
import sys

sys.path.insert(0, "scripts")
import apply_status  # noqa: E402


def _row(i, **kw):
    row = {
        "id": i,
        "company": f"Company{i}",
        "role": f"Role{i}",
        "job_url": f"https://job-boards.greenhouse.io/c{i}/jobs/{i}",
        "stage": "saved",
        "automation_status": "idle",
        "pick_verdict": "strong",
        "resume_file_ref": f"r/{i}.pdf",
        "cover_letter_file_ref": f"c/{i}.pdf",
        "resume_error": None,
        "apply_blocked_reason": None,
    }
    row.update(kw)
    return row


_ELIGIBILITY = json.dumps({"work_authorized_us": "Yes", "gender": "SECRET-VALUE", "salary": "120000"})


def _text(rows, detail, eligibility=_ELIGIBILITY):
    return "\n".join(apply_status.summarize(rows, eligibility, detail=detail))


def test_counts_by_automation_status_and_stage():
    rows = [_row(1), _row(2, automation_status="ready_for_review", stage="ready_to_submit"),
            _row(3, automation_status="ready_for_review", stage="ready_to_submit")]
    text = _text(rows, detail=False)
    assert "ready_for_review: 2" in text
    assert "idle: 1" in text
    assert "ready_to_submit: 2" in text


def test_public_repo_never_prints_company_role_or_reason():
    rows = [_row(1, automation_status="needs_input", apply_blocked_reason="Preview couldn't fill: Salary")]
    text = _text(rows, detail=False)
    assert "Company1" not in text
    assert "Role1" not in text
    assert "Salary" not in text
    assert "greenhouse.io/c1" not in text


def test_private_repo_prints_per_row_detail():
    rows = [_row(1, automation_status="needs_input", apply_blocked_reason="Preview couldn't fill: Salary")]
    text = _text(rows, detail=True)
    assert "Company1" in text
    assert "Role1" in text
    assert "Salary" in text


def test_eligibility_values_never_print_even_in_detail_mode():
    text = _text([_row(1)], detail=True)
    assert "SECRET-VALUE" not in text
    assert "120000" not in text
    assert "gender" in text  # key names are fine


def test_missing_expected_eligibility_keys_are_listed():
    text = _text([_row(1)], detail=False, eligibility=json.dumps({"gender": "x"}))
    missing_line = next(line for line in text.splitlines() if "missing" in line.lower())
    assert "work_authorized_us" in missing_line
    assert "requires_visa_sponsorship" in missing_line


def test_unparseable_or_absent_eligibility_is_reported_not_raised():
    assert "unparseable" in _text([_row(1)], detail=False, eligibility="{not json").lower()
    assert "not set" in _text([_row(1)], detail=False, eligibility=None).lower()


def test_funnel_counts_only_supported_platforms():
    rows = [
        _row(1),                                                         # strong + docs
        _row(2, resume_file_ref=None),                                   # strong, no resume
        _row(3, pick_verdict="maybe"),                                   # not strong
        _row(4, job_url="https://acme.wd5.myworkdayjobs.com/x"),         # workday: excluded
        _row(5, resume_error="lint failed", resume_file_ref=None,
             cover_letter_file_ref=None),                                # strong, resume build failed
    ]
    funnel = apply_status.funnel(rows)
    assert funnel["supported_strong"] == 3
    assert funnel["with_documents"] == 1
    assert funnel["resume_error"] == 1


def test_submitted_count_uses_automation_status_and_stage():
    rows = [_row(1, automation_status="submitted", stage="applied"),
            _row(2, automation_status="needs_confirmation"), _row(3)]
    assert apply_status.funnel(rows)["submitted"] == 1
    assert apply_status.funnel(rows)["needs_confirmation"] == 1


def test_malformed_rows_do_not_crash():
    rows = [{}, {"id": None, "job_url": None, "automation_status": None}, _row(1)]
    assert apply_status.summarize(rows, None, detail=True)


def test_detail_text_is_single_line_and_bounded():
    rows = [_row(1, company="Evil\nCo", apply_blocked_reason="x" * 5000, automation_status="needs_input")]
    text = _text(rows, detail=True)
    assert "Evil Co" in text
    assert max(len(line) for line in text.splitlines()) < 400


def test_main_only_enables_detail_for_exact_true(mocker, capsys):
    mocker.patch("db.get_job_applications", return_value=[_row(1)])
    mocker.patch("db.load_prompts", return_value={"applicant_eligibility": _ELIGIBILITY})
    for value in ("True", "1", "yes", ""):
        mocker.patch.dict("os.environ", {"REPO_PRIVATE": value})
        assert apply_status.main() == 0
        assert "Company1" not in capsys.readouterr().out
    mocker.patch.dict("os.environ", {"REPO_PRIVATE": "true"})
    assert apply_status.main() == 0
    assert "Company1" in capsys.readouterr().out


def test_main_returns_nonzero_when_the_database_read_fails(mocker, capsys):
    mocker.patch("db.get_job_applications", side_effect=RuntimeError("boom"))
    mocker.patch("db.load_prompts", return_value={})
    assert apply_status.main() == 1
