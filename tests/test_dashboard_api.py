"""Unit tests for api.dashboard (TestClient, in-memory store)."""
import pytest
from fastapi.testclient import TestClient

from api import dashboard
from ops.state_machine import ApplicationState as S


@pytest.fixture
def env(monkeypatch):
    store = dashboard.DashboardStore()
    monkeypatch.setattr(dashboard, "STORE", store)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    client = TestClient(dashboard.app, headers={"Authorization": "Bearer test-token"})
    return client, store


def test_fail_closed_when_token_unset(env, monkeypatch):
    client, store = env
    monkeypatch.delenv("DASHBOARD_API_TOKEN")
    _seed(store)
    for method, url in [("get", "/api/queue"), ("get", "/api/status"),
                        ("get", "/api/metrics"), ("post", "/api/approve/a1")]:
        resp = client.request(method, url)
        assert resp.status_code == 403, (method, url)
        assert resp.json()["detail"] == "dashboard auth is not configured"


def test_wrong_token_is_401(env):
    client, store = env
    _seed(store)
    resp = client.get("/api/queue", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def _seed(store, app_id="a1", **kw):
    store.add_application(app_id, "Product Manager", "Acme", "greenhouse", "/r/a.pdf", **kw)


def test_health_ok(env):
    client, _ = env
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["version"]


def test_queue_returns_list(env):
    client, store = env
    assert client.get("/api/queue").json() == []
    _seed(store)
    _seed(store, "a2", state=S.BLOCKED)
    rows = client.get("/api/queue").json()
    assert [r["application_id"] for r in rows] == ["a1"]
    assert set(rows[0]) == {
        "application_id", "job_title", "company", "ats_provider", "created_at", "resume_path"
    }


def test_queue_state_param_and_invalid(env):
    client, store = env
    _seed(store, state=S.BLOCKED)
    assert len(client.get("/api/queue", params={"state": "BLOCKED"}).json()) == 1
    assert client.get("/api/queue", params={"state": "nope"}).status_code == 422


def test_approve_transitions_state(env):
    client, store = env
    _seed(store)
    assert client.post("/api/approve/a1").json() == {"approved": True}
    assert store.state_of("a1") is S.APPLYING
    assert client.post("/api/approve/a1").status_code == 409  # no longer awaiting


def test_approve_unknown_is_404(env):
    client, _ = env
    assert client.post("/api/approve/missing").status_code == 404


def test_reject_requires_reason(env):
    client, store = env
    _seed(store)
    assert client.post("/api/reject/a1").status_code == 422
    assert client.post("/api/reject/a1", json={"reason": ""}).status_code == 422
    assert client.post("/api/reject/a1", json={"reason": "   "}).status_code == 422
    assert store.state_of("a1") is S.AWAITING_APPROVAL


def test_reject_transitions_and_records_reason(env):
    client, store = env
    _seed(store)
    assert client.post("/api/reject/a1", json={"reason": "wrong role"}).json() == {"rejected": True}
    assert store.state_of("a1") is S.BLOCKED
    assert "wrong role" in store.machine.get_history("a1")[-1]["reason"]


def test_status_returns_workers(env):
    client, store = env
    store.report_worker("apply", "running")
    _seed(store)
    body = client.get("/api/status").json()
    assert set(body["workers"]) == {"discovery", "screening", "tailoring", "apply"}
    assert body["workers"]["apply"] == "running" and body["workers"]["discovery"] == "unknown"
    assert body["queue_depth"] == 1
    assert body["claude_status"] and body["timestamp"]


def test_metrics_empty(env):
    client, _ = env
    body = client.get("/api/metrics").json()
    assert body["submitted_total"] == 0 and body["success_rate"] is None
    assert body["avg_tailor_seconds"] is None and body["by_ats"] == {}


def test_metrics_counts(env):
    client, store = env
    _seed(store, "s1", state=S.SUBMITTED, tailor_seconds=10.0)
    _seed(store, "s2", state=S.SUBMITTED, tailor_seconds=20.0)
    _seed(store, "b1", state=S.BLOCKED)
    body = client.get("/api/metrics").json()
    assert body["submitted_total"] == 2 and body["submitted_today"] == 2
    assert body["by_ats"] == {"greenhouse": 2}
    assert body["success_rate"] == pytest.approx(2 / 3)
    assert body["avg_tailor_seconds"] == 15.0


def test_token_required_when_configured(env, monkeypatch):
    client, store = env
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "secret")
    _seed(store)
    assert client.post("/api/approve/a1").status_code == 401
    assert client.post("/api/approve/a1", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/api/approve/a1", headers={"Authorization": "Bearer secret"}).status_code == 200
    assert client.get("/api/health").status_code == 200  # reads stay open
