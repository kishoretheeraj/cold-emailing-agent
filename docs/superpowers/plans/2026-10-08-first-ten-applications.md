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

- [ ] Migration: `application_runs` table (spec §8), anon/authenticated SELECT only, inserts via
      `log_application_run(...)` SECURITY DEFINER with argument validation.
- [ ] Migration: `job_applications.takeover JSONB` written only by `request_takeover(id, lease,
      reason)` (lease-checked) and `takeover_continue(id)` (UI; sets `continue_at` only when a
      takeover is open) and cleared by `clear_takeover(id, lease)`.
- [ ] Migration: private bucket `application-evidence`; `record_submission` gains
      `p_evidence JSONB` (new signature, old one dropped, same transition guards).
- [ ] `db.py` wrappers + tests (exact RPC names and params, never `table().update`).

## Phase C: apply-side AI on the subscription

- [ ] `config.APPLY_CLAUDE_BACKEND` (`api` default, `subscription` on the Beelink units).
- [ ] `apply_agent._generate_screening_answers` dispatches through `claude_subscription.complete`
      on the subscription backend; usage logged with `billing='subscription'`, cost 0. Tests mirror
      `tests/test_claude_subscription.py` (subprocess mocked).
- [ ] A usage-limit error stops the prepare loop without marking the row (resume-worker pattern).

## Phase D: Beelink units and takeover

- [ ] `apply_worker.py` with `prepare_loop()` and `submit_loop()` (pure scheduling logic tested
      with fakes: pause scope, priority, display lock, idle sleep, error isolation).
- [ ] Browser launch: real Chrome (`channel="chrome"`), headful on `DISPLAY=:1`, ATS profile
      separate from LinkedIn's; submit-blocking init script on every page (fixture test: the
      form's submit event never fires while it is installed).
- [ ] Takeover protocol (`request_takeover` -> wait with heartbeats -> `continue_at` -> recheck);
      timeouts map to `needs_input` / `failed_retryable` / `needs_confirmation` exactly as spec §4.2.
      Tests inject each timeout and each continue.
- [ ] Units: `apply-prepare.service`, `apply-submit.service` (ARMED only here), `xvfb@1`,
      `x11vnc@1` with `-rfbauth`, `novnc-tailnet@1` bound to the Tailscale address. Extend
      `tests/test_beelink_units.py`: ARMED appears in exactly one unit; LinkedIn units unchanged.
- [ ] Move the ARMED rule: CLAUDE.md, `apply_agent.submit` docstring, tests that pin the
      workflow; disable `apply_agent_submit.yml` and the preview schedule once Beelink submits.
- [ ] Provisioning script + RUNBOOK: Tailscale-bound noVNC with a VNC password, `vault.env`,
      the two units. Operator deploys and runs the watched checks in RUNBOOK.

## Phase E: sessions, vault, signup, email verification

- [ ] `ats_sessions.py`: `tenant_key(url)`, load/save `storage_state` (0700 dir). Table-driven
      URL tests.
- [ ] `credential_vault.py`: Fernet-encrypted file, generated passwords (length and classes
      tested), never logged (test captures logs and asserts absence).
- [ ] `email_verification.py`: `wait_for_code(sender_domains, since, timeout)` over the receipt
      inbox; tests for sender scoping, time window, code vs link extraction, allowed link hosts,
      timeout.
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

## Phase G: evidence and the approval queue

- [ ] Preview screenshot stored and referenced in `apply_preview` (hash covers it).
- [ ] Confirmation screenshot, text and URL through `record_submission(p_evidence)`.
- [ ] Queue UI (spec §9): cards, inline edit, Submit with 5 s undo, Skip, live status, takeover
      card, proof links, "N of 10". vitest for every state; Playwright e2e with screenshots
      checked by eye.

## Phase H: watched preparation, then the pilot

- [ ] Beelink prepares real postings (nothing approved); review cards and `application_runs`.
- [ ] Pilot: operator submits one at a time; after each, confirm evidence and receipt before
      the next. Ten confirmed submissions closes the plan.
