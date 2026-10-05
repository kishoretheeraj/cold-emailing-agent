# Lifecycle RPCs, form-drift check, and Gmail-receipt reconciler — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** (a) Remove the anon key's direct write access to the execution-lifecycle columns by moving every lifecycle write behind `SECURITY DEFINER` RPCs with server-enforced transitions; (b) ship strategic-plan item 5's `form_signature` drift check and Gmail-receipt reconciler, plus the evidence UI.

**Architecture:** One additive migration adds `form_signature`, `submit_attempted_at`, `submission_evidence`, revokes anon UPDATE/INSERT on `automation_status`/`worker_lease_id`/`worker_heartbeat_at`, and defines the RPCs below. `db.py`'s lease/lifecycle functions become thin `.rpc()` wrappers. `apply_agent.py` computes a form signature right after page load in both passes. A new `submission_reconciler.py` (called from `monitor.py`, best-effort) matches `needs_confirmation` rows to INBOX receipts.

**Tech Stack:** Postgres/Supabase (plpgsql), Python 3.11 + supabase-py + imaplib, Next.js (contact-manager), pytest / vitest / Playwright.

**Spec:** `docs/superpowers/plans/2026-09-28-application-reliability-plan.md` §5 Phase 3 (form_signature + reconciler amendments) and §8 item 5; `docs/reviews/2026-09-30-application-reliability-validation.md`; the known-limitation sentence in CLAUDE.md's "Execution lifecycle" paragraph (anon-writable lifecycle columns).

