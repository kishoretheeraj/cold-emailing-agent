import threading
import time

import pytest
from fastapi.testclient import TestClient

import db
from api import dashboard
from ops.state_machine import ApplicationState as S


class _Persister:
    def __init__(self, rows=None, saved=None, fail=None, delay=0.0, pending_fails=False):
        self.rows, self.saved, self.fail, self.delay = rows or [], saved or {}, fail, delay
        self.pending_fails, self.calls = pending_fails, []

    def save(self, application_id, decision, reason=None):
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise self.fail
        self.calls.append((application_id, decision, reason))

    def load(self):
        return self.saved

    def pending(self):
        if self.pending_fails:
            raise RuntimeError("db down")
        return self.rows


class _Conflict(RuntimeError):
    conflict = True


def _env(monkeypatch, persister):
    store = dashboard.DashboardStore(persister=persister)
    monkeypatch.setattr(dashboard, "STORE", store)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "t")
    return TestClient(dashboard.app, headers={"Authorization": "Bearer t"}), store


ROW = {"id": 12, "company": "Acme", "role": "PM", "platform": "greenhouse", "resume_file_ref": "r.pdf"}


def test_queue_is_read_from_the_database(monkeypatch):
    client, store = _env(monkeypatch, _Persister(rows=[ROW]))
    body = client.get("/api/queue").json()
    assert [i["application_id"] for i in body] == ["12"] and body[0]["company"] == "Acme"
    assert client.get("/api/queue").json() == body  # a second refresh does not duplicate it
    assert client.get("/api/status").json()["queue_depth"] == 1


def test_actual_application_id_is_decidable_not_404(monkeypatch):
    client, store = _env(monkeypatch, _Persister(rows=[ROW]))
    assert client.post("/api/approve/12").status_code == 200
    assert store.state_of("12") is S.APPLYING
    assert client.post("/api/approve/999").status_code == 404


def test_database_failure_degrades_to_the_cache(monkeypatch):
    client, store = _env(monkeypatch, _Persister(pending_fails=True))
    assert client.get("/api/queue").json() == []
    assert client.post("/api/approve/12").status_code == 404


def test_startup_restores_decisions_then_fills_the_queue(monkeypatch):
    persister = _Persister(rows=[ROW, {**ROW, "id": 13}], saved={"12": ("approved", None)})
    _, store = _env(monkeypatch, persister)
    dashboard._restore_decisions()
    assert store.state_of("12") is S.APPLYING
    assert store.state_of("13") is S.AWAITING_APPROVAL


def test_store_without_a_pending_source_is_unchanged():
    store = dashboard.DashboardStore()
    assert store.sync_pending() == 0 and store.records == {}


def test_database_conflict_is_409_and_changes_nothing(monkeypatch):
    client, store = _env(monkeypatch, _Persister(rows=[ROW], fail=_Conflict("dashboard_decision_conflict")))
    assert client.post("/api/reject/12", json={"reason": "no"}).status_code == 409
    assert store.state_of("12") is S.AWAITING_APPROVAL


def test_concurrent_approve_and_reject_persist_exactly_one(monkeypatch):
    persister = _Persister(rows=[ROW], delay=0.05)
    client, store = _env(monkeypatch, persister)
    client.get("/api/queue")
    codes = []

    def call(method, url, **kw):
        c = TestClient(dashboard.app, headers={"Authorization": "Bearer t"})
        codes.append(c.post(url, **kw).status_code)

    threads = [threading.Thread(target=call, args=("post", "/api/approve/12")),
               threading.Thread(target=call, args=("post", "/api/reject/12"), kwargs={"json": {"reason": "no"}})]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200, 409]
    assert len(persister.calls) == 1  # the loser failed before persisting anything
    assert store.state_of("12") in (S.APPLYING, S.BLOCKED)


# ── db.py ──────────────────────────────────────────────────────────────────────

class _Q:
    """Records the chained PostgREST calls and returns fixed data."""

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


def test_awaiting_approval_query(mocker):
    q = _Q([ROW])
    mocker.patch.object(db, "get_client", return_value=q)
    assert db.get_applications_awaiting_approval() == [ROW]
    assert ("eq", ("automation_status", "ready_for_review"), {}) in q.calls
    assert ("eq", ("stage", "ready_to_submit"), {}) in q.calls


def test_unsigned_intents_are_dashboard_approved_rows_still_in_review(mocker):
    mocker.patch.object(db, "get_dashboard_decisions", return_value={
        "1": ("approved", None), "2": ("rejected", "x"), "3": ("approved", None)})
    q = _Q([{"id": 3}])
    mocker.patch.object(db, "get_client", return_value=q)
    assert db.get_unsigned_approval_intents() == [3]
    assert ("in_", ("id", [1, 3]), {}) in q.calls
    assert ("eq", ("automation_status", "ready_for_review"), {}) in q.calls


def test_unsigned_intents_with_none_approved_makes_no_query(mocker):
    mocker.patch.object(db, "get_dashboard_decisions", return_value={"2": ("rejected", "x")})
    client = mocker.patch.object(db, "get_client")
    assert db.get_unsigned_approval_intents() == []
    client.assert_not_called()
