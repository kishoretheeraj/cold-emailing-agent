from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
from fakes import FakeMailbox, make_msg


class StubMailbox(FakeMailbox):
    connected, degraded, auth_failed = True, False, False
    last_successful_poll, last_auth_failure = None, None
    state, last_error = "connected", None

    def stop(self): ...


@pytest.fixture
def client(tmp_path):
    cfg = main.Config("me@gmail.com", tmp_path / "pw", tmp_path / "db", False)
    mb = StubMailbox([make_msg(1, "Your code is 515151")])
    with TestClient(main.create_app(cfg, mb)) as c:
        c.mb = mb
        yield c


def body(**kw):
    b = {"sender_domains": ["example.com"], "kind": "otp", "timeout_s": 1,
         "after": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}
    return {**b, **kw}


def test_wait_ok_and_timeout(client):
    r = client.post("/v1/wait", json=body())
    assert r.status_code == 200
    j = r.json()
    assert j["code"] == "515151" and j["request_id"] and j["message_id"] and j["received_at"]
    r = client.post("/v1/wait", json=body())
    assert r.status_code == 408 and r.json() == {"error": "timeout"}


def test_conflict_409(client):
    client.mb.messages = [make_msg(5, "code is 111111 code is 222222")]
    r = client.post("/v1/wait", json=body())
    assert r.status_code == 409 and r.json() == {"error": "conflict"}


def test_validation(client):
    assert client.post("/v1/wait", json=body(kind="sms")).status_code == 422
    assert client.post("/v1/wait", json=body(sender_domains=[])).status_code == 422


def test_health_schema(client):
    j = client.get("/health").json()
    assert set(j) == {"imap_connected", "last_successful_poll", "active_waiter_count",
                      "degraded", "uptime_seconds", "last_auth_failure", "state", "last_error"}
    assert j["imap_connected"] is True and j["active_waiter_count"] == 0


def test_health_auth_failed_is_503(client):
    client.mb.auth_failed = True
    r = client.get("/health")
    assert r.status_code == 503 and r.headers["x-unhealthy-reason"] == "auth_failed"
    assert client.post("/v1/wait", json=body()).status_code == 503


# ---- validation / hardening ----------------------------------------------------
@pytest.mark.parametrize("kw", [
    {"timeout_s": "1"}, {"timeout_s": 301}, {"timeout_s": 0}, {"bogus": 1},
    {"sender_domains": [""]}, {"sender_domains": ["*"]}, {"sender_domains": ["."]}, {"sender_domains": ["a b.com"]},
    {"allowed_link_hosts": ["*"]},
    {"after": "0001-01-01T00:00:00+00:00"}, {"after": "0001-01-02T00:00:00Z"},
    {"after": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
    {"after": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()},
])
def test_rejects_bad_requests(client, kw):
    assert client.post("/v1/wait", json=body(**kw)).status_code == 422


def test_docs_disabled(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_bearer_token_required(tmp_path):
    tok = tmp_path / "tok"
    cfg = main.Config("me@gmail.com", tmp_path / "pw", tmp_path / "db", False, tok)
    with TestClient(main.create_app(cfg, StubMailbox([make_msg(1, "Your code is 515151")]))) as c:
        assert (tok.stat().st_mode & 0o777) == 0o600
        token = tok.read_text().strip()
        assert c.post("/v1/wait", json=body()).status_code == 401
        assert c.post("/v1/wait", json=body(), headers={"Authorization": "Bearer nope"}).status_code == 401
        assert c.get("/health").status_code == 200  # health stays open
        r = c.post("/v1/wait", json=body(), headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200 and r.json()["code"] == "515151"
