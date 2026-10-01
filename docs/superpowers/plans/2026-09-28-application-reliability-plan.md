# Application reliability and browser automation plan

Date: 2026-09-28 (amended 2026-09-30 to incorporate [the validation review](../../reviews/2026-09-30-application-reliability-validation.md))
Status: proposed implementation plan; no implementation, deployment, or merges performed by this plan.

## 1. Product decision

Keep the existing application dashboard, database, and application engine. Build reliable authentication, progress recovery, verified form preparation, and submission evidence around a persistent ATS browser. Use Playwright for known steps and one replaceable AI browser adapter for unfamiliar steps.

**Amended 2026-09-30:** drop Skyvern from the local Beelink plan rather than evaluating it. Skyvern's deployment is a full Docker Compose stack (its own FastAPI server, Postgres instance, web UI, and Chromium runtime) — on a 16GB quad-core mini-PC already running Next.js, the Postgres pollers, Xvfb, VNC, and `job_pick.py`'s PyTorch-backed embeddings, that operational footprint (2-4GB RAM, CPU-heavy container services) is unjustified when roughly 90% of target postings are guest-submission forms on Greenhouse, Lever, or Ashby. Repair Browser Use as the initial comparison baseline, same as before, but compare it against **Stagehand** and an **in-tree constrained schema adapter (`ats_agent.py`)** instead of Skyvern:
- **Stagehand** runs in-process with Playwright, resolves custom React comboboxes/shadow-DOM widgets (the actual, specific failure mode on Ashby-style forms — these do not fail because they need autonomous browsing, they fail on nonstandard form controls) via DOM/accessibility-tree inspection rather than constant screenshotting, and caches resolved selectors for near-zero token cost on replay.
- **`ats_agent.py`** (named in the Sept 17 Beelink spec) passes the page's accessibility tree to Claude with exactly two tools, `fill_field(label, value)` and `select_option(label, value)` — **no click tool, no submit tool** — for absolute action containment by construction rather than by prompt instruction ("do not click submit" is not a boundary; an absent tool is).

Both are lighter on Beelink's CPU/RAM than Browser Use's vision-heavy screenshot loop and dramatically lighter than Skyvern's container stack. Stagehand and `ats_agent.py` are not mutually exclusive: use Stagehand where Playwright hits a custom widget Stagehand can resolve cheaply, and `ats_agent.py` for the containment-critical path of filling/selecting standard fields without ever exposing a submit-capable tool. Browser Use remains the baseline exactly one of these needs to beat on the Phase 2 selection criteria (§5 Phase 2); it is not assumed to win.

Do not migrate the application engine to Hermes or OpenClaw. They may later provide an optional conversational interface that calls the engine's typed operations. The database remains the authority for application status, approval, and submission evidence.

The product promise is: every attempted application has an accurate, recoverable status; successful submissions have evidence; unresolved issues are visible. Universal autonomous success is not an achievable guarantee. Preparation, submission, and hiring outcomes are distinct metrics.

## 2. Initial scope and existing design changes

- Initial supported ATS families: Greenhouse, Lever, and Ashby, with tenant-specific coverage established by tests.
- Support returning users, ordinary sign-in, authorized account creation, email verification, expired sessions, multi-page forms, and file uploads within that tested scope.
- **Amended 2026-09-30:** email verification (a 6-digit code or magic link, common on Ashby and Workday-style accounts) is handled automatically first, not treated as an immediate takeover event. The repo already has a working Gmail API/IMAP client (`gmail.py`); see §4's authentication subsection for the interception flow and its escalation boundary.
- Unknown sites initially run through preparation or supervised evaluation. Expand supported submission coverage one tested flow at a time.
- Workday remains explicitly unsupported in the first release. Its existing exclusion must be changed deliberately in a separate expansion, after representative flows pass evaluation. Unsupported rows remain visible and are counted in coverage reporting.
- LinkedIn remains a separate discovery workflow. Keep its browser/profile separate from ATS execution. Do not alter its existing service configuration in this work.
- Preserve the existing final application approval requirement. Account creation and authentication are distinct workflow operations; configure authorization for those explicitly during onboarding.
- CAPTCHA and device-bound authentication can require user takeover. Resume the same session afterward.

