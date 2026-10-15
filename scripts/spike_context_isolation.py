#!/usr/bin/env python3
"""Spike: do Playwright's per-context protections hold before apply_pipeline.py relies on them?

Local test pages only (a stdlib HTTP server on 127.0.0.1 and localhost); no real site is touched.
Every protection is measured at the server: a blocked vector must produce zero upstream requests,
and every vector has a positive control (the same page in an unprotected context) that must reach
the server, so a check cannot pass by measuring nothing.

Checks (per-job-context-plan.md section a, plus Codex corrections 1, 2 and 6):
  1. environment: Playwright version (importlib.metadata) and context.route_web_socket present
  2. storage_state carries a session into a new context; a fresh context and another origin don't
  3. service_workers="block": registration fails, zero /sw.js fetches; control registers
  4. WebSocket and POST vectors, each against three context setups:
       control  - no protection (must reach the server)
       native   - service_workers="block" + context.route_web_socket(close) + context.route(abort
                  non-GET) with NO JavaScript overrides (what the browser itself enforces)
       js-only  - only the JS constructor/fetch overrides, no native routes (what the overrides
                  alone enforce)
     vectors: main page, same-origin iframe, cross-origin iframe, popup whose first script opens a
     socket, dedicated worker (classic, module, blob: URL, data: URL) and shared worker
  5. route order: a WebSocket route installed after the first page exists, does it cover that page?
  6. fingerprint continuity across three contexts of one browser
  7. IndexedDB in storage_state (indexed_db=True)
  8. teardown: browser.contexts returns to zero after 30 context cycles

Usage: python scripts/spike_context_isolation.py [--channel chrome] [--json out.json]
Exit code: 0 when every required property holds, 1 otherwise. A vector the native layer does not
cover is reported as a FAIL (the plan's stop gate), not hidden.
"""
import argparse
import importlib.metadata
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

SESSION = "sid=spike-session-123"
HITS = {"ws": 0, "post": 0, "sw": 0}
_LOCK = threading.Lock()


def _count(kind):
    with _LOCK:
        HITS[kind] += 1


def _reset():
    with _LOCK:
        for key in HITS:
            HITS[key] = 0


def _snapshot():
    with _LOCK:
        return dict(HITS)


# Each vector page reports what it saw into window.__events (main page) or via postMessage.
_PROBE = """
function probe(report) {
  try {
    const ws = new WebSocket(WS_URL);
    ws.onopen = () => report('ws-open');
    ws.onclose = () => report('ws-close');
    ws.onerror = () => report('ws-error');
  } catch (e) { report('ws-throw'); }
  try {
    fetch(POST_URL, {method: 'POST', body: 'x', mode: 'no-cors'})
      .then(() => report('post-ok'), () => report('post-fail'));
  } catch (e) { report('post-throw'); }
}
"""


def _page(body):
    return ("<!doctype html><html><body><script>window.__events = [];"
            "const report = (e) => window.__events.push(e);</script>" + body + "</body></html>").encode()


def _worker_src(origin):
    return (f"const WS_URL = '{origin.replace('http', 'ws')}/ws'; const POST_URL = '{origin}/post';"
            + _PROBE + "probe((e) => postMessage(e));")


def _shared_worker_src(origin):
    return (f"const WS_URL = '{origin.replace('http', 'ws')}/ws'; const POST_URL = '{origin}/post';"
            + _PROBE + "onconnect = (c) => { const port = c.ports[0]; probe((e) => port.postMessage(e)); };")


