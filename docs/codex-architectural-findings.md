# Codex Architectural Findings — per-job browser contexts

**Date:** 2026-10-12 (updated 2026-10-13)
**Status (2026-10-13):** Findings 1 and 2 CLOSED for pages, frames and popups by the per-job context
redesign (branch `feat/per-job-contexts`, PR #20), **with workers closed by a JavaScript-level
refusal** rather than by the browser — see "What was built (2026-10-13)" at the end. Finding 3
unchanged (closed separately, see its closeout). Everything between here and that section is the
2026-10-12 record, kept as written.

**Original status (2026-10-12):** DEFERRED — not implementable without the redesign below. No half-fixes attempted.
**Context:** Commit `6878d68` ("Pipeline security hardening: Codex feedback implementation")
closed the implementable findings. Codex's verdict: 3 findings remain, all architectural —
they require an isolated browser context per job instead of the current CDP-shared Chrome
(`connect_over_cdp` to an already-running Chrome, one shared default context for all jobs).

Workstream E (2026-10-12) implemented the safely-separable hardening that does NOT need the
redesign (see "What was done without the redesign"). Everything below is documented, not built.

---

## The redesign (applies to all three findings)

**What would change.** Stop attaching to the shared Chrome over CDP. Instead, for each job:

1. Playwright launches (or is given) a browser and creates a **fresh, dedicated browser
   context per job**: `browser.new_context(service_workers="block", ...)` — one incognito-like
   storage partition per job, destroyed at job end.
2. All per-job enforcement (routes, init scripts, WebSocket handlers, challenge detection)
   lives on that context. Nothing is shared between jobs; there are no "pre-existing tabs"
   inside the job's context by construction.
3. The warm logged-in session problem must be solved differently: either persistent
   per-job storage state (`storage_state=...` loaded into the fresh context) or an explicit
   re-login per job. The current "reuse the warm Chrome" shortcut goes away.

**Why CDP can't do it.** `connect_over_cdp()` attaches to the default browser context of an
already-running Chrome. Playwright's `service_workers="block"` context option, per-context
`route_web_socket`, and storage-partition isolation are context-creation options — they do not
exist on a CDP-attached default context. CDP has `ServiceWorker.unregister` (removal, not
prevention) and no per-context WebSocket attribution for foreign tabs.

**Risk.** HIGH. This is a fundamental architecture change:
- The warm-session / human-pace login flow (AGENTS.md: reuse warm logged-in sessions) must be
  re-engineered; per-job contexts with fresh storage partitions will look like new devices to
  ATS bot detection (new cookies, new fingerprints per job — the opposite of the current
  "one stable fingerprint" guidance).
- `storage_state` reuse across contexts mitigates login cost but reintroduces shared-state
  questions (which origins' storage is safe to clone?).
- Every gate that currently assumes "the shared context" (`JobScope.before`, `adopt()`,
  `refresh()`, the route handler's foreign-tab policy) must be re-audited; much of that
  machinery becomes dead code, and dead security machinery is worse than none.
- Rollback is "revert the patch and go back to CDP attach", but any jobs mid-flight during
  the swap need reconciliation.

**Effort.** 2–4 focused days: spike the context-per-job lifecycle against the local fake
ATS site (login persistence, SW behavior, WS attribution), rework `JobScope`/`attach()`,
re-run the full browser test matrix, then a supervised dry-run soak. Plus a Codex review
round. Not a one-evening change.

**Decision needed from Kishore** before anyone starts: accept the bot-detection tradeoff
(fresh storage partition per job vs. today's warm persistent profile).

---

## Finding 1 — Service-worker isolation needs `service_workers="block"` in an isolated context

**Codex claim.** Dormant or malicious service workers can exfiltrate form/PII data outside the
route handler's view. True prevention is Playwright's `service_workers="block"` context option,
which requires a Playwright-created (isolated) context — not CDP attach.

**Why the current architecture can't fully fix it.**
- The pipeline shares one browser profile/context across jobs. Service workers are registered
  per origin per storage partition; a worker registered by job N (or by any pre-existing tab)
  persists and can activate during job N+1.
- CDP `ServiceWorker.unregister` / `stopAllWorkers` is *removal after the fact*: enumerate →
  unregister → verify. A worker can register and fire between the sweep and the navigation, and
  Playwright may not route worker-initiated requests on a CDP-attached browser at all (the
  route handler's `frame is None` branch is best-effort).
- The pre-existing preflight (scan `context.service_workers` + `getRegistrations()` against
  `test_base`) only sees *running* workers and compares against the test base, not the
  production allowlist.

**What was done without the redesign (Workstream E, commit TBD).**
1. `_service_worker` no longer silently ignores workers on non-approved origins — those are
   the dangerous ones. A worker is now flagged when the job has an **owned page on the
   worker's origin** (`service_worker_unapproved_origin` if that origin isn't approved,
   `service_worker` otherwise). Workers from foreign tabs (no owned page on their origin)
   are ignored: service workers are origin-scoped and cannot observe the job's pages, so
   flagging them would block jobs over unrelated tabs (Codex review round 1).
2. The module-global `_SW_VIOLATIONS` list (which leaked across jobs: one worker event aborted
   *every request in every later job* until process restart) is replaced by per-scope
   `JobScope.sw_violations`. The route handler's fail-closed abort now runs **after** the
   ownership check, so a violated job still never disturbs pre-existing (foreign) tabs'
   traffic — only its own pages and frameless worker-style requests are aborted
   (Codex review round 1).
3. The `serviceWorker.register` block (previously dry-run-only via `DRY_RUN_JS`) is now
   installed in live mode too, via the new `LIVE_NET_GUARD_JS` init script on every adopted page.

These are correctness fixes to the existing mitigation layer, not isolation. They do not claim
to prevent a worker from registering — they ensure a worker that can observe the job is
*detected, flagged, and the job blocked*, without false-positiving on foreign tabs.

On the cross-job worker-persistence question (Codex review round 1): per-scope reset is
correct, not a regression. Service workers are origin-scoped — a worker that outlives job N
cannot observe job N+1's pages unless N+1 navigates to the same origin, in which case N+1's
own `_service_worker` listener fires on activation and flags it. The old global wasn't
"persistent protection", it was a liveness bug (every later job's traffic aborted forever).
The process-start preflight already scans for pre-existing workers.

On event-time attribution blind spots (Codex review round 2: a foreign worker ignored at
event time could remain active when an owned page later navigates to its origin; an owned
page's iframe could sit on the worker's origin): analyzed and not practical threats.
- Later navigation to an *unapproved* origin is already blocked by `_police`
  (`host_not_allowed`) before any worker matters; the worker flag would be redundant.
- Later navigation to an *approved* origin means the worker is that origin's own
  first-party worker — trusted under the pipeline's origin policy, not a threat.
- Iframes on unapproved origins inside an owned page cannot receive the job's PII
  (`gate_field` pins entry to approved origins; same-origin policy isolates the frame).
Retaining every observed worker and re-checking on each navigation would only reintroduce
the foreign-tab false positives without closing a real exfiltration path. Precise,
stateful worker lifecycle tracking across navigations is part of what the isolated-context
redesign provides (`service_workers="block"` eliminates the category).

**What the redesign would add.** `service_workers="block"` on the per-job context: the
browser refuses registration outright, per storage partition, with no sweep race window.

---

## Finding 2 — WebSocket enforcement needs an isolated job context (pre-existing tabs escape)

**Codex claim.** `context.route("**/*")` never sees WebSocket traffic (Playwright limitation —
`route_web_socket` is the API, and it is per-page). Pre-existing tabs are never adopted, so
they never get the block; a job's exfiltration surface via a foreign tab's socket can't be
attributed in the shared context.

**Why the current architecture can't fully fix it.**
- `page.route_web_socket` must be installed per page. Pages in `scope.before` (pre-existing
  tabs) are deliberately never adopted/touched, so no handler is ever installed on them.
- A context-level `route_web_socket` on the *shared* context would intercept foreign tabs'
  sockets too — but `WebSocketRoute` doesn't carry reliable per-page attribution on all
  Playwright versions, and closing foreign tabs' sockets breaks the "never disturb
  pre-existing tabs" contract.
- Net result: in the shared context, per-job WebSocket policy and non-interference with
  foreign tabs are mutually exclusive.

**What was done without the redesign (Workstream E, commit TBD).**
- WebSocket blocking moved out of the `if DRY_RUN:` gate in `JobScope.adopt()`: every page
  the job adopts (its tab + its popups) now gets `page.route_web_socket("**/*", close)` **and**
  the `WebSocket`-constructor override, in **both** dry-run and live mode, fail-closed
  (`websocket_interception_unavailable` aborts the job if the API is missing).
- Live mode previously had *zero* WebSocket enforcement (`STATIC_BLOCKED_TYPES` listed
  `websocket`, but `context.route` never sees WS traffic). This closes that gap for the
  job's own pages — which is the defined enforcement boundary of the current architecture.

**Residual (documented, needs redesign).** Sockets opened by pre-existing/foreign tabs are
still outside the job's policy. The isolated-context redesign eliminates the category: a
per-job context has no foreign tabs by construction, and `context.route_web_socket` on the
job's own context covers everything in it.

---

## Finding 3 — Nested widget token deduplication (complex iframe/widget identity)

**Codex claim.** `CHALLENGE_JS` can't reliably bind a captcha token to the exact widget
instance that produced it across nested/cross-origin iframe structures: token reuse across
widgets, stale tokens after expiry (~120s reCAPTCHA / ~300s Turnstile), and widget re-renders
(SPA remounts) that orphan the field value.

**Assessment: deferred — deliberately NOT implemented.**
- The pipeline **never solves captchas**. `check_challenge` is a tri-state gate (clear /
  active / error): detect → wait once (30s) → give up (`blocked`). There is no code path that
  consumes a token, so token *binding* has no security consumer — only the fail-closed
  detector matters. It fails closed on inspection errors and on unrecognised providers
  detected by the scan (a visible captcha-looking element with widget-like structure —
  an iframe, or an input/textarea/select inside or itself — that maps to no known
  provider reads `unknown`, which the Python side treats as `error` and blocks the job).
  The scan is heuristic and has documented gaps: it can miss a challenge input nested
  inside a container that also wraps the invisible-reCAPTCHA badge, and a challenge
  widget made only of buttons or links. It does NOT fail closed on a stale
  token or on a token belonging to a different widget instance: such a token can read
  `clear` until the provider itself clears or resets the response field. That window is an
  accepted residual: the submit then fails server-side and is classified
  `rejected`/`ambiguous`, never counted as submitted.
- The full fix (plan item 10c: `ChallengeLedger` with WeakMap uid per widget, provider APIs
  `grecaptcha.getResponse(widgetId)`, per-widget TTL tracking, five call-site updates) is
  **untestable in this environment** — no real challenge widgets, and the fake site can't
  reproduce provider expiry semantics. Shipping an untestable token-binding system would be
  exactly the half-fix this document exists to prevent: complexity plus a false sense of
  precision.
- Operational impact of the residual is bounded: a stale token reads "clear", the job
  proceeds, `live_submit` preconditions run, and the submit fails server-side on the dead
  token → classified `rejected`/`ambiguous`, never silently counted. No exfiltration path.

**What the redesign would change.** Honestly assessed: an isolated context does not by itself
solve cross-origin iframe token attribution either. If token binding is ever needed (i.e. if
the pipeline ever moves from "detect and hand off" to "act on solved state"), it should be
built as the `ChallengeLedger` design with real-widget fixtures — independent of, not gated
on, the context redesign. Until then, the fail-closed tri-state stands.

---

## Verdicts

| Finding | Without redesign | Needs redesign |
|---|---|---|
| 1. Service-worker isolation | Done: flag unapproved-origin workers; per-scope violations (no cross-job leak); register-block in live mode | **Closed 2026-10-13 (isolated context):** `service_workers="block"`, event is a fail-closed tripwire |
| 2. WebSocket enforcement | Done: WS block on adopted pages in live mode too, fail-closed | **Closed 2026-10-13 for pages/frames/popups (isolated context)**; dedicated/shared workers refused at JS level (the browser routes do not reach them) |
| 3. Nested widget token dedup | Intentionally not built (untestable; no security consumer; fail-closed detector suffices) | If ever needed: `ChallengeLedger` with real-widget fixtures — not gated on the context redesign |

All three redesigns are **blocked on Kishore's decision** re: the bot-detection tradeoff of
per-job storage partitions (see "The redesign" above). No code for the redesign has been
written.

## Agreed revised designs (2026-10-12 closeout)

This section records the designs agreed between the Codex review and the Claude solutions
(claude_solutions.md ITEMS B, C, D) at closeout. It supersedes nothing above; it refines the
deferred designs and states exactly what was and was not changed in this closeout.

### Finding 1 — service-worker isolation: agreed revisions

- **Storage filtering on both import and export.** When a per-tenant `storage_state` is
  loaded into a fresh context, and again when it is captured at job end, cookies and origin
  storage are filtered to the approved origin allowlist. IndexedDB is included in that filter
  whenever IndexedDB capture is enabled (`storage_state(indexed_db=True)`); it is never cloned
  unfiltered.
- **B2 uses a sanitized tenant-specific profile.** If the persistent-context fallback (B2) is
  used, each tenant gets its own profile directory, sanitized before first use. There is never
  a shared "golden copy" profile cloned across tenants or jobs.
- **Fingerprint continuity is a measured hypothesis, not a claim.** A fresh context in the same
  browser build is expected to be close to the warm profile, but viewport, screen, and other
  context defaults may differ and must be measured against the fake ATS and one supervised
  real ATS before the soak. The design does not assert identical fingerprints.
- **Enforcement installed before any job page exists.** Routes, init scripts
  (`LIVE_NET_GUARD_JS`), and WebSocket handlers are installed on the context before the first
  job page is created or navigated. A job page that is created before enforcement is in place
  is a defect, not a race to be tolerated.
- **A revert reopens the finding.** Reverting to CDP attach on the shared default context
  reopens Finding 1 in full; the "What was done without the redesign" mitigations are not a
  substitute.

### Finding 2 — WebSocket enforcement: agreed revisions

- **Worker socket coverage is a mandatory acceptance gate in the spike.** Dedicated and
  shared-worker WebSocket paths must be covered. The constructor overrides are tested
  adversarially across frames, popups, and every worker creation path (`new Worker`,
  `SharedWorker`, blob and data URLs, module workers). The spike does not pass until these
  tests exist and pass.
- **Pin the tested Playwright version.** The `route_web_socket` behavior relied on is
  version-specific. The spike pins the exact Playwright version it validated, and an upgrade
  reruns the gate.
- **Tests must show zero upstream handshakes.** Acceptance is measured at the network edge
  (fake ATS server logs or a local capture), not by the absence of a JS error: no WebSocket
  handshake reaches any upstream host during a job.
- **Explicit operator decision on workers.** The operator decides whether workers are
  prohibited outright for job contexts. If prohibited, the compatibility consequences (ATS
  pages that depend on workers failing or degrading) are accepted and documented; if not,
  worker socket coverage above is the only acceptable alternative.
- **Extension exclusion verified.** Browser extensions are excluded from job contexts and the
  exclusion is verified, with particular attention to B2 persistent contexts where an
  extension can be loaded from the profile.
- **Scope of the claim.** The design claims per-job WebSocket enforcement for the job's own
  context only. It does not claim universal network-exfiltration prevention, and no document
  or report may describe it that way.

### No code for Findings 1 and 2 in this change

No code for Findings 1 or 2 is implemented in this closeout. Implementation is blocked on the
operator's Phase-0 decision, which must cover all four points:

1. Accept the isolated-context architecture (per-job Playwright-created context) in place of
   CDP attach to the shared Chrome.
2. B1 first (fresh context plus per-tenant `storage_state`), with B2 (sanitized per-tenant
   persistent context) as the fallback.
3. The per-tenant `storage_state` session model, including the import/export filtering above.
4. The soak go/no-go criteria (challenge rate, login persistence, zero upstream handshakes,
   zero service-worker registrations) before any live traffic.

### Finding 3 — nested widget token dedup: closeout

Deferral of the `ChallengeLedger` is **agreed, with adjustments**. Three assertions made in the
deferral argument (claude_solutions.md ITEM D) are **withdrawn** as unsupported:

- No claim that a ledger necessarily requires hooking provider globals (`grecaptcha.render`,
  `turnstile.render`) in the main world. Other designs exist; this was not evaluated.
- No claim that a ledger necessarily raises challenge rates. That was a consequence of the
  hooking assumption above and falls with it.
- No claim that a ledger constitutes a solver or the first half of one. Token bookkeeping is
  not solving.

The deferral stands on the remaining grounds: the pipeline has no token consumer under the
detect-and-hand-off policy; the detector fails closed on inspection errors and on
unrecognised providers detected by the scan (heuristic — see the documented gaps above),
while a stale or wrong-widget token reading `clear` is an accepted residual
handled server-side; and a ledger cannot be tested here without real widgets.

**Closeout checks.** Coverage. Python-side behaviour (the `unknown`/`error` parse mapping, tri-state aggregation
across the page and the application frame, and the wait-once-then-block flow) is covered by
`tests/test_challenge_detector.py` (24 tests). Real-DOM behaviour of `CHALLENGE_JS` (badge
exclusion, captcha-named wrappers and help text, unrecognised providers, empty / filled /
mixed known widgets, invisible reCAPTCHA, SPA remount) is covered by
`tests/test_challenge_dom.py`: executable fixtures run in headless Chromium via Playwright,
skipped as a whole when no browser can be launched.

**One real bug fixed.** A visible captcha container from an unrecognized provider (no known
widget selector, no known iframe pattern) previously fell through the provider loop with
`total === 0` and read `'clear'`. `CHALLENGE_JS` now returns the `'unknown'` sentinel for such
containers (class, id, or iframe src matching "captcha", excluding known providers and
already-counted widgets), and `parse_challenge_result` maps `'unknown'` to `'error'`, so the
job is blocked at `challenge_inspection_error` instead of proceeding. Unreadable fields on a
*known* provider are unchanged: they remain `'active'` (one 30 s wait, then blocked).

**Accepted residual.** The operator accepts that a stale or wrong-widget token can read
`'clear'` for the seconds before the widget itself clears it, leading to a server-side
rejection classified `rejected` or `ambiguous`, never a silent `submitted`. The operator must
confirm that the pipeline stays detect-and-hand-off. Finding 3 reopens only if the pipeline
ever acts on a human-solved challenge automatically; at that point the `ChallengeLedger` is
built with real-widget fixtures on a staging ATS, independent of the context redesign.


---

## What was built (2026-10-13)

Plan: `per-job-context-plan.md`. Spike, with every result and the decision record:
`docs/reviews/2026-10-13-context-isolation-spike.md`. All results were measured at a local server,
each against an unprotected control.

**Browser.** `apply_pipeline.launch_browser` launches the pipeline's own Chrome.
- There is no CDP attach, no remote-debugging port, and no flags that hide automation. Kishore
  decided "pacing, not evasion" on 2026-10-13, so `navigator.webdriver` reads true.
- `_loop` owns the browser:
  - It holds the display lock (`APPLY_DISPLAY_LOCK`) for the browser's whole life.
  - It runs `network_self_test` after every launch, in both modes.
  - It recycles the browser after `APPLY_BROWSER_MAX_JOBS` jobs, or when a job context failed to
    close (`BROWSER_UNUSABLE`).
  - It stops (rc 2) when the browser itself can't be closed.
- The old pre-job service-worker sweep of the shared context is gone; a fresh context has nothing
  to sweep.

**Per-job context.** `JobScope.attach` creates
`browser.new_context(service_workers="block", accept_downloads=False)`. It sets `no_viewport` only
when headful, and passes the tenant's filtered `storage_state` when one exists. Before any page
exists, it installs:
- `context.route_web_socket("**/*", _swallow_websocket)`;
- `NO_WORKERS_JS`, `SUBMIT_GUARD_JS`, and `DRY_RUN_JS` or `LIVE_NET_GUARD_JS` as init scripts;
- the `page` and `serviceworker` listeners;
- the request route.

Every page in the context is the job's, so the shared-context machinery is deleted: `before`,
`owns()`, `closable`, `refresh()`, `_opened_by_job()`, `_explicit_adopt`, and the foreign-tab route
branch. `detach` closes the context, and never raises. The route of a detached scope aborts
everything, and its callbacks do nothing.

**Sessions, the answer to "which origins are safe to clone".** There is no global `warm_state.json`;
each job gets only its own tenant's state.
- **Filtering on load.** `ats_sessions.load_state_for(key, host, allowed_origins)` keeps:
  - cookies whose domain is the job's host or a subdomain of it, never a parent or sibling domain,
    which would ride along to other tenants;
  - storage only for explicit `(scheme, host, port)` origins;
  - on a shared multi-company host (Lever, Greenhouse, Ashby, ...), cookies only, since origin-wide
    `localStorage`/IndexedDB there belongs to every company's tenant.
- **Failing closed.** A file that isn't 0600, is over 1 MiB or is malformed blocks the job
  (`session_state_untrusted`).
- **Refresh.** The session is saved back, IndexedDB included, only after a clean finish, never
  after a violation.
- **Entry.** `python apply_pipeline.py --warm-login <job_url>` is the only way a session enters.
  A human signs in on `:2`, the pipeline types nothing, and each run uses a fresh sentinel. The
  session is saved only once the page is on the tenant's host and shows no sign-in wall.
- **Expiry.** A restored session that shows a sign-in wall on two polls is renamed `.expired`
  (kept seven days), and the job goes to `needs_review/session_expired`. Automatic vault sign-in
  stays out of this change.

Tests: `tests/test_job_sessions.py`, plus the session round trip in
`tests/test_job_context_browser.py`.

**Finding 1, closed.**
- `service_workers="block"` stops registration outright: zero script fetches, measured.
- A `serviceworker` event is now a tripwire: it records `service_worker_despite_block`, and the
  route then aborts all of the job's traffic.

**Finding 2, closed for pages, frames and popups.**
- `context.route_web_socket` covers the main page, iframes on the same and on other origins, and a
  popup whose first script opens a socket. Zero handshakes were measured, with the JS overrides
  removed.
- The handler never calls `ws.close()`. In the sync API that call deadlocks: the handler runs on
  Playwright's own event loop. The page-level handler on this branch had that bug before this
  change (commit `9cc9fed`).
- The `WebSocket` JS override stays as defence in depth.

**Workers: the gap the redesign did not close by itself.** The spike measured, on Playwright 1.60.0
and 1.63.0, that the browser-level routes do not reach workers:
- `context.route_web_socket` does not reach a dedicated worker's WebSocket.
- `context.route` does not reach a shared worker's HTTP.

So:
- **Refusal.** `NO_WORKERS_JS` makes `Worker` and `SharedWorker` throw in every frame. The
  properties are non-writable and non-configurable. A fresh `about:blank` iframe and
  `window.open('about:blank')` were both tried as ways around it, and both failed.
- **Count.** Refusals are counted per page (`__pipelineWorkerRefusals`), so the soak shows how many
  real forms tried to start a worker.
- **Backstop.** `page.on("worker")` is the backstop for a bypass. It records
  `worker_despite_block`, `check()` also scans `page.workers`, and the route then aborts.
  Shared workers raise no Playwright event, so the refusal is their only layer.
- **Incidental.** The pipeline's route answers allowed requests with `route.fetch`, which cannot
  complete a WebSocket upgrade, so a dedicated worker's socket is also stopped there. That is a side
  effect, not a designed layer.

Residual: the worker refusal is a JavaScript-level refusal, not a browser-enforced one.

**Tests.**
- `tests/test_job_context.py` (pure): attach order, fail-closed cleanup, popups, tripwires, the
  `_loop` lifecycle, the display lock, and structural checks.
- `tests/test_job_context_browser.py` (real Chromium): every vector, plus sensitivity tests
  (removing one layer must let traffic through) and the self-test in both modes. These fail rather
  than skip with `PIPELINE_BROWSER_TESTS_REQUIRED=1`.
- `tests/test_ws_route_no_deadlock.py`: the deadlock regression, run in a subprocess with a timeout.
- `tests/test_sw_ws_hardening.py`: the rewritten tripwire tests.

**Rollback.** A rollback is not just a revert:
1. `systemctl disable --now jobagent24-pipeline.service`, and remove
   `/etc/systemd/system/jobagent24-pipeline.service`.
2. Either start a verified CDP Chrome on `:2` again, or leave the pipeline stopped.

Reverting reopens Findings 1 and 2 as recorded above. Finding 3's closeout is unaffected.
