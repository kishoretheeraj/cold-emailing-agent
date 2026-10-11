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

def test_record_dashboard_approval_writes_an_audit_row_only(mocker):
    log = mocker.patch.object(db, "log_application_run", return_value="run-1")
    stage = mocker.patch.object(db, "update_job_application_stage")
    db.record_dashboard_decision("12", "approved")
    kwargs = log.call_args.kwargs
    assert kwargs["application_id"] == 12 and kwargs["adapter"] == "dashboard"
    assert kwargs["details"] == {"decision": "approved", "reason": None}
    stage.assert_not_called()


def test_record_dashboard_reject_withdraws_the_row(mocker):
    mocker.patch.object(db, "log_application_run", return_value="run-1")
    stage = mocker.patch.object(db, "update_job_application_stage")
    db.record_dashboard_decision("12", "rejected", "bad fit")
    stage.assert_called_once_with(12, "withdrawn")


def test_record_dashboard_decision_fails_closed(mocker):
    mocker.patch.object(db, "log_application_run", return_value=None)
    stage = mocker.patch.object(db, "update_job_application_stage")
    with pytest.raises(RuntimeError):
        db.record_dashboard_decision("12", "rejected", "x")
    stage.assert_not_called()
    with pytest.raises(ValueError):
        db.record_dashboard_decision("abc", "approved")
    with pytest.raises(ValueError):
        db.record_dashboard_decision("12", "maybe")


def test_get_dashboard_decisions_newest_wins(mocker):
    client = mocker.MagicMock()
    chain = client.table.return_value.select.return_value.eq.return_value.order.return_value
    chain.execute.return_value.data = [
        {"application_id": 1, "details": {"decision": "approved", "reason": None}},
        {"application_id": 1, "details": {"decision": "rejected", "reason": "late"}},
        {"application_id": 2, "details": None},
    ]
    mocker.patch.object(db, "get_client", return_value=client)
    assert db.get_dashboard_decisions() == {"1": ("rejected", "late")}
