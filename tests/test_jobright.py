"""Tests for jobright.py. All HTTP is mocked -- no real network calls, no real credentials."""

import json
import urllib.error
from unittest.mock import MagicMock

import pytest

import config
import db
import jobright


def _fake_response(body_dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(body_dict).encode("utf-8")
    ctx = MagicMock()
    ctx.__enter__.return_value = resp
    ctx.__exit__.return_value = False
    return ctx


@pytest.fixture(autouse=True)
def no_real_calls(mocker):
    mocker.patch.object(db, "record_run")
    mocker.patch.object(config, "JOBRIGHT_EMAIL", "test@example.com")
    mocker.patch.object(config, "JOBRIGHT_PASSWORD", "test-password")
    mocker.patch.object(config, "JOBRIGHT_RETRY_BACKOFF_SECONDS", 0)
    mocker.patch.object(config, "JOBRIGHT_PAGE_DELAY_SECONDS", 0)


# ── _job_from_result ──────────────────────────────────────────────────────────

def test_job_from_result_builds_expected_shape():
    entry = {
        "jobResult": {
            "jobTitle": "Product Manager",
            "jobLocation": "Remote",
            "originalUrl": "https://example.com/job/1",
            "applyLink": "https://example.com/apply/1",
            "jobSummary": "Own the roadmap.",
        },
        "companyResult": {"companyName": "Acme"},
    }
    job = jobright._job_from_result(entry)
    assert job == {
        "title": "Product Manager",
        "location": "Remote",
        "url": "https://example.com/job/1",
        "description": "Own the roadmap.",
        "company": "Acme",
        "source": "jobright",
    }


def test_job_from_result_falls_back_to_apply_link_when_no_original_url():
    entry = {
        "jobResult": {"jobTitle": "PM", "applyLink": "https://example.com/apply/2"},
        "companyResult": {},
    }
    job = jobright._job_from_result(entry)
    assert job["url"] == "https://example.com/apply/2"
    assert job["company"] == ""


def test_job_from_result_returns_none_when_title_missing():
    assert jobright._job_from_result({"jobResult": {}, "companyResult": {}}) is None
    assert jobright._job_from_result({}) is None


# ── _request retry/backoff ────────────────────────────────────────────────────

def test_request_retries_on_5xx_then_succeeds(mocker):
    opener = MagicMock()
    opener.open.side_effect = [
        urllib.error.HTTPError("http://x", 500, "Server Error", None, None),
        _fake_response({"ok": True}),
    ]
    result = jobright._request(opener, "GET", "/swan/auth/newinfo")
    assert result == {"ok": True}
    assert opener.open.call_count == 2


def test_request_does_not_retry_on_4xx(mocker):
    opener = MagicMock()
    opener.open.side_effect = urllib.error.HTTPError("http://x", 401, "Unauthorized", None, None)
    with pytest.raises(urllib.error.HTTPError):
        jobright._request(opener, "GET", "/swan/auth/newinfo")
    assert opener.open.call_count == 1


def test_request_raises_after_max_attempts_on_repeated_failure(mocker):
    mocker.patch.object(config, "JOBRIGHT_MAX_ATTEMPTS", 2)
    opener = MagicMock()
    opener.open.side_effect = urllib.error.URLError("dns failure")
    with pytest.raises(urllib.error.URLError):
        jobright._request(opener, "GET", "/swan/auth/newinfo")
    assert opener.open.call_count == 2


# ── _login / _session_is_valid ────────────────────────────────────────────────

def test_login_returns_true_on_success(mocker):
    mocker.patch.object(jobright, "_request", return_value={"success": True})
    assert jobright._login(MagicMock(), "e", "p") is True


def test_login_returns_false_on_unsuccessful_response(mocker):
    mocker.patch.object(jobright, "_request", return_value={"success": False})
    assert jobright._login(MagicMock(), "e", "p") is False


def test_session_is_valid_reads_logined_field(mocker):
    mocker.patch.object(jobright, "_request", return_value={"result": {"logined": True}})
    assert jobright._session_is_valid(MagicMock()) is True


def test_session_is_valid_false_on_missing_result(mocker):
    mocker.patch.object(jobright, "_request", return_value={})
    assert jobright._session_is_valid(MagicMock()) is False


# ── fetch_recommended_jobs ────────────────────────────────────────────────────

def test_fetch_recommended_jobs_returns_empty_when_credentials_missing(mocker):
    mocker.patch.object(config, "JOBRIGHT_EMAIL", None)
    assert jobright.fetch_recommended_jobs() == []


def test_fetch_recommended_jobs_returns_empty_when_login_fails(mocker):
    mocker.patch.object(jobright, "_login", return_value=False)
    assert jobright.fetch_recommended_jobs() == []


def test_fetch_recommended_jobs_returns_empty_when_session_check_fails(mocker):
    mocker.patch.object(jobright, "_login", return_value=True)
    mocker.patch.object(jobright, "_session_is_valid", return_value=False)
    assert jobright.fetch_recommended_jobs() == []


def test_fetch_recommended_jobs_paginates_until_short_page(mocker):
    mocker.patch.object(config, "JOBRIGHT_PAGE_SIZE", 2)
    mocker.patch.object(config, "JOBRIGHT_MAX_JOBS", 100)
    mocker.patch.object(jobright, "_login", return_value=True)
    mocker.patch.object(jobright, "_session_is_valid", return_value=True)
    full_page = [
        {"jobResult": {"jobTitle": "A", "originalUrl": "u1"}, "companyResult": {"companyName": "C1"}},
        {"jobResult": {"jobTitle": "B", "originalUrl": "u2"}, "companyResult": {"companyName": "C2"}},
    ]
    short_page = [
        {"jobResult": {"jobTitle": "C", "originalUrl": "u3"}, "companyResult": {"companyName": "C3"}},
    ]
    mocker.patch.object(jobright, "_request", side_effect=[
        {"result": {"jobList": full_page}},              # page 1 (full -- keep paging)
        {"result": {"jobList": short_page}},              # page 2 (short -- stop)
    ])
    jobs = jobright.fetch_recommended_jobs()
    assert [j["title"] for j in jobs] == ["A", "B", "C"]


def test_fetch_recommended_jobs_stops_at_max_jobs_cap(mocker):
    mocker.patch.object(config, "JOBRIGHT_PAGE_SIZE", 2)
    mocker.patch.object(config, "JOBRIGHT_MAX_JOBS", 2)
    mocker.patch.object(jobright, "_login", return_value=True)
    mocker.patch.object(jobright, "_session_is_valid", return_value=True)
    full_page = [
        {"jobResult": {"jobTitle": "A", "originalUrl": "u1"}, "companyResult": {}},
        {"jobResult": {"jobTitle": "B", "originalUrl": "u2"}, "companyResult": {}},
    ]
    request = mocker.patch.object(jobright, "_request", side_effect=[
        {"result": {"jobList": full_page}},
    ])
    jobs = jobright.fetch_recommended_jobs()
    assert len(jobs) == 2
    assert request.call_count == 1   # exactly one page, not a second, once the cap is reached


def test_fetch_recommended_jobs_never_raises_on_unexpected_error(mocker):
    mocker.patch.object(jobright, "_login", side_effect=RuntimeError("boom"))
    assert jobright.fetch_recommended_jobs() == []


# ── run() ──────────────────────────────────────────────────────────────────────

def test_run_persists_jobs_and_records_run(mocker):
    mocker.patch.object(jobright, "fetch_recommended_jobs", return_value=[
        {"title": "PM", "location": "Remote", "url": "https://x/1", "description": "", "company": "Acme", "source": "jobright"},
    ])
    create = mocker.patch.object(db, "create_job_application", return_value={"id": 1})
    jobright.run()
    assert create.call_count == 1
    _, kwargs = create.call_args
    assert kwargs["company"] == "Acme"
    assert kwargs["role"] == "PM"
    assert kwargs["job_url"] == "https://x/1"
    assert kwargs["source"] == "jobright"
    db.record_run.assert_called_once()
    assert db.record_run.call_args.kwargs["source"] == "jobright"


def test_run_falls_back_to_unknown_company_when_blank(mocker):
    mocker.patch.object(jobright, "fetch_recommended_jobs", return_value=[
        {"title": "PM", "location": "", "url": "https://x/1", "description": "", "company": "", "source": "jobright"},
    ])
    create = mocker.patch.object(db, "create_job_application", return_value={"id": 1})
    jobright.run()
    assert create.call_args.kwargs["company"] == "Unknown"


def test_run_counts_dedup_skip_separately_from_saved(mocker):
    mocker.patch.object(jobright, "fetch_recommended_jobs", return_value=[
        {"title": "PM", "location": "", "url": "https://x/1", "description": "", "company": "Acme", "source": "jobright"},
    ])
    mocker.patch.object(db, "create_job_application", return_value=None)
    jobright.run()
    args, kwargs = db.record_run.call_args
    assert args[0] == "success"
    assert args[1] == 0   # saved
    assert args[2] == 1   # skipped (dedup)
    assert args[3] == 0   # errors


def test_run_isolates_one_postings_persist_failure_from_the_rest(mocker):
    mocker.patch.object(jobright, "fetch_recommended_jobs", return_value=[
        {"title": "First", "location": "", "url": "https://x/1", "description": "", "company": "Acme", "source": "jobright"},
        {"title": "Second", "location": "", "url": "https://x/2", "description": "", "company": "Acme", "source": "jobright"},
    ])
    mocker.patch.object(db, "create_job_application", side_effect=[RuntimeError("boom"), {"id": 2}])
    jobright.run()
    args, kwargs = db.record_run.call_args
    assert args[1] == 1   # saved (Second succeeded)
    assert args[3] == 1   # errors (First's persist failure counted, did not stop Second)


def test_run_with_no_jobs_records_success_and_does_nothing(mocker):
    mocker.patch.object(jobright, "fetch_recommended_jobs", return_value=[])
    create = mocker.patch.object(db, "create_job_application")
    jobright.run()
    create.assert_not_called()
    args, kwargs = db.record_run.call_args
    assert args[0] == "success"
