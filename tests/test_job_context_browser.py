"""Per-job browser contexts against a real Chromium and local test pages (tests/fixtures/pipeline_site).

What is asserted is what reached the server -- WebSocket handshakes and POSTs -- never what the page
reports (a routed socket looks open to the page but connects to nothing). Every protection has a
sensitivity test: the same vector with that one protection removed must reach the server, so a test
cannot pass by measuring nothing. Skipped without Playwright/Chromium unless
PIPELINE_BROWSER_TESTS_REQUIRED=1 (the acceptance run), which turns the skip into a failure.
"""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parent / "fixtures" / "pipeline_site"
HITS = {"ws": 0, "post": 0, "sw": 0}
_LOCK = threading.Lock()


def _count(kind):
    with _LOCK:
        HITS[kind] += 1


def _reset():
    with _LOCK:
        for k in HITS:
            HITS[k] = 0


class _Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _count("post")
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/ws":
            if self.headers.get("Upgrade"):
                _count("ws")
            self.send_error(400)
            return
        if path == "/sw.js":
            _count("sw")
        name = path.lstrip("/") or "vectors.html"
        target = SITE / name
        if "/" in name or not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        ctype = "application/javascript" if name.endswith(".js") else "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sync_playwright = None
    if sync_playwright is None:
        if os.environ.get("PIPELINE_BROWSER_TESTS_REQUIRED") == "1":
            pytest.fail("Playwright required for the acceptance run")
        pytest.skip("playwright not installed")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:
            if os.environ.get("PIPELINE_BROWSER_TESTS_REQUIRED") == "1":
                pytest.fail(f"Chromium required for the acceptance run: {exc}")
            pytest.skip(f"no launchable Chromium here: {exc}")
        yield b
        b.close()


@pytest.fixture(scope="module")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def ap(site, monkeypatch):
    import apply_pipeline
    apply_pipeline.register_local_origin(site)
    monkeypatch.setattr(apply_pipeline, "DRY_RUN", True)
    apply_pipeline.BROWSER_UNUSABLE.clear()
    return apply_pipeline


def _visit(ap, browser, site, vector, wait_ms=2500):
    _reset()
    scope = ap.JobScope(browser, "t")
    scope.attach()
    try:
        page = scope.open_page()
        page.goto(f"{site}/vectors.html?v={vector}", wait_until="domcontentloaded", timeout=10000)
        page.wait_for_timeout(wait_ms)
        events = page.evaluate("window.__events || []")
        refused = scope.worker_refusals()
        violation = scope.violation
    finally:
        scope.detach()
    return dict(HITS), events, refused, violation


VECTORS = ["main", "popup", "worker-classic", "worker-module", "worker-blob", "worker-shared"]


@pytest.mark.parametrize("vector", VECTORS)
def test_nothing_reaches_the_server_from_any_vector_in_dry_run(ap, browser, site, vector):
    hits, events, _, _ = _visit(ap, browser, site, vector)
    assert hits["ws"] == 0 and hits["post"] == 0, (vector, hits, events)


@pytest.mark.parametrize("vector", VECTORS)
def test_live_mode_never_lets_a_websocket_or_a_worker_through(ap, browser, site, monkeypatch, vector):
    # Live mode must submit, so a page's own POST is allowed; sockets and workers never are.
    monkeypatch.setattr(ap, "DRY_RUN", False)
    hits, events, _, _ = _visit(ap, browser, site, vector)
    assert hits["ws"] == 0, (vector, hits, events)
    if vector.startswith("worker"):
        assert hits["post"] == 0, (vector, hits, events)


@pytest.mark.parametrize("vector", ["worker-classic", "worker-module", "worker-shared"])
def test_worker_constructions_are_refused_and_counted(ap, browser, site, vector):
    _, events, refused, violation = _visit(ap, browser, site, vector)
    assert refused >= 1
    assert any("workers are blocked" in e for e in events), events
    assert violation is None  # refused before any worker existed: nothing for the backstop to trip on


def test_service_worker_is_never_fetched(ap, browser, site):
    hits, events, _, violation = _visit(ap, browser, site, "sw")
    assert hits["sw"] == 0
    assert "sw-registered" not in events
    assert violation is None


# ── sensitivity: remove one protection, the matching vector must reach the server ──

def test_without_the_worker_block_the_backstop_trips_on_a_dedicated_worker(ap, browser, site, monkeypatch):
    # Second layer on its own: page.on("worker") marks the job violated and the route then aborts.
    monkeypatch.setattr(ap, "NO_WORKERS_JS", "/* removed for the sensitivity test */")
    _, _, _, violation = _visit(ap, browser, site, "worker-classic")
    assert violation == "worker_despite_block"


