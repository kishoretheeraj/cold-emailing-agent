"""Per-job browser contexts in apply_pipeline: JobScope.attach/detach wiring, fail-closed cleanup,
popup policing, the worker backstop, the browser lifecycle in _loop (self-test after every launch,
recycle on a failed context close or the job cap, stop when the browser can't be closed) and
structural guarantees (no CDP attach, no debugging port, no stealth flags). Pure tests with fake
browser objects; real-browser coverage is tests/test_job_context_browser.py. playwright is stubbed
because apply_pipeline imports it at module level.
"""

import inspect
import sys
from pathlib import Path
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

SOURCE = (Path(__file__).resolve().parent.parent / "apply_pipeline.py").read_text()


class FakePage:
    def __init__(self, url="about:blank"):
        self.url, self.closed, self.listeners, self.workers = url, False, [], []

    def on(self, event, cb):
        self.listeners.append(event)

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    def evaluate(self, js):
        return 2


class FakeContext:
    def __init__(self, calls, ws=True, ws_raises=False, close_raises=False):
        self.calls, self.pages, self.closed = calls, [], 0
        self._ws_raises, self._close_raises = ws_raises, close_raises
        if not ws:
            self.route_web_socket = None

    def route_web_socket(self, pattern, handler):
        self.calls.append(("route_web_socket", pattern, handler))
        if self._ws_raises:
            raise RuntimeError("no ws routing")

    def add_init_script(self, script):
        self.calls.append(("add_init_script", script))

    def on(self, event, cb):
        self.calls.append(("on", event))

    def route(self, pattern, handler):
        self.calls.append(("route", pattern))

    def new_page(self):
        self.calls.append(("new_page",))
        page = FakePage()
        self.pages.append(page)
        return page

    def close(self):
        self.closed += 1
        if self._close_raises:
            raise RuntimeError("close failed")


class FakeBrowser:
    def __init__(self, **ctx_kwargs):
        self.calls, self.kwargs, self.ctx_kwargs, self.contexts = [], None, ctx_kwargs, []

    def new_context(self, **kwargs):
        self.kwargs = kwargs
        ctx = FakeContext(self.calls, **self.ctx_kwargs)
        self.contexts.append(ctx)
        return ctx


@pytest.fixture(autouse=True)
def _clean_flag():
    ap.BROWSER_UNUSABLE.clear()
    yield
    ap.BROWSER_UNUSABLE.clear()


# ── attach ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dry_run", [True, False])
def test_attach_installs_every_protection_before_any_page(monkeypatch, dry_run):
    monkeypatch.setattr(ap, "DRY_RUN", dry_run)
    browser = FakeBrowser()
    scope = ap.JobScope(browser, "j")
    scope.attach()
    scope.open_page()

    assert browser.kwargs["service_workers"] == "block"
    assert browser.kwargs["accept_downloads"] is False
    names = [c[0] for c in browser.calls]
    first_page = names.index("new_page")
    assert names.index("route_web_socket") < first_page
    assert names.index("route") < first_page
    scripts = [c[1] for c in browser.calls if c[0] == "add_init_script"]
    assert scripts == [ap.NO_WORKERS_JS, ap.SUBMIT_GUARD_JS, ap.DRY_RUN_JS if dry_run else ap.LIVE_NET_GUARD_JS]
    assert ("on", "page") in browser.calls and ("on", "serviceworker") in browser.calls
    ws_call = next(c for c in browser.calls if c[0] == "route_web_socket")
    assert ws_call[2] is ap._swallow_websocket  # never the deadlocking ws.close() handler


def test_attach_passes_storage_state_and_viewport_only_when_headful(monkeypatch):
    monkeypatch.setattr(ap.config, "APPLY_BROWSER_HEADLESS", False)
    browser = FakeBrowser()
    ap.JobScope(browser, "j", storage_state={"cookies": []}).attach()
    assert browser.kwargs["storage_state"] == {"cookies": []}
    assert browser.kwargs["no_viewport"] is True

    monkeypatch.setattr(ap.config, "APPLY_BROWSER_HEADLESS", True)
    browser = FakeBrowser()
    ap.JobScope(browser, "j").attach()
    assert "no_viewport" not in browser.kwargs and "storage_state" not in browser.kwargs


@pytest.mark.parametrize("ctx_kwargs", [{"ws": False}, {"ws_raises": True}])
def test_attach_without_websocket_interception_closes_the_context_and_blocks(ctx_kwargs):
    browser = FakeBrowser(**ctx_kwargs)
    scope = ap.JobScope(browser, "j")
    with pytest.raises(ap.JobEnd) as exc:
        scope.attach()
    assert "websocket_interception_unavailable" in str(exc.value)
    assert browser.contexts[0].closed == 1
    assert scope.context is None and ("new_page",) not in browser.calls