Branch: continue on `feat/automation-status-and-leases` (PR #11, unmerged) — this work rewrites that PR's db.py functions, so one merge is simpler than a stacked PR.

## Global Constraints

- `APPLY_AGENT_ARMED` must never be set anywhere except `apply_agent_submit.yml`'s env block (tests: only via `mocker.patch.dict`). Do not touch the ARMED check or its function-local `import os`.
- The three-condition approval gate in `submit()` and the hash/lease conditions stay verbatim, before `_launch_page`.
- No type annotations, `# ── Section ──` banners, no docstrings on `_private` helpers, f-string pipe logs.
- All outbound calls mocked in tests. Never `git add -A`.
- `claim_application` is never wrapped in `_retry` (re-read and compare the lease id instead).
- Blind retries from `needs_confirmation` stay impossible: no RPC may move `needs_confirmation` anywhere except `submitted` (receipt evidence or human) or the human `resolve_confirmation(false)` path.
- The reconciler never raises past `run()`, never retries a submission, and only ever moves `needs_confirmation → submitted`.
- `documents_version`, `apply_preview`, `stage`, `apply_blocked_reason` stay anon-writable (UI and resume builds use them; any change only invalidates approval via the trigger-owned hash — the safe direction).

## Review Focus

1. A form whose field ids are framework-generated (`:r3:`) must not mismatch on every load — signature uses name/label/aria-label/placeholder, never `id`. (Task 3 test.)
2. Rows previewed before this ships have `form_signature = NULL` — submit must skip the check with a warning, not block. (Task 3 test.)
3. Two pending rows at the same company and one receipt — must not auto-resolve either unless the role disambiguates. (Task 4 test.)
4. A receipt older than the submit attempt (an earlier application to the same company) must not match. (Task 4 test.)
5. `recover_stale_leases` called by anon with `p_stale_seconds = 0` must not steal live leases — SQL floors it at 1800. (Task 1 test.)

---

### Task 1: Migration — lifecycle RPCs, grants, new columns

**Files:**
- Create: `supabase/migrations/20261004000000_lifecycle_rpcs_form_signature_receipts.sql`
- Create: `tests/test_lifecycle_rpcs_migration.py` (static assertions, same style as `tests/test_automation_status_migration.py`)

**Produces (RPC contracts used by Tasks 2–5; all `SECURITY DEFINER`, `SET search_path = public, pg_temp`, `REVOKE EXECUTE ... FROM PUBLIC`, `GRANT EXECUTE ... TO anon`):**

| RPC | Returns | Allowed transition (guard) |
|---|---|---|
| `claim_application(p_id BIGINT, p_lease UUID, p_to TEXT)` | BOOLEAN | `preparing`: from `idle`/`failed_retryable`, `stage='saved'`. `submitting`: from `approved`, `stage='ready_to_submit'`, `approved_at IS NOT NULL`, `approved_revision_hash = preview_revision_hash`. Both require `worker_lease_id IS NULL`. Any other `p_to` → `RAISE EXCEPTION`. Sets lease + heartbeat `now()`; the `submitting` claim also sets `submit_attempted_at = NULL` (a prior attempt's value must not leak into this attempt or the reconciler window). |
| `heartbeat_application(p_id, p_lease UUID)` | BOOLEAN | lease matches |
| `renew_submission_lease(p_id, p_lease UUID, p_revision_hash TEXT)` | BOOLEAN | lease matches, `automation_status='submitting'`, `stage='ready_to_submit'`, both hashes `= p_revision_hash`, `approved_at IS NOT NULL`. Sets heartbeat AND `submit_attempted_at = now()` (records submission intent before the click). |
| `complete_preview(p_id, p_lease UUID, p_preview JSONB, p_form_signature TEXT)` | BOOLEAN | lease matches, `automation_status='preparing'` → `ready_for_review`; sets `apply_preview`, `stage='ready_to_submit'`, `form_signature`, clears `apply_blocked_reason`, `approved_at`, `approved_revision_hash`, lease, heartbeat. |
| `release_application(p_id, p_lease UUID, p_to TEXT, p_reason TEXT DEFAULT NULL)` | BOOLEAN | lease matches; from `preparing` → `failed_retryable`/`needs_input`/`unsupported`; from `submitting` → `approved`/`failed_retryable`/`needs_confirmation`/`needs_input`. **From `submitting` with `submit_attempted_at IS NOT NULL` (the click may have happened) the ONLY allowed target is `needs_confirmation`** — the lease id is SELECT-able by anon, so it is a concurrency token, not authorization; without this rule anyone with the public key could release a post-click row to `failed_retryable` and open a duplicate-application path. Disallowed pair → `RAISE EXCEPTION`. Sets `apply_blocked_reason = p_reason`, clears lease. When `p_to = 'needs_input'` also clears `approved_at`/`approved_revision_hash`. |
| `record_submission(p_id, p_lease UUID, p_source_channel TEXT, p_applied_date DATE)` | BOOLEAN | lease matches, `automation_status='submitting'` → `submitted`, `stage='applied'`, sets channel/date, clears reason + lease, `submission_evidence = jsonb_build_object('source','page_confirmation','at',now())`. |
| `mark_application_unsupported(p_id, p_reason TEXT)` | BOOLEAN | `worker_lease_id IS NULL` and status NOT IN (`preparing`,`submitting`,`submitted`,`needs_confirmation`); clears approval. |
| `recover_stale_leases(p_stale_seconds INT)` | INT (rows recovered) | one UPDATE over rows with lease set and `worker_heartbeat_at < now() - make_interval(secs => GREATEST(p_stale_seconds, 1800))`; `submitting` with `submit_attempted_at IS NOT NULL` → `needs_confirmation`; `submitting` with NULL `submit_attempted_at` (renew precedes the click, so no click happened) → `failed_retryable`; anything else → `failed_retryable`; reason `'worker lease expired while <old>; recovered as <new>'`. |
| `record_receipt_evidence(p_id, p_evidence JSONB)` | BOOLEAN | `automation_status='needs_confirmation'`, lease NULL, `p_evidence ? 'message_id'` (else RAISE) → `submitted`, `stage='applied'`, `applied_date = coalesce(applied_date, submit_attempted_at::date, current_date)`, `source_channel = coalesce(source_channel, apply_preview->>'platform')`, `submission_evidence = p_evidence`, clears reason. |
| `requeue_preview(p_id)` | void | `automation_status='needs_input'`, lease NULL → `idle`, `stage='saved'`, clears reason, approval, `form_signature`. Else RAISE. |

All transitions also set `updated_at = now()`. Use `UPDATE ... ; RETURN FOUND;` for BOOLEAN functions (a lost lease is a normal `false`, not an exception).

- [x] **Step 1: Write failing static tests** in `tests/test_lifecycle_rpcs_migration.py`: read the SQL file; assert each function name above appears with `SECURITY DEFINER` and a `REVOKE EXECUTE ... FROM PUBLIC` + `GRANT EXECUTE ... TO anon` line with its exact signature; assert `REVOKE UPDATE (automation_status, worker_lease_id, worker_heartbeat_at` and `REVOKE INSERT (automation_status, worker_lease_id, worker_heartbeat_at` from anon; assert `GREATEST(p_stale_seconds, 1800)`; assert `release_application`'s body references `submit_attempted_at IS NOT NULL` and `claim_application` sets `submit_attempted_at = NULL`; assert the new columns `form_signature TEXT`, `submit_attempted_at TIMESTAMPTZ`, `submission_evidence JSONB` via `ADD COLUMN IF NOT EXISTS`; assert no RPC body contains an UPDATE that moves `needs_confirmation` to anything but `submitted` (regex: in `release_application`, `needs_confirmation` appears only as a target, never in a `WHERE automation_status = 'needs_confirmation'` guard).
- [x] **Step 2: Run** `python3 -m pytest tests/test_lifecycle_rpcs_migration.py -q` → FAIL (file missing).
- [x] **Step 3: Write the migration.** Header comment explains: closes the anon-writable lifecycle limitation from 20261001; columns still SELECT-able by anon. The column-grant revokes are valid because 20260925000000 replaced table-level grants with column grants. Add `COMMENT ON COLUMN` for the three new columns.
- [x] **Step 4: Run tests** → PASS. Full suite `python3 -m pytest -q` still green (old db.py untouched in this task).
- [x] **Step 5: Commit** `feat(db): lifecycle RPCs with server-enforced transitions; revoke anon lifecycle writes`.

(Controller, not the subagent: dry-run on prod inside `BEGIN; … ROLLBACK;` with a functional script committed at `supabase/tests/lifecycle_rpcs_dryrun.sql` — insert a temp row, exercise every RPC including disallowed transitions, post-click release to `failed_retryable` raising, and `SET LOCAL ROLE anon` / `SET LOCAL ROLE authenticated` direct `UPDATE ... SET automation_status` failing with permission denied (revoke from `authenticated` too if it can write). **`supabase db push` only after Task 2 is pushed to the branch** — otherwise a merge of PR #11 in between would run direct lifecycle `.update()`s against revoked grants.)

### Task 2: db.py wrappers + apply_agent.py rewiring + form signature

**Files:** Modify `db.py`, `apply_agent.py`, `config.py` (none expected), `tests/test_application_leases_db.py`, `tests/test_apply_agent.py`.

**Interfaces (Produces):**
- `db.claim_application(application_id, to_status) -> lease_id | None` — local `uuid4`, calls RPC once (no `_retry`); on exception or `False`/empty data, re-read `worker_lease_id` and compare.
- `db.heartbeat_application(application_id, lease_id)` — best-effort, never raises.
- `db.renew_submission_lease(application_id, lease_id, revision_hash) -> bool` — `False` without calling when hash falsy; `_retry`; errors propagate.
- `db.complete_preview(application_id, lease_id, preview, form_signature) -> bool`
- `db.release_application(application_id, lease_id, to_status, reason=None) -> bool` (`fields` dict param removed)
- `db.record_submission(application_id, lease_id, source_channel, applied_date) -> bool`
- `db.mark_unsupported(application_id, reason) -> bool` (replaces `set_automation_status`; delete the old function)
- `db.recover_stale_leases(stale_after_seconds) -> int`
- `apply_agent._form_signature(page) -> str | None`
- `apply_agent.FormChangedError(Exception)`

Helper: `def _rpc(name, params): return get_client().rpc(name, params).execute().data` — wrappers coerce with `bool(...)`/`int(... or 0)`.

`_form_signature(page)`: first `page.wait_for_selector("input, select, textarea", timeout=15000)` (`_launch_page` only waits for the load event; Ashby is an SPA), then `page.evaluate(JS)` returning a list of identifiers for every `input, select, textarea` that is not `type` in (`hidden`,`submit`,`button`,`reset`,`image`) and not `disabled`, skipping names matching `/captcha/i` (`g-recaptcha-response` appears on Ashby); identifier = first non-empty of `el.name`, `el.getAttribute('aria-label')`, `el.labels?.[0]?.innerText`, `el.placeholder` — **never `el.id`** — prefixed with `el.type || el.tagName.toLowerCase()` + `:`; Python side lowercases, collapses whitespace, dedupes, sorts, returns `sha256(json.dumps(list)).hexdigest()`. Empty list or any exception → `None` (log warning).

Preview (`_process_one_preview`): workday/aggregator → `db.mark_unsupported`. Claim with `db.claim_application(job_id, "preparing")`. Immediately after `_launch_page`, `signature = _form_signature(page)` (before any fill). Success → `db.complete_preview(job_id, lease, preview, signature)`; `False` → `"lost"`. Error → `db.release_application(job_id, lease, "failed_retryable", f"preview pass error: {exc}")`.

Submit: excluded platform → `db.mark_unsupported`. Claim `db.claim_application(job_id, "submitting")`. Right after `_launch_page`, before filling:
```python
expected = job.get("form_signature")
if not expected:
    log.warning(f"[APPLY-SUBMIT] | {job.get('company')} | no preview form_signature, drift check skipped")
elif _form_signature(page) != expected:
    raise FormChangedError("Form changed after approval")
```
Unarmed → `db.release_application(job_id, lease, "approved")`. Success → `db.record_submission(job_id, lease, platform, date.today().isoformat())` (falsy → existing warning). Except: `to_status = "needs_confirmation" if clicked else ("needs_input" if isinstance(exc, FormChangedError) else "failed_retryable")`, `db.release_application(job_id, lease, to_status, str(exc))`.

- [x] **Step 1: Rewrite tests first** in `tests/test_application_leases_db.py`: each wrapper calls `client.rpc` with the exact name and params (`p_id`, `p_lease`, `p_to`, `p_reason`, `p_preview`, `p_form_signature`, `p_source_channel`, `p_applied_date`, `p_revision_hash`, `p_stale_seconds`); `claim_application` returns lease on `True`, re-reads and returns lease when the RPC raises but the row holds our lease, returns `None` otherwise, and is not retried (rpc called exactly once); `heartbeat` swallows exceptions; `renew_submission_lease` returns `False` without calling when hash is `None`; no lifecycle wrapper calls `.table(...).update(` (assert `table().update` never called). In `tests/test_apply_agent.py`: update mocks to new names/signatures; add `_form_signature` tests with a fake page whose `evaluate` returns lists — order-independence, `id` ignored (two loads differing only in generated ids produce equal hashes — the JS never reads id, so test the Python normalization: same identifiers different order/case/whitespace → same hash), empty → `None`, exception → `None`; submit tests: stored signature mismatch → `release_application(..., "needs_input", "Form changed after approval")` and no click; stored `None` → proceeds with warning; match → proceeds. Preview test: `complete_preview` receives the signature computed before the filler runs (assert call order with a `mocker.Mock()` parent).
- [x] **Step 2: Run** targeted tests → FAIL.
- [x] **Step 3: Implement** in db.py and apply_agent.py. Remove `set_automation_status` and the `fields` param. Grep the repo for old names/signatures and fix every caller (`monitor.py` calls `recover_stale_leases` — unchanged signature).
- [x] **Step 4:** `python3 -m pytest -q` → all green.
- [x] **Step 5: Mutation check** (report results, revert each): (a) make `_form_signature` include `el.id` → a test fails; (b) swap `needs_input`/`failed_retryable` in the except → a test fails; (c) wrap `claim_application`'s RPC call in `_retry` → a test fails.
- [x] **Step 6: Commit** `refactor(apply): route lifecycle writes through RPCs; add form_signature drift check`.

### Task 3: Gmail-receipt reconciler

**Files:** Modify `gmail.py`, `db.py`, `monitor.py`, `config.py`, `.github/workflows/monitor.yml`, `tests/test_agent_paused.py` (if monitor ordering asserted). Create `submission_reconciler.py`, `tests/test_submission_reconciler.py`, `tests/test_gmail_inbox_receipts.py`.

**Interfaces:**
- **Receipt mailbox is NOT `GMAIL_ADDRESS`.** Forms are filled with `kishoretheerajvj@gmail.com` (user decision 2026-10-04) (`apply_agent._standard_field_values`), while `GMAIL_ADDRESS` is the outreach mailbox (different account). New soft-optional `config.RECEIPT_IMAP_ADDRESS` / `config.RECEIPT_IMAP_APP_PASSWORD` (`os.environ.get`, like `JOBRIGHT_*`). Unset → `run()` logs one `[RECONCILE] | SKIP | receipt mailbox not configured` line and returns without IMAP **and without escalation** (the UI's needs_confirmation copy already tells the human to check). `monitor.yml` passes both from secrets.
- `gmail.fetch_inbox_since(since_date, address, password) -> list[dict]` — opens its own `IMAP4_SSL`, `SELECT "[Gmail]/All Mail"` (readonly — Gmail filters may archive ATS mail out of INBOX), `SEARCH SINCE dd-Mon-YYYY NOT FROM <address>`, `SEARCH SINCE dd-Mon-YYYY`, fetch `BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES)]` per message; returns `{"num", "message_id", "from", "subject", "date", "is_reply"}` (`is_reply` = has In-Reply-To/References or subject starts `re:`) (date as aware UTC datetime via `email.utils.parsedate_to_datetime`; unparseable → skip message). Returns the open connection's results and logs out; body fetches happen inside the same call via an optional `want_body(msg) -> bool` callback that adds `msg["body"]` (`BODY.PEEK[TEXT]`, first 4000 chars) only for messages it approves. Never marks messages read (PEEK).
- `db.get_applications_needing_confirmation() -> list` — select `id,company,role,apply_blocked_reason,submit_attempted_at,approved_at,updated_at,apply_preview` where `automation_status='needs_confirmation'` and `worker_lease_id is null`.
- `db.record_receipt_evidence(application_id, evidence) -> bool` (RPC).
- `submission_reconciler.match_receipt(app, messages, now) -> dict | None` (pure; messages may carry `body`)
- `submission_reconciler.run(now=None) -> dict` (`{"checked", "resolved", "escalated", "errors"}`), never raises.

Matching rules (`match_receipt`):
0. Skip any message with `is_reply` (ATS receipts are never replies; this inbox has real human threads with these companies).
1. Window start = first non-null of `submit_attempted_at`, `approved_at`, `updated_at` (parse ISO → aware UTC). Only messages with `window_start - 2 minutes <= date <= window_start + 72 hours`; rows whose window started more than 72h ago are not searched at all (bounded SINCE, no unbounded refetch on a stuck row).
2. Company match: normalize company (lowercase, strip punctuation, drop trailing tokens in `{inc, llc, ltd, corp, corporation, co, company, technologies, labs}`); match if the normalized name appears as a whole-word phrase in the From display name or the Subject, or its space-stripped form appears in the From address domain.
3. Receipt phrase (case-insensitive, on subject + body; bodies are fetched only for company-matched, non-reply messages). "thank you for your interest" alone is deliberately NOT a trigger (rejections and human mail use it). Patterns are uncalibrated against real receipts (none reachable from this session) — keep them in one module-level constant: `\bthank(s| you) for (applying|your application|submitting)\b|\b(application|submission) (has been |was )?(received|submitted)\b|\bwe(?:'ve| have)? received your application\b|\byour application (to|for|at)\b`.
4. Return evidence `{"source": "gmail_receipt", "message_id", "from", "subject", "date": iso}` for the earliest qualifying message, else `None`.

`run()`:
- rows empty → return without opening IMAP.
- One `fetch_inbox_since(min window_start date, ...)` call per run, `want_body` = company-matches-any-pending-row and not `is_reply`.
- For each row compute candidates; build `message_id → [row ids]` across rows. A message matching >1 row is used only for the row whose normalized `role` appears in its subject (exactly one such row); otherwise skipped for all. A message is consumed by at most one row.
- Match → `db.record_receipt_evidence`; log `[RECONCILE] | {company} | resolved | {message_id}`.
- No match and `now - window_start > 15 min` and reason does not start with `"No receipt email"` → `db.set_apply_blocked(id, f"No receipt email found 15+ min after submit -- check the employer portal. Original: {reason or 'unknown'}")`; log escalated. Never touches `automation_status` on escalation; rows keep being checked on later runs (late receipts still resolve).
- Per-row try/except; IMAP failure → log warning, return counts with errors+1. Never raises.

Monitor: after the existing best-effort `recover_stale_leases`, call `submission_reconciler.run()` inside its own `try/except` (warning on failure). Import `submission_reconciler` after `basicConfig` (logging-order invariant).

- [x] **Step 1: Failing tests:** `match_receipt` — reply-shaped message (`is_reply`) with receipt wording → no; message 73h after window → no; "Thank you for your interest in Acme" alone → no; Greenhouse-style (`"Acme Recruiting" <no-reply@us.greenhouse-mail.io>`, subject "Thank you for applying to Acme") matches; Ashby domain-style (`no-reply@acme.ashbyhq.com`? use from `"Acme" <no-reply@ashbyhq.com>`) matches; Lever domain (`no-reply@hire.lever.co` with display "Acme Corp") matches after suffix strip; message before window → no; company match but no receipt phrase (newsletter) → no; receipt phrase but other company → no; `body_fn` not called for non-company messages; "Acme" must not match "Acmeville Bank" (whole word). `run()` — receipt mailbox unset → no IMAP, no escalation, returns; no rows → `fetch_inbox_since` not called; two rows same company + one receipt whose subject names one role → only that row resolved; same without role → neither; escalation after 16 min sets reason once (second run with prefixed reason → no second `set_apply_blocked`); before 15 min → no escalation; `record_receipt_evidence` raising for one row doesn't stop the next; IMAP open failure → returns errors=1, no raise. `fetch_inbox_since` with mocked `imaplib.IMAP4_SSL`: uses PEEK, readonly select of All Mail, `NOT FROM` own address, `want_body` gating, parses date, skips unparseable. Monitor: reconciler called after recovery; reconciler raising doesn't fail monitor.
- [x] **Step 2:** run → FAIL.
- [x] **Step 3:** implement (log file: reconciler logs through monitor's root logger; marker `[RECONCILE]` — add to CLAUDE.md marker list in Task 5).
- [x] **Step 4:** `python3 -m pytest -q` green.
- [x] **Step 5: Mutation check:** drop the window filter → a test fails; drop the ambiguity rule → a test fails.
- [x] **Step 6: Commit** `feat(reconcile): resolve needs_confirmation rows from Gmail application receipts`.

### Task 4: contact-manager — requeue route, needs_input / evidence UI

**Files:** Create `contact-manager/src/app/api/applications/[id]/requeue-preview/route.ts` + `route.test.ts` (mirror `resolve-confirmation`). Modify `contact-manager/src/lib/types.ts` (`form_signature?`, `submit_attempted_at?`, `submission_evidence?: { source: string; message_id?: string; from?: string; subject?: string; date?: string; at?: string } | null`), `ApplicationsPage.tsx`, its unit test, and `tests/e2e/18-applications*.spec.ts` fixtures.

- Route: POST, numeric id check (400), `supabase.rpc("requeue_preview", { p_id })`, error → 409 with message, success → 200 `{ ok: true }`.
- Action cell: new `automationStatus === "needs_input"` branch → amber reason text (`blockedReasons[app.id] ?? app.apply_blocked_reason ?? "Needs your input"`) + "Re-prepare" button → POST requeue-preview, then clear `liveStatuses[app.id]`/`blockedReasons[app.id]` and refetch (same pattern as `handleTryAgain`). Note the cell is wrapped by `app.stage === "ready_to_submit" && app.apply_preview` — `needs_input` rows keep `stage='ready_to_submit'`, so it renders.
- `needs_confirmation` branch: also show the reason line (`blockedReasons ?? apply_blocked_reason`) above the buttons so the reconciler's "No receipt email found…" escalation is visible.
- Detail sheet: when `submission_evidence` present, a "Submission evidence" block: source label (`gmail_receipt` → "Confirmation email", `page_confirmation` → "Confirmation page", else raw), and subject / from / date / message id when present (plain text, no HTML rendering).
- Grep `contact-manager/src` for any direct write of `automation_status`, `worker_lease_id`, `worker_heartbeat_at` in an `.update(`/`.insert(` — there must be none (the PATCH route only *filters* on `worker_lease_id`). If found, route it through an RPC.

- [x] **Step 1: Failing tests:** route tests (400 non-numeric, 409 on rpc error, 200 calls `requeue_preview` with `p_id` number); ApplicationsPage tests: `needs_input` row shows reason + Re-prepare and clicking POSTs `/api/applications/<id>/requeue-preview`; `needs_confirmation` shows reason; evidence block renders subject for `gmail_receipt`.
- [x] **Step 2:** `npx vitest run` → FAIL. **Step 3:** implement. **Step 4:** `npx vitest run`, `npx tsc --noEmit`, `npm run lint`, and e2e `18-applications` (run the worktree's own dev server on port 3100 — port 3000 may be the main checkout's; `node_modules` is an APFS clone, not a symlink).
- [x] **Step 5: Commit** `feat(applications): re-prepare for changed forms; show submission evidence`.

### Task 5: Docs, deploy, verify (controller)

- [x] Prod dry-run of the migration (BEGIN … functional checks … ROLLBACK), then `supabase db push`; verify `has_column_privilege('anon','job_applications','automation_status','UPDATE')` is false and the RPCs exist.
- [x] CLAUDE.md: replace the "known limitation" sentence in the Execution-lifecycle paragraph with the RPC model; add form_signature + reconciler paragraph; add `[RECONCILE]` marker; add new test files to the Tests list. `docs/python/db-schema.md`: new columns + RPC table. Strategic plan §8 item 5 marked shipped (Gmail OTP / takeover still item 3).
- [x] Memory: update `project-application-reliability-plan.md` + MEMORY.md line.
- [x] Full suites green; commit; push; update PR #11 description.
- [ ] After user merges PR #11: Vercel deploy check + browser check of `/applications` (no Approve click). <!-- blocked: needs user merge of PR #11 -->
