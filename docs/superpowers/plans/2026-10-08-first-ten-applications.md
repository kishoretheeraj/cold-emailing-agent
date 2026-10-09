# First ten applications: implementation plan

**Spec:** [2026-10-08-first-ten-applications-design.md](../specs/2026-10-08-first-ten-applications-design.md)
**Method:** test first for every change; each phase ships as its own PR, reviewed by Codex
(`@codex review`) and merged only with the full suite green. Migrations are dry-run against
production through `db_migrate.yml` (`dryrun` mode, schema fingerprint unchanged) before `push`.
Beelink changes are deployed by the operator (spec §11.1) and verified with a watched run.

Checkboxes are the progress record.

## Phase A: ops visibility (PR: this branch)

- [x] `scripts/sql_guard.py` + tests: compose BEGIN/migration/test/ROLLBACK; refuse transaction
      control, non-transactional statements, unsafe filenames (`fullmatch`, so `x.sql\n` fails).
- [x] `scripts/apply_status.py` + tests: counts, supported-ATS funnel, eligibility key names;
      per-row detail only when `REPO_PRIVATE == "true"`; eligibility values never.
- [x] `scripts/apply_dryrun.py` + tests: read-only probe; no DB writes, no Claude, no
      browser-use, no clicks; refuses when `APPLY_AGENT_ARMED` is present; error text and HTML
      hidden unless private.
- [x] Workflows `apply_status.yml`, `apply_dryrun.yml`, `db_migrate.yml` + static tests
      (dispatch-only, `contents: read`, no `${{ }}` in `run:`, never armed, fail-closed
      visibility check, push needs `confirm=push`, guard before any query).
- [x] `apply_agent_preview.yml` prints its log only when private.
- [x] Test hygiene: autouse `_no_real_dns` fixture (an unmocked MX lookup made
      `test_run_prepares_multiple_contacts` pass or fail by network).
