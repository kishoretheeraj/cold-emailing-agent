
---

# Plan: per-job isolated browser contexts for `apply_pipeline.py`

Branch `overnight-247-restart`, file `apply_pipeline.py` at 2895 lines. Line numbers below are from the current HEAD, commit `279e214`.

## 0. What I verified before planning

- `connect()` at lines 2671 to 2679 attaches over CDP and returns `None` when the attached browser has no context. The main loop at 2813 to 2820 reconnects on `is_connected()` being false.
- Every job runs on `browser.contexts[0]` at lines 2822, 2830 and 2857. The pre-job service-worker sweep at 2829 to 2856 scans that shared context against `test_base` only, not the production allowlist.
- `JobScope` at 499 to 707 carries five pieces of shared-context machinery: `before` at 507 and 516, `owns()` at 554 to 569, `_opened_by_job()` at 574 to 579, `refresh()` at 664 to 667, and the `closable` list threaded through `adopt()`, `open_page()` and `detach()`. The route handler at 391 to 404 has the matching foreign-tab branch.
- WebSocket blocking is per page at 612 to 624, inside `adopt()`.
- No systemd unit launches a Chrome with a debugging port. `chrome-profile@.service` forbids it in its header, and `RUNBOOK-24-7.md` section 5 says the apply worker launches its own Playwright Chrome. So today `apply_pipeline.py` depends on a hand-started Chrome that nothing provisions. The redesign removes that dependency as a side effect.
- The repo already has the two building blocks this plan reuses: `ats_sessions.py` for per-tenant `storage_state` files, and `apply_agent._launch_page` at 914 to 936 for the launch kwargs convention.
- `test_form.html` is referenced by `FORM_PATH` at line 57 but is not tracked in git. The spike needs a fixture that is checked in.
- Playwright: `requirements-beelink.txt` pins 1.60.0, `requirements-apply.txt` has a floor of 1.47.0. `BrowserContext.route_web_socket` arrived in 1.48, so the floor must rise.
- There is no `AGENTS.md` in this repo root. The one at `contact-manager/AGENTS.md` is the Next.js app's. The warm-session guidance lives in `RUNBOOK-24-7.md` section 5 and the CLAUDE.md "Sessions, vault" paragraph. I treat those as the rule to preserve.

## a. Spike first

Goal: prove the three properties against local fixtures only, with sensitivity controls, before any production line changes. Nothing here touches `apply_pipeline.py`.

**Files to add**