def test_without_both_worker_layers_a_dedicated_worker_posts_in_live_mode(ap, browser, site,
                                                                                       monkeypatch):
    # Live mode, where the route lets non-document requests through: with the refusal and the
    # backstop both removed, context.route_web_socket alone does not stop a worker's socket.
    monkeypatch.setattr(ap, "DRY_RUN", False)
    monkeypatch.setattr(ap, "NO_WORKERS_JS", "/* removed */")
    monkeypatch.setattr(ap.JobScope, "_worker", lambda self, worker: None)
    hits, events, _, _ = _visit(ap, browser, site, "worker-classic")
    # The worker ran and its POST reached the server. (Its socket did not: the pipeline's route
    # answers requests with route.fetch, which cannot complete a WebSocket upgrade.)
    assert hits["post"] >= 1, (hits, events)


def test_without_the_worker_block_a_shared_worker_posts_to_the_server_even_in_dry_run(ap, browser, site,
                                                                                    monkeypatch):
    # Shared workers raise no Playwright event and context.route does not see their HTTP, so the
    # script refusal is their only layer -- even the dry-run route does not stop this POST.
    monkeypatch.setattr(ap, "NO_WORKERS_JS", "/* removed */")
    hits, events, _, _ = _visit(ap, browser, site, "worker-shared")
    assert hits["post"] >= 1, (hits, events)


def test_native_websocket_route_alone_holds_the_main_page(ap, browser, site, monkeypatch):
    # JS overrides removed: only context.route_web_socket stands between the page and the server.
    monkeypatch.setattr(ap, "DRY_RUN_JS", "/* removed */")
    monkeypatch.setattr(ap, "LIVE_NET_GUARD_JS", "/* removed */")
    hits, events, _, _ = _visit(ap, browser, site, "main")
    assert hits["ws"] == 0 and "ws-throw" not in events  # the socket was really attempted, and routed


def test_without_any_protection_the_main_page_reaches_the_server(browser, site):
    # Control for the test above: a plain context, same page, same counters.
    _reset()
    ctx = browser.new_context()
    try:
        page = ctx.new_page()
        page.goto(f"{site}/vectors.html?v=main", wait_until="domcontentloaded", timeout=10000)
        page.wait_for_timeout(2000)
    finally:
        ctx.close()
    assert HITS["ws"] >= 1 and HITS["post"] >= 1


def test_js_overrides_alone_hold_the_main_page(ap, browser, site, monkeypatch):
    # The other layer on its own: no native WebSocket route, only DRY_RUN_JS.
    monkeypatch.setattr(ap, "_swallow_websocket", None)
    _reset()
    ctx = browser.new_context()
    ctx.add_init_script(ap.DRY_RUN_JS)
    try:
        page = ctx.new_page()
        page.goto(f"{site}/vectors.html?v=main", wait_until="domcontentloaded", timeout=10000)
        page.wait_for_timeout(2000)
        events = page.evaluate("window.__events")
    finally:
        ctx.close()
    assert HITS["ws"] == 0 and "ws-throw" in events


# ── self-test and lifecycle ───────────────────────────────────────────────────

@pytest.mark.parametrize("dry_run", [True, False])
def test_network_self_test_passes_in_both_modes(ap, browser, monkeypatch, dry_run):
    monkeypatch.setattr(ap, "DRY_RUN", dry_run)
    base = ap.start_test_server()
    ap.register_local_origin(base)
    assert ap.network_self_test(browser, base) is True


def test_network_self_test_fails_without_the_worker_block(ap, browser, monkeypatch):
    monkeypatch.setattr(ap, "NO_WORKERS_JS", "/* removed */")
    base = ap.start_test_server()
    ap.register_local_origin(base)
    assert ap.network_self_test(browser, base) is False


def test_contexts_do_not_accumulate(ap, browser, site):
    for _ in range(20):
        scope = ap.JobScope(browser, "t")
        scope.attach()
        scope.open_page().goto(f"{site}/vectors.html?v=none", wait_until="domcontentloaded")
        scope.detach()
    assert browser.contexts == []
    assert not ap.BROWSER_UNUSABLE.is_set()


def test_one_jobs_storage_never_reaches_the_next(ap, browser, site):
    first = ap.JobScope(browser, "a")
    first.attach()
    page = first.open_page()
    page.goto(f"{site}/vectors.html?v=none", wait_until="domcontentloaded")
    page.evaluate("() => { localStorage.setItem('k', 'job-a'); document.cookie = 'c=job-a; path=/'; }")
    first.detach()
    second = ap.JobScope(browser, "b")
    second.attach()
    try:
        page = second.open_page()
        page.goto(f"{site}/vectors.html?v=none", wait_until="domcontentloaded")
        assert page.evaluate("() => [localStorage.getItem('k'), document.cookie]") == [None, ""]
    finally:
        second.detach()
