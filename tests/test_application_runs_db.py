"""db.py wrappers for migration 20261008000000: run log, takeover, evidence on record_submission.
Every write is an .rpc() call; the table is never written directly."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

import db


@pytest.fixture
def fake_client(mocker):
    client = MagicMock(name="supabase_client")
    mocker.patch.object(db, "_client", client)
    mocker.patch.object(db, "get_client", return_value=client)
    mocker.patch.object(db.time, "sleep")
    return client


def _rpc_returns(client, data):
    client.rpc.return_value.execute.return_value.data = data


# ── log_application_run ───────────────────────────────────────────────────────

def test_log_run_calls_rpc_with_every_param(fake_client):
    _rpc_returns(fake_client, 41)
    started = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)
    run_id = db.log_application_run(
        application_id=7, kind="prepare", adapter="deterministic", host="boards.greenhouse.io",
        started_at=started, outcome="ready", stop_reason=None, fields_filled=9, fields_missing=0,
        takeovers=1, model_calls=2, error_class=None, details={"fill_report": {"email": True}},
        run_id="11111111-1111-1111-1111-111111111111")
    assert run_id == 41
    fake_client.rpc.assert_called_once_with("log_application_run", {
        "p_run_id": "11111111-1111-1111-1111-111111111111", "p_application_id": 7,
        "p_kind": "prepare", "p_adapter": "deterministic", "p_host": "boards.greenhouse.io",
        "p_started_at": "2026-10-08T03:00:00+00:00", "p_outcome": "ready", "p_stop_reason": None,
        "p_fields_filled": 9, "p_fields_missing": 0, "p_takeovers": 1, "p_model_calls": 2,
        "p_error_class": None, "p_details": {"fill_report": {"email": True}}})


def test_log_run_generates_a_run_id_when_none_is_given(fake_client):
    _rpc_returns(fake_client, 1)
    db.log_application_run(application_id=None, kind="bakeoff", adapter="ats_agent", host=None,
                           started_at=datetime.now(timezone.utc), outcome="scored")
    params = fake_client.rpc.call_args[0][1]
    assert len(params["p_run_id"]) == 36
    assert params["p_application_id"] is None


def test_log_run_truncates_long_text_instead_of_losing_the_row(fake_client):
    _rpc_returns(fake_client, 1)
    db.log_application_run(application_id=7, kind="prepare", adapter="x" * 100, host="h" * 300,
                           started_at=datetime.now(timezone.utc), outcome="failed_retryable",
                           stop_reason="r" * 900, error_class="E" * 200)
    params = fake_client.rpc.call_args[0][1]
    assert len(params["p_adapter"]) == 64
    assert len(params["p_host"]) == 255
    assert len(params["p_stop_reason"]) == 500
    assert len(params["p_error_class"]) == 128


def test_log_run_is_best_effort(fake_client):
    fake_client.rpc.return_value.execute.side_effect = RuntimeError("database unavailable")
    assert db.log_application_run(application_id=7, kind="prepare", adapter="d", host=None,
                                  started_at=datetime.now(timezone.utc), outcome="ready") is None


@pytest.mark.parametrize("field,value", [("kind", "deploy"), ("outcome", "great")])
def test_log_run_rejects_unknown_vocabulary_locally(fake_client, field, value):
    kwargs = dict(application_id=7, kind="prepare", adapter="d", host=None,
                  started_at=datetime.now(timezone.utc), outcome="ready")
    kwargs[field] = value
    assert db.log_application_run(**kwargs) is None
    fake_client.rpc.assert_not_called()


# ── takeover ──────────────────────────────────────────────────────────────────

def test_request_takeover_calls_rpc(fake_client):
    _rpc_returns(fake_client, True)
    assert db.request_takeover(7, "lease-1", "captcha", "hCaptcha on Lever") is True
    fake_client.rpc.assert_called_once_with("request_takeover", {
        "p_id": 7, "p_lease": "lease-1", "p_kind": "captcha", "p_reason": "hCaptcha on Lever"})


def test_request_takeover_rejects_unknown_kind(fake_client):
    with pytest.raises(ValueError):
        db.request_takeover(7, "lease-1", "solve_it", "x")
    fake_client.rpc.assert_not_called()


def test_request_takeover_truncates_reason(fake_client):
    _rpc_returns(fake_client, True)
    db.request_takeover(7, "lease-1", "other", "x" * 900)
    assert len(fake_client.rpc.call_args[0][1]["p_reason"]) == 500


def test_clear_takeover_calls_rpc(fake_client):
    _rpc_returns(fake_client, True)
    assert db.clear_takeover(7, "lease-1") is True
    fake_client.rpc.assert_called_once_with("clear_takeover", {"p_id": 7, "p_lease": "lease-1"})


def _row(client, data):
    client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = data


@pytest.mark.parametrize("row,expected", [
    ({"worker_lease_id": "L", "takeover": {"lease": "L", "continue_at": "2026-10-08T03:00:00Z"}}, "continued"),
    ({"worker_lease_id": "L", "takeover": {"lease": "L", "continue_at": None}}, "waiting"),
    ({"worker_lease_id": "L", "takeover": None}, "none"),
    # the lease was recovered or re-claimed: this worker no longer owns the row
    ({"worker_lease_id": "OTHER", "takeover": {"lease": "L", "continue_at": None}}, "lost"),
    ({"worker_lease_id": None, "takeover": None}, "lost"),
])
def test_takeover_state(fake_client, row, expected):
    _row(fake_client, [row])
    assert db.takeover_state(7, "L") == expected


def test_takeover_state_for_a_missing_row_is_lost(fake_client):
    _row(fake_client, [])
    assert db.takeover_state(7, "L") == "lost"


# ── record_submission evidence ────────────────────────────────────────────────

def test_record_submission_without_evidence_keeps_the_four_arg_call(fake_client):
    # Works whether or not 20261008000000 is deployed yet.
    _rpc_returns(fake_client, True)
    db.record_submission(7, "lease-1", "greenhouse", "2026-10-01")
    assert "p_evidence" not in fake_client.rpc.call_args[0][1]


def test_record_submission_passes_evidence(fake_client):
    _rpc_returns(fake_client, True)
    ev = {"url": "https://x/thanks", "text": "Thank you for applying", "screenshot": "7/run/confirm.png"}
    assert db.record_submission(7, "lease-1", "greenhouse", "2026-10-01", evidence=ev) is True
    fake_client.rpc.assert_called_once_with("record_submission", {
        "p_id": 7, "p_lease": "lease-1", "p_source_channel": "greenhouse",
        "p_applied_date": "2026-10-01", "p_evidence": ev})


def test_new_wrappers_never_write_the_table_directly(fake_client):
    _rpc_returns(fake_client, True)
    db.log_application_run(application_id=7, kind="prepare", adapter="d", host=None,
                           started_at=datetime.now(timezone.utc), outcome="ready")
    db.request_takeover(7, "l", "captcha", "r")
    db.clear_takeover(7, "l")
    db.record_submission(7, "l", "gh", "2026-10-01", evidence={"url": "u"})
    fake_client.table.return_value.update.assert_not_called()
    fake_client.table.return_value.insert.assert_not_called()
