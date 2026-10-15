"""Service-worker / worker tripwires and the route's fail-closed rules for a per-job context.

Covers: any serviceworker or worker event in a job's context is a violation (the browser and the
NO_WORKERS_JS init script refuse both, so an event means a bypass); violations are per JobScope and
never leak into other jobs' route handlers; a detached scope's callbacks do nothing and its route
aborts everything; LIVE_NET_GUARD_JS blocks WebSocket and service-worker registration without
blocking submits. attach()/detach() wiring is in test_job_context.py. Pure tests: no network, no
browser. playwright is stubbed because apply_pipeline imports it at module level.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_sentinel = object()
_had = {name: sys.modules.get(name, _sentinel) for name in ("playwright", "playwright.sync_api")}
_pw = MagicMock()
_pw_sync = MagicMock()
_pw_sync.Error = Exception
_pw_sync.TimeoutError = Exception
sys.modules["playwright"] = _pw
sys.modules["playwright.sync_api"] = _pw_sync
try:
    import apply_pipeline as ap  # noqa: E402
finally:
    for _name, _prev in _had.items():
        if _prev is _sentinel:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _prev
del _sentinel, _had, _pw, _pw_sync, _name, _prev

APPROVED = "https://boards.example.com"
ALLOWED_URL = "https://boards.example.com/acme/jobs/123"
EVIL_SW = "https://evil.example/sw.js"


def _allow_only_approved(url):
    return str(url or "").startswith(APPROVED)


def _get_request(url=ALLOWED_URL):
    # frame=None -> handler treats it as a detached/service-worker style request (owner unknown).
    return SimpleNamespace(frame=None, method="GET", resource_type="document", url=url, headers={})


def _route():
    route = MagicMock()
    route.fetch.return_value = SimpleNamespace(status=200, headers={})
    return route


def test_service_worker_event_is_a_tripwire(monkeypatch):
    # The context blocks service workers (service_workers="block"): any serviceworker event means the
    # block failed, on whatever origin -- there is no foreign tab to be lenient about any more.
    monkeypatch.setattr(ap, "origin_allowed", _allow_only_approved)
    scope = ap.JobScope(MagicMock(), "j")

    scope._service_worker(SimpleNamespace(url=ALLOWED_URL + "/sw.js"))

    assert scope.violation == "service_worker_despite_block"
    assert scope.sw_violations == [ALLOWED_URL + "/sw.js"]


def test_worker_event_is_a_tripwire():
    scope = ap.JobScope(MagicMock(), "j")

    scope._worker(SimpleNamespace(url="blob:https://boards.example.com/1"))

    assert scope.violation == "worker_despite_block"
    assert scope.worker_violations == ["blob:https://boards.example.com/1"]


def test_detached_scope_callbacks_do_nothing():
    # A leaked context's late events must not act under (or leak into) the next job's policy.
    scope = ap.JobScope(MagicMock(), "j")
    scope.detach()

    scope._service_worker(SimpleNamespace(url=EVIL_SW))
    scope._worker(SimpleNamespace(url=EVIL_SW))

    assert scope.violation is None and scope.sw_violations == [] and scope.worker_violations == []
    assert scope.adopt(MagicMock()) is False


def test_sw_violation_does_not_leak_across_scopes(monkeypatch):
    monkeypatch.setattr(ap, "origin_allowed", lambda url: True)
    monkeypatch.setattr(ap, "net_decision", lambda *a, **k: (True, ""))

    scope1 = ap.JobScope(MagicMock(), "j1")
    scope1._service_worker(SimpleNamespace(url=ALLOWED_URL + "/sw.js"))
    assert scope1.sw_violations == [ALLOWED_URL + "/sw.js"]

    scope2 = ap.JobScope(MagicMock(), "j2")
    assert scope2.sw_violations == []
    handler2 = ap.make_route_handler(scope2)
    route2 = _route()
    handler2(route2, _get_request())
    route2.fulfill.assert_called_once()
    route2.abort.assert_not_called()

    handler1 = ap.make_route_handler(scope1)
    route1 = _route()
    handler1(route1, _get_request())
    route1.abort.assert_called_once_with("blockedbyclient")
    route1.fulfill.assert_not_called()


@pytest.mark.parametrize("trip", ["sw", "worker", "detached"])
def test_route_handler_fails_closed_after_a_tripwire_or_detach(monkeypatch, trip):
    monkeypatch.setattr(ap, "origin_allowed", lambda url: True)
    monkeypatch.setattr(ap, "net_decision", lambda *a, **k: (True, ""))
    scope = ap.JobScope(MagicMock(), "j")
    handler = ap.make_route_handler(scope)
    if trip == "sw":
        scope._service_worker(SimpleNamespace(url=ALLOWED_URL + "/sw.js"))
    elif trip == "worker":
        scope._worker(SimpleNamespace(url=ALLOWED_URL + "/w.js"))
    else:
        scope.detach()
    route = _route()
    handler(route, _get_request())
    route.abort.assert_called_once_with("blockedbyclient")
    route.fulfill.assert_not_called()


def test_live_net_guard_js_blocks_ws_and_sw_register_not_submit():
    js = ap.LIVE_NET_GUARD_JS
    assert "WebSocket" in js
    assert "serviceWorker" in js
    assert "submit" not in js.lower()
    assert "preventDefault" not in js
