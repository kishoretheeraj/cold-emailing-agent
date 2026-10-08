# First ten applications: design

**Status:** Draft for review. **Date:** 2026-10-08 (rev. 2: Workday in scope, quality bar, approval authenticity).
**Goal:** ten real job applications submitted end to end, each one reviewed and released by the
operator with a single tap, each with retained proof that the employer received it.
**Builds on:** [the application reliability plan](../plans/2026-09-28-application-reliability-plan.md)
(Phases 1-4 and packaging items 3, 4 and 6 still open),
[the Beelink design](2026-09-17-beelink-24-7-automation-design.md),
[the resume subscription transport](2026-10-04-resume-subscription-transport-design.md).

---

## 1. Where things stand (2026-10-08)

- No application has ever been submitted. `apply_agent_submit.yml` has never run.
- Previews run daily on GitHub Actions (headless Chromium, datacenter IP). The Oct 7 run
  crashed on the generic browser-use path (`coroutine 'Agent.run' was never awaited`); per-job
  results live only in a log artifact.
- The safety model is in place and stays: `automation_status` lifecycle with RPC-only
  transitions, leases, approval bound to `preview_revision_hash`, `submit_attempted_at`
  forcing `needs_confirmation` after a click, form-signature drift check, Gmail-receipt
  reconciler, required-field gate.
- Resumes and cover letters are built on the Beelink (`resume-worker.timer`, Claude
  subscription). A row enters preview only once both files exist.
- The repo is public, so Actions logs and artifacts are world-readable; the operator is
  making it private.

## 2. Decisions (operator, 2026-10-08)

