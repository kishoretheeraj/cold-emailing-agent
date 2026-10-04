"""Tests for db.py's application lifecycle wrappers: every one is a thin .rpc() call."""

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


def _reread_returns(client, lease):
    client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"worker_lease_id": lease}]


def test_claim_success_returns_locally_generated_lease(fake_client):
    _rpc_returns(fake_client, True)
    lease = db.claim_application(7, "preparing")
    assert isinstance(lease, str) and len(lease) == 36
    fake_client.rpc.assert_called_once_with(
        "claim_application", {"p_id": 7, "p_lease": lease, "p_to": "preparing"})


def test_claim_lost_to_another_worker_returns_none(fake_client):
    _rpc_returns(fake_client, False)
    _reread_returns(fake_client, "someone-else")
    assert db.claim_application(7, "preparing") is None


def test_claim_whose_response_was_lost_is_recovered_by_reread(fake_client):
    fake_client.rpc.return_value.execute.side_effect = RuntimeError("connection reset after commit")

    def rpc(name, params):
        _reread_returns(fake_client, params["p_lease"])
        return fake_client.rpc.return_value

    fake_client.rpc.side_effect = rpc
    lease = db.claim_application(7, "submitting")
    assert lease is not None
    assert fake_client.rpc.call_count == 1


def test_claim_is_never_wrapped_in_retry(fake_client, mocker):
    retry = mocker.spy(db, "_retry")
    _rpc_returns(fake_client, True)
    db.claim_application(7, "preparing")
    assert retry.call_count == 0


def test_claim_failed_rpc_is_called_exactly_once(fake_client):
    fake_client.rpc.return_value.execute.side_effect = RuntimeError("down")
    _reread_returns(fake_client, "someone-else")
    assert db.claim_application(7, "preparing") is None
    assert fake_client.rpc.call_count == 1


def test_claim_rejects_unknown_status(fake_client):
    with pytest.raises(ValueError):
        db.claim_application(7, "queued")


def test_release_calls_rpc_with_reason(fake_client):
    _rpc_returns(fake_client, True)
    assert db.release_application(7, "lease-1", "failed_retryable", "boom") is True
    fake_client.rpc.assert_called_once_with(
        "release_application", {"p_id": 7, "p_lease": "lease-1", "p_to": "failed_retryable", "p_reason": "boom"})


def test_release_returns_false_when_lease_lost(fake_client):
    _rpc_returns(fake_client, False)
    assert db.release_application(7, "lease-1", "approved") is False
    assert fake_client.rpc.call_args[0][1]["p_reason"] is None


def test_release_rejects_unknown_status(fake_client):
    with pytest.raises(ValueError):
        db.release_application(7, "lease-1", "queued")


def test_complete_preview_calls_rpc(fake_client):
    _rpc_returns(fake_client, True)
    assert db.complete_preview(7, "lease-1", {"platform": "greenhouse"}, "sig") is True
    fake_client.rpc.assert_called_once_with("complete_preview", {
        "p_id": 7, "p_lease": "lease-1", "p_preview": {"platform": "greenhouse"}, "p_form_signature": "sig"})


def test_heartbeat_calls_rpc(fake_client):
    _rpc_returns(fake_client, True)
    db.heartbeat_application(7, "lease-1")
    fake_client.rpc.assert_called_once_with("heartbeat_application", {"p_id": 7, "p_lease": "lease-1"})


def test_heartbeat_never_raises(fake_client):
    fake_client.rpc.side_effect = RuntimeError("down")
    db.heartbeat_application(7, "lease-1")


def test_renewal_calls_rpc(fake_client):
    _rpc_returns(fake_client, True)
    assert db.renew_submission_lease(7, "lease-1", "h1") is True
    fake_client.rpc.assert_called_once_with(
        "renew_submission_lease", {"p_id": 7, "p_lease": "lease-1", "p_revision_hash": "h1"})


def test_renewal_false_from_server_is_false(fake_client):
    _rpc_returns(fake_client, False)
    assert db.renew_submission_lease(7, "lease-1", "h1") is False


@pytest.mark.parametrize("revision_hash", [None, ""])
def test_renewal_without_hash_is_false_without_calling(fake_client, revision_hash):
    assert db.renew_submission_lease(7, "lease-1", revision_hash) is False
    fake_client.rpc.assert_not_called()


def test_final_renewal_raises_on_database_outage(fake_client):
    fake_client.rpc.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        db.renew_submission_lease(7, "lease-1", "h1")


def test_record_submission_calls_rpc(fake_client):
    _rpc_returns(fake_client, True)
    assert db.record_submission(7, "lease-1", "greenhouse", "2026-10-01") is True
    fake_client.rpc.assert_called_once_with("record_submission", {
        "p_id": 7, "p_lease": "lease-1", "p_source_channel": "greenhouse", "p_applied_date": "2026-10-01"})


def test_mark_unsupported_calls_rpc(fake_client):
    _rpc_returns(fake_client, True)
    assert db.mark_unsupported(7, "workday") is True
    fake_client.rpc.assert_called_once_with(
        "mark_application_unsupported", {"p_id": 7, "p_reason": "workday"})


@pytest.mark.parametrize("data,expected", [(2, 2), (None, 0), (0, 0)])
def test_recover_stale_leases_returns_count(fake_client, data, expected):
    _rpc_returns(fake_client, data)
    assert db.recover_stale_leases(1800) == expected
    fake_client.rpc.assert_called_once_with("recover_stale_leases", {"p_stale_seconds": 1800})


def test_lifecycle_wrappers_never_write_the_table_directly(fake_client):
    _rpc_returns(fake_client, True)
    _reread_returns(fake_client, "x")
    db.claim_application(7, "preparing")
    db.heartbeat_application(7, "l")
    db.renew_submission_lease(7, "l", "h")
    db.complete_preview(7, "l", {}, "s")
    db.release_application(7, "l", "approved")
    db.record_submission(7, "l", "gh", "2026-10-01")
    db.mark_unsupported(7, "r")
    db.recover_stale_leases(1800)
    fake_client.table.return_value.update.assert_not_called()
    assert not hasattr(db, "set_automation_status")
