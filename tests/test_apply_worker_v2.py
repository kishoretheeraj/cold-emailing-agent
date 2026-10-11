import pytest

import apply_worker
import claude_subscription
import config
from ats_api import resolve
from resume.v2 import delivery, tailor_v2

JOB = {"id": 5, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/acme/jobs/1",
       "posting_snapshot": {"description": "snapshot text"}}


@pytest.fixture
def v2(mocker):
    mocker.patch.object(config, "APPLY_RESUME_V2", True)
    mocker.patch.object(resolve, "fetch_posting", return_value={"description": "live jd"})
    mocker.patch.object(tailor_v2, "load_candidate_profile", return_value={"bullets": [1]})
    tailor = mocker.patch.object(tailor_v2, "tailor_resume",
                                 return_value={"pdf_path": "/tmp/x.pdf", "content_json": {}, "violations": []})
    mocker.patch.object(delivery, "generate_cover_letter", return_value="letter")
    mocker.patch.object(delivery, "render_cover_letter_pdf", return_value="/tmp/cl.pdf")
    mocker.patch.object(delivery, "upload_pdf", side_effect=[("r-ref", "r-sha"), ("c-ref", "c-sha")])
    save = mocker.patch.object(apply_worker.db, "set_resume_files")
    return tailor, save


def test_flag_off_does_nothing(mocker):
    mocker.patch.object(config, "APPLY_RESUME_V2", False)
    fetch = mocker.patch.object(resolve, "fetch_posting")
    assert apply_worker._prepare_documents(JOB) is False
    fetch.assert_not_called()


def test_row_with_documents_is_left_alone(v2):
    tailor, save = v2
    assert apply_worker._prepare_documents({**JOB, "resume_file_ref": "x"}) is False
    tailor.assert_not_called()


def test_happy_path_tailors_from_the_ats_posting_and_saves_refs(v2):
    tailor, save = v2
    assert apply_worker._prepare_documents(JOB) is True
    assert tailor.call_args.args[0] == "live jd"
    save.assert_called_once_with(5, resume_file_ref="r-ref", cover_letter_file_ref="c-ref",
                                resume_sha256="r-sha", cover_letter_sha256="c-sha")


def test_falls_back_to_snapshot_when_ats_api_misses(v2, mocker):
    tailor, _ = v2
    mocker.patch.object(resolve, "fetch_posting", return_value=None)
    assert apply_worker._prepare_documents(JOB) is True
    assert tailor.call_args.args[0] == "snapshot text"


def test_no_description_anywhere_skips(v2, mocker):
    tailor, _ = v2
    mocker.patch.object(resolve, "fetch_posting", return_value=None)
    assert apply_worker._prepare_documents({**JOB, "posting_snapshot": {}}) is False
    tailor.assert_not_called()


def test_unusable_resume_saves_nothing(v2):
    tailor, save = v2
    tailor.return_value = {"pdf_path": None, "violations": ["layout"], "content_json": {}}
    assert apply_worker._prepare_documents(JOB) is False
    save.assert_not_called()


def test_other_errors_fall_back_but_subscription_errors_propagate(v2, mocker):
    tailor, save = v2
    tailor.side_effect = RuntimeError("boom")
    assert apply_worker._prepare_documents(JOB) is False
    tailor.side_effect = claude_subscription.ClaudeSubscriptionError("usage limit")
    with pytest.raises(claude_subscription.ClaudeSubscriptionError):
        apply_worker._prepare_documents(JOB)
    save.assert_not_called()


def test_run_prepare_rereads_the_row_after_documents_are_written(mocker, tmp_path):
    mocker.patch.object(config, "APPLY_DISPLAY_LOCK", str(tmp_path / "d.lock"))
    for name, value in (("get_pause_scope", "none"), ("recover_stale_leases", 0),
                        ("get_approved_application_ids", []), ("log_application_run", 1)):
        mocker.patch.object(apply_worker.db, name, return_value=value)
    fresh = {"id": 5, "resume_file_ref": "r-ref", "automation_status": "ready_for_review"}
    mocker.patch.object(apply_worker.db, "get_job_application", return_value=fresh)
    mocker.patch.object(apply_worker.apply_agent, "preview_candidates", return_value=[JOB])
    process = mocker.patch.object(apply_worker.apply_agent, "_process_one_preview", return_value="filled")
    mocker.patch.object(apply_worker, "_prepare_documents", return_value=True)
    assert apply_worker.run_prepare(5) == 0
    process.assert_called_once_with(fresh)