| Question | Decision |
|---|---|
| Repo visibility | Private. Detailed per-job logs are allowed once it is. |
| Where the Submit click runs | The Beelink (residential IP, real Chrome, takeover possible), not GitHub Actions. |
| Ops access from the cloud | Dispatch-only workflows for status, dry runs and migrations (`apply_status.yml`, `apply_dryrun.yml`, `db_migrate.yml`). |
| Scope before the first submission | The full reliability plan: sessions, automatic signup, email verification, adapter bake-off, evidence. |
| Choosing jobs | Fully automatic queue: every strong verdict that prepares cleanly lands in the approval queue. |
| Accounts | Fully automatic signup with `kishoretheerajvj@gmail.com` and a unique generated password per site. |
| AI spend | None. All new model calls run on the Claude subscription (`claude -p`) on the Beelink. |
| Adapter bake-off | Subscription-only: deterministic fillers (baseline), an in-tree constrained adapter, and Claude Code driving the browser through Playwright MCP. Browser Use, Stagehand and Skyvern are dropped because they need a paid API key. |
| Approve UX | Queue cards; one tap on Submit starts a 5-second undo bar; nothing is sent until it ends. |
| CAPTCHA | Human takeover over noVNC, reachable from the Mac or phone through Tailscale. |
| Merging | Codex reviews each PR (`@codex review`); merge after its review, green tests and, for migrations, a rolled-back dry run against the real database. |
| Workday and other sites | In scope (operator, 2026-10-08, overriding the reliability plan's exclusion): Workday gets its own adapter (§6.1); any other site goes through the generic adapters. |
| Autonomy | The Beelink reads the receipt inbox itself for verification codes and links (§5); a human is needed only for CAPTCHAs, SMS codes, and the Submit tap. |

Two decisions are still open (§11): how code reaches the Beelink, and how to stay inside the
GitHub Actions minutes quota once the repo is private.

## 3. Non-goals for the first ten

- **No automated CAPTCHA solving, stealth patches or fingerprint spoofing.** Pacing and a real
  browser on a residential connection are the only measures; a challenge goes to a human.
- **No submission without the operator's tap.** "Fully automatic queue" automates finding,
  preparing and queueing; releasing each application stays a human action.
- **No password resets and no second account** when a login fails (reliability plan, Phase 1).

## 4. Architecture

```text
GitHub Actions (cloud)                    Supabase                      Beelink (residential)
--------------------------                --------                      ---------------------
jobright_pull.yml -> job_pick ----------> job_applications <--------- resume-worker (existing)
apply_status / apply_dryrun (read)        + application_runs  <------- apply-prepare.service (unarmed)
db_migrate (dry run, push)                + lifecycle RPCs     <------ apply-submit.service (ARMED)
                                          + evidence bucket                 |
contact-manager (Vercel) --approve RPC--> approved_revision_hash      Chrome on Xvfb :1
  queue cards, 5 s undo, takeover link    takeover {reason, continue} <- x11vnc + noVNC on the
                                                                          Tailscale address
```

### 4.1 Two Beelink units, one display

- **`apply-prepare.service`** (unarmed): loops over eligible rows. Per row it restores or creates
  the tenant session (§5), fills the form with the selected adapter (§6), captures a screenshot
  of the filled form, runs the required-field gate, and calls `complete_preview`. It never
  clicks a submit control; the executor has no action that can.
- **`apply-submit.service`** (the only unit with `APPLY_AGENT_ARMED=1`): polls for
  `automation_status='approved'` rows, claims one, and runs the existing `submit()` path:
  approval and hash re-checked after the claim, form-signature drift check, refill from the
  stored preview, `renew_submission_lease`, click, confirmation, evidence (§7).
- `APPLY_AGENT_ARMED` moves from `apply_agent_submit.yml` to `apply-submit.service`, and the
  "only one place" rule moves with it. `apply_agent_submit.yml` is retired (kept only as a
  disabled file until the Beelink path has submitted at least once) and `apply_agent_preview.yml`
  loses its schedule once `apply-prepare` is live. Approval is still proven by the database
  (`approved_at` plus `approved_revision_hash == preview_revision_hash`, both RPC-only), not by
  who launched the process.
- Both units drive real Chrome (`channel="chrome"`, headful) on **Xvfb display `:1`**, separate
  from LinkedIn's `:0` and its profile. A shared `flock` on `/run/job-agent/display1.lock`
  keeps the two from opening browsers at the same time; `apply-submit` takes priority.
  Running headful always (not headless-by-default as the plan suggested) is deliberate: a
  takeover must show the same browser the worker is using, and headless Chrome is easier to
  flag. Only one browser exists at a time, so the RAM cost is one Chrome.
- Both units respect `pause_scope` (`agent`/`all` stops them before the next row), have
  `MemoryMax`, run as `jobagent`, and log structured outcomes to `application_runs` (§8) so the
  cloud can read results through `apply_status.yml`.

### 4.2 Takeover

A worker that meets a CAPTCHA, a device check, an SMS code or a step it cannot finish keeps its
lease and its browser open and writes `takeover = {reason, requested_at}` on the row through a
new RPC. The queue card shows "Needs you" with a link to noVNC for display `:1` (served on the
Beelink's Tailscale address with a VNC password, never on the LAN or internet) and an
**I'm done** button that sets `takeover.continue_at` through an RPC. The worker heartbeats while
it waits, rechecks the page after Continue, and resumes.

Timeouts (30 minutes) end the wait safely: a prepare goes to `needs_input`; a submit before the
click goes to `failed_retryable`; anything after the click goes to `needs_confirmation`, exactly
as today. A challenge that appears after the Submit click is treated as post-click: the worker
waits for the confirmation page while the human solves it, and never clicks Submit a second
time.

## 5. Sessions, accounts and email verification

- **Tenant key.** `ats_sessions.tenant_key(url)` normalizes to the account boundary: the host for
  single-tenant domains, host plus company path for shared hosts such as `jobs.lever.co/acme`.
- **Sessions.** Playwright `storage_state` per tenant key in `/var/lib/job-agent/sessions/`
  (0700, `jobagent`). Loaded when a context opens, saved after a successful login or signup.
- **Credential vault.** `/var/lib/job-agent/vault.json`, encrypted with a key from
  `/etc/job-agent/vault.env` (root, 0600, loaded only by `apply-prepare`). One entry per tenant:
  login email, generated password (24 characters across four classes), login URL, created time.
  Values never enter a prompt, a log or a screenshot: the adapter sees a `secret:password`
  placeholder and a deterministic executor types the real value.
- **Automatic signup and login.** An auth wall (password field, sign-in or create-account
  copy) is detected deterministically. Vault entry present: log in. Absent: sign up with the
  applications address and a new password, accept the required terms checkbox, verify email,
  save the session and the vault entry. A failed login never triggers signup or a reset; it goes
  to takeover.
- **Email verification.** `email_verification.wait_for_code(sender_domains, since, timeout=60)`
  reads only the receipt inbox (`RECEIPT_IMAP_*`), only messages from the expected sender domains
  that arrived after the triggering action, and returns a 6-digit code or a link on an allowed
  host. No match in 60 seconds escalates to takeover. It is not a general inbox reader and is
  never exposed to an adapter or a prompt.

## 6. Adapters and the bake-off

**Contract** (`ats_adapter.py`): `fill(page, job, facts, budget) -> FillResult` where
`FillResult` carries the per-field report, actions taken, stop reason, model calls, duration and
whether a takeover was requested. Adapters may navigate between form steps but have no submit
action; on top of that, every page gets an init script that blocks form submission and disables
submit-labelled controls until `apply-submit` removes it at the final step.

**Candidates:**
1. **Deterministic** (baseline, no model): the current hand-mapped fillers plus inventory-driven
   eligibility filling.
2. **`ats_agent.py` (constrained):** sends the form inventory (labels, kinds, options, required,
   filled) plus the candidate facts to `claude -p` and receives JSON actions from a closed set:
   `fill`, `select`, `check`, `upload`, `next_step`. The executor rejects anything else, any
   field not in the inventory, and any value not traceable to the facts or a free-text answer.
   It loops observe, plan, act up to a round limit.
3. **Claude Code + Playwright MCP (agentic):** `claude -p` with only the Playwright MCP server,
   attached to the same Chrome over CDP, restricted to snapshot, type, select, check, click and
   file-upload tools, under the submit-blocking init script.

### 6.1 Workday

Workday is about a third of large-employer postings and every tenant
(`<company>.wd<N>.myworkdayjobs.com/<site>`) is its own account. It gets a dedicated deterministic
prelude keyed on Workday's stable `data-automation-id` attributes, followed by the bake-off
winner for the wizard pages. The selector inventory and flow are taken from MIT-licensed
open-source work (credited in `workday_adapter.py`): `djwmobley/claude-interview-coach`
(entry and auth ids verified read-only on live tenants on 2026-10-05), `amgenene/workday_auto`,
`Prajay-vats/workday-autofill`.

- **Entry:** `adventureButton` (Apply), then `applyManually` only. Never `autofillWithResume`,
  `useMyLastApplication` or LinkedIn apply: they import data we did not review.
- **Auth gate** (`signInContent`, `signInFormContainer`, `createAccountForm`): tenant key is the
  host plus site path. Vault entry present: `signInLink`, `email`, `password`, then click the
  `click_filter` overlay inside `noCaptchaWrapper` (the real submit button is aria-hidden behind
  it). Absent: the password is generated and **written to the vault before any typing**, so a
  crash mid-signup never loses a password for an account that may exist; then `email`,
  `password`, `verifyPassword`, `createAccountCheckbox`, overlay click. An `errorMessage` after
  either goes to takeover, never to a reset or a second account.
- **Verification:** a `verificationCode` field is filled from the receipt inbox (§5); a link-only
  email is opened in the same browser context only if its host is the tenant's own host.
- **Wizard:** pages advance only through `pageFooterNextButton`, and only when `progressBar`
  shows a later step than `progressBarActiveStep`. On the last (Review) step the prepare pass
  stops; the submit pass clicks Submit there. Resume goes to `file-upload-input-ref`.
  `alreadyApplied` or an `errorBanner` that does not clear stops the run.
- **Widgets:** listbox prompts (`button[aria-haspopup=listbox]` + `[role=option]`) are picked by
  exact option text and verified from the committed value; multiselect prompts
  (`multiSelectContainer`) are typed and confirmed through `promptOption`; date fields through
  `dateSectionMonth/Day/Year-display`. A required widget we cannot set parks the row in
  `needs_input` naming the question.
- **Prefilled values:** a field the site prefilled (resume parse, saved draft) that is outside the
  sensitive classes (authorization, sponsorship, salary, EEO, criminal history, consent) is left
  and listed on the card; inside them it must equal our answer or the row parks.

### 6.2 Lessons adopted from open-source apply agents

From `santifer/career-ops` (MIT) `docs/APPLY_AUTOFILL.md` and `shankswhite/JobApplyAgent` (MIT):

- **Liveness sweep** before preparing: a closed posting (404, "no longer accepting") goes to
  `stage='withdrawn'` with a reason instead of a failed fill.
- **Knock-out pre-scan** of the description for hard disqualifiers against the candidate's facts
  (sponsorship refused, clearance, years, degree, on-site location). A hit downgrades the row to
  `maybe` with the reason; it never reaches the queue as strong.
- **Lever** shows hCaptcha after programmatic checkbox and radio clicks; those go through
  label clicks with human-like pacing, and a challenge goes to takeover.
- **React-select comboboxes** are typed character by character and the option list re-read
  before choosing.
- **Ashby** deduplicates candidates by email. Re-applying after a failed attempt is a takeover,
  never a `+alias` address.
- **Answer bank:** every answer the operator edits or approves is stored with its normalized
  question (fuzzy containment match) and reused before any model call. Approved answers win over
  generated ones; a model only answers what the bank cannot.
- **Field ids:** the inventory stamps each control with a stable `data-cv-field-id` so a plan
  made from one snapshot acts on the same element after a re-render.

**Harness:** local HTML fixtures modelled on real Greenhouse, Lever and Ashby forms (captured
with `apply_dryrun.yml --dump-html`), plus renamed fields, React comboboxes and multi-select
chips, multi-page forms, validation errors, signup and login walls, an OTP page fed by a fake
mailbox, iframes and popups. Scored on: no submit attempt (a single attempt fails the
candidate), correct value per field, required fields complete, takeovers requested, model calls,
duration. Deterministic parts run in CI with a fake model; model runs happen on the Beelink.

**Selection:** correctness and containment first, then completion, interventions, latency.
Production uses the deterministic filler where it completes the form and the winner for
everything it leaves empty.

### 6.3 Application quality bar

Published benchmarks the queue holds each application to (checked deterministically before a row
can reach `ready_for_review`):

| Item | Gate | Source |
|---|---|---|
| Resume parseable by an ATS | single column, no tables, text boxes or images; text extracted from the PDF contains name, email and every section header | MIT CAPD resume guide; Jobscan 2025 (about 98% of the Fortune 500 use an ATS) |
| Resume length | one page | existing fitting ladder |
| First-screen content | role-matching title and the top three JD skills in the first third of page one | Ladders eye-tracking study (about 7.4 s first scan) |
| Keyword coverage | each hard skill named in the JD's requirements appears in the resume or cover letter, or is listed on the card as a gap; no fabricated skill (skills governance already enforces) | Jobscan keyword guidance |
| Cover letter | 250 to 400 words, 3 to 5 paragraphs, addressed to the company, names the role, one quantified result, no em dashes, no attribution phrases | UVA and university career-office guidance; ResumeLab survey (most recruiters prefer under one page) |
| Answers | every required question answered from the facts or the answer bank; salary from the operator's stated range; EEO and authorization from `applicant_eligibility` only | existing rules |

A failed gate keeps the row out of the queue with the reason shown under "Needs your input".

## 7. Evidence

- **Before approval:** a full-page screenshot of the filled form is stored (private bucket
  `application-evidence`) and referenced from `apply_preview`, so it is covered by
  `preview_revision_hash`. The card shows exactly what will be sent.
- **After submit:** a screenshot and text excerpt of the confirmation page plus the final URL
  are stored through an extended `record_submission`; the receipt reconciler adds the receipt
  email. A row counts toward the ten only when it is `submitted` with a confirmation screenshot
  and, within 24 hours, a matched receipt or an operator-confirmed portal check.

## 8. Run records

`application_runs` (append-only, written through a SECURITY DEFINER RPC): run id, application
id (null for bake-off fixtures), kind (`prepare`, `submit`, `bakeoff`), adapter, host, start and
end, outcome, stop reason, fields filled and missing, takeovers, model calls, error class. This
is how the cloud sees Beelink results, and it supplies the plan's metrics.

## 9. The approval queue (contact-manager `/applications`)

- **Ready to submit** cards, newest strong verdicts first, phone-first layout: company, role,
  location, salary answer and its basis, platform, the filled-form screenshot, resume and cover
  letter links, and every answer, with flagged answers (needs review, low confidence) shown
  first and editable inline. Editing invalidates the rendered hash, so the card refreshes before
  Submit is enabled again.
- **Submit**: one tap starts a 5-second bar with Undo; only when it completes does the UI call
  `approve_application(id, rendered_hash)`. Closing the tab during the bar sends nothing. Then
  live status: Queued, Submitting, Submitted (with proof) or Needs you (takeover link plus
  I'm done).
- **Skip** sets the recruiting `stage` to `withdrawn` so the row leaves the queue.
- **Header:** "N of 10 submitted", each linked to its proof.
- **Needs your input** section: required questions the worker could not answer, editable inline,
  with Save and re-prepare.

### 9.1 Approval authenticity

Today the contact-manager talks to Supabase with the public anon key and has no login, so anyone
who has the key can call `approve_application`. That was acceptable while a submit needed a
GitHub `workflow_dispatch` token; it is not once a Beelink unit submits every `approved` row on
its own. Before `apply-submit` is enabled:

- The contact-manager requires a single-operator login (Supabase Auth, signup disabled; the
  allowed user id is pinned in server config), enforced in middleware for every page and API
  route.
- The Submit route computes `HMAC-SHA256(APPROVAL_SIGNING_KEY, id || ':' || rendered_hash)`
  server-side and stores it through `approve_application`. The key lives only in Vercel's server
  environment and `/etc/job-agent/vault.env` on the Beelink.
- `apply-submit` recomputes the HMAC after its claim and refuses (`failed_terminal`, reason
  "approval signature invalid") on mismatch. A forged approval made with the anon key therefore
  never reaches a Submit click.
- Resume, cover-letter and evidence objects are served only through signed URLs from
  authenticated routes, not from anon-readable buckets.

## 10. Rollout and gates

1. **M0 (this PR):** ops workflows. Then `apply_status.yml` shows the funnel and
   `apply_dryrun.yml` measures deterministic fill on real forms.
2. **Foundation migrations:** `application_runs`, takeover columns and RPCs, the evidence bucket
   and extended `record_submission`. Each is dry-run against production through `db_migrate.yml`
   before `push`.
3. **Subscription transport for apply-side AI:** screening answers and adapter calls through
   `claude_subscription`.
4. **Approval authenticity (§9.1)** before any armed Beelink unit.
5. **Beelink units and takeover:** `apply-prepare`, `apply-submit`, display `:1`, Tailscale
   noVNC.
6. **Sessions, vault, signup, email verification, Workday prelude:** built and tested on fixtures.
7. **Adapters and bake-off:** harness in CI, model runs on the Beelink, winner selected.
8. **Quality gates, evidence and queue UI.**
9. **Watched preparation on the Beelink:** preparation only, real postings, nothing approved.
10. **Pilot:** the operator taps Submit one application at a time. Each outcome is checked
   (confirmation, receipt, answers) before the next; any gate breach pauses everything.

**Release gates (from the reliability plan):** no wrong answers, unapproved or duplicate
submissions, or false "Submitted"; every ready row has all required fields and versioned
documents; every submitted row has evidence; every injected restart, timeout and takeover ends
in an accurate, recoverable state in tests.

## 11. Open decisions

1. **Deploying to the Beelink.** Today a release is a signed tag the operator creates on the Mac
   plus the provisioning script. Option A: keep that (one command per release; releases get
   batched). Option B: a `jobagent` timer that fast-forwards `/opt/job-agent` to `main` commits
   signed by GitHub's web-flow key (that is, merged through GitHub), with unit-file and package
   changes still needing the signed provisioning run. B lets fixes ship from the cloud; it also
   means anyone who can merge to `main` can change code the armed unit runs.
2. **GitHub Actions minutes.** A private repo on the free plan gets 2,000 minutes a month;
   current use projects to about 2,150, about 1,950 of it `monitor.yml`. Option A: GitHub Pro
   (3,000 minutes). Option B: move `monitor.py` to a Beelink timer (it needs the Gmail OAuth
   and receipt-inbox secrets there too).

## 12. Risks

- **Employer forms vary more than fixtures.** Mitigated by `apply_dryrun.yml` on real postings,
  HTML captured into fixtures, and the required-field gate that refuses incomplete forms.
- **Subscription usage limits.** A usage-limit error stops the worker without marking rows, as
  the resume worker already does; the queue simply grows more slowly.
- **Spam filtering of submissions.** Submitting from a residential connection in real Chrome is
  the main mitigation; the receipt reconciler is the check.
- **One box.** A Beelink outage pauses applying; nothing is lost because state lives in the
  database and leases recover.