- [ ] Operator: make the repo private; add `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD`.
- [ ] Run `db_migrate.yml mode=list`, then dry-run and push pending migrations
      (`20261006000000`; `20261007000000` after PR #16).
- [ ] Run `apply_status.yml`; record the funnel in this plan.
- [ ] Run `apply_dryrun.yml` on every ready/strong supported row with `dump_html`; save the
      HTML as fixtures (Phase F) and list the fill gaps.

## Phase B: foundation schema

Tests first: static SQL tests in the style of `tests/test_lifecycle_rpcs_migration.py`, and a
`supabase/tests/*_dryrun.sql` functional test run through `db_migrate.yml`.

- [x] Migration: `application_runs` table (spec §8), anon/authenticated SELECT only, inserts via
      `log_application_run(...)` SECURITY DEFINER with argument validation.
- [x] Migration: `job_applications.takeover JSONB` written only by `request_takeover(id, lease,
      reason)` (lease-checked) and `takeover_continue(id)` (UI; sets `continue_at` only when a
      takeover is open) and cleared by `clear_takeover(id, lease)`.
- [x] Migration: private bucket `application-evidence`; `record_submission` gains
      `p_evidence JSONB` (new signature, old one dropped, same transition guards).
- [x] `db.py` wrappers + tests (exact RPC names and params, never `table().update`).
      Functional dry run passed against a local Postgres 16 built from `setup_supabase.sql` + every
      migration (Supabase roles/storage stubbed); 11 mutations of the migration all caught.
- [ ] Operator: `db_migrate.yml mode=dryrun migration=20261008000000_application_runs_takeover_evidence.sql
      test=application_runs_takeover_dryrun.sql`, then `mode=push confirm=push`.

## Phase C: apply-side AI on the subscription

- [x] `config.APPLY_CLAUDE_BACKEND` (`api` default, `subscription` on the Beelink units).
- [x] `apply_agent._generate_screening_answers` dispatches through `claude_subscription.complete`
      on the subscription backend; usage logged with `billing='subscription'`, cost 0. Tests mirror
      `tests/test_claude_subscription.py` (subprocess mocked).
- [x] A usage-limit error stops the prepare loop (resume-worker pattern); the row is released
      `failed_retryable` with a "usage limit" reason, so the next run picks it up again.

## Phase C2: approval authenticity (spec §9.1) -- before any armed Beelink unit

- [x] contact-manager: single-operator login (`OPERATOR_PASSWORD` + HMAC session cookie, not Supabase
      Auth: signup is open on the project), `src/proxy.ts` on every page and API route; vitest + e2e.
- [x] Migration `20261008000001`: `approval_signature`/`approval_signed_at_ms` (RPC-only),
      `approve_application(p_id, p_revision_hash, p_signature, p_signed_at_ms)`; 2-arg dropped.
      Static + dry-run tests (local Postgres; 6 mutations caught).
- [x] Submit route computes the HMAC server-side (`APPROVAL_SIGNING_KEY`, server env only).
- [x] `apply_agent.submit()` verifies the HMAC after the claim; mismatch -> `needs_input` (clears the
      approval) before any browser launch; missing key refuses before the claim.
- [ ] Resumes/evidence served only via signed URLs from authenticated routes (the files route is
      covered by the proxy once login is configured; the `resumes` bucket's anon read policy is still open).
- [ ] Operator: set `OPERATOR_PASSWORD`, `SESSION_SECRET`, `APPROVAL_SIGNING_KEY` in Vercel and the
      `APPROVAL_SIGNING_KEY` GitHub secret; then dry-run + push `20261008000001` (after the route deploys).

## Phase D: Beelink units and takeover

- [x] `apply_worker.py` with `run_prepare()` and `run_submit()` (oneshots on timers, not loops) (pure scheduling logic tested
      with fakes: pause scope, priority, display lock, idle sleep, error isolation).
- [x] Browser launch: real Chrome (`channel="chrome"`), headful on `DISPLAY=:1`, separate from LinkedIn's
      display and profile.
- [ ] Submit-blocking init script on every prepare page (moved to Phase F with the model adapters,
      the only fillers that could click something unexpected).
- [x] Takeover protocol (`request_takeover` -> wait with heartbeats -> `continue_at` -> recheck);
      timeouts map to `needs_input` / `failed_retryable` / `needs_confirmation` exactly as spec §4.2.
      Tests inject each timeout and each continue.
- [x] Units: `apply-prepare.service`, `apply-submit.service` (ARMED only here), `xvfb@1`,
      `x11vnc@1` with `-rfbauth`, `novnc@1` on 127.0.0.1 published to the tailnet by `tailscale serve`. Extend
      `tests/test_beelink_units.py`: ARMED appears in exactly one unit; LinkedIn units unchanged.
- [x] Move the ARMED rule: CLAUDE.md + `tests/test_armed_rule.py` (exactly two setters).
- [ ] Disable `apply_agent_submit.yml` and the preview schedule once the Beelink has submitted once
      (`APPLY_SUBMIT_HOST=beelink` already stops the dispatch).
- [x] Provisioning script + RUNBOOK section 10: `approval.env`, display :1, `tailscale serve`, the units.
- [ ] Operator: deploy a new signed tag and run the watched checks in RUNBOOK section 10.

## Phase E: sessions, vault, signup, email verification

- [x] `ats_sessions.py`: `tenant_key(url)`, load/save `storage_state` (0700 dir). Table-driven
      URL tests.
- [x] `credential_vault.py`: Fernet-encrypted file, generated passwords (length and classes
      tested), never logged (test captures logs and asserts absence).
- [x] `email_verification.py`: `wait_for_code(sender_domains, since, timeout)` over the receipt
      inbox; tests for sender scoping, time window, code vs link extraction, allowed link hosts,
      timeout.
- [x] `workday_adapter.py` prelude (spec §6.1): entry (Apply -> Apply Manually only), sign-in,
      signup with vault-write-before-typing, `click_filter` overlay, verification code, wizard
      advance only while the step bar shows a later step. Fixture pages for each branch.
- [x] Wire Workday into `apply_agent` (prepare: reach the wizard, fill each step, stop at Review;
      submit: replay, then press Submit on Review only) behind `APPLY_WORKDAY_ENABLED` (Beelink units
      only), with listbox support in the inventory and filler. Real-browser tests on the fixture tenant.
- [ ] Capture one real Workday tenant's pages read-only (`apply_dryrun.yml` cannot reach Workday's
      auth; do it on the Beelink with a watched prepare) and turn them into fixtures.
- [ ] `ats_auth.py`: auth-wall detection, login with vault entry, automatic signup, verification,
      takeover escalation; never reset, never a second account. Fixture pages for each path.

## Phase F: adapters and bake-off

- [ ] `ats_adapter.py` contract + `DeterministicAdapter` wrapping today's fillers.
- [ ] `ats_agent.py` constrained adapter: closed action schema, executor validation (rejects
      unknown fields, submit-like controls, untraceable values), round limit. Tests with a fake
      model returning hostile actions.
- [ ] `mcp_adapter.py`: `claude -p` + Playwright MCP over CDP with a tool allowlist; argv/env
      tests (subprocess mocked).
- [ ] Fixture suite (captured real forms + synthetic cases) served locally; scorer; harness CLI.
      Deterministic adapter scored in CI; model adapters scored on the Beelink, results to
      `application_runs`.
- [ ] Select the production composition; record the numbers in this plan.

## Phase F2: quality gates (spec §6.2, §6.3)

- [x] Liveness check at prepare (closed posting -> `unsupported`, "Posting closed: ..."; a separate
      sweep of saved rows is not needed while every row is checked before filling).
- [x] Knock-out pre-scan (sponsorship, citizenship, permanent residency, clearance; only when the
      operator needs sponsorship) -> `unsupported` before any download or browser. Years/degree/
      location are left to the fit judge.
- [x] `application_quality.py`: resume PDF text checks, cover-letter word/role/company checks (a
      paragraph count is not reliable from extracted PDF text, so it is not gated), JD keyword
      coverage report; failures block `ready_for_review` with the reason.
- [x] Answer bank: approved previews' short factual answers, by normalized question, reused before any
      model call (never company-specific or long answers).

## Phase G: evidence and the approval queue

- [ ] Preview screenshot stored and referenced in `apply_preview` (hash covers it).
- [x] Confirmation screenshot, text and URL through `record_submission(p_evidence)` (falls back to
      recording without evidence on a database that lacks the migration).
- [ ] Decide how the operator views evidence screenshots (the bucket is insert-only for anon by
      design; options: an authenticated read via Supabase Auth, or a server route with a scoped key).
- [x] Queue UI (spec §9): cards, Submit with 5 s undo, Skip, live status, takeover card, proof
      links, "N of 10". vitest for every state; Playwright e2e (phone and desktop) with screenshots
      checked by eye; the page no longer scrolls sideways on a phone.
- [ ] Inline edit of an answer on the card (today: edit through the detail sheet, then Prepare again).

## Phase H: watched preparation, then the pilot

- [ ] Beelink prepares real postings (nothing approved); review cards and `application_runs`.
- [ ] Pilot: operator submits one at a time; after each, confirm evidence and receipt before
      the next. Ten confirmed submissions closes the plan.