def test_attach_failure_whose_cleanup_also_fails_marks_the_browser_unusable():
    browser = FakeBrowser(ws=False, close_raises=True)
    with pytest.raises(ap.JobEnd):
        ap.JobScope(browser, "j").attach()
    assert ap.BROWSER_UNUSABLE.is_set()


# ── detach ───────────────────────────────────────────────────────────────────

def test_detach_closes_the_context_once_and_is_idempotent():
    browser = FakeBrowser()
    scope = ap.JobScope(browser, "j")
    scope.attach()
    scope.detach()
    scope.detach()
    assert browser.contexts[0].closed == 1
    assert scope.context is None and scope.live is False
    assert not ap.BROWSER_UNUSABLE.is_set()


def test_detach_close_failure_never_raises_and_marks_the_browser_unusable():
    browser = FakeBrowser(close_raises=True)
    scope = ap.JobScope(browser, "j")
    scope.attach()
    scope.detach()  # must not raise
    assert ap.BROWSER_UNUSABLE.is_set()


# ── pages, popups, workers ────────────────────────────────────────────────────

def test_popup_on_unapproved_host_is_closed_and_main_is_not(monkeypatch):
    monkeypatch.setattr(ap, "origin_allowed", lambda url: url.startswith("https://ok.example"))
    scope = ap.JobScope(FakeBrowser(), "j")
    scope.attach()
    main = scope.open_page()
    popup = FakePage("https://evil.example/x")
    assert scope.adopt(popup) is True
    assert popup in scope.owned and "framenavigated" in popup.listeners and "worker" in popup.listeners
    assert popup.closed and scope.violation == "popup_host_not_allowed"
    main.url = "https://evil.example/main"
    scope._police(main)
    assert not main.closed


def test_check_catches_a_worker_whose_event_was_missed():
    scope = ap.JobScope(FakeBrowser(), "j")
    scope.attach()
    page = scope.open_page()
    page.workers.append(SimpleNamespace(url="blob:x"))
    with pytest.raises(ap.JobEnd) as exc:
        scope.check()
    assert "worker_despite_block" in str(exc.value)


def test_worker_refusals_sums_open_pages_and_degrades_to_unreadable():
    scope = ap.JobScope(FakeBrowser(), "j")
    scope.attach()
    scope.open_page()
    scope.open_page()
    assert scope.worker_refusals() == 4
    bad = FakePage()
    bad.evaluate = lambda js: (_ for _ in ()).throw(RuntimeError("gone"))
    scope.owned.append(bad)
    assert scope.worker_refusals() == -1


def test_no_workers_script_refuses_both_constructors_and_counts_tamper_proof():
    js = ap.NO_WORKERS_JS
    assert "'Worker', 'SharedWorker'" in js
    assert "configurable: false" in js and "writable: false" in js
    assert "__pipelineWorkerRefusals" in js and "__pipelineWorkerRefusals" in ap.WORKER_REFUSALS_JS


# ── browser lifecycle (_loop) ────────────────────────────────────────────────

class LoopBrowser:
    def __init__(self, close_ok=True):
        self.closed, self.close_ok = 0, close_ok

    def is_connected(self):
        return True

    def close(self):
        self.closed += 1
        if not self.close_ok:
            raise RuntimeError("cannot close")

    def on(self, *a):
        pass


def _run_loop(monkeypatch, jobs, browsers, on_job=None, max_jobs=100):
    pending = list(jobs)
    launched, selftests, processed = [], [], []
    monkeypatch.setattr(ap, "BROWSER_MAX_JOBS", max_jobs)
    monkeypatch.setattr(ap, "recover_in_progress_ex", lambda: {})
    monkeypatch.setattr(ap, "load_queue", lambda: [])
    monkeypatch.setattr(ap, "pending_jobs", lambda queue: list(pending))
    monkeypatch.setattr(ap, "ev", lambda *a, **k: None)

    def launch(pw):
        b = browsers.pop(0)
        launched.append(b)
        ap.BROWSER_UNUSABLE.clear()
        return b

    def process(browser, job, test_base):
        processed.append((browser, job["id"]))
        pending.remove(job)
        if on_job:
            on_job(job)
        return "dry_run_ok"

    monkeypatch.setattr(ap, "launch_browser", launch)
    monkeypatch.setattr(ap, "network_self_test", lambda browser, base: selftests.append(browser) or True)
    monkeypatch.setattr(ap, "process_job", process)
    rc = ap._loop(object(), True, False, "http://127.0.0.1:1", [], set(), lambda out: False, 0)
    return rc, launched, selftests, processed


