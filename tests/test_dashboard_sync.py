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


# ── reconciliation ─────────────────────────────────────────────────────────────

def test_refresh_drops_records_no_longer_awaiting_approval(monkeypatch):
    persister = _Persister(rows=[ROW, {**ROW, "id": 13}])
    client, store = _env(monkeypatch, persister)
    assert client.get("/api/status").json()["queue_depth"] == 2
    persister.rows = [ROW]  # 13 was approved/withdrawn/submitted elsewhere
    assert [i["application_id"] for i in client.get("/api/queue").json()] == ["12"]
    assert client.get("/api/status").json()["queue_depth"] == 1
    assert client.post("/api/approve/13").status_code == 404


def test_a_dropped_row_that_is_requeued_reappears(monkeypatch):
    persister = _Persister(rows=[ROW])
    client, store = _env(monkeypatch, persister)
    client.get("/api/queue")
    persister.rows = []
    assert client.get("/api/queue").json() == []
    persister.rows = [ROW]
    assert len(client.get("/api/queue").json()) == 1
    assert client.post("/api/approve/12").status_code == 200


def test_refresh_applies_a_decision_another_process_recorded(monkeypatch):
    persister = _Persister(rows=[ROW])
    client, store = _env(monkeypatch, persister)
    client.get("/api/queue")
    persister.saved = {"12": ("approved", None)}
    assert client.get("/api/status").json()["queue_depth"] == 0
    assert store.state_of("12") is S.APPLYING
    assert client.post("/api/approve/12").status_code == 409


def test_a_failed_refresh_drops_nothing(monkeypatch):
    persister = _Persister(rows=[ROW])
    client, store = _env(monkeypatch, persister)
    client.get("/api/queue")
    persister.pending_fails = True
    assert len(client.get("/api/queue").json()) == 1


def test_unknown_id_is_fetched_directly_before_404(monkeypatch):
    class P(_Persister):
        def pending_one(self, application_id):
            return ROW if str(application_id) == "12" else None
    client, store = _env(monkeypatch, P(rows=[]))  # the listing does not hold it
    assert client.post("/api/approve/12").status_code == 200
    assert client.post("/api/approve/999").status_code == 404


# ── db.py: pagination and direct fetch ─────────────────────────────────────────

class _Paged:
    def __init__(self, pages):
        self.pages, self.after = list(pages), []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            if name == "gt":
                self.after.append(args[1])
            return self
        return call

    def execute(self):
        result = type("R", (), {})()
        result.data = self.pages.pop(0)
        return result


def test_awaiting_approval_is_keyset_paginated(mocker):
    pages = [[{"id": i} for i in range(1, 201)], [{"id": 201}]]
    q = _Paged(pages)
    mocker.patch.object(db, "get_client", return_value=q)
    rows = db.get_applications_awaiting_approval()
    assert len(rows) == 201 and rows[-1]["id"] == 201
    assert q.after == [0, 200]  # the second page starts after the first page's last id


def test_single_awaiting_row_fetch(mocker):
    q = _Q([ROW])
    mocker.patch.object(db, "get_client", return_value=q)
    assert db.get_application_awaiting_approval("12") == ROW
    assert ("eq", ("id", 12), {}) in q.calls and ("eq", ("automation_status", "ready_for_review"), {}) in q.calls
    mocker.patch.object(db, "get_client", return_value=_Q([]))
    assert db.get_application_awaiting_approval(99) is None


def test_sync_pending_reads_and_reconciles_under_the_lock(monkeypatch):
    seen = {}

    class P(_Persister):
        def pending(self):
            got = []
            t = threading.Thread(target=lambda: got.append(store._lock.acquire(timeout=0.2)))
            t.start(); t.join()
            seen["other_thread_got_lock"] = got[0]
            return self.rows

    store = dashboard.DashboardStore(persister=P(rows=[ROW]))
    assert store.sync_pending() == 1
    assert seen["other_thread_got_lock"] is False
