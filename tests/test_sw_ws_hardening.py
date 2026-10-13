"""Service-worker / WebSocket hardening: per-scope SW violations and live-mode page guard.

Covers: a service worker on a non-approved origin is flagged (never ignored); SW violations are
per JobScope and never leak into other jobs' route handlers; LIVE_NET_GUARD_JS blocks WebSocket and
service-worker registration without blocking submits; adopt() installs the WebSocket block in LIVE
mode and fails closed when route_web_socket is unavailable. Pure tests: no network, no browser.
playwright is stubbed because apply_pipeline imports it at module level.
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


class _LivePage:
    """Fake page with route_web_socket; hashable so it can live in scope._explicit_adopt."""

    def __init__(self):
        self.init_scripts = []
        self.ws_patterns = []
        self.on = MagicMock()
        self.url = ALLOWED_URL

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def route_web_socket(self, pattern, handler):
        self.ws_patterns.append(pattern)

    def is_closed(self):
        return False

    def opener(self):
        return None


class _NoWsPage:
    """Fake page WITHOUT route_web_socket (old Playwright / interception unavailable)."""

    def __init__(self):
        self.init_scripts = []
        self.on = MagicMock()
        self.url = ALLOWED_URL

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def is_closed(self):
        return False

    def opener(self):
        return None


def test_service_worker_unapproved_origin_is_flagged(monkeypatch):
    monkeypatch.setattr(ap, "origin_allowed", _allow_only_approved)
    worker = SimpleNamespace(url=EVIL_SW)
    scope = ap.JobScope(MagicMock(), "j")
    # The job itself has a page on the evil origin, so a worker there is in scope for this job.
    scope.owned.append(SimpleNamespace(url="https://evil.example/apply"))

    scope._service_worker(worker)

    assert scope.violation == "service_worker_unapproved_origin"
    assert scope.sw_violations == [EVIL_SW]


def test_service_worker_from_foreign_tab_is_ignored(monkeypatch):
    monkeypatch.setattr(ap, "origin_allowed", _allow_only_approved)
    scope = ap.JobScope(MagicMock(), "j")

    scope._service_worker(SimpleNamespace(url=EVIL_SW))

    assert scope.violation is None
    assert scope.sw_violations == []


def test_sw_violation_does_not_leak_across_scopes(monkeypatch):
    monkeypatch.setattr(ap, "origin_allowed", lambda url: True)
    monkeypatch.setattr(ap, "net_decision", lambda *a, **k: (True, ""))

    ctx1 = MagicMock()
    ctx1.pages = []
    scope1 = ap.JobScope(ctx1, "j1")
    # Owned page on the worker's origin -> the worker is attributed to this job and flagged on scope1 only.
    scope1.owned.append(SimpleNamespace(url=ALLOWED_URL + "/apply"))
    scope1._service_worker(SimpleNamespace(url=ALLOWED_URL + "/sw.js"))
    assert scope1.sw_violations == [ALLOWED_URL + "/sw.js"]
    assert scope1.violation == "service_worker"

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

    # A pre-existing foreign tab's request is left alone even through the violated job's handler.
    foreign_page = MagicMock()
    scope1.before = [foreign_page]
    assert scope1.owns(foreign_page) is False
    foreign_request = SimpleNamespace(
        frame=SimpleNamespace(page=foreign_page, parent_frame=None),
        method="GET", resource_type="document", url=ALLOWED_URL, headers={},
    )
    route_foreign = _route()
    handler1(route_foreign, foreign_request)
    route_foreign.fulfill.assert_called_once()
    route_foreign.abort.assert_not_called()


def test_live_net_guard_js_blocks_ws_and_sw_register_not_submit():
    js = ap.LIVE_NET_GUARD_JS
    assert "WebSocket" in js
    assert "serviceWorker" in js
    assert "submit" not in js.lower()
    assert "preventDefault" not in js


def test_adopt_blocks_websocket_in_live_mode(monkeypatch):
    monkeypatch.setattr(ap, "DRY_RUN", False)
    monkeypatch.setattr(ap, "origin_allowed", lambda url: True)
    page = _LivePage()
    scope = ap.JobScope(MagicMock(), "j")
    scope._explicit_adopt.add(page)

    assert scope.adopt(page) is True

    assert any("WebSocket" in s for s in page.init_scripts)
    assert ap.DRY_RUN_JS not in page.init_scripts
    assert page.ws_patterns == ["**/*"]


def test_adopt_fails_closed_when_ws_interception_unavailable_in_live_mode(monkeypatch):
    monkeypatch.setattr(ap, "DRY_RUN", False)
    monkeypatch.setattr(ap, "origin_allowed", lambda url: True)
    page = _NoWsPage()
    scope = ap.JobScope(MagicMock(), "j")
    scope._explicit_adopt.add(page)

    with pytest.raises(ap.JobEnd) as exc:
        scope.adopt(page)

    assert "websocket_interception_unavailable" in str(exc.value)