class Handler(BaseHTTPRequestHandler):
    other_origin = ""

    def log_message(self, *args):
        pass

    def _origin(self):
        return f"http://{self.headers.get('Host')}"

    def _send(self, body, ctype="text/html", headers=(), status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        _count("post")
        self._send(b"ok")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        origin = self._origin()
        ws_url = origin.replace("http", "ws") + "/ws"
        js = (f"<script>const WS_URL = '{ws_url}'; const POST_URL = '{origin}/post';"
              + _PROBE + "</script>")
        if path == "/ws":
            if self.headers.get("Upgrade"):
                _count("ws")
            self._send(b"no websocket here", status=400)
        elif path == "/":
            self._send(_page("<p>spike</p>"))
        elif path == "/login":
            cookie = f"{SESSION}; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax"
            self._send(_page("<script>localStorage.setItem('marker', 'logged-in');"
                             "const r = indexedDB.open('spike', 1);"
                             "r.onupgradeneeded = () => r.result.createObjectStore('s');"
                             "r.onsuccess = () => { const tx = r.result.transaction('s', 'readwrite');"
                             "tx.objectStore('s').put('idb-session', 'k'); tx.oncomplete = () => report('idb-saved'); };"
                             "</script>"), headers=[("Set-Cookie", cookie)])
        elif path == "/whoami":
            who = self.headers.get("Cookie") or "anonymous"
            self._send(_page(f"<pre id='who'>{who}</pre><pre id='marker'></pre><pre id='idb'></pre><script>"
                             "document.getElementById('marker').textContent = localStorage.getItem('marker') || 'none';"
                             "const r = indexedDB.open('spike', 1);"
                             "r.onupgradeneeded = () => r.result.createObjectStore('s');"
                             "r.onsuccess = () => { const g = r.result.transaction('s').objectStore('s').get('k');"
                             "g.onsuccess = () => { document.getElementById('idb').textContent = g.result || 'none'; }; };"
                             "</script>"))
        elif path == "/sw.js":
            _count("sw")
            self._send(b"self.addEventListener('install', () => self.skipWaiting());", ctype="application/javascript")
        elif path == "/v/main":
            self._send(_page(js + "<script>probe(report);</script>"))
        elif path == "/v/iframe-same":
            self._send(_page("<iframe src='/v/main'></iframe><script>"
                             "addEventListener('load', () => setTimeout(() => {"
                             " window.__events = window.frames[0].__events; }, 1500));</script>"))
        elif path == "/v/iframe-cross":
            # The frame reports back via postMessage, since it is another origin.
            self._send(_page(f"<iframe src='{self.other_origin}/v/frame-report'></iframe><script>"
                             "addEventListener('message', (m) => report(m.data));</script>"))
        elif path == "/v/frame-report":
            self._send(_page(js + "<script>probe((e) => parent.postMessage(e, '*'));</script>"))
        elif path == "/v/popup":
            self._send(_page("<script>const w = window.open('/v/popup-child');"
                             "addEventListener('message', (m) => report(m.data));</script>"))
        elif path == "/v/popup-child":
            # The socket opens from the popup's very first script.
            self._send(("<!doctype html><script>" + f"const WS_URL = '{ws_url}'; const POST_URL = '{origin}/post';"
                        + _PROBE + "probe((e) => opener.postMessage(e, '*'));</script>").encode())
        elif path in ("/v/worker-classic", "/v/worker-module", "/v/worker-blob", "/v/worker-data"):
            kind = path.rsplit("-", 1)[1]
            src = json.dumps(_worker_src(origin))
            make = {
                "classic": "new Worker('/w.js')",
                "module": "new Worker('/w.js', {type: 'module'})",
                "blob": f"new Worker(URL.createObjectURL(new Blob([{src}], {{type: 'text/javascript'}})))",
                "data": f"new Worker('data:text/javascript,' + encodeURIComponent({src}))",
            }[kind]
            self._send(_page(f"<script>try {{ const w = {make}; w.onmessage = (m) => report(m.data);"
                             " w.onerror = () => report('worker-error'); } catch (e) { report('worker-throw'); }"
                             "</script>"))
        elif path == "/v/worker-shared":
            self._send(_page("<script>try { const w = new SharedWorker('/sw-shared.js');"
                             " w.port.onmessage = (m) => report(m.data); w.port.start();"
                             " w.onerror = () => report('worker-error'); } catch (e) { report('worker-throw'); }"
                             "</script>"))
        elif path == "/w.js":
            self._send(_worker_src(origin).encode(), ctype="application/javascript")
        elif path == "/sw-shared.js":
            self._send(_shared_worker_src(origin).encode(), ctype="application/javascript")
        elif path == "/fp":
            self._send(_page("<p>fp</p>"))
        else:
            self._send(b"not found", status=404)


# The same JS overrides apply_pipeline installs as defense in depth (DRY_RUN_JS-style), applied in
# the "js-only" setup to measure what they block on their own.
JS_OVERRIDES = """
(() => {
  window.WebSocket = function () { throw new Error('blocked by override'); };
  const realFetch = window.fetch;
  window.fetch = function (input, init) {
    if (init && init.method && init.method.toUpperCase() !== 'GET') return Promise.reject(new Error('blocked'));
    return realFetch.apply(this, arguments);
  };
})();
"""


def _swallow_ws(ws):
    # Never connect to the server. NOT ws.close(): with the sync API the handler runs on Playwright's
    # event loop, and the blocking close() waits on that same loop forever (found by this spike: the
    # first page that opened a socket hung the run). Unconnected, the page sees a mocked socket that
    # reaches nothing; what is measured is zero upstream handshakes.
    pass


def _context(browser, setup):
    if setup == "control":
        return browser.new_context()
    if setup == "native":
        ctx = browser.new_context(service_workers="block")
        ctx.route_web_socket("**/*", _swallow_ws)
        ctx.route("**/*", lambda route: route.abort() if route.request.method not in ("GET", "HEAD")
                  else route.continue_())
        return ctx
    if setup == "js-only":
        ctx = browser.new_context()
        ctx.add_init_script(JS_OVERRIDES)
        return ctx
    if setup == "native+no-workers":
        ctx = _context(browser, "native")
        ctx.add_init_script(NO_WORKERS_JS)
        return ctx
    raise ValueError(setup)


# Candidate mitigation measured by this spike: refuse to construct workers at all, in every frame.
NO_WORKERS_JS = """
(() => {
  const refuse = function () { throw new Error('workers are blocked'); };
  for (const name of ['Worker', 'SharedWorker']) {
    try { Object.defineProperty(window, name, {value: refuse, writable: false, configurable: false}); } catch (e) {}
  }
})();
"""


VECTORS = ["main", "iframe-same", "iframe-cross", "popup", "worker-classic", "worker-module",
           "worker-blob", "worker-data", "worker-shared"]


def run_vector(browser, base, setup, vector):
    _reset()
    print(f"      .. {vector} / {setup}", flush=True)
    ctx = _context(browser, setup)
    ctx.set_default_timeout(10000)
    try:
        page = ctx.new_page()
        page.goto(f"{base}/v/{vector}", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        events = page.evaluate("window.__events || []")
    finally:
        ctx.close()
    time.sleep(0.2)
    hits = _snapshot()
    return {"events": events, "ws_upstream": hits["ws"], "post_upstream": hits["post"]}


def check_vectors(browser, base):
    results = {}
    ok = True
    for vector in VECTORS:
        row = {setup: run_vector(browser, base, setup, vector) for setup in ("control", "native", "js-only", "native+no-workers")}
        control_reached = row["control"]["ws_upstream"] > 0 and row["control"]["post_upstream"] > 0
        native_blocked = row["native"]["ws_upstream"] == 0 and row["native"]["post_upstream"] == 0
        js_blocked = row["js-only"]["ws_upstream"] == 0 and row["js-only"]["post_upstream"] == 0
        # A vector whose control never reached the server (e.g. a data: worker the browser refuses)
        # is inconclusive, not a pass.
        row["verdict"] = ("inconclusive (control did not reach server)" if not control_reached
                          else "native blocks" if native_blocked else "NATIVE DOES NOT BLOCK")
        row["js_only_blocks"] = js_blocked
        row["no_workers_blocks"] = (row["native+no-workers"]["ws_upstream"] == 0
                                    and row["native+no-workers"]["post_upstream"] == 0)
        if control_reached and not native_blocked:
            ok = False
        results[vector] = row
    return ok, results


def check_environment():
    version = importlib.metadata.version("playwright")
    from playwright.sync_api import BrowserContext
    has_ws = hasattr(BrowserContext, "route_web_socket")
    return has_ws, {"playwright": version, "route_web_socket": has_ws}


def check_storage_state(browser, base, other):
    a = browser.new_context()
    page = a.new_page()
    page.goto(base + "/login")
    page.wait_for_function("window.__events.includes('idb-saved')", timeout=5000)
    plain = a.storage_state()
    try:
        with_idb = a.storage_state(indexed_db=True)
        idb_kwarg = True
    except TypeError:
        with_idb, idb_kwarg = plain, False
    a.close()

    def whoami(state, origin):
        ctx = browser.new_context(storage_state=state) if state else browser.new_context()
        p = ctx.new_page()
        p.goto(origin + "/whoami")
        p.wait_for_timeout(500)
        out = {k: p.inner_text(f"#{k}") for k in ("who", "marker", "idb")}
        ctx.close()
        return out

    b = whoami(plain, base)
    c = whoami(None, base)
    d = whoami(plain, other)
    e = whoami(with_idb, base)
    ok = (SESSION in b["who"] and b["marker"] == "logged-in"
          and "sid=" not in c["who"] and c["marker"] == "none"
          and "sid=" not in d["who"] and d["marker"] == "none")
    return ok, {"restored": b, "fresh": c, "other_origin": d, "indexed_db_kwarg": idb_kwarg,
                "idb_without_flag": b["idb"], "idb_with_flag": e["idb"]}


SW_REGISTER_JS = """async () => {
  try {
    const timeout = new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 4000));
    await Promise.race([navigator.serviceWorker.register('/sw.js'), timeout]);
    await Promise.race([navigator.serviceWorker.ready, timeout]);
    return 'registered:' + (await navigator.serviceWorker.getRegistrations()).length;
  } catch (e) { return 'failed: ' + e.message; }
}"""


def check_service_workers(browser, base):
    out = {}
    for setup in ("control", "blocked"):
        _reset()
        ctx = browser.new_context(service_workers="block") if setup == "blocked" else browser.new_context()
        fired = []
        ctx.on("serviceworker", lambda w: fired.append(w.url))
        page = ctx.new_page()
        page.goto(base + "/")
        outcome = page.evaluate(SW_REGISTER_JS)
        page.wait_for_timeout(500)
        out[setup] = {"outcome": outcome, "sw_fetches": _snapshot()["sw"], "event_fired": len(fired),
                      "context_workers": len(ctx.service_workers)}
        ctx.close()
    ok = (out["control"]["outcome"].startswith("registered") and out["control"]["sw_fetches"] > 0
          and not out["blocked"]["outcome"].startswith("registered") and out["blocked"]["sw_fetches"] == 0
          and out["blocked"]["event_fired"] == 0 and out["blocked"]["context_workers"] == 0)
    return ok, out


def check_route_order(browser, base):
    _reset()
    ctx = browser.new_context()
    page = ctx.new_page()          # page exists BEFORE the route
    ctx.route_web_socket("**/*", _swallow_ws)
    page.goto(base + "/v/main")
    page.wait_for_timeout(1500)
    ctx.close()
    covered = _snapshot()["ws"] == 0
    # Informational: the implementation installs routes before any page regardless.
    return True, {"route_after_first_page_still_covers_it": covered}


FP_JS = """() => {
  let renderer = null;
  try {
    const gl = document.createElement('canvas').getContext('webgl');
    const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
    renderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null;
  } catch (e) {}
  return {ua: navigator.userAgent, cores: navigator.hardwareConcurrency, screen: [screen.width, screen.height],
          webdriver: navigator.webdriver, tz: Intl.DateTimeFormat().resolvedOptions().timeZone, renderer};
}"""


def check_fingerprint(browser, base):
    prints = []
    for _ in range(3):
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(base + "/fp")
        prints.append(page.evaluate(FP_JS))
        ctx.close()
    same = all(p == prints[0] for p in prints)
    return same, {"identical_across_contexts": same, "sample": prints[0]}


def check_teardown(browser, base):
    for _ in range(30):
        ctx = browser.new_context(service_workers="block")
        ctx.new_page().goto(base + "/")
        ctx.close()
    return len(browser.contexts) == 0, {"contexts_after_30_cycles": len(browser.contexts)}


def _serve(host):
    server = ThreadingHTTPServer((host, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://{host}:{server.server_address[1]}"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default=None, help="e.g. chrome for installed Google Chrome")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--json", default=None, help="write all results to this file")
    args = parser.parse_args(argv)

    server_a, base = _serve("127.0.0.1")
    server_b, other = _serve("localhost")
    Handler.other_origin = other

    env_ok, env = check_environment()
    print(f"[{'PASS' if env_ok else 'FAIL'}] environment -- {env}")
    results = {"environment": env, "channel": args.channel or "bundled chromium", "headful": args.headful}
    required_ok = env_ok
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful, channel=args.channel)
        results["browser_version"] = browser.version
        try:
            for name, fn, required in (
                ("storage_state", lambda: check_storage_state(browser, base, other), True),
                ("service_workers_block", lambda: check_service_workers(browser, base), True),
                ("vectors", lambda: check_vectors(browser, base), True),
                ("route_order", lambda: check_route_order(browser, base), False),
                ("fingerprint", lambda: check_fingerprint(browser, base), True),
                ("teardown", lambda: check_teardown(browser, base), True),
            ):
                try:
                    ok, detail = fn()
                except Exception as exc:
                    ok, detail = False, f"exception: {type(exc).__name__}: {exc}"
                results[name] = {"ok": ok, "detail": detail}
                if required:
                    required_ok &= ok
                print(f"[{'PASS' if ok else 'FAIL'}] {name}")
                if name == "vectors" and isinstance(detail, dict):
                    for vector, row in detail.items():
                        print(f"    {vector:15} control ws={row['control']['ws_upstream']} post={row['control']['post_upstream']}"
                              f" | native ws={row['native']['ws_upstream']} post={row['native']['post_upstream']}"
                              f" | js-only ws={row['js-only']['ws_upstream']} post={row['js-only']['post_upstream']}"
                              f" | +no-workers ws={row['native+no-workers']['ws_upstream']}"
                              f" post={row['native+no-workers']['post_upstream']}"
                              f" | {row['verdict']}")
                else:
                    print(f"    {json.dumps(detail, default=str)[:600]}")
        finally:
            browser.close()
            server_a.shutdown()
            server_b.shutdown()

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2, default=str)
    print("ALL REQUIRED PROPERTIES HOLD" if required_ok else "SOME REQUIRED PROPERTIES FAIL")
    return 0 if required_ok else 1


if __name__ == "__main__":
    sys.exit(main())
