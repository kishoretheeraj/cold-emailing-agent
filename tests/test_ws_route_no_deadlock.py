"""Regression: the WebSocket route handler must not deadlock Playwright's sync API.

`route_web_socket(..., lambda ws: ws.close())` hung the process the first time a page opened a socket:
the handler runs on Playwright's event loop and the blocking close() waits on that same loop. The
scenario runs in a subprocess with a timeout, so a regression fails this test instead of hanging the
suite. Real Chromium against a local server only; skipped without Playwright/Chromium unless
PIPELINE_BROWSER_TESTS_REQUIRED=1 (the acceptance run), which turns the skip into a failure.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SCENARIO = textwrap.dedent("""
    import sys, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    sys.path.insert(0, sys.argv[1])
    import apply_pipeline as ap
    from playwright.sync_api import sync_playwright

    UPGRADES = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_GET(self):
            if self.headers.get("Upgrade"):
                UPGRADES.append(1)
            body = (b"<script>window.ev=[];const s=new WebSocket('ws://'+location.host+'/ws');"
                    b"s.onopen=()=>ev.push('open');s.onclose=()=>ev.push('close');</script>")
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context().new_page()
        page.route_web_socket("**/*", ap._swallow_websocket)
        page.goto(f"http://127.0.0.1:{srv.server_address[1]}/", wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        browser.close()
    srv.shutdown()
    print("UPGRADES", len(UPGRADES))
""")


def _browser_available():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


def test_websocket_route_handler_never_deadlocks_and_sends_no_handshake():
    if not _browser_available():
        if os.environ.get("PIPELINE_BROWSER_TESTS_REQUIRED") == "1":
            pytest.fail("Playwright/Chromium required for the acceptance run")
        pytest.skip("no launchable Chromium here")
    env = {**os.environ, "APPLY_DRY_RUN": "1"}
    try:
        out = subprocess.run([sys.executable, "-c", SCENARIO, str(ROOT)], capture_output=True, text=True,
                             timeout=90, env=env, cwd=ROOT)
    except subprocess.TimeoutExpired:
        pytest.fail("route_web_socket handler deadlocked the sync API")
    assert out.returncode == 0, out.stderr[-2000:]
    assert "UPGRADES 0" in out.stdout


def test_close_handler_is_not_used_anywhere():
    # The deadlocking shape must not come back in the pipeline source.
    source = (ROOT / "apply_pipeline.py").read_text()
    assert "lambda ws: ws.close()" not in source
