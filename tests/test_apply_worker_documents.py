from datetime import datetime, timedelta, timezone

import pytest

import apply_worker
import claude_subscription
import config
import db


class _Q:
    def __init__(self, data):
        self.calls, self.data = [], data

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self
        return call

    def execute(self):
        class R:
            pass
        r = R()
        r.data = self.data
        return r


def _job(i, **kw):
    return {"id": i, "company": f"Co{i}", "role": "PM", "automation_status": "idle", **kw}


@pytest.fixture
def pool(mocker):
    mocker.patch.object(config, "APPLY_RESUME_V2", True)
    mocker.patch.object(apply_worker.ats_platform, "unpreparable_platforms", return_value=[])
    mocker.patch.object(apply_worker.db, "get_pause_scope", return_value="none")
    mocker.patch.object(apply_worker.db, "get_approved_application_ids", return_value=[])
    mocker.patch.object(apply_worker.db, "claim_application", return_value="lease-1")
    finish = mocker.patch.object(apply_worker.db, "finish_document_generation", return_value=True)
    candidates = mocker.patch.object(apply_worker.db, "get_document_candidates", return_value=[_job(1)])
    return finish, candidates


def test_flag_off_touches_nothing(mocker):
    mocker.patch.object(config, "APPLY_RESUME_V2", False)
    candidates = mocker.patch.object(apply_worker.db, "get_document_candidates")
    assert apply_worker._run_document_pass(5) == 0
    candidates.assert_not_called()


def test_row_without_documents_is_claimed_tailored_and_returned_to_idle(pool, mocker):
    finish, _ = pool
    prep = mocker.patch.object(apply_worker, "_prepare_documents", return_value=True)
    assert apply_worker._run_document_pass(5) == 0
    apply_worker.db.claim_application.assert_called_once_with(1, "preparing")
    prep.assert_called_once()
    finish.assert_called_once_with(1, "lease-1", True, None)


def test_a_lost_claim_is_skipped(pool, mocker):
    apply_worker.db.claim_application.return_value = None
    prep = mocker.patch.object(apply_worker, "_prepare_documents")
    assert apply_worker._run_document_pass(5) == 0
    prep.assert_not_called()


def test_no_documents_produced_parks_the_row_for_retry(pool, mocker):
    finish, _ = pool
    mocker.patch.object(apply_worker, "_prepare_documents", return_value=False)
    assert apply_worker._run_document_pass(5) == 0
    assert finish.call_args.args[:3] == (1, "lease-1", False) and finish.call_args.args[3]


def test_unexpected_error_is_counted_and_released(pool, mocker):
    finish, _ = pool
    mocker.patch.object(apply_worker, "_prepare_documents", side_effect=RuntimeError("boom"))
    assert apply_worker._run_document_pass(5) == 1
    assert finish.call_args.args[2] is False and "boom" in finish.call_args.args[3]


def test_subscription_error_releases_and_stops(pool, mocker):
    finish, candidates = pool
    candidates.return_value = [_job(1), _job(2)]
    mocker.patch.object(apply_worker, "_prepare_documents",
                        side_effect=claude_subscription.ClaudeSubscriptionError("usage limit"))
    assert apply_worker._run_document_pass(5) == 1
    assert finish.call_count == 1 and apply_worker.db.claim_application.call_count == 1


def test_waiting_approval_stops_before_claiming(pool, mocker):
    apply_worker.db.get_approved_application_ids.return_value = [9]
    assert apply_worker._run_document_pass(5) == 0
    apply_worker.db.claim_application.assert_not_called()


def test_backoff_and_limit_apply(pool, mocker):
    _, candidates = pool
    recent = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    candidates.return_value = [
        _job(1, automation_status="failed_retryable", prepare_attempts=1, updated_at=recent),
        _job(2, automation_status="failed_retryable", prepare_attempts=1, updated_at=old),
        _job(3), _job(4)]
    mocker.patch.object(apply_worker, "_prepare_documents", return_value=True)
    apply_worker._run_document_pass(2)
    assert [c.args[0] for c in apply_worker.db.claim_application.call_args_list] == [2, 3]


def test_run_prepare_runs_the_document_pass_before_choosing_preview_candidates(mocker, tmp_path):
    mocker.patch.object(config, "APPLY_DISPLAY_LOCK", str(tmp_path / "d.lock"))
    for name, value in (("get_pause_scope", "none"), ("recover_stale_leases", 0)):
        mocker.patch.object(apply_worker.db, name, return_value=value)
    order = []
    mocker.patch.object(apply_worker, "_run_document_pass", side_effect=lambda limit: order.append("docs") or 1)
    mocker.patch.object(apply_worker.apply_agent, "preview_candidates",
                        side_effect=lambda: order.append("preview") or [])
    assert apply_worker.run_prepare(5) == 1  # the document pass's errors are reported
    assert order == ["docs", "preview"]


def test_unsigned_intents_are_logged_and_never_submitted(mocker, caplog):
    mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": "1"})
    mocker.patch.object(apply_worker.db, "get_pause_scope", return_value="none")
    mocker.patch.object(apply_worker.db, "get_unsigned_approval_intents", return_value=[7])
    mocker.patch.object(apply_worker.db, "get_approved_application_ids", return_value=[])
    submit = mocker.patch.object(apply_worker.apply_agent, "submit")
    with caplog.at_level("INFO"):
        assert apply_worker.run_submit(5) == 0
    submit.assert_not_called()
    assert "await the signed approve_application step" in caplog.text


def test_document_candidates_query_requires_a_strong_row_with_no_resume(mocker):
    q = _Q([_job(1)])
    mocker.patch.object(db, "get_client", return_value=q)
    assert db.get_document_candidates(10, exclude_platforms=["workday"]) == [_job(1)]
    names = [(c[0], c[1]) for c in q.calls]
    assert ("is_", ("resume_file_ref", "null")) in names
    assert ("eq", ("pick_verdict", "strong")) in names and ("eq", ("stage", "saved")) in names
    assert any(n == "or_" for n, _ in names)


def test_finish_document_generation_is_one_rpc(mocker):
    rpc = mocker.patch.object(db, "_rpc", return_value=True)
    assert db.finish_document_generation(5, "L", True) is True
    assert rpc.call_args.args == ("finish_document_generation",
                                  {"p_id": 5, "p_lease": "L", "p_ok": True, "p_reason": None})
