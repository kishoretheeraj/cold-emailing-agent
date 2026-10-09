"""Tests for scripts/apply_dryrun.py -- a read-only probe of real application forms run from
GitHub Actions. Load-bearing properties: it never writes to the database, never clicks a
submit-like button, never calls Claude, refuses to run when APPLY_AGENT_ARMED is set at all,
and prints identifying detail (company, page HTML) only when the repo is confirmed private."""

import base64
import gzip
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "scripts")
import apply_dryrun  # noqa: E402

_WRITES = ["claim_application", "heartbeat_application", "renew_submission_lease",
           "complete_preview", "release_application", "record_submission",
           "recover_stale_leases", "mark_unsupported", "set_apply_blocked",
           "update_job_application_stage", "create_job_application", "set_resume_files",
           "set_resume_error", "set_pick_verdict", "upsert_prompt", "log_api_usage"]


def _job(i=7, url="https://job-boards.greenhouse.io/acme/jobs/123", **kw):
    job = {"id": i, "company": "AcmeSecret", "role": "APM", "job_url": url,
           "resume_file_ref": "r.pdf", "cover_letter_file_ref": "c.pdf"}
    job.update(kw)
    return job


@pytest.fixture
def harness(mocker):
    for name in _WRITES:
        mocker.patch(f"db.{name}", side_effect=AssertionError(f"dry run must never call db.{name}"))
    claude = mocker.patch("apply_agent._call_claude", side_effect=AssertionError("dry run must never call Claude"))
    page = MagicMock()
    page.content.return_value = "<html><form><input name='first_name'></form></html>"
    mocker.patch("apply_agent._launch_page", return_value=page)
    close = mocker.patch("apply_agent._close_page")
    mocker.patch("apply_agent._form_signature", return_value="sig123")
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value={"resume": True, "cover_letter": True})
    mocker.patch("apply_agent._eligibility_answers", return_value={"gender": "Decline"})
    mocker.patch("apply_agent._fill_eligibility_answers", return_value={"Gender": True})
    mocker.patch("apply_agent._form_inventory", return_value=[
        {"label": "First Name", "kind": "input", "required": True, "filled": True, "options": []},
        {"label": "Desired salary", "kind": "input", "required": True, "filled": False, "options": []},
    ])
    fill = mocker.patch("ats_fillers.fill_greenhouse", return_value={"first_name": True, "last_name": True, "email": True})
    mocker.patch.dict("os.environ", {}, clear=False)
    import os
    os.environ.pop("APPLY_AGENT_ARMED", None)
    return {"page": page, "close": close, "fill": fill, "claude": claude}


def test_probe_reports_fill_results_and_missing_required_without_writing(harness, mocker):
    mocker.patch("db.get_job_application", return_value=_job())
    report = apply_dryrun.probe(7)
    assert report["platform"] == "greenhouse"
    assert report["signature"] is True
    assert report["fields"]["email"] is True
    assert report["required_unfilled"] == ["Desired salary"]
    assert "Desired salary" in report["missing"]
    harness["close"].assert_called_once()
    harness["claude"].assert_not_called()


def test_probe_never_clicks_a_button(harness, mocker):
    mocker.patch("db.get_job_application", return_value=_job())
    apply_dryrun.probe(7)
    page = harness["page"]
    for call in page.get_by_role.call_args_list:
        assert call.args[:1] != ("button",), call
    page.click.assert_not_called()


def test_probe_skips_excluded_platforms_without_opening_a_browser(harness, mocker):
    mocker.patch("db.get_job_application", return_value=_job(url="https://acme.wd5.myworkdayjobs.com/x"))
    report = apply_dryrun.probe(7)
    assert report["skipped"] == "workday"
    apply_dryrun.apply_agent._launch_page.assert_not_called()


