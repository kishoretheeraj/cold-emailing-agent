#!/usr/bin/env python3
"""Spike: check Playwright context-isolation features before making production changes.

Checks:
  1. storage_state carries a login session into a new context.
  2. service_workers="block" stops service worker registration.
  3. context.route_web_socket blocks WebSockets.

Usage: python scripts/spike_context_isolation.py
Exit code: 0 if every check passes, otherwise 1.
"""
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

SESSION = "sid=spike-session-123"
WS_HITS = []  # requests that actually reached the server's /ws endpoint

SW_JS = b"self.addEventListener('install', () => self.skipWaiting());\n"
WS_HTML = b"""<!doctype html><html><body><script>
window.__events = [];
const ws = new WebSocket('ws://' + location.host + '/ws');
ws.onopen = () => window.__events.push('open');
ws.onerror = () => window.__events.push('error');
ws.onclose = () => window.__events.push('close');
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body, ctype="text/html", headers=(), status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(b"<!doctype html><title>spike</title>")
        elif path == "/login":
            cookie = f"{SESSION}; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax"
            self._send(b"<p>logged in</p>", headers=[("Set-Cookie", cookie)])
        elif path == "/whoami":
            who = self.headers.get("Cookie") or "anonymous"
            self._send(f"<pre id='who'>{who}</pre>".encode())
        elif path == "/sw.js":
            self._send(SW_JS, ctype="application/javascript")
        elif path == "/ws.html":
            self._send(WS_HTML)
        elif path == "/ws":
            WS_HITS.append(self.headers.get("Upgrade"))
            self._send(b"no websocket here", status=400)
        else:
            self._send(b"not found", status=404)


def test_storage_state(browser, base):
    a = browser.new_context()
    a.new_page().goto(base + "/login")
    state = a.storage_state()
    a.close()
    saved = any(c["name"] == "sid" for c in state["cookies"])

    b = browser.new_context(storage_state=state)
    pb = b.new_page()
    pb.goto(base + "/whoami")
    who_b = pb.inner_text("#who")
    b.close()

    c = browser.new_context()
    pc = c.new_page()
    pc.goto(base + "/whoami")
    who_c = pc.inner_text("#who")
    c.close()

    ok = saved and SESSION in who_b and "sid=" not in who_c
    return ok, f"saved={saved} B={who_b!r} C={who_c!r}"


SW_CONTROL_JS = """async () => {
  const timeout = new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 5000));
  await Promise.race([navigator.serviceWorker.register('/sw.js'), timeout]);
  await Promise.race([navigator.serviceWorker.ready, timeout]);
  return (await navigator.serviceWorker.getRegistrations()).length;
}"""

SW_BLOCKED_JS = """async () => {
  let outcome;
  try {
    const r = await navigator.serviceWorker.register('/sw.js');
    outcome = r ? 'registered' : 'resolved-without-registration';
  } catch (e) {
    outcome = 'rejected: ' + e.message;
  }
  await new Promise(r => setTimeout(r, 1000));
  let regs = 0;
  try { regs = (await navigator.serviceWorker.getRegistrations()).length; } catch (e) {}
  return {outcome, regs};
}"""


def test_service_workers_block(browser, base):
    # Control: without blocking, registration must work, otherwise the check proves nothing.
    ctrl = browser.new_context()
    page = ctrl.new_page()
    page.goto(base + "/")
    try:
        ctrl_regs = page.evaluate(SW_CONTROL_JS)
    except Exception as e:
        ctrl_regs = f"error: {e}"
    ctrl.close()
    if ctrl_regs != 1:
        return False, f"control context failed to register SW (got {ctrl_regs!r}); inconclusive"

    ctx = browser.new_context(service_workers="block")
    page = ctx.new_page()
    page.goto(base + "/")
    res = page.evaluate(SW_BLOCKED_JS)
    workers = len(ctx.service_workers)
    ctx.close()

    # Playwright may turn register() into a no-op instead of rejecting it.
    # Accept either, as long as no worker actually ends up registered.
    ok = res["outcome"] != "registered" and res["regs"] == 0 and workers == 0
    rejected = res["outcome"].startswith("rejected")
    note = "" if rejected else " (NOTE: register() did not reject)"
    return ok, f"outcome={res['outcome']!r} regs={res['regs']} ctx.service_workers={workers}{note}"


def test_route_web_socket(browser, base):
    WS_HITS.clear()
    ctx = browser.new_context()
    ctx.route_web_socket("**/*", lambda ws: ws.close())
    page = ctx.new_page()
    page.goto(base + "/ws.html")
    try:
        page.wait_for_function("window.__events.includes('close')", timeout=5000)
    except Exception:
        pass
    page.wait_for_timeout(300)  # leave time for a late 'open' event
    events = page.evaluate("window.__events")
    ctx.close()

    ok = "close" in events and "open" not in events and not WS_HITS
    return ok, f"events={events} server_hits={len(WS_HITS)}"


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    tests = [
        ("storage_state preserves session", test_storage_state),
        ("service_workers=block prevents registration", test_service_workers_block),
        ("route_web_socket blocks WebSockets", test_route_web_socket),
    ]
    all_ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for name, fn in tests:
                try:
                    ok, detail = fn(browser, base)
                except Exception as e:
                    ok, detail = False, f"exception: {type(e).__name__}: {e}"
                all_ok &= ok
                print(f"[{'PASS' if ok else 'FAIL'}] {name} -- {detail}")
        finally:
            browser.close()
            server.shutdown()

    print("ALL PASS" if all_ok else "SOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
