"""Tests for db.py's application worker lease accessors."""

from unittest.mock import MagicMock

import pytest

import db


@pytest.fixture
def fake_client(mocker):
    client = MagicMock(name="supabase_client")
    mocker.patch.object(db, "_client", client)
    mocker.patch.object(db, "get_client", return_value=client)
    return client


def _update_chain(client):
    return client.table.return_value.update.return_value


def test_claim_success_returns_locally_generated_lease(fake_client):
    chain = _update_chain(fake_client).eq.return_value.in_.return_value.is_.return_value
    chain.execute.return_value.data = [{"id": 7}]
    lease = db.claim_application(7, ("idle", "failed_retryable"), "preparing")
    assert isinstance(lease, str) and len(lease) == 36
    payload = fake_client.table.return_value.update.call_args[0][0]
    assert payload["automation_status"] == "preparing"
    assert payload["worker_lease_id"] == lease
    _update_chain(fake_client).eq.return_value.in_.assert_called_with(
        "automation_status", ["idle", "failed_retryable"])
    _update_chain(fake_client).eq.return_value.in_.return_value.is_.assert_called_with(
        "worker_lease_id", "null")


def test_claim_lost_to_another_worker_returns_none(fake_client):
    chain = _update_chain(fake_client).eq.return_value.in_.return_value.is_.return_value
    chain.execute.return_value.data = []
    fake_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"worker_lease_id": "someone-else"}]
    assert db.claim_application(7, ("idle",), "preparing") is None


def test_claim_whose_response_was_lost_is_recovered_by_reread(fake_client, mocker):
    chain = _update_chain(fake_client).eq.return_value.in_.return_value.is_.return_value
    chain.execute.side_effect = RuntimeError("connection reset after commit")
    captured = {}

    def reread(*a, **k):
        result = MagicMock()
        result.data = [{"worker_lease_id": captured["lease"]}]
        return result

    def capture_update(payload):
        captured["lease"] = payload["worker_lease_id"]
        return _update_chain(fake_client)

    fake_client.table.return_value.update.side_effect = capture_update
    fake_client.table.return_value.select.return_value.eq.return_value.execute.side_effect = reread
    lease = db.claim_application(7, ("approved",), "submitting")
    assert lease == captured["lease"]


def test_claim_is_never_wrapped_in_retry(fake_client, mocker):
    retry = mocker.spy(db, "_retry")
    chain = _update_chain(fake_client).eq.return_value.in_.return_value.is_.return_value
    chain.execute.return_value.data = [{"id": 7}]
    db.claim_application(7, ("idle",), "preparing")
    assert retry.call_count == 0


def test_claim_rejects_unknown_status(fake_client):
    with pytest.raises(ValueError):
        db.claim_application(7, ("idle",), "queued")


def test_release_is_conditional_on_own_lease_and_clears_it(fake_client):
    chain = _update_chain(fake_client).eq.return_value.eq.return_value
    chain.execute.return_value.data = [{"id": 7}]
    db.release_application(7, "lease-1", "ready_for_review", {"stage": "ready_to_submit"})
    payload = fake_client.table.return_value.update.call_args[0][0]
    assert payload["automation_status"] == "ready_for_review"
    assert payload["worker_lease_id"] is None and payload["worker_heartbeat_at"] is None
    assert payload["stage"] == "ready_to_submit"
    _update_chain(fake_client).eq.return_value.eq.assert_called_with("worker_lease_id", "lease-1")


def test_heartbeat_never_raises(fake_client):
    fake_client.table.side_effect = RuntimeError("down")
    db.heartbeat_application(7, "lease-1")


@pytest.mark.parametrize("held_status,expected", [
    ("submitting", "needs_confirmation"),
    ("preparing", "failed_retryable"),
])
def test_recover_stale_leases_never_makes_a_clicked_submit_retryable(fake_client, held_status, expected):
    select_chain = fake_client.table.return_value.select.return_value.not_.is_.return_value.lt.return_value
    select_chain.execute.return_value.data = [
        {"id": 7, "automation_status": held_status, "worker_lease_id": "old"}]
    upd = _update_chain(fake_client).eq.return_value.eq.return_value.lt.return_value
    upd.execute.return_value.data = [{"id": 7}]
    assert db.recover_stale_leases(1800) == 1
    payload = fake_client.table.return_value.update.call_args[0][0]
    assert payload["automation_status"] == expected
    assert payload["worker_lease_id"] is None
    assert held_status in payload["apply_blocked_reason"]


def test_record_submission_marks_submitted_and_clears_lease(fake_client):
    chain = _update_chain(fake_client).eq.return_value.eq.return_value
    chain.execute.return_value.data = [{"id": 7}]
    db.record_submission(7, "greenhouse", "2026-10-01", lease_id="lease-1")
    payload = fake_client.table.return_value.update.call_args[0][0]
    assert payload["stage"] == "applied"
    assert payload["automation_status"] == "submitted"
    assert payload["worker_lease_id"] is None