def test_generic_platform_reports_inventory_without_any_ai_fill(harness, mocker):
    mocker.patch("db.get_job_application", return_value=_job(url="https://careers.example.com/apply/1"))
    browser_use = mocker.patch("apply_agent._fill_generic_via_browser_use",
                               side_effect=AssertionError("dry run must never run browser-use"))
    report = apply_dryrun.probe(7)
    assert report["platform"] == "generic"
    assert report["fields"] is None
    assert report["inventory"]
    browser_use.assert_not_called()


def test_probe_closes_the_page_even_when_filling_raises(harness, mocker):
    mocker.patch("db.get_job_application", return_value=_job())
    harness["fill"].side_effect = RuntimeError("selector exploded")
    report = apply_dryrun.probe(7)
    assert "selector exploded" in report["error"]
    harness["close"].assert_called_once()


def test_missing_row_is_reported_not_raised(harness, mocker):
    mocker.patch("db.get_job_application", return_value=None)
    assert apply_dryrun.probe(99)["error"]


@pytest.mark.parametrize("raw,ok", [
    ("7", [7]), ("7,8, 9", [7, 8, 9]), (" 12 ", [12]),
    ("7;rm -rf /", None), ("", None), ("abc", None), ("1," * 30 + "1", None),
])
def test_parse_ids_accepts_only_short_lists_of_integers(raw, ok):
    if ok is None:
        with pytest.raises(ValueError):
            apply_dryrun.parse_ids(raw)
    else:
        assert apply_dryrun.parse_ids(raw) == ok


def test_render_hides_company_and_html_unless_private(harness, mocker):
    mocker.patch("db.get_job_application", return_value=_job())
    report = apply_dryrun.probe(7, dump_html=True)
    public = "\n".join(apply_dryrun.render(report, detail=False))
    private = "\n".join(apply_dryrun.render(report, detail=True))
    assert "AcmeSecret" not in public and "HTML-BEGIN" not in public
    assert "AcmeSecret" in private and "HTML-BEGIN" in private
    # Field labels (form structure) are fine in both.
    assert "Desired salary" in public


def test_public_render_hides_error_text_that_quotes_the_url(harness, mocker):
    mocker.patch("db.get_job_application", return_value=_job())
    harness["fill"].side_effect = TimeoutError("navigating to https://job-boards.greenhouse.io/acmesecret/jobs/1")
    report = apply_dryrun.probe(7)
    public = "\n".join(apply_dryrun.render(report, detail=False))
    assert "acmesecret" not in public.lower()
    assert "TimeoutError" in public
    assert "acmesecret" in "\n".join(apply_dryrun.render(report, detail=True)).lower()


def test_html_dump_round_trips(harness, mocker):
    mocker.patch("db.get_job_application", return_value=_job())
    report = apply_dryrun.probe(7, dump_html=True)
    lines = apply_dryrun.render(report, detail=True)
    start = lines.index(next(line for line in lines if line.startswith("HTML-BEGIN")))
    end = lines.index(next(line for line in lines if line.startswith("HTML-END")))
    blob = "".join(lines[start + 1:end])
    assert b"first_name" in gzip.decompress(base64.b64decode(blob))


def test_main_refuses_when_armed_is_set_to_anything(harness, mocker, capsys):
    mocker.patch("db.get_job_application", return_value=_job())
    for value in ("1", "0", ""):
        mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": value, "JOB_IDS": "7"})
        assert apply_dryrun.main([]) == 2
    apply_dryrun.apply_agent._launch_page.assert_not_called()


def test_main_runs_each_id_and_exits_zero(harness, mocker, capsys):
    mocker.patch("db.get_job_application", side_effect=lambda i: _job(i))
    mocker.patch.dict("os.environ", {"JOB_IDS": "7,8", "REPO_PRIVATE": "false"})
    assert apply_dryrun.main([]) == 0
    out = capsys.readouterr().out
    assert out.count("== job #") == 2
    assert "AcmeSecret" not in out


def test_main_rejects_bad_ids(harness, mocker):
    mocker.patch.dict("os.environ", {"JOB_IDS": "1;ls"})
    assert apply_dryrun.main([]) == 2