The September 17 design calls for removing Browser Use and prohibits Skyvern. This plan proposes replacing the blanket Browser-Use rejection with a constrained, measured evaluation against Stagehand and `ats_agent.py` (§1's amendment) — Skyvern stays excluded, though for a measured hardware-footprint reason rather than the earlier blanket policy. It preserves human handling of CAPTCHA and does not enable automated challenge-solving or stealth features. Before implementation, update the earlier architecture document to name the selected adapter, its permitted capabilities, and the revised navigation policy. A package's available features are not automatically authorized features.

This proposal also expands the older fill-only tool design: signup and multi-page forms require navigation. Bounded navigation must be implemented deliberately; unrestricted clicks, JavaScript, terminal access, or a prompt saying “do not submit” are not a sufficient final-submit boundary.

## 3. User experience

1. The user maintains an approved profile containing experience, education, employment dates, location, work authorization, and other necessary facts. Unknown answers remain unknown until resolved.
2. The application worker opens the job, restores the relevant account session, and prepares the application using the approved profile and selected documents.
3. The dashboard shows actionable states: Preparing, Needs your input, Ready for review, Approved/queued, Submitting, Submitted, Needs confirmation, Unsupported, or Failed with a reason.
4. Review displays employer, role, URL, resume and cover-letter versions, answers, unresolved questions, and the latest validation result.
5. Approval freezes that exact revision. Edits invalidate the approval and create a new revision.
6. When human help is required, “Take over” opens the browser through the configured remote access path. The worker releases control; “Continue” reacquires it and rechecks the page before resuming.
7. Submission displays confirmation evidence. Uncertain outcomes are labeled Needs confirmation and are reconciled before another submission attempt.

Refreshing the dashboard, restarting a worker, or losing network access must not lose the application state. A browser session can expire; progress records still survive and guide recovery.

## 4. Architecture and ownership

```text
Dashboard (optional Hermes/OpenClaw client later)
                       |
Application API + Postgres
profile versions / preview revisions / approvals / run records
                       |
Durable job worker on Beelink
claims / leases / checkpoints / budgets / outcome reconciliation
                       |
Dedicated ATS browser session
Playwright actions + one bounded AI browser adapter
                       |
Validation -> approved submit executor -> confirmation evidence
```

The worker owns browser mutation. Playwright and the AI adapter share its browser session and act sequentially. Human takeover suspends automation. An application lease and a browser-profile lock prevent conflicting workers from acting on the same application or account session.

Start with one active ATS worker. Increase concurrency only after measuring Beelink memory/CPU, account-session conflicts, queue delay, and reliability. Persistent browser processes change resource assumptions in the older Beelink plan; verify them rather than inheriting its concurrency estimate.

**Amended 2026-09-30 — hardware/concurrency, from the validation review's §6:** the Beelink's resource boundaries are tighter than a generic "one worker" rule covers:
- Run ATS form preparation in **headless Chromium by default**. Headful Chrome plus Xvfb plus x11vnc is measurably heavier on CPU/RAM; reserve the headful/VNC-visible display slot for the `waiting_for_user` (takeover) state only, launched on demand rather than kept running for every attempt.
- `job_pick.py`'s `sentence_transformers` embedding stage (already module-level, per the Module layout convention in root `CLAUDE.md`) saturates all CPU cores during scoring. Enforce — via systemd or an advisory lock — that the job-pick/scoring service and the ATS-apply/Chromium service never run concurrently on the Beelink; this is a real resource conflict distinct from the application-lease/browser-profile-lock concurrency control above, which only prevents two *apply* workers from fighting over the same account.

Use a separate execution state from the existing recruiting pipeline stage. Proposed execution states include queued, preparing, waiting_for_user, ready_for_review, approved, submitting, submitted, submission_unknown, failed_retryable, failed_terminal, and unsupported. Store the reason and next action with each blocked state.

**Amended 2026-09-30:** the validation review arrived independently at the same decoupling and proposed concrete column names worth adopting, with one deliberate deviation from its literal suggestion. The review proposes renaming `stage` to `pipeline_stage` and adding `automation_status` alongside it; this plan instead **keeps `job_applications.stage` unchanged** (`saved`, `applied`, `phone_screen`, `onsite`, `offer`, `rejected`, ...; already a Postgres `CHECK` constraint, see `docs/python/db-schema.md`) and adds `automation_status` (the enum in the paragraph above) as a pure addition. A rename touches every existing reader/writer of `stage` across this repo — multiple Python scripts and Next.js API routes, per root `CLAUDE.md`'s module-layout notes — for no behavioral gain the addition doesn't already deliver; the overloading problem is fixed by giving execution state its own column, not by renaming the business-status one. This is the Phase 0 fix for merge-review findings #4-6's root cause: those findings' symptoms (stuck "Submitting...", a PATCH route that didn't recognize `ready_to_submit`, failed submissions leaving inconsistent rows) all trace back to overloading one `stage` column with both meanings. Phase 0 already applied narrower, targeted fixes for each symptom (see the Phase 0 status note below) without this column split; the split itself is additive schema work that belongs in Phase 1, where the run/checkpoint/lease records are introduced, rather than blocking Phase 0's already-shipped defect repairs on a migration.

Suggested data additions, adjusted to existing schema during implementation:

- Versioned applicant facts and document references.
- Immutable preview revisions with answer values, document hashes, and source profile revision. Concretely: `preview_revision_hash` (`SHA256(answers + resume_hash + candidate_facts)`, computed when a preview is generated) and `approved_revision_hash` (set atomically, only by the approval RPC, never a plain column write — the same `SECURITY DEFINER` pattern the existing `approved_at`/`approve_application()` gate already uses). Approval is valid only while `preview_revision_hash == approved_revision_hash`; any edit after approval changes the former and invalidates the latter without a separate "is this stale" check.
- Approval records bound to one preview revision (i.e. `approved_revision_hash` above, not a bare timestamp).
- Application runs with worker lease (`worker_lease_id UUID`), attempt ID, heartbeat (`worker_heartbeat_at TIMESTAMPTZ`, so an abandoned lease is detectable and recoverable rather than permanently held), checkpoint, error classification, adapter/model versions, and cost.
- Submission attempt records with pre-submit intent, observed outcome, and evidence references.

Use atomic claims and transitions. Expired leases permit recovery only after checking whether another worker or browser action may still be active. A database transaction cannot make an external website submission exactly-once. If the site may have received the submission before a crash, reconcile the outcome instead of blindly replaying it.

The AI adapter receives only relevant applicant facts and page observations. It returns structured progress, requested actions, and failure reasons. Credentials are injected by the authentication layer for the intended domain, excluded from ordinary prompts and logs. Persist browser state with restricted access; avoid credentials and session tokens in screenshots or debug artifacts.

Before approval, allow only constrained tools and verified non-submission transitions. Account creation and login submission have their own operation type; they must not be confused with final application submission. On flows where final submission cannot be reliably separated from navigation, require supervised completion. Do not claim that removing a tool named Submit prevents all website side effects.

## 5. Delivery sequence and acceptance criteria

### Phase 0 — Repair confirmed correctness defects

**Status as of 2026-09-30: shipped and merged.** Use [the merge review](../../reviews/2026-09-28-merge-review.md) as the initial backlog.

- Bind approval to an immutable preview, with atomic edit/approve conflict handling. — Shipped: PR #9, a conditional `UPDATE ... WHERE approved_at IS NULL` on the `apply_preview` PATCH path, disambiguated into 404/409 rather than check-then-act. (Note: this closes the correctness defect with a column-level guard, not yet the `preview_revision_hash`/`approved_revision_hash` design in §4's amendment above — that pair is the Phase 1 schema work, not required to close this Phase 0 finding.)
- Restore UI status reconciliation after reload. — Shipped: PR #9, a mount-time effect resumes polling for any id restored from `sessionStorage`.
- Recover confirmed failed jobs, including failures before the Python worker starts. — Shipped: PR #9, a `timedOutIds` state distinguishes "may still be running" from "Approve & Submit" after a poll timeout, with explicit Check-now/Reset-approval actions rather than a silent, unexplained reset.
- Make approval reset and associated cleanup atomic and repeatable. — Shipped: PR #9, `reset_approval`'s own guarded `UPDATE` now clears both `approved_at` and `apply_blocked_reason` in one statement (new migration), removing the two-step partial-failure window.
- Supply the real approved candidate facts to screening and job-fit prompts. — Shipped: PR #10, a shared `candidate_profile.py` grounds both `apply_agent.py`'s screening prompt and `job_pick.py`'s fit judge; an empty profile now degrades to a human-reviewable outcome instead of a fabricated or unsupported verdict. (The validation review's §4 proposes consolidating this further into one authoritative `candidate_facts` record, including work-authorization/EEO facts presently sourced from the `applicant_eligibility` prompts key — worth doing, but as a Phase 1/3 data-model refinement, not a Phase 0 blocker: Phase 0's defect was *no real facts reaching the prompt at all*, which is fixed.)
- Propagate generic browser failures; failed preparation cannot become Ready for review. — Shipped: PR #10, `browser-use` pinned to the exact installed/tested version (`==0.1.40`, not a floor pin), the adapter rewritten against that version's real API via a CDP bridge, and a fill failure now raises into the existing `set_apply_blocked` handlers instead of being swallowed. Still open, consistent with this plan's Phase 2 (§5): whether the LLM-driven fill actually *completes* a real ATS form correctly end-to-end is unverified pending a live smoke test — this item closed the silent-failure safety gap, not the fill-quality question Phase 2's adapter comparison exists to answer.
- Fix PR #6 email syntax/null-MX defects and resolve its conflicts as a separate workstream. It need not block unrelated browser infrastructure work. — Shipped: PR #6 merged independently, RFC 5322 dot-atom-aware syntax check and RFC 7505 null-MX detection, both covered with regression tests against real objects (not mocks) rather than the prior narrower-first-character regex and any-successful-MX-resolution-is-valid logic.

Acceptance: targeted regressions cover the review findings, concurrent transitions, and pre-worker failure recovery. Required repository checks pass on each proposed merge. Review the final commits before merging; earlier passing suites did not cover these defects. (Met: 1255 tests passing on `main` post-merge, including new regressions for every finding above — an unmount/remount poll-resume test, a real-`dnspython`-object null-MX test, and a real-`browser_use`-object adapter smoke test among them.)

**Amendment from the validation review, not yet started:** decouple `job_applications.stage` (recruiting pipeline) from a new `automation_status` column (execution lifecycle) per §4's amended architecture section above. The review frames this as "same [Phase 0], plus decouple DB schema immediately" to avoid re-migrating patches built on the overloaded column; this plan places it in Phase 1 instead (where the run/lease/checkpoint records are introduced together) since Phase 0's specific defects are already fixed without it and re-opening Phase 0 for a schema migration this late offers no defect-closure benefit — but it must land before Phase 1's run/lease records are added on top of today's single-`stage` model, not after.

### Phase 1 — Durable state, authentication, and takeover

- Decouple `job_applications.stage` from a new `automation_status` column (§4's amended architecture section), and add `preview_revision_hash`/`approved_revision_hash` so Phase 0's approval-freeze fix (already shipped with a column-level guard) gains the stronger revision-binding the validation review recommends.
- Add run/checkpoint/lease records and status reconciliation.
- Introduce the dedicated persistent ATS browser and account/session mapping. **Amended 2026-09-30:** use Playwright's native `context.storage_state(path=...)`, one file per tenant domain (`sessions/{tenant_domain}.json`), rather than a single shared `--user-data-dir`. Workday-style providers are multi-tenant by company (`apple.wd5.myworkdayjobs.com` vs. `target.wd5.myworkdayjobs.com` are separate accounts, not one "Workday session"), and a shared profile directory risks `SingletonLock` crashes under any concurrent access and cross-tenant cookie contamination. On opening a job: check for `sessions/{tenant_domain}.json`; if present, launch the context with it; if the session has expired, fall through to reauthentication (still gated by the credential-injection/authorized-signup bullet below) rather than silently failing.
- Add controlled credential injection, authorized signup, and email verification scoped to the active request, recipient, sender/domain, and time window. **Amended 2026-09-30 — automated OTP/magic-link interception via the existing `gmail.py`:** when the worker reaches a step asking for a verification code, it enters an internal polling state (not an immediate takeover) and searches recent mail scoped to the active request's time window and the expected sender domain (e.g. `ashbyhq.com`/`workday.com`) for a 6-digit code or confirmation link, extracts it (`re.search(r'\b\d{6}\b', body)` for the code form), fills it, and continues. This removes human intervention for the common case. **Escalation boundary, not a relaxation of it:** if no matching message arrives within 60 seconds, escalate to `waiting_for_user` with the takeover path below — same as any other blocked state, and still scoped to the one sender/recipient/time-window the active request names, not an open-ended inbox scan.
- Provide takeover/resume using the existing remote-browser infrastructure after verifying the deployed connection path.
- Distinguish retryable navigation failures from invalid credentials, missing facts, unsupported flows, and uncertain submissions.

Acceptance: login survives normal worker restarts where the site permits; session expiration triggers reauthentication; takeover and resume work; terminating a worker mid-flow recovers without concurrent control or lost status. Never automatically reset a password or create another account merely because login failed. The OTP interceptor only ever reads mail matching the active request's own scoping (recipient, expected sender domain, time window around the triggering action) — it must not become a general-purpose inbox-reading capability exposed to the browser adapter or any prompt.

### Phase 2 — Browser adapter comparison

**Amended 2026-09-30:** Skyvern is dropped from this comparison per §1's amendment (Docker/Postgres/FastAPI footprint unjustified on the Beelink's 16GB/quad-core budget for a workload that's ~90% guest-submission Greenhouse/Lever/Ashby forms). Compare Browser Use against **Stagehand** and the **in-tree `ats_agent.py` constrained-schema adapter** instead — both are lighter and both are purpose-fit for the actual observed failure mode (nonstandard form controls, not a need for open-ended autonomous browsing).

- Pin and smoke-test a compatible Browser Use version using a real local browser fixture. Avoid mocking away the integration under test. (Phase 0 already did this for the *silent-failure* defect — pinned `browser-use==0.1.40`, rewrote the adapter against its real API, added a smoke test against the real library classes. This bullet is about the comparison harness, not re-doing that fix.)
- Implement a common adapter contract for page observation, bounded actions, checkpoint reporting, stop reasons, and usage accounting. All three candidates (Browser Use, Stagehand, `ats_agent.py`) implement this one contract so the comparison harness in the next bullet is adapter-agnostic.
- Implement a Stagehand evaluation adapter (in-process with Playwright; no separate service to deploy) and an `ats_agent.py` evaluation adapter (Claude given exactly `fill_field(label, value)` and `select_option(label, value)` tools over the page's accessibility tree — no click tool, no submit tool, so action containment is structural, not prompt-enforced). Verify each one's actual local resource requirements and integration boundaries before scoring it, same diligence this bullet previously asked for Skyvern.
- Benchmark all three (Browser Use, Stagehand, `ats_agent.py`) on controlled fixtures and supervised live preparation. Do not create duplicate real accounts or submit repeated real applications to compare tools.

Start with 30 representative scenarios spanning the supported ATS families and at least 10 different tenant configurations. Include renamed fields, custom selects, resume upload, validation errors, multi-step signup, existing accounts, email verification, expired sessions, popups/frames, human takeover, browser crash, and lost connectivity. Repeat controlled cases to assess variability. Weight the suite toward Ashby's custom comboboxes/multi-select chips specifically — that is the concrete, named failure mode motivating Stagehand's inclusion, not a generic "harder ATS" placeholder.

Use the same inputs, equivalent browser state, and comparable model budgets. Where model choices differ, report that separately so results measure the complete configuration honestly. Note going in that token cost per application differs by roughly an order of magnitude between Browser Use's per-step vision+DOM calls (~$0.15-0.35), Stagehand's cached-replay model (~$0.01-0.03 first run, ~$0.00 on a cache hit), and `ats_agent.py`'s single-prompt form-field call (~$0.01-0.02) — these are planning estimates from the validation review, not measured Beelink numbers, and the benchmark itself is what establishes the real figures.

Selection order: correctness and approval compliance, correct completion, intervention rate, total cost per correct completion, latency, then maintenance effort. Choose one production adapter from the three. If none meets the release gate, narrow supported coverage or improve the model/session/action layer before adding another framework.

### Phase 3 — Verified preparation and submission

- Detect actual fields, required markers, options, conditional sections, and validation errors.
- Map answers to approved facts and surface missing evidence. Cache reusable procedures, with checks that the page still matches before replay.
- Confirm uploads and field values using explicit checks where possible; AI-only visual assertions do not establish every critical outcome.
- Generate the immutable review revision only when required information is complete.
- Before submission, verify the active job/account, approved document versions and answers, and absence of new unanswered required fields. Material changes return to review. **Amended 2026-09-30 — concrete form-drift check:** when the preview is generated, compute and store `form_signature = SHA256(sorted(field_names))` over the page's actual detected fields. Before the submit worker acts on an approved revision, it reopens the page, recomputes `form_signature`, and compares. A mismatch (the employer added a new required field, changed the form, or closed the role) aborts submission, sets `automation_status = 'needs_input'` (not a silent retry), and records `apply_blocked_reason = 'Form changed after approval'` for the review UI — this is a stronger, automatic version of "material changes return to review" rather than a separate mechanism.
- Record submission intent before clicking. Capture job-specific confirmation, receipt ID, account history, or a matching confirmation email where available.
- Mark ambiguous outcomes submission_unknown and reconcile. Do not count a click or a generic success message as sufficient evidence by itself. **Amended 2026-09-30 — concrete reconciliation mechanism:** a crash or lost connectivity immediately after the submit click moves the row to `needs_confirmation` (never directly to `failed_retryable` — the site may have received the submission before the crash, and a retry from `failed_retryable` risks a real duplicate application). A separate reconciler job polls `gmail.py` for an incoming receipt matching the employer/role within the attempt's time window (most ATS platforms send an automated "Thank you for applying" email within 60-120 seconds); a match records the email's `Message-ID` as submission evidence and transitions to `submitted`. No match within 15 minutes escalates to a user-visible manual-check prompt (portal check via the existing takeover path) — it does not retry on its own. **Blind retries from `needs_confirmation` stay permanently blocked**, consistent with the exactly-once caveat in §4: a database transaction cannot make an external website submission exactly-once, and this reconciler is how that gap is closed without guessing.

Acceptance: revision edits invalidate approval; changed forms (including ones detected only via the `form_signature` mismatch, not just a manual re-review) return to review; crash-after-click cases never trigger blind resubmission and instead resolve via the Gmail-receipt reconciler or an explicit manual check; the UI shows evidence matching the actual approved content.

### Phase 4 — Controlled release and coverage expansion

**Amended 2026-09-30:** run unattended batch preparation headless-by-default per §4's amended architecture section; the headful/VNC-visible display slot is reserved for `waiting_for_user` takeover and launched on demand, not kept running across the pilot.

- Run preparation-only checks first.
- Pilot on 10 unique, user-approved real applications, with immediate outcome inspection. Begin with one ATS family and expand within supported scope as evidence supports it.
- Observe the next 50 eligible applications before broad unattended preparation. These are actual needed applications, not duplicate benchmark submissions.
- Keep submission approval per application. Provide pause, per-application cancellation, bounded retries, and a global spending stop.
- Roll back by pausing claims and draining/reconciling active runs before changing the adapter. Preserve approvals, run history, and evidence; keep migrations backward-compatible during rollout.
- Add Workday or other complex providers as separately evaluated coverage increments. Do not remove exclusions simply because a generic agent exists.

## 6. Proposed release gates and metrics

Targets below are proposed acceptance criteria, not measured performance or guarantees.

- No observed wrong-person answers, unapproved submissions, duplicate submissions, or false Submitted status in evaluation and pilot. Any such event blocks rollout and requires root-cause repair.
- At least 95% correct preparation on the predefined supported evaluation set. Report autonomous and assisted completion separately. A small suite passing this target does not establish a universal production success rate.
- Every injected restart, timeout, takeover, and lost-response scenario reaches a recoverable, accurate state in controlled tests.
- Every Ready for review record has validated required fields and versioned documents; every Submitted record has retained confirmation evidence.
- All attempts carry an outcome, duration, model/adapter attribution, and measured cost when the provider exposes it. Report unknown costs explicitly.

Dashboard metrics: supported coverage across all attempted jobs; correct preparation rate; autonomous and assisted completion; submission confirmation rate; intervention reasons; retries; p50/p95 duration; model and browser spend per correctly completed application. Include failed-attempt spend in cost totals. Do not hide unsupported sites or user interventions from denominators.

Set configurable per-run step, time, retry, and spending limits before live evaluation. Select production dollar limits from measured pilot usage and the user's operating budget; do not invent an unmeasured per-application cost promise. Reuse sessions and deterministic actions before downgrading model quality solely to reduce token price.

## 7. Optional assistant layer

After the engine meets the release gates, consider Hermes for broader research/task coordination or OpenClaw for conversational access across messaging channels. Either should call typed application operations such as prepare_application, get_status, request_review, and resume_application. Final submit authorization is still verified by the engine against its stored approval.

**Validated 2026-09-30:** the review reached the same conclusion independently and for the same reason — a conversational framework lacks a deterministic state machine, transactional guarantees, and lease management, so using an LLM chat loop as the orchestrator itself (rather than as a thin client in front of it) risks lost state and missed errors. Concretely, the typed operations above correspond to a plain REST surface (`POST /api/applications/:id/prepare`, `GET /api/applications/:id/status`, `POST /api/applications/:id/approve`, `POST /api/applications/:id/resume`); Hermes/OpenClaw would be thin clients of exactly that surface, never a parallel path that bypasses the stored-approval check.

Learned skill suggestions may become versioned ATS procedures only after validation. Memory is not the authority for applicant facts or whether an application was submitted. Adding an assistant requires a demonstrated product benefit; it is not a prerequisite for reliable browser automation.

## 8. Implementation packaging

Deliver reviewable changes in this order:

1. ~~Existing review fixes and candidate grounding; email-verification fixes remain independently reviewable.~~ **Shipped as of 2026-09-30** (PRs #6, #9, #10 — see Phase 0's status note in §5).
2. New `automation_status` column (recruiting `stage` stays unchanged, per §4), `preview_revision_hash`/`approved_revision_hash`, execution state, atomic recovery, and worker ownership.
3. Domain-scoped session storage, Gmail OTP interception, and takeover/resume UI.
4. Browser Use repair (shipped, see Phase 0 status), common adapter contract, and a Stagehand-vs-`ats_agent.py` comparison harness — no Skyvern flag; it is dropped from this plan per §1.
5. Verified form preparation (including the `form_signature` drift check), submission reconciliation (including the Gmail-receipt reconciler), and evidence UI.
6. Pilot metrics, limits, operational recovery, and deployment configuration (headless-by-default execution).

Each change includes the tests appropriate to its risk. Use real local browser fixtures for authentication and form interaction; controlled fault injection for concurrency/recovery; and supervised live checks for real provider behavior. No additional full-suite runs are needed without new changes or unresolved concerns.

Do not attach a calendar promise before the first adapter spike and Beelink readiness check. The release gates determine rollout, rather than a speculative deadline.

## References

- [Existing Beelink design](../specs/2026-09-17-beelink-24-7-automation-design.md)
- [Merge review and reproduced defects](../../reviews/2026-09-28-merge-review.md)
- [Validation review (2026-09-30) — source of the amendments marked "Amended 2026-09-30" throughout this plan](../../reviews/2026-09-30-application-reliability-validation.md)
- [Browser Use authentication](https://docs.browser-use.com/open-source/customize/browser/authentication)
- [Browser Use and Playwright integration](https://docs.browser-use.com/open-source/examples/templates/playwright-integration)
- [Skyvern SDK](https://github.com/Skyvern-AI/skyvern/blob/main/docs/sdk-reference/complete-reference.mdx) — considered and dropped for the local Beelink deployment per §1's amendment; kept here as the record of what was evaluated and why, not as a pending integration target.
- [Stagehand configuration and caching](https://github.com/browserbase/stagehand/blob/main/packages/docs/v3/references/stagehand.mdx)
- [Hermes browser backends](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/browser.md)
- [OpenClaw browser](https://docs.openclaw.ai/tools/browser)
