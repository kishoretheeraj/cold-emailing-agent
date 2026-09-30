# Application reliability and browser automation plan

Date: 2026-09-28
Status: proposed implementation plan; no implementation, deployment, or merges performed by this plan.

## 1. Product decision

Keep the existing application dashboard, database, and application engine. Build reliable authentication, progress recovery, verified form preparation, and submission evidence around a persistent ATS browser. Use Playwright for known steps and one replaceable AI browser adapter for unfamiliar steps.

Repair Browser Use as the initial comparison baseline. Evaluate Skyvern against it using the same tasks and equivalent resources. Stagehand is a conditional alternative if most failures are isolated element-identification failures and reusable actions provide material savings. Do not integrate all three into the production execution path.

Do not migrate the application engine to Hermes or OpenClaw. They may later provide an optional conversational interface that calls the engine's typed operations. The database remains the authority for application status, approval, and submission evidence.

The product promise is: every attempted application has an accurate, recoverable status; successful submissions have evidence; unresolved issues are visible. Universal autonomous success is not an achievable guarantee. Preparation, submission, and hiring outcomes are distinct metrics.

## 2. Initial scope and existing design changes

- Initial supported ATS families: Greenhouse, Lever, and Ashby, with tenant-specific coverage established by tests.
- Support returning users, ordinary sign-in, authorized account creation, email verification, expired sessions, multi-page forms, and file uploads within that tested scope.
- Unknown sites initially run through preparation or supervised evaluation. Expand supported submission coverage one tested flow at a time.
- Workday remains explicitly unsupported in the first release. Its existing exclusion must be changed deliberately in a separate expansion, after representative flows pass evaluation. Unsupported rows remain visible and are counted in coverage reporting.
- LinkedIn remains a separate discovery workflow. Keep its browser/profile separate from ATS execution. Do not alter its existing service configuration in this work.
- Preserve the existing final application approval requirement. Account creation and authentication are distinct workflow operations; configure authorization for those explicitly during onboarding.
- CAPTCHA and device-bound authentication can require user takeover. Resume the same session afterward.

The September 17 design calls for removing Browser Use and prohibits Skyvern. This plan proposes replacing the blanket tool rejection with a constrained, measured evaluation. It preserves human handling of CAPTCHA and does not enable automated challenge-solving or stealth features. Before implementation, update the earlier architecture document to name the selected adapter, its permitted capabilities, and the revised navigation policy. A package's available features are not automatically authorized features.

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

Use a separate execution state from the existing recruiting pipeline stage. Proposed execution states include queued, preparing, waiting_for_user, ready_for_review, approved, submitting, submitted, submission_unknown, failed_retryable, failed_terminal, and unsupported. Store the reason and next action with each blocked state.

Suggested data additions, adjusted to existing schema during implementation:

- Versioned applicant facts and document references.
- Immutable preview revisions with answer values, document hashes, and source profile revision.
- Approval records bound to one preview revision.
- Application runs with worker lease, attempt ID, heartbeat, checkpoint, error classification, adapter/model versions, and cost.
- Submission attempt records with pre-submit intent, observed outcome, and evidence references.

Use atomic claims and transitions. Expired leases permit recovery only after checking whether another worker or browser action may still be active. A database transaction cannot make an external website submission exactly-once. If the site may have received the submission before a crash, reconcile the outcome instead of blindly replaying it.

The AI adapter receives only relevant applicant facts and page observations. It returns structured progress, requested actions, and failure reasons. Credentials are injected by the authentication layer for the intended domain, excluded from ordinary prompts and logs. Persist browser state with restricted access; avoid credentials and session tokens in screenshots or debug artifacts.

Before approval, allow only constrained tools and verified non-submission transitions. Account creation and login submission have their own operation type; they must not be confused with final application submission. On flows where final submission cannot be reliably separated from navigation, require supervised completion. Do not claim that removing a tool named Submit prevents all website side effects.

## 5. Delivery sequence and acceptance criteria

### Phase 0 — Repair confirmed correctness defects

Use [the merge review](../../reviews/2026-09-28-merge-review.md) as the initial backlog.

- Bind approval to an immutable preview, with atomic edit/approve conflict handling.
- Restore UI status reconciliation after reload.
- Recover confirmed failed jobs, including failures before the Python worker starts.
- Make approval reset and associated cleanup atomic and repeatable.
- Supply the real approved candidate facts to screening and job-fit prompts.
- Propagate generic browser failures; failed preparation cannot become Ready for review.
- Fix PR #6 email syntax/null-MX defects and resolve its conflicts as a separate workstream. It need not block unrelated browser infrastructure work.

Acceptance: targeted regressions cover the review findings, concurrent transitions, and pre-worker failure recovery. Required repository checks pass on each proposed merge. Review the final commits before merging; earlier passing suites did not cover these defects.

