"""Tests for job_discovery.py."""

from unittest.mock import MagicMock

import pytest

import db
import job_discovery


@pytest.fixture(autouse=True)
def no_real_calls(mocker):
    mocker.patch.object(db, "record_run")


def test_company_universe_dedupes_case_insensitively(mocker):
    mocker.patch.object(db, "get_all_contacts", return_value=[
        {"company": "Acme"}, {"company": "acme"}, {"company": ""}, {"company": None},
    ])
    mocker.patch.object(db, "get_all_company_intel_names", return_value=["ACME", "Globex"])
    universe = job_discovery._company_universe()
    assert universe == ["Acme", "Globex"]


def test_target_role_token_sets_splits_lines_and_drops_blank_lines(mocker):
    result = job_discovery._target_role_token_sets("Product Manager\n\n  Data Scientist  \n")
    assert result == [{"product", "manager"}, {"data", "scientist"}]


def test_target_role_token_sets_handles_none_and_empty(mocker):
    assert job_discovery._target_role_token_sets(None) == []
    assert job_discovery._target_role_token_sets("") == []


def test_matches_target_roles_true_on_any_token_overlap():
    job = {"title": "Senior Product Manager, Growth"}
    role_token_sets = [{"product", "manager"}]
    assert job_discovery._matches_target_roles(job, role_token_sets) is True


def test_matches_target_roles_false_on_no_overlap():
    job = {"title": "Staff Software Engineer"}
    role_token_sets = [{"product", "manager"}]
    assert job_discovery._matches_target_roles(job, role_token_sets) is False


def test_matches_target_roles_true_for_everything_when_no_roles_configured():
    job = {"title": "Anything At All"}
    assert job_discovery._matches_target_roles(job, []) is True


def test_run_persists_matching_jobs_and_records_run(mocker):
    mocker.patch.object(db, "load_prompts", return_value={"target_roles": "Product Manager"})
    mocker.patch.object(job_discovery, "_company_universe", return_value=["Acme"])
    mocker.patch.object(job_discovery.ats, "fetch_jobs", return_value=[
        {"title": "Product Manager", "url": "https://x/1", "location": "", "description": "", "source": "greenhouse"},
        {"title": "Software Engineer", "url": "https://x/2", "location": "", "description": "", "source": "greenhouse"},
    ])
    create = mocker.patch.object(db, "create_job_application", return_value={"id": 1})
    job_discovery.run()
    assert create.call_count == 1
    _, kwargs = create.call_args
    assert kwargs["company"] == "Acme"
    assert kwargs["role"] == "Product Manager"
    assert kwargs["job_url"] == "https://x/1"
    assert kwargs["source"] == "ats_scan"
    db.record_run.assert_called_once()
    assert db.record_run.call_args.kwargs["source"] == "job_discovery"


def test_run_counts_dedup_skip_separately_from_saved(mocker):
    mocker.patch.object(db, "load_prompts", return_value={"target_roles": ""})
    mocker.patch.object(job_discovery, "_company_universe", return_value=["Acme"])
    mocker.patch.object(job_discovery.ats, "fetch_jobs", return_value=[
        {"title": "Anything", "url": "https://x/1", "location": "", "description": "", "source": "greenhouse"},
    ])
    mocker.patch.object(db, "create_job_application", return_value=None)
    job_discovery.run()
    args, kwargs = db.record_run.call_args
    assert args[0] == "success"
    assert args[1] == 0   # saved
    assert args[2] == 1   # skipped (dedup)
    assert args[3] == 0   # errors


def test_run_isolates_one_companys_ats_failure_from_the_rest(mocker):
    mocker.patch.object(db, "load_prompts", return_value={"target_roles": ""})
    mocker.patch.object(job_discovery, "_company_universe", return_value=["Broken", "Acme"])

    def fake_fetch(company, max_jobs=None):
        if company == "Broken":
            raise RuntimeError("boom")
        return [{"title": "Anything", "url": "https://x/1", "location": "", "description": "", "source": "greenhouse"}]

    mocker.patch.object(job_discovery.ats, "fetch_jobs", side_effect=fake_fetch)
    mocker.patch.object(db, "create_job_application", return_value={"id": 1})
    job_discovery.run()
    args, kwargs = db.record_run.call_args
    assert args[1] == 1   # saved (Acme still processed)
    assert args[3] == 1   # errors (Broken counted, did not stop the run)


def test_run_isolates_one_postings_persist_failure_from_the_rest(mocker):
    mocker.patch.object(db, "load_prompts", return_value={"target_roles": ""})
    mocker.patch.object(job_discovery, "_company_universe", return_value=["Acme"])
    mocker.patch.object(job_discovery.ats, "fetch_jobs", return_value=[
        {"title": "First", "url": "https://x/1", "location": "", "description": "", "source": "greenhouse"},
        {"title": "Second", "url": "https://x/2", "location": "", "description": "", "source": "greenhouse"},
    ])
    mocker.patch.object(db, "create_job_application", side_effect=[RuntimeError("boom"), {"id": 2}])
    job_discovery.run()
    args, kwargs = db.record_run.call_args
    assert args[1] == 1   # saved (Second succeeded)
    assert args[3] == 1   # errors (First's persist failure counted, did not stop Second)


def test_run_with_empty_universe_records_success_and_does_nothing(mocker):
    mocker.patch.object(db, "load_prompts", return_value={"target_roles": ""})
    mocker.patch.object(job_discovery, "_company_universe", return_value=[])
    create = mocker.patch.object(db, "create_job_application")
    job_discovery.run()
    create.assert_not_called()
    args, kwargs = db.record_run.call_args
    assert args[0] == "success"


def test_run_survives_load_prompts_failure(mocker):
    mocker.patch.object(db, "load_prompts", side_effect=RuntimeError("supabase down"))
    mocker.patch.object(job_discovery, "_company_universe", return_value=["Acme"])
    mocker.patch.object(job_discovery.ats, "fetch_jobs", return_value=[
        {"title": "Anything", "url": "https://x/1", "location": "", "description": "", "source": "greenhouse"},
    ])
    mocker.patch.object(db, "create_job_application", return_value={"id": 1})
    job_discovery.run()   # must not raise
    args, kwargs = db.record_run.call_args
    assert args[0] == "success"
    assert args[1] == 1   # target_roles defaulted to "match everything" on load_prompts failure


def test_run_survives_company_universe_failure(mocker):
    mocker.patch.object(db, "load_prompts", return_value={})
    mocker.patch.object(job_discovery, "_company_universe", side_effect=RuntimeError("supabase down"))
    job_discovery.run()   # must not raise
    args, kwargs = db.record_run.call_args
    assert args[0] == "failure"
