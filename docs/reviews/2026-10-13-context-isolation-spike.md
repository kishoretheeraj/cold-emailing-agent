# Spike: per-job browser contexts (2026-10-13)

Script: `scripts/spike_context_isolation.py`. It uses local test pages only: a stdlib HTTP server
on `127.0.0.1` and a second one on `localhost`, so the cross-origin checks are real. Every
protection is measured at the server, as upstream WebSocket handshakes and POSTs. Every vector
has an unprotected control that must reach the server, so no check can pass by measuring nothing.

Runs (Mac, headless bundled Chromium, Python 3.11):

| Playwright | Chromium | Result |
|---|---|---|
| 1.63.0 (latest that `requirements-apply.txt`'s floor resolves to) | 153.0.8010.12 | same as below |
| **1.60.0** (`requirements-beelink.txt` pin) | bundled with 1.60 | same as below |

Not run here: `channel="chrome"` headful on the Beelink's display `:2`. It belongs in the soak.

## Results

| # | Check | Result |
|---|---|---|
| 1 | Environment: `importlib.metadata.version`, `BrowserContext.route_web_socket` | PASS on 1.60.0 and 1.63.0 |
| 2 | `storage_state` carries cookie + `localStorage` into a new context; fresh context and another origin get neither | PASS |
| 3 | `service_workers="block"`: registration never resolves, zero `/sw.js` fetches, no `serviceworker` event; control registers and fetches | PASS |
| 4 | WebSocket + POST vectors (table below) | **FAIL for workers: stop gate** |
| 5 | Route installed after the first page exists still covers it | Covered (informational) |
| 6 | Fingerprint (UA, cores, screen, `navigator.webdriver`, timezone, WebGL renderer) identical across three contexts of one browser | PASS; `navigator.webdriver` is `true` (no stealth flags, by decision) |
| 7 | IndexedDB in `storage_state` | Omitted by default; `storage_state(indexed_db=True)` captures it, and a context restored from that state reads it back |
| 8 | 30 context open/close cycles leave `browser.contexts == []` | PASS (RSS not measured on the Mac; soak item) |

### Vector matrix

Each cell counts requests that reached the server, as WebSocket handshakes / POSTs.
- **native**: `service_workers="block"` + `context.route_web_socket` + `context.route` aborting
  non-GET.
- **js-only**: the page-level `WebSocket`/`fetch` overrides alone.
- **+no-workers**: native plus an init script that makes `Worker` and `SharedWorker` throw.

| Vector | control | native | js-only | native + no-workers |
|---|---|---|---|---|
| main page | 1 / 1 | 0 / 0 | 0 / 0 | 0 / 0 |
| same-origin iframe | 1 / 1 | 0 / 0 | 0 / 0 | 0 / 0 |
| cross-origin iframe | 1 / 1 | 0 / 0 | 0 / 0 | 0 / 0 |
| popup, socket from its first script | 1 / 1 | 0 / 0 | 0 / 0 | 0 / 0 |
| dedicated worker, classic | 1 / 1 | **1** / 0 | 1 / 1 | 0 / 0 |
| dedicated worker, module | 1 / 1 | **1** / 0 | 1 / 1 | 0 / 0 |
| dedicated worker, blob: URL | 1 / 1 | **1** / 0 | 1 / 1 | 0 / 0 |
| dedicated worker, data: URL | 1 / 1 | **1** / 0 | 1 / 1 | 0 / 0 |
| shared worker | 1 / 1 | **1** / **1** | 1 / 1 | 0 / 0 |

What this shows:
- `context.route_web_socket` covers pages, iframes and popups. It does **not** cover WebSockets
  opened by any dedicated worker.
- `context.route` covers dedicated workers' HTTP, but not shared workers' HTTP.
- The page-level JS overrides, alone, cover no worker vector (expected; Codex correction 2).

The +no-workers script also held against three ways around it. Each threw, with zero upstream:
- `new iframe.contentWindow.Worker(...)` from a freshly appended `about:blank` iframe;
- the same with `SharedWorker`;
- `new window.open('about:blank').Worker(...)`.

Two limits remain:
- It is JavaScript-level, not browser-enforced.
- It will break any site that needs a worker to work, e.g. some captcha or upload widgets. Only
  the soak against real postings can show how often that happens.

## Second finding: `ws.close()` deadlocks the sync API

`context.route_web_socket("**/*", lambda ws: ws.close())` hangs the process forever the first time
a page opens a socket.
- **Why:** the sync handler is invoked on Playwright's event loop (`_on_web_socket_route`), and the
  blocking `close()` waits on that same loop. A `faulthandler` dump shows it stuck in
  `_sync_base._sync`.
- **Fix:** a handler that never calls `connect_to_server()` (it does nothing) reaches no server and
  does not hang.
- **Cost:** the page sees the mocked socket as open. The plan's "`onclose` and never `onopen`"
  criterion is therefore unreachable. The measured property is zero upstream handshakes instead.
- **The current code has the same bug.** `JobScope.adopt` in `apply_pipeline.py` (on
  `overnight-247-restart`) uses `page.route_web_socket("**/*", lambda ws: ws.close())`, the same
  handler shape at page level. Any page that opens a WebSocket should hang the pipeline today. That
  path wasn't reproduced at page level here, only at context level.

## Decision needed before implementation (plan's stop gate)

The redesign's claim that Findings 1 and 2 become **closed** does not hold as written: worker
WebSockets, and shared-worker HTTP, bypass the context's native protections. The options:

1. **Implement with the worker block.** Add the `Worker`/`SharedWorker` init script at context
   level, plus a real-browser test per worker vector. Findings 1 and 2 become "closed for pages,
   frames and popups; workers blocked by a JS-level refusal". Watch in the soak how many real forms
   break.
2. **Implement without it** and record worker vectors as a known residual, the same exposure as
   today's CDP design. The redesign still fixes the deadlock, the shared-context machinery and
   cross-job storage.
3. **Stop**, and keep the CDP design plus a fix for the deadlock only.