### Phase 1 — Durable state, authentication, and takeover

- Add run/checkpoint/lease records and status reconciliation.
- Introduce the dedicated persistent ATS browser and account/session mapping.
- Add controlled credential injection, authorized signup, and email verification scoped to the active request, recipient, sender/domain, and time window.
- Provide takeover/resume using the existing remote-browser infrastructure after verifying the deployed connection path.
- Distinguish retryable navigation failures from invalid credentials, missing facts, unsupported flows, and uncertain submissions.

Acceptance: login survives normal worker restarts where the site permits; session expiration triggers reauthentication; takeover and resume work; terminating a worker mid-flow recovers without concurrent control or lost status. Never automatically reset a password or create another account merely because login failed.

### Phase 2 — Browser adapter comparison

- Pin and smoke-test a compatible Browser Use version using a real local browser fixture. Avoid mocking away the integration under test.
- Implement a common adapter contract for page observation, bounded actions, checkpoint reporting, stop reasons, and usage accounting.
- Implement a Skyvern evaluation adapter with unwanted cloud/automation features disabled. Verify the actual deployment configuration, local resource requirements, and integration boundaries.
- Benchmark both on controlled fixtures and supervised live preparation. Do not create duplicate real accounts or submit repeated real applications to compare tools.

Start with 30 representative scenarios spanning the supported ATS families and at least 10 different tenant configurations. Include renamed fields, custom selects, resume upload, validation errors, multi-step signup, existing accounts, email verification, expired sessions, popups/frames, human takeover, browser crash, and lost connectivity. Repeat controlled cases to assess variability.

Use the same inputs, equivalent browser state, and comparable model budgets. Where model choices differ, report that separately so results measure the complete configuration honestly.

Selection order: correctness and approval compliance, correct completion, intervention rate, total cost per correct completion, latency, then maintenance effort. Choose one production adapter. If neither meets the release gate, narrow supported coverage or improve the model/session/action layer before adding another framework.

### Phase 3 — Verified preparation and submission

- Detect actual fields, required markers, options, conditional sections, and validation errors.
- Map answers to approved facts and surface missing evidence. Cache reusable procedures, with checks that the page still matches before replay.
- Confirm uploads and field values using explicit checks where possible; AI-only visual assertions do not establish every critical outcome.
- Generate the immutable review revision only when required information is complete.
- Before submission, verify the active job/account, approved document versions and answers, and absence of new unanswered required fields. Material changes return to review.
- Record submission intent before clicking. Capture job-specific confirmation, receipt ID, account history, or a matching confirmation email where available.
- Mark ambiguous outcomes submission_unknown and reconcile. Do not count a click or a generic success message as sufficient evidence by itself.

Acceptance: revision edits invalidate approval; changed forms return to review; crash-after-click cases never trigger blind resubmission; the UI shows evidence matching the actual approved content.

### Phase 4 — Controlled release and coverage expansion

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

Learned skill suggestions may become versioned ATS procedures only after validation. Memory is not the authority for applicant facts or whether an application was submitted. Adding an assistant requires a demonstrated product benefit; it is not a prerequisite for reliable browser automation.

## 8. Implementation packaging

Deliver reviewable changes in this order:

1. Existing review fixes and candidate grounding; email-verification fixes remain independently reviewable.
2. Execution state, immutable approvals, atomic recovery, and worker ownership.
3. Persistent authentication and takeover/resume UI.
4. Browser Use repair, common adapter contract, and comparison harness; Skyvern evaluation behind a flag.
5. Verified form preparation, submission reconciliation, and evidence UI.
6. Pilot metrics, limits, operational recovery, and deployment configuration.

Each change includes the tests appropriate to its risk. Use real local browser fixtures for authentication and form interaction; controlled fault injection for concurrency/recovery; and supervised live checks for real provider behavior. No additional full-suite runs are needed without new changes or unresolved concerns.

Do not attach a calendar promise before the first adapter spike and Beelink readiness check. The release gates determine rollout, rather than a speculative deadline.

## References

- [Existing Beelink design](../specs/2026-09-17-beelink-24-7-automation-design.md)
- [Merge review and reproduced defects](../../reviews/2026-09-28-merge-review.md)
- [Browser Use authentication](https://docs.browser-use.com/open-source/customize/browser/authentication)
- [Browser Use and Playwright integration](https://docs.browser-use.com/open-source/examples/templates/playwright-integration)
- [Skyvern SDK](https://github.com/Skyvern-AI/skyvern/blob/main/docs/sdk-reference/complete-reference.mdx)
- [Stagehand configuration and caching](https://github.com/browserbase/stagehand/blob/main/packages/docs/v3/references/stagehand.mdx)
- [Hermes browser backends](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/browser.md)
- [OpenClaw browser](https://docs.openclaw.ai/tools/browser)
