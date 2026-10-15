import pytest
from fastapi.testclient import TestClient

import db
from api import dashboard
from ops.state_machine import ApplicationState as S


class _Persister:
    def __init__(self, fail=False, saved=None):
        self.calls, self.fail, self.saved = [], fail, saved or {}

    def save(self, application_id, decision, reason=None):
        if self.fail:
            raise RuntimeError("db down")
        self.calls.append((application_id, decision, reason))

    def load(self):
        return self.saved


def _env(monkeypatch, persister):
    store = dashboard.DashboardStore(persister=persister)
    monkeypatch.setattr(dashboard, "STORE", store)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "t")
    return TestClient(dashboard.app, headers={"Authorization": "Bearer t"}), store


def test_approve_persists_before_transitioning(monkeypatch):
    persister = _Persister()
    client, store = _env(monkeypatch, persister)
    store.add_application("12", "PM", "Acme", "greenhouse")
    assert client.post("/api/approve/12").status_code == 200
    assert persister.calls == [("12", "approved", None)]
    assert store.state_of("12") is S.APPLYING


def test_reject_persists_the_reason(monkeypatch):
    persister = _Persister()
    client, store = _env(monkeypatch, persister)
    store.add_application("12", "PM", "Acme", "greenhouse")
    assert client.post("/api/reject/12", json={"reason": "bad fit"}).status_code == 200
    assert persister.calls == [("12", "rejected", "bad fit")]
    assert store.state_of("12") is S.BLOCKED


def test_persist_failure_is_503_and_changes_nothing(monkeypatch):
    client, store = _env(monkeypatch, _Persister(fail=True))
    store.add_application("12", "PM", "Acme", "greenhouse")
    assert client.post("/api/approve/12").status_code == 503
    assert store.state_of("12") is S.AWAITING_APPROVAL


def test_persisted_decision_survives_a_restart():
    store = dashboard.DashboardStore(persister=_Persister(saved={"7": ("approved", None), "8": ("rejected", "no")}))
    store.load_decisions()
    store.add_application("7", "PM", "A", "greenhouse")
    store.add_application("8", "PM", "B", "lever")
    store.add_application("9", "PM", "C", "lever")
    assert store.state_of("7") is S.APPLYING
    assert store.state_of("8") is S.BLOCKED
    assert store.state_of("9") is S.AWAITING_APPROVAL


def test_load_decisions_applies_to_records_already_present():
    store = dashboard.DashboardStore(persister=_Persister(saved={"7": ("approved", None)}))
    store.add_application("7", "PM", "A", "greenhouse")
    store.load_decisions()
    assert store.state_of("7") is S.APPLYING


# ── db.py ──────────────────────────────────────────────────────────────────────

def test_record_dashboard_decision_is_a_single_rpc(mocker):
    rpc = mocker.patch.object(db, "_rpc", return_value=41)
    stage = mocker.patch.object(db, "update_job_application_stage")
    db.record_dashboard_decision("12", "rejected", "bad fit")
    name, params = rpc.call_args.args
    assert rpc.call_count == 1 and name == "record_dashboard_decision"
    assert params["p_application_id"] == 12 and params["p_decision"] == "rejected"
    assert params["p_reason"] == "bad fit" and params["p_run_id"]
    stage.assert_not_called()  # the withdrawal is inside the RPC's transaction, never a second write


def test_record_dashboard_decision_fails_closed(mocker):
    mocker.patch.object(db, "_rpc", return_value=None)
    with pytest.raises(RuntimeError):
        db.record_dashboard_decision("12", "approved")
    with pytest.raises(ValueError):
        db.record_dashboard_decision("abc", "approved")
    with pytest.raises(ValueError):
        db.record_dashboard_decision("12", "maybe")


def test_rpc_failure_propagates_as_a_plain_error(mocker):
    mocker.patch.object(db.time, "sleep")
    mocker.patch.object(db, "_rpc", side_effect=RuntimeError("db down"))
    with pytest.raises(RuntimeError) as err:
        db.record_dashboard_decision("12", "rejected", "x")
    assert not isinstance(err.value, db.DecisionConflict)


def test_conflict_is_not_retried_and_is_typed(mocker):
    rpc = mocker.patch.object(db, "_rpc", side_effect=Exception("dashboard_decision_conflict: already approved"))
    with pytest.raises(db.DecisionConflict) as err:
        db.record_dashboard_decision("12", "rejected", "x")
    assert rpc.call_count == 1 and err.value.conflict is True


def test_get_dashboard_decisions_pages_by_keyset(mocker):
    mocker.patch.object(db, "_DECISION_PAGE", 2)
    rpc = mocker.patch.object(db, "_rpc", side_effect=[
        [{"application_id": 1, "decision": "approved", "reason": None},
         {"application_id": 2, "decision": "rejected", "reason": "late"}],
        [{"application_id": 5, "decision": "approved", "reason": None}],
    ])
    assert db.get_dashboard_decisions() == {
        "1": ("approved", None), "2": ("rejected", "late"), "5": ("approved", None)}
    assert [c.args[1]["p_after"] for c in rpc.call_args_list] == [0, 2]


def test_get_dashboard_decisions_empty(mocker):
    mocker.patch.object(db, "_rpc", return_value=[])
    assert db.get_dashboard_decisions() == {}