- `scripts/spike_context_isolation.py`, a standalone script, stdlib plus Playwright, not imported by anything.
- `tests/fixtures/pipeline_site/` with four files: `login.html` (a form that sets a `sid` cookie server-side and a `localStorage` marker client-side), `whoami.html` (renders the cookie and the marker), `sw.js` (a trivial fetch-intercepting worker), `ws.html` (opens `new WebSocket` to the page's own host and reports `onopen`/`onclose`, and a button that calls `window.open('ws.html')`).
- The spike's server is a `ThreadingHTTPServer` modelled on `_Handler` at 1240 to 1264, extended to count three things: fetches of `/sw.js`, GETs carrying an `Upgrade: websocket` header, and POSTs. It binds two origins by starting twice, once on `127.0.0.1` and once on `localhost`, so cross-origin storage checks are real.

**Steps and pass criteria**

1. Environment check. Print `playwright.__version__` and assert `hasattr(BrowserContext, "route_web_socket")`. Run on the dev machine headless, then on the Beelink inside the `/opt/jobagent24/.venv` with `channel="chrome"` on display `:2`. Fail if either is below 1.48.
2. Session survives via storage_state. Launch one browser. Context A: goto `/login`, submit, confirm `/whoami` shows the cookie and the marker. Call `context.storage_state(path=tmp)`, chmod 0600, close A. Context B with `storage_state=tmp`: `/whoami` shows both. Context C with no state: `/whoami` shows neither. Context D with state, visiting the `localhost` origin: shows neither. Pass only if all four hold.
3. `service_workers="block"` works. Context with the option: `navigator.serviceWorker.register('/sw.js')` rejects, `context.service_workers` is empty after a 2 s pump, server `/sw.js` fetch count is 0. Control context without the option: registration resolves and the fetch count is 1. The control proves the test can fail. Also assert `context.on("serviceworker")` never fires on the blocked context.
4. Context-level WebSocket routing works. Context with `context.route_web_socket("**/*", lambda ws: ws.close())`: `ws.html` reports `onclose` and never `onopen`, server Upgrade count stays 0. Then click the popup button: the popup's socket is also closed, Upgrade count still 0, with no per-page call made. Control context without the route: Upgrade count becomes 1. Confirm the route must be installed before `new_page()`; test the reverse order and record whether the first page is covered.
5. Fingerprint continuity. In three contexts of one browser, read `navigator.userAgent`, `navigator.hardwareConcurrency`, `screen.width/height`, `navigator.webdriver`, timezone from `Intl.DateTimeFormat().resolvedOptions()`, and the WebGL `UNMASKED_RENDERER_WEBGL`. All must be identical across the three. Record `navigator.webdriver` separately; see the decision in section j.
6. Storage coverage. Check whether `storage_state` on 1.60 includes IndexedDB, and whether the `indexed_db=True` kwarg exists. Record the answer. Workday and Okta-fronted tenants sometimes keep session material there, and the plan in section d depends on this.
7. Teardown. After closing each context, assert `len(browser.contexts)` drops and the Chrome RSS from `/proc/<pid>/status` returns to within 20 percent of its post-launch value after 30 context cycles.

Output: a short `docs/reviews/2026-10-xx-context-isolation-spike.md` with the seven results and the Playwright version. Codex reviews that before implementation starts.

## b. Browser lifecycle

**Replace `connect()` at 2671 to 2679 with `launch_browser(pw)`:**

```python
def launch_browser(pw):
    try:
        browser = pw.chromium.launch(
            headless=config.APPLY_BROWSER_HEADLESS, channel=config.APPLY_BROWSER_CHANNEL,
            executable_path=config.APPLY_BROWSER_EXECUTABLE,
            args=["--disable-dev-shm-usage"])
    except Exception as exc:
        ev("browser_launch_failed", reason=type(exc).__name__)
        return None
    browser.on("disconnected", lambda *_: ev("browser_disconnected"))
    return browser
```

Rules for this function:

- Never pass `--remote-debugging-port`. The `chrome-profile@.service` header explains why, and a new structural test asserts the string is absent from `apply_pipeline.py`.
- No `--disable-blink-features=AutomationControlled` or other stealth flags by default. That is an explicit decision item, section j.
- Delete `CDP_URL` at line 50 and `cdp=clean_url(CDP_URL)` from the `pipeline_start` event at 2765. Rename `CDP_BACKOFF_S` at 58 to `BROWSER_BACKOFF_S`. Remove `connect_over_cdp` from the module entirely; a structural test asserts it.

**Ownership.** The browser is owned by `_main()`. Wrap the `while not STOP.is_set()` loop at 2792 in `try/finally` and close the browser in the finally. Today the pipeline attaches and never closes; after this change a missed close leaves an orphan Chrome holding the display, so this is required, not nice-to-have.

**Crash and restart.** Keep the existing shape at 2813 to 2820: `if browser is None or not browser.is_connected(): browser = launch_browser(pw)`, backoff `BROWSER_BACKOFF_S` on `None`. A crash mid-job surfaces as `PWError` inside `_run_job`, is converted by `run_job` to `JobEnd("failed", ...)`, and the phase rules at 88 to 93 decide requeue: only phase `started` requeues. No change to that contract. Add a recycle counter: after `APPLY_BROWSER_MAX_JOBS` (default 100) processed jobs, close and relaunch between jobs. Fingerprint is unaffected by a relaunch, this only bounds memory growth.

**Self-test.** `network_self_test(context, test_base)` at 2692 becomes `network_self_test(browser, test_base)`. It constructs `JobScope(browser, "selftest")`, so it runs on its own fresh context like every job. Extend its pass condition with two probes that the spike proved: a `/sw.js` registration that must reject with zero server fetches, and a WebSocket open that must close with zero Upgrade requests. The `_Handler` at 1240 gains the two counters and the two routes. The self-test stays the gate before any job runs, lines 2821 to 2828 unchanged in shape.

**Delete** the pre-job sweep at 2829 to 2856. It exists because the shared context could carry workers from earlier jobs or foreign tabs. A fresh context with `service_workers="block"` has none by construction, and section e adds a tripwire for the impossible case.

**Deployment.** Add `deploy/beelink/systemd/jobagent24-pipeline.service`: `Type=simple`, `Restart=on-failure`, `RestartSec=60`, `User=jobagent`, `DISPLAY=:2`, `JoinsNamespaceOf=xvfb@2.service`, `HOME=/var/lib/jobagent24`, `APPLY_SESSIONS_DIR=/var/lib/jobagent24/sessions`, `APPLY_DISPLAY_LOCK=/var/lib/jobagent24/display2.lock`, `APPLY_BROWSER_CHANNEL=chrome`, `APPLY_BROWSER_HEADLESS=0`, `APPLY_DRY_RUN=1`, `APPLY_AGENT_ARMED=` empty, `MemoryMax=3072M`. The pipeline must take the same `fcntl.flock` on `APPLY_DISPLAY_LOCK` that `apply_worker` takes, held for the browser's lifetime, so two headful Chromes never share `:2`. `tests/test_beelink_units.py` gets the unit added to its no-debugging-port and never-armed assertions.

## c. Per-job context lifecycle and what `JobScope` becomes

**Signature changes**

- `JobScope.__init__(self, browser, jid="-", storage_state=None)`. `self.context = None` until `attach()`.
- `run_job(browser, job, test_base, ...)` at 2247, `process_job(browser, job, test_base)` at 2629, `network_self_test(browser, test_base)` at 2692. Call sites at 2653, 2822 and 2857 pass `browser`.

**`attach()` at 515 to 522 becomes**

```python
def attach(self):
    self.context = self.browser.new_context(
        service_workers="block", storage_state=self.storage_state,
        no_viewport=True, accept_downloads=False)
    try:
        if not callable(getattr(self.context, "route_web_socket", None)):
            raise JobEnd("blocked", "websocket_interception_unavailable")
        self.context.route_web_socket("**/*", lambda ws: ws.close())
        self.context.add_init_script(SUBMIT_GUARD_JS)
        self.context.add_init_script(DRY_RUN_JS if DRY_RUN else LIVE_NET_GUARD_JS)
        self._listener = self.adopt
        self.context.on("page", self._listener)
        self._sw_listener = self._service_worker
        self.context.on("serviceworker", self._sw_listener)
        self._route = make_route_handler(self)
        self.context.route("**/*", self._route)
    except BaseException:
        with contextlib.suppress(Exception):
            self.context.close()
        raise
```

Order matters and is tested: WebSocket route and init scripts are installed before any page exists, so the first document of the main tab and of every popup is covered from the first byte. Today `page.add_init_script` in `adopt()` runs after the `page` event, which can miss a popup's first document. Moving `SUBMIT_GUARD_JS` here also removes the per-page calls at 2326 to 2328 in `_run_job`.

`no_viewport=True` is for headful mode so the page uses the real window size, matching what the CDP-attached Chrome did. Headless runs keep Playwright's default viewport; the fingerprint continuity property only has to hold across contexts of one process, which it does either way.

**Remove**

- `self.before` (507, 516) and every read of it (557, 666, 398).
- `owns()` (554 to 569). Nothing outside the scope calls it after the route handler change below.
- `_opened_by_job()` (574 to 579) and `self.closable` (507, 604 to 605, 651, 659 to 660, 682 to 685, 697 to 704). Every page in the context is this job's, and `context.close()` closes them all.
- `self._explicit_adopt` (508, 587, 657). `open_page()` just calls `new_page()`, appends to `owned`, sets `main`; the `page` event fires `adopt()` for the same page, which is idempotent.
- `refresh()` (664 to 667) and its calls at 670 and 680. `context.pages` is `owned` by construction; `check()` iterates `context.pages` directly.
- `_job_has_page_on_origin()` (541 to 552).
- In `make_route_handler` at 391 to 404: the whole `if page is not None and not scope.owns(page)` branch, including the `unattributed_tab_blocked` violation. The `ev("net_blocked", ...)` and `sw_violations` fail-closed branch at 405 to 410 stay.
- In `adopt()`: the opener-chain logic at 585 to 602, the per-page init scripts at 606 to 611, the per-page `route_web_socket` block at 612 to 624.

**Keep**

- `owned`, `main`, `violation`, `blocked`, `mutating`, `sw_violations`, `note_violation()`, `_police()`, `_navigated()`, `check()`, `_hooks`.
- `adopt(page)` reduces to: append to `owned` if new, hook `framenavigated`, `_police(page)`. Return `True` always.
- `_police()` at 638 to 653: a non-main page on an unapproved host is still closed. Replace `if page in self.closable` with `if page is not self.main`.
- `_service_worker(worker)` at 524 to 539 becomes a tripwire. The browser refuses registration, so the event must never fire. If it does: `note_violation("service_worker_despite_block")`, append to `sw_violations`, and the route handler's existing fail-closed branch aborts the job's traffic. The origin-approval nuance disappears because there is no foreign tab to be lenient about.

**`detach()` at 677 to 706 becomes**

```python
def detach(self, persist_key=None, persist_domain=None):
    if self.context is None:
        return
    try:
        if persist_key and not self.violation:
            with contextlib.suppress(Exception):
                ats_sessions.save_state(self.context, persist_key, domain=persist_domain)
    finally:
        try:
            self.context.close()
        except Exception as exc:
            ev("context_close_failed", self.jid, reason=type(exc).__name__)
        finally:
            self.context = None
```

The old two-phase order, pages first while the route still enforces, then hooks, is preserved automatically: `context.close()` tears down pages under the still-installed route, and the route dies with the context. The `RuntimeError('cleanup: owned pages still open')` guard is no longer reachable and goes. A failed `context.close()` is logged and the browser is marked for recycle at the next loop iteration, since a leaked context would hold the storage partition.

`run_job`'s `finally` at 2279 calls `scope.detach(persist_key, persist_domain)` where those come from section d, and the `scope.detach()` in `network_self_test`'s finally at 2725 passes nothing.

## d. Session-state hygiene

**Which storage is safe to clone.** Only the tenant's own. Cookies ride on every request to their domain, including subresource requests a page on tenant A makes to tenant B in live mode, where `net_decision` only holds top-level navigations. One global `warm_state.json` with every tenant's cookies would turn a cross-tenant subresource into a cookie leak. So there is no global file. Per job:

1. `key = ats_sessions.tenant_key(url)` and `host = urlsplit(url).hostname`. For `platform == "test_local"`, both are `None` and the job runs cold.
2. `path = ats_sessions.state_path(key)`. If present, load it through a new `ats_sessions.load_state_for(key, host)` that returns a dict, not a path: parse the JSON, keep only cookies whose `domain` is `_within(host)` and origins whose hostname is `_within(host)`, refuse the file if its mode is not 0600 or it exceeds 1 MiB, and raise `JobEnd("blocked", "session_state_untrusted")` if any cookie outside the host survives a filter pass. Pass the dict as `storage_state`; Playwright accepts a dict or a path.
3. Never include Google, LinkedIn, Gmail or any identity-provider cookies. `save_state(domain=...)` already filters at write; the load-time filter makes the read side fail closed too, so a hand-edited file cannot widen it.

**How the warm state gets created.** New CLI `python apply_pipeline.py --warm-login <job_url>`:

- Launches the same `launch_browser`, creates a context with the same kwargs minus `storage_state`, opens the tenant's sign-in page, and waits for a sentinel file `/var/lib/jobagent24/warm_login_done` or a 20 minute timeout. The human signs in over the existing noVNC tunnel to `:2` at human pace; the pipeline types nothing.
- On the sentinel: `ats_sessions.save_state(context, key, domain=host)`, close the context, exit 0. On timeout: exit 1, nothing written.
- Documented in `RUNBOOK-24-7.md` as the one way sessions enter the sessions dir for this pipeline.

**How it refreshes.** After the final `scope.check()` at 2566 in `_run_job`, before the dry-run or live return, set `scope.persist = True`. `run_job` then passes `persist_key`/`persist_domain` to `detach()` only when `_run_job` returned normally and `scope.violation` is unset. A blocked or failed job never writes back, so state set by a hostile redirect cannot persist. The write is domain-filtered as today.

**Re-login on expiry.** Detection reuses `ats_accounts.detect` for a sign-in wall after `settle(page)` at 2336 and after `assert_destination` at 2337. On detection with a state file present: `ats_sessions.forget(key)`, `ev("session_expired", jid, tenant=ref(key))`, `raise JobEnd("needs_review", "session_expired")`. The job stays in the queue for the operator to run `--warm-login` and requeue. Automatic vault sign-in through `ats_accounts.flows` is a later step, not this change; wiring it here would mix two reviews.

**IndexedDB.** If spike step 6 shows the 1.60 `storage_state` omits IndexedDB and a target tenant needs it, the fallback is `storage_state(indexed_db=True)` if the kwarg exists, else that tenant is marked cold in a `SESSION_COLD_TENANTS` set and always goes through the expiry path. Decide after the spike, not before.

## e. WebSocket enforcement

- API: `context.route_web_socket("**/*", lambda ws: ws.close())` in `attach()`, before `new_page()`. The handler never calls `ws.connect_to_server()`, so no socket to any server is ever opened; the page sees an immediate close.
- Coverage: every page in the context, main tab and popups, without per-page installation. The per-page block at 612 to 624 is deleted.
- Fail closed: if the attribute is missing or the call raises, `attach()` closes the context and raises `JobEnd("blocked", "websocket_interception_unavailable")` before any navigation. This preserves the current abort semantics at 621 to 624 but moves them earlier.
- Defense in depth: keep the `window.WebSocket` constructor override in `DRY_RUN_JS` at 478 and `LIVE_NET_GUARD_JS` at 493, now installed at context level.
- Keep `"websocket"` in `STATIC_BLOCKED_TYPES` at 357. It is unreachable for real WS traffic but harmless and documents intent.
- Raise the floor in `requirements-apply.txt` to `playwright>=1.48.0`. `requirements-beelink.txt` at 1.60.0 is already fine.

## f. Warm-session and human-pace preservation

- Same browser process for all jobs, launched once, so the hardware-level fingerprint is constant across contexts. Only the storage partition changes per job, and for a given tenant the partition is seeded from that tenant's saved state, so the ATS sees the same cookies and `localStorage` it set last time.
- Third-party trackers embedded on ATS pages see a fresh partition per job. That is a privacy improvement, not a detection risk, since those trackers are not the party holding the session.
- `pace()` at 1274 and every call site is untouched. Human sign-in stays human: `--warm-login` never types credentials, and the pipeline never calls any `ats_accounts` sign-in flow in this change.
- `channel="chrome"` keeps real Google Chrome on the Beelink, same as `jobagent24-apply.service` line 26.
- The CDP-based "reuse the running Chrome" shortcut goes away. What is preserved is the property that mattered: one warm, reused, logged-in session per tenant, no re-login per job.

## g. Test plan

**Existing tests to re-run after the change**

- `tests/test_sw_ws_hardening.py`: 6 tests. Rewrite `test_service_worker_from_foreign_tab_is_ignored` at line 107 into `test_service_worker_event_is_a_tripwire` asserting `service_worker_despite_block`. Rewrite `test_adopt_blocks_websocket_in_live_mode` at 166 and `test_adopt_fails_closed_when_ws_interception_unavailable_in_live_mode` at 180 to exercise `attach()` with a fake browser whose `new_context()` returns a fake context, asserting the route is installed before `new_page()` and that a missing attribute closes the context and raises. The other three stay.
- `tests/test_live_submit.py`: contains `inspect`-based structural checks on the source. Re-run and fix any that pin the old `_run_job` init-script lines.
- `tests/test_apply_pipeline_state.py`, `tests/test_challenge_dom.py`, `tests/test_challenge_detector.py`, `tests/test_prepare_assets_imports.py`, `tests/test_answers.py`: unchanged, must stay green.
- `tests/test_beelink_units.py`, `tests/test_armed_rule.py`, `tests/test_ops_workflows.py`: re-run after adding the new unit.
- `tests/test_universal_apply.py` and `tests/test_apply_load.py`: real-browser suites for `apply_agent`, unaffected in code but they are the template for the new real-browser tests.

**New pure tests, `tests/test_job_context.py`**

- `attach()` calls `new_context` with exactly `service_workers="block"`, the given `storage_state`, `no_viewport=True`, `accept_downloads=False`.
- Call order: `route_web_socket`, both `add_init_script`, `on("page")`, `on("serviceworker")`, `route`, all before any `new_page`.
- Missing `route_web_socket` closes the context and raises `JobEnd("blocked", "websocket_interception_unavailable")`.
- `detach()` closes the context exactly once, also when `save_state` raises, also when `close()` raises, and sets `self.context` to `None`.
- `detach(persist_key=...)` calls `save_state` only when `violation` is unset.
- Popup adoption: a second page from the `page` event is in `owned`, gets `framenavigated`, and is closed by `_police` on an unapproved host while `main` is not.
- Route handler: a request from any page in the context goes through `net_decision`; there is no allow-through path for an unowned page. Parametrize dry run and live.
- Structural: `"connect_over_cdp" not in source`, `"remote-debugging-port" not in source`, `"before" not in getsource(JobScope)`, `"contexts[0]" not in source`.
- `ats_sessions.load_state_for`: foreign-domain cookie dropped, oversized file refused, mode 0644 refused, `localhost` versus `127.0.0.1` treated as different hosts.
- Session expiry path: sign-in wall with a state file present calls `forget` and ends `needs_review`; without a state file it does not call `forget`.

**New real-browser tests, `tests/test_job_context_browser.py`**, skipped without Playwright and Chromium, same pattern as `tests/test_universal_apply.py`, using the `tests/fixtures/pipeline_site/` fixtures from the spike:

- Service worker registration rejects, zero `/sw.js` fetches, `context.service_workers` empty.
- WebSocket from main tab and from a popup both close, zero Upgrade requests.
- Session survives: context A logs in, state saved, context B sees it, context C and a different-origin context do not.
- `--self-test` exits 0 with the extended probes, and exits 2 when the WS route is monkeypatched away.
- After 20 `attach()`/`detach()` cycles, `len(browser.contexts) == 0`.

**Supervised dry-run soak on the Beelink**, display `:2`, `APPLY_DRY_RUN=1`, 48 hours, queue of at least 20 `test_local` jobs plus 10 real public Greenhouse, Lever and Ashby postings:

- Zero `loop_error`, zero `context_close_failed`, zero `service_worker_despite_block`, zero `browser_disconnected` not followed by a clean relaunch.
- `dry_run_ok` rate at or above the CDP baseline from the current results file.
- `network_self_test` passes on every pipeline start, including after each recycle.
- Chrome RSS after 100 jobs within 25 percent of the value after job 1.
- Session files in the sessions dir remain 0600, and the tenants that had warm state show no `session_expired`.
- One watched `--warm-login` on a real tenant, then a job on that tenant proves the saved state is honored.

## h. Rollback

- Rollback is `git revert` of the single implementation commit. No runtime `APPLY_BROWSER_MODE=cdp|launch` flag. A dual-mode flag would keep the `before`/`owns()`/foreign-tab machinery alive as a second code path, which is the "dead security machinery" risk the findings doc warns about, and it would require a hand-started debug-port Chrome nothing provisions.
- The pipeline is dry-run only and has no live gate file on the box, so there is no production live traffic to protect during the swap.
- Mid-flight jobs: the existing lease and phase model handles them. `recover_in_progress_ex()` at 1169 requeues a job that crashed at phase `started` and marks anything past `navigating` for reconciliation. Stop the unit with `systemctl stop`, which sends SIGTERM, `STOP` is set, the current job raises `Shutdown` at its next `pace()`, `release_after_shutdown` runs, the browser closes in the new finally, and the revert can be deployed. Nothing else is needed.
- Sessions dir files are forward and backward compatible; they are plain Playwright `storage_state`.

## i. `docs/codex-architectural-findings.md` updates

- Header: change `Status: DEFERRED` to `Status: Findings 1 and 2 CLOSED by commit <sha> (2026-10-xx); Finding 3 deferred, unchanged`.
- "The redesign" section: rewrite in past tense as what was built, with the per-tenant storage_state answer to the "which origins are safe to clone" question from section d, and the no-stealth-flags decision.
- Finding 1: replace "What the redesign would add" with "Closed": `service_workers="block"` on every job context, the `serviceworker` event as a fail-closed tripwire, the 2829 to 2856 sweep deleted and why, the real-browser test names. Delete the "Residual" wording.
- Finding 2: replace "Residual (documented, needs redesign)" with "Closed": `context.route_web_socket` in `attach()` before any page, fail-closed on absence, popups covered, the real-browser test names. Note the per-page block was removed and the JS override kept as defense in depth.
- Finding 3: add one line confirming the redesign landed and, as the section already states, does not change the Finding 3 assessment.
- Verdicts table: rows 1 and 2 become "Closed (isolated context)"; row 3 unchanged.
- Also update: the module docstring at `apply_pipeline.py` line 4 ("new tab in the already-running Chrome (CDP)"), `RUNBOOK-24-7.md` section 5 to add the pipeline and `--warm-login`, CLAUDE.md's "24/7 namespace" paragraph with one sentence on per-job contexts and the new unit, and the memory file `per-job-context-plan.md`.

## j. Effort and risk

Effort, sequential:

| Step | Time |
|---|---|
| Spike, fixtures, spike report | 0.5 day |
| Codex review of spike report | 0.5 day |
| `JobScope`, route handler, lifecycle, self-test, `--warm-login`, `ats_sessions.load_state_for` | 1.5 days |
| Pure and real-browser tests, unit file, docs | 1 day |
| Codex review round and fixes | 0.5 day |
| Soak | 2 days wall clock, low attention |

Risks and mitigations:

- **`navigator.webdriver` reads true on a Playwright-launched Chrome.** The current hand-started debug-port Chrome likely read false. Spike step 5 records the fact. Options: accept it, or add `--disable-blink-features=AutomationControlled`. Recommendation: accept it for the soak and treat stealth flags as a separate decision for Kishore, consistent with the "pacing, not evasion" posture already documented for the LinkedIn work. Patchright and Camoufox are noted as prior art, not adopted.
- **Session material in IndexedDB.** Covered by spike step 6 and the cold-tenant fallback.
- **Two headful Chromes on display `:2`.** Covered by taking the existing display lock.
- **A sign-in wall misdetected as expiry** would `forget` a good session. Mitigation: `forget` only after the detector fires on two consecutive polls, and the forgotten file is renamed to `.expired` rather than deleted for 7 days so the operator can restore it.
- **Memory.** Each context costs roughly 50 to 150 MB while open; only one is open at a time, plus the recycle cap. `MemoryMax=3072M` on the unit is the hard ceiling.
- **Test fixture gap.** `test_form.html` is untracked; the new `tests/fixtures/pipeline_site/` must be checked in so the real-browser tests and the self-test fixture are reproducible.

Decision needed from Kishore before implementation starts: stealth flags yes or no. Everything else in this plan is a routine engineering call and proceeds under the stated assumptions.

---

## Codex Review (2026-10-13) — AGREE-WITH-CHANGES

Codex (gpt-6-astra) reviewed this plan and returned AGREE-WITH-CHANGES. The core APIs are appropriate: `service_workers="block"` composes with `storage_state`, and `BrowserContext.route_web_socket` is the correct context-level API. The following 8 corrections are REQUIRED before implementation:

### 1. Restore mandatory worker coverage
Test dedicated/shared workers, classic/module workers, blob/data URLs, frames, and immediate popup scripts, with positive controls and zero upstream handshakes. Blocking service workers does not block dedicated/shared workers; page constructor overrides do not establish worker coverage. Pin the validated Playwright version instead of merely specifying `>=1.48`.

### 2. Separate enforcement tests from JavaScript overrides
The existing overrides make SW registration reject and WebSocket construction throw even when native enforcement is absent. Test each layer independently, then their composition in both modes. Require the WS route handler to be exercised; constructor exceptions cannot satisfy an `onclose` assertion. Removing either underlying protection must fail its corresponding sensitivity test.

### 3. Fix self-test lifecycle
Reset the self-test gate after every launch/recycle. Existing code initializes `selftest_done = not DRY_RUN`, and `network_self_test` returns immediately in live mode. Add SW/WS verification in both modes while retaining dry-run-specific POST checks.

### 4. Make cleanup failures genuinely fail closed
Translate WS route-installation exceptions into `JobEnd("blocked", "websocket_interception_unavailable")`; the sample currently rethrows them unchanged. On context-close failure, including failed attachment cleanup, mark the browser unusable and close it before another job. If browser termination fails, stop processing. Test these paths and prevent leaked-context callbacks from running under the next job's policy.

### 5. Complete storage acceptance criteria
Define explicit approved storage origins, including scheme/port, and cookie-domain boundary rules. Host filtering alone cannot separate tenants sharing one origin and origin-wide localStorage. Add shared-host tenant, parent/sibling-domain, malformed-state, foreign-origin, and IndexedDB filtering tests. `indexed_db=True` is available from 1.51 and is opt-in; implement capture on both warm-login and refresh paths where required.

### 6. Correct spike/config details
Use `importlib.metadata.version("playwright")`, not `playwright.__version__`. Make `no_viewport` conditional on headful mode as the prose promises. Require browser tests to run, rather than silently skip, in the implementation acceptance environment.

### 7. Tighten warm-login and soak requirements
Use a fresh invocation-specific completion sentinel and verify authenticated state before saving. Explicitly apply the display lock to warm-login. Measure fingerprint/login compatibility against the old baseline; remove categorical claims that relaunches or fresh tracker state cannot affect detection. Add challenge-rate criteria and enough jobs to exercise the 100-job recycle.

### 8. Complete rollback and preserve Finding 3
Reverting code does not remove an installed systemd unit or provision the old CDP Chrome. Specify stopping/disabling the new unit, service-file cleanup, and either verified CDP restoration or remaining stopped; explicitly state that rollback reopens Findings 1 and 2. Finding 3's separately closed status must be preserved.

## How each correction was carried out (2026-10-13)

The line that stood here ("All 8 corrections are incorporated into the implementation below") had
nothing below it. This is the record of what the implementation (branch `feat/per-job-contexts`,
PR #20) actually did, correction by correction. Where it departs from sections a–j above, this
section wins.

1. **Worker coverage.** Done; this check found the plan's central gap.
   - The spike covers dedicated workers (classic, module, blob: URL, data: URL), shared workers,
     iframes on the same and other origins, and an immediate popup, each with an unprotected
     control.
   - On 1.60.0 and 1.63.0, neither the browser-level routes nor the JS overrides reach workers.
   - Kishore chose a JS-level worker refusal (`NO_WORKERS_JS`) with a `page.on("worker")` backstop.
   - Playwright is pinned to the validated `1.60.0` in both requirements files.
2. **Layers tested separately.** `tests/test_job_context_browser.py` measures each layer on its own:
   - the native WebSocket route with the JS overrides removed;
   - the JS overrides with no native route;
   - the worker refusal and its backstop, separately;
   - and every layer together, in both modes.

   Removing a layer turns its sensitivity test red. The plan's "`onclose` and never `onopen`"
   criterion is replaced by "zero upstream handshakes". A routed socket that never connects looks
   open to the page, and `ws.close()` in the handler deadlocks the sync API (reproduced, now
   regression-tested).
3. **Self-test lifecycle.** The gate resets after every launch and recycle (`_loop`), and runs in
   both modes.
   - It probes a service worker (zero script fetches), a WebSocket (zero handshakes) and
     Worker/SharedWorker (both refused, zero fetches), plus the dry-run POST checks.
   - Its scope is built with `page_guards=False`, so the probes reach the browser-level protections
     themselves, not the submit/network JS guards in front of them.
4. **Cleanup fails closed.**
   - A missing WebSocket route, or one whose installation raises, becomes `JobEnd("blocked",
     "websocket_interception_unavailable")`, with the context closed.
   - A context-close failure, including during attach cleanup, sets `BROWSER_UNUSABLE`. The browser
     is closed and relaunched before another job, and a browser that cannot be closed stops the
     loop (rc 2).
   - A detached scope's callbacks do nothing, and its route aborts everything.
5. **Storage acceptance.** This is the storage slice: `load_state_for`, approved origins with scheme
   and port, cookie-domain boundaries, shared-host tenants restoring cookies only, IndexedDB via
   `storage_state(indexed_db=True)` (validated by the spike), and the malformed and foreign-origin
   tests.
6. **Spike and config details.**
   - The version is read with `importlib.metadata`.
   - `no_viewport` is set only when headful.
   - `PIPELINE_BROWSER_TESTS_REQUIRED=1` turns a missing Chromium into a failure.
7. **Warm-login and soak.** This is the storage slice too:
   - a fresh sentinel per invocation, authentication verified before saving, and the display lock
     held;
   - soak criteria: worker-refusal counts (now in every result's facts as `workers_refused`), the
     challenge rate, and at least 100 jobs to exercise the recycle.
8. **Rollback.** Recorded in `docs/codex-architectural-findings.md`, "What was built (2026-10-13)":
   disable and remove the unit, then restore a verified CDP Chrome or stay stopped. Reverting
   reopens Findings 1 and 2, and Finding 3's closeout is untouched.

**No stealth flags** (Kishore, 2026-10-13): a structural test asserts that `AutomationControlled`
and `remote-debugging-port` are absent from `apply_pipeline.py`.