def test_failed_context_close_recycles_the_browser_and_reruns_the_self_test(monkeypatch):
    b1, b2 = LoopBrowser(), LoopBrowser()
    jobs = [{"id": "a"}, {"id": "b"}]
    rc, launched, selftests, processed = _run_loop(
        monkeypatch, jobs, [b1, b2], on_job=lambda job: ap.BROWSER_UNUSABLE.set() if job["id"] == "a" else None)
    assert rc == 0
    assert launched == [b1, b2] and selftests == [b1, b2]
    assert processed == [(b1, "a"), (b2, "b")]
    assert b1.closed == 1 and b2.closed == 1  # recycled, then closed on the way out


def test_job_cap_recycles_between_jobs(monkeypatch):
    b1, b2 = LoopBrowser(), LoopBrowser()
    rc, launched, selftests, _ = _run_loop(monkeypatch, [{"id": "a"}, {"id": "b"}], [b1, b2], max_jobs=1)
    assert rc == 0 and launched == [b1, b2] and selftests == [b1, b2]


def test_browser_that_cannot_be_closed_stops_the_loop(monkeypatch):
    b1 = LoopBrowser(close_ok=False)
    b1.is_connected = lambda: True
    rc, launched, _, processed = _run_loop(
        monkeypatch, [{"id": "a"}, {"id": "b"}], [b1, LoopBrowser()],
        on_job=lambda job: ap.BROWSER_UNUSABLE.set())
    assert rc == 2
    assert launched == [b1] and processed == [(b1, "a")]  # job b never ran next to the leaked context


def test_failed_self_test_runs_no_job(monkeypatch):
    monkeypatch.setattr(ap, "recover_in_progress_ex", lambda: {})
    monkeypatch.setattr(ap, "load_queue", lambda: [])
    monkeypatch.setattr(ap, "pending_jobs", lambda queue: [{"id": "a"}])
    monkeypatch.setattr(ap, "ev", lambda *a, **k: None)
    browser = LoopBrowser()
    monkeypatch.setattr(ap, "launch_browser", lambda pw: browser)
    monkeypatch.setattr(ap, "network_self_test", lambda b, base: False)
    process = MagicMock()
    monkeypatch.setattr(ap, "process_job", process)
    rc = ap._loop(object(), True, False, "http://127.0.0.1:1", [], set(), lambda out: False, 0)
    assert rc == 2 and process.call_count == 0 and browser.closed == 1


def test_display_lock_is_held_for_the_browser_lifetime_and_released(monkeypatch, tmp_path):
    lock = str(tmp_path / "display2.lock")
    monkeypatch.setattr(ap, "DISPLAY_LOCK_PATH", lock)
    held = []

    def on_job(job):
        held.append(ap.take_display_lock(lock))  # another taker while the pipeline runs

    _run_loop(monkeypatch, [{"id": "a"}], [LoopBrowser()], on_job=on_job)
    assert held == [None]  # busy during the job
    again = ap.take_display_lock(lock)
    assert again is not None  # released on the way out
    ap.release_display_lock(again)


def test_busy_display_launches_nothing(monkeypatch, tmp_path):
    lock = str(tmp_path / "display2.lock")
    monkeypatch.setattr(ap, "DISPLAY_LOCK_PATH", lock)
    other = ap.take_display_lock(lock)
    try:
        rc, launched, _, processed = _run_loop(monkeypatch, [{"id": "a"}], [LoopBrowser()])
    finally:
        ap.release_display_lock(other)
    assert rc == 1 and launched == [] and processed == []


def test_launch_browser_uses_no_debug_port_and_no_stealth_flags(monkeypatch):
    pw = MagicMock()
    monkeypatch.setattr(ap, "ev", lambda *a, **k: None)
    ap.launch_browser(pw)
    kwargs = pw.chromium.launch.call_args.kwargs
    assert kwargs["args"] == ["--disable-dev-shm-usage"]
    assert "headless" in kwargs and "channel" in kwargs and "executable_path" in kwargs


# ── structural ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("gone", ["connect_over_cdp", "remote-debugging-port", "AutomationControlled",
                                  "contexts[0]", "CDP_URL", "lambda ws: ws.close()"])
def test_removed_shared_browser_machinery_stays_gone(gone):
    assert gone not in SOURCE


def test_jobscope_has_no_shared_context_machinery():
    src = inspect.getsource(ap.JobScope)
    for gone in ("self.before", "def owns", "closable", "_opened_by_job", "def refresh", "_explicit_adopt"):
        assert gone not in src
