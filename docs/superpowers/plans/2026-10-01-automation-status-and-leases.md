# Automation Status, Revision-Bound Approval, and Worker Leases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `job_applications` an execution lifecycle (`automation_status`) separate from the recruiting `stage`. Bind approval to an immutable preview revision hash. Add atomic worker leases with stale-lease recovery. Make a crash or ambiguity after the Submit click land in `needs_confirmation`, which can never be blindly retried, instead of today's "Try again" path that can file a duplicate real application.

**Architecture:** One additive Postgres migration does several things: adds the columns, a `BEFORE INSERT OR UPDATE` trigger that computes `preview_revision_hash` server-side (the only writer, so no role can forge it), rewritten `SECURITY DEFINER` RPCs (`approve_application(id, hash)`, `reset_approval`, new `resolve_confirmation`), and explicit anon column grants. `db.py` gains lease accessors. `apply_agent.py`'s preview and submit passes claim, heartbeat, and release through them. The contact-manager sends the hash it rendered when approving and gains a "did it go through?" resolution path.

**Tech Stack:** Python 3.11 + supabase-py + pytest/pytest-mock; Postgres (Supabase) plpgsql; Next.js App Router + supabase-js + vitest; Playwright e2e.

**Spec:** [docs/superpowers/plans/2026-09-28-application-reliability-plan.md](2026-09-28-application-reliability-plan.md) §4 and §8 item 2, as amended by [docs/reviews/2026-09-30-application-reliability-validation.md](../../reviews/2026-09-30-application-reliability-validation.md) §3 and §5. This plan implements **packaging item 2 only**. Items 3–6 (domain session storage + Gmail OTP, the adapter benchmark, form-drift and receipt reconciler, pilot and headless deploy) need the live Beelink and real ATS tenants. They are out of scope here.

## Global Constraints

- Root `CLAUDE.md` code style applies: no type annotations, `# ── Section ──` banners, no docstrings on `_private` helpers, pipe-separated f-string log lines.
- **`APPLY_AGENT_ARMED` rule is unchanged.** It is never set anywhere except `apply_agent_submit.yml`, plus transiently in tests via `mocker.patch.dict`.
- `submit()`'s existing three approval conditions (`stage == 'ready_to_submit'`, truthy `apply_preview`, truthy `approved_at`) stay **verbatim**. This plan only *adds* conditions: `automation_status` and hash equality. Never remove or weaken an existing check.
- `job_applications.stage` is **not renamed**. It keeps being dual-written: `set ready_to_submit` on preview and `applied` on submit, exactly as today. `automation_status` is the authority for execution gating. `stage` remains the recruiting pipeline and the UI's stage filter.
- Every test mocks Supabase/Playwright/Claude. Tests never travel. Python tests run with `/Users/kishoretheeraj/Documents/cold-email-agent/.venv/bin/python -m pytest` from the worktree root.
- Contact-manager tests: `cd contact-manager && npx vitest run <path>`; e2e: `npx playwright test tests/e2e/18-applications.spec.ts`.
- Never `git add -A` / `git add .`. Stage the exact files each task lists.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01BEGRpnGvSYrJPESikJdEB1
  ```

## State vocabulary (resolves a naming conflict in the strategic plan)

The strategic plan's §4 lists `queued / waiting_for_user / submission_unknown / unsupported`. Its own amended Phase 1/3 text and the validation review use `idle / needs_input / needs_confirmation`. **This plan adopts the review's names, plus `unsupported`.** The canonical set is:

| status | meaning | mapped from strategic §4 |
|---|---|---|
| `idle` | not being worked; eligible for preview | `queued` |
| `preparing` | a preview worker holds the lease | — |
| `needs_input` | blocked on a human (CAPTCHA, missing fact). Reserved; no writer in this plan | `waiting_for_user` |
| `ready_for_review` | preview written, awaiting approval | — |
| `approved` | approval RPC bound it to `approved_revision_hash` | — |
| `submitting` | submit worker holds the lease | — |
| `submitted` | confirmed on-page and recorded | — |
| `needs_confirmation` | Submit may have been clicked, outcome unknown. **No automatic retry, ever** | `submission_unknown` |
| `failed_retryable` | failed **before** any Submit click | — |
| `failed_terminal` | cannot proceed (expired job). Reserved; no writer in this plan | — |
| `unsupported` | Workday / aggregator link | `unsupported` |

Task 6 edits the strategic plan so it uses these names.

## Review Focus

1. **Duplicate application after an ambiguous click.** Any exception raised from `.click()` onward (including inside `.click()` itself, and a failed `record_submission` after a confirmed submit) must land in `needs_confirmation`. `reset_approval` must refuse it. Owned by Task 2 (RPC guard) and Task 4 (`clicked` flag tests).
2. **New columns silently unwritable by anon in prod.** Migration `20260925000000` granted column privileges once, at migration time. Its comment claiming "a new column needs no manual edit" is wrong. Task 1 must grant anon UPDATE/INSERT on `automation_status`, `worker_lease_id`, and `worker_heartbeat_at`, and **only** those. Task 7 verifies it live, because mocks can't see grants.
3. **Old `approve_application(BIGINT)` overload surviving.** `CREATE OR REPLACE` with a new arg list creates an overload, and PostgREST would still route a 1-arg call to the unbound version. Task 1 must `DROP FUNCTION` it explicitly. Task 7 verifies exactly one signature exists.
4. **Lost claim response under retry.** A conditional claim wrapped in `db._retry` can succeed on attempt 1 with the response lost, then match zero rows on attempt 2, and the worker thinks it lost. Task 3: generate the lease UUID locally, never `_retry` the conditional UPDATE, and on empty or exception re-read and compare `worker_lease_id`.
5. **Approval of a revision the human didn't see.** If the preview is edited between page render and the tap, approval must fail (409), not bind to the new hash. Task 1's RPC compares against the client-sent hash; Task 5's UI sends `app.preview_revision_hash` from the rendered row.

---

### Task 1: Migration — columns, hash trigger, grants, RPCs, backfill

**Files:**
- Create: `supabase/migrations/20261001000000_add_automation_status_and_revision_binding.sql`
- Test: `tests/test_automation_status_migration.py` (static assertions over the SQL text; there is no live DB in tests)

**Interfaces:**
- Produces (DB): columns `automation_status TEXT NOT NULL DEFAULT 'idle'` (CHECK over the 11 statuses above), `preview_revision_hash TEXT`, `approved_revision_hash TEXT`, `worker_lease_id UUID`, `worker_heartbeat_at TIMESTAMPTZ`.
- Produces (RPC): `approve_application(p_id BIGINT, p_revision_hash TEXT)`, `reset_approval(p_id BIGINT)`, `resolve_confirmation(p_id BIGINT, p_submitted BOOLEAN)`.

- [ ] **Step 1: Write the failing static test**

```python
"""Static checks over the automation_status migration. There is no live DB in the test suite;
these pin the properties Task 7 later verifies live (grants, overload drop, trigger)."""

import re
from pathlib import Path

SQL = (Path(__file__).resolve().parent.parent / "supabase" / "migrations"
       / "20261001000000_add_automation_status_and_revision_binding.sql").read_text()
SQL_NO_COMMENTS = re.sub(r"--[^\n]*", "", SQL)

STATUSES = ["idle", "preparing", "needs_input", "ready_for_review", "approved", "submitting",
            "submitted", "needs_confirmation", "failed_retryable", "failed_terminal", "unsupported"]


def test_check_constraint_lists_every_status():
    m = re.search(r"automation_status IN \(([^)]*)\)", SQL_NO_COMMENTS)
    assert m
    listed = re.findall(r"'([a-z_]+)'", m.group(1))
    assert sorted(listed) == sorted(STATUSES)


def test_old_one_arg_approve_overload_is_dropped():
    assert re.search(r"DROP FUNCTION IF EXISTS approve_application\(BIGINT\)", SQL_NO_COMMENTS)


def test_anon_grants_cover_lifecycle_columns_but_never_hash_columns():
    grants = re.findall(r"GRANT\s+(?:UPDATE|INSERT)\s*\(([^)]*)\)\s+ON\s+job_applications\s+TO\s+anon",
                        SQL_NO_COMMENTS)
    assert grants, "explicit column grants to anon are required"
    granted = {c.strip() for g in grants for c in g.split(",")}
    assert {"automation_status", "worker_lease_id", "worker_heartbeat_at"} <= granted
    assert "preview_revision_hash" not in granted
    assert "approved_revision_hash" not in granted
    assert "approved_at" not in granted


def test_preview_hash_is_trigger_computed_on_every_write():
    assert re.search(r"BEFORE INSERT OR UPDATE ON job_applications", SQL_NO_COMMENTS)
    assert "NEW.preview_revision_hash :=" in SQL_NO_COMMENTS


def test_approve_binds_to_client_sent_hash():
    body = SQL_NO_COMMENTS.split("FUNCTION approve_application(p_id BIGINT, p_revision_hash TEXT)")[1]
    body = body.split("$$;")[0]
    assert "preview_revision_hash = p_revision_hash" in body
    assert "approved_revision_hash = preview_revision_hash" in body
    assert "automation_status = 'ready_for_review'" in body


def test_reset_approval_refuses_post_click_states():
    body = SQL_NO_COMMENTS.split("FUNCTION reset_approval(p_id BIGINT)")[1].split("$$;")[0]
    assert "automation_status IN ('approved', 'failed_retryable')" in body
    assert "worker_lease_id IS NULL" in body
    for forbidden in ("needs_confirmation", "submitting", "submitted"):
        assert forbidden not in body


def test_every_rpc_revokes_public_and_grants_anon():
    for sig in ("approve_application(BIGINT, TEXT)", "reset_approval(BIGINT)",
                "resolve_confirmation(BIGINT, BOOLEAN)"):
        assert f"REVOKE EXECUTE ON FUNCTION {sig} FROM PUBLIC" in SQL_NO_COMMENTS
        assert f"GRANT  EXECUTE ON FUNCTION {sig} TO anon" in SQL_NO_COMMENTS
```

- [ ] **Step 2: Run it and confirm it fails** (file not found)

Run: `/Users/kishoretheeraj/Documents/cold-email-agent/.venv/bin/python -m pytest tests/test_automation_status_migration.py -v`

- [ ] **Step 3: Write the migration**

```sql
-- Application reliability plan, packaging item 2 (docs/superpowers/plans/2026-10-01-automation-status-and-leases.md).
--
-- 1. automation_status: execution lifecycle, decoupled from the recruiting `stage` (which is kept,
--    unrenamed, and still dual-written -- see the strategic plan §4's recorded deviation).
-- 2. preview_revision_hash: computed ONLY by the trigger below, on every insert/update, from
--    apply_preview + resume/cover-letter refs. Because the trigger overwrites it on every write,
--    no role (anon, or even a buggy service-role script) can forge it. Not granted to anon.
-- 3. approved_revision_hash: set ONLY by approve_application(), to the hash the human was shown.
--    Approval is valid only while approved_revision_hash = preview_revision_hash; any later edit
--    to the preview changes the latter and silently invalidates the approval. Not granted to anon.
-- 4. worker_lease_id / worker_heartbeat_at: atomic claim + stale-lease recovery (db.py).
--
-- GRANTS: migration 20260925000000 revoked table-level UPDATE/INSERT from anon and re-granted
-- per-column privileges ONCE, computed at that migration's run time. Its comment claimed new
-- columns would need no manual edit -- that is wrong: a column added later has NO anon
-- privilege. Every column anon must write needs an explicit grant, as below.
--
-- RPC OVERLOAD: approve_application's signature changes from (BIGINT) to (BIGINT, TEXT).
-- CREATE OR REPLACE with a new argument list creates a second overload instead of replacing,
-- and PostgREST would keep routing 1-arg calls to the old, hash-unbound version -- so it is
-- DROPPED explicitly. Deploy order: `supabase db push`, then the Vercel deploy, back to back.
-- Between the two, the deployed route's 1-arg call fails closed (cannot approve), which is safe.

ALTER TABLE job_applications
  ADD COLUMN IF NOT EXISTS automation_status TEXT NOT NULL DEFAULT 'idle',
  ADD COLUMN IF NOT EXISTS preview_revision_hash TEXT,
  ADD COLUMN IF NOT EXISTS approved_revision_hash TEXT,
  ADD COLUMN IF NOT EXISTS worker_lease_id UUID,
  ADD COLUMN IF NOT EXISTS worker_heartbeat_at TIMESTAMPTZ;

ALTER TABLE job_applications DROP CONSTRAINT IF EXISTS job_applications_automation_status_check;
ALTER TABLE job_applications
  ADD CONSTRAINT job_applications_automation_status_check
  CHECK (automation_status IN ('idle', 'preparing', 'needs_input', 'ready_for_review', 'approved',
                               'submitting', 'submitted', 'needs_confirmation', 'failed_retryable',
                               'failed_terminal', 'unsupported'));

-- ── Preview revision hash (trigger-owned) ──────────────────────────────────────

CREATE OR REPLACE FUNCTION job_application_preview_revision_hash(p_preview JSONB, p_resume TEXT, p_cover TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT CASE
    WHEN p_preview IS NULL THEN NULL
    -- jsonb::text is canonical (keys sorted, whitespace normalized), so equal previews hash equal
    ELSE encode(sha256(convert_to(p_preview::text || '|' || coalesce(p_resume, '') || '|' || coalesce(p_cover, ''), 'UTF8')), 'hex')
  END
$$;

CREATE OR REPLACE FUNCTION set_job_application_preview_revision_hash()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.preview_revision_hash := job_application_preview_revision_hash(NEW.apply_preview, NEW.resume_file_ref, NEW.cover_letter_file_ref);
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_job_applications_preview_revision_hash ON job_applications;
CREATE TRIGGER trg_job_applications_preview_revision_hash
  BEFORE INSERT OR UPDATE ON job_applications
  FOR EACH ROW EXECUTE FUNCTION set_job_application_preview_revision_hash();

-- ── Backfill (the trigger fills preview_revision_hash for every row this touches) ──

UPDATE job_applications SET automation_status = CASE
  WHEN stage = 'ready_to_submit' AND apply_blocked_reason LIKE '%clicked Submit%' THEN 'needs_confirmation'
  WHEN stage = 'ready_to_submit' AND approved_at IS NOT NULL AND apply_blocked_reason IS NOT NULL THEN 'failed_retryable'
  WHEN stage = 'ready_to_submit' AND approved_at IS NOT NULL THEN 'approved'
  WHEN stage = 'ready_to_submit' THEN 'ready_for_review'
  WHEN stage = 'saved' AND (apply_blocked_reason LIKE 'workday%' OR apply_blocked_reason LIKE 'aggregator%') THEN 'unsupported'
  WHEN stage = 'saved' AND apply_blocked_reason IS NOT NULL THEN 'failed_retryable'
  ELSE 'idle'
END;

-- Carry any pre-existing approval over to the hash it was granted on (live data on 2026-10-01
-- had zero approved rows; this is for correctness on any other environment).
UPDATE job_applications SET approved_revision_hash = preview_revision_hash
WHERE approved_at IS NOT NULL AND automation_status IN ('approved', 'failed_retryable', 'needs_confirmation');

-- ── Anon column grants (see GRANTS note above) ─────────────────────────────────

GRANT UPDATE (automation_status, worker_lease_id, worker_heartbeat_at) ON job_applications TO anon;
GRANT INSERT (automation_status, worker_lease_id, worker_heartbeat_at) ON job_applications TO anon;

-- ── RPCs ───────────────────────────────────────────────────────────────────────

DROP FUNCTION IF EXISTS approve_application(BIGINT);

CREATE OR REPLACE FUNCTION approve_application(p_id BIGINT, p_revision_hash TEXT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET approved_at = now(),
      approved_revision_hash = preview_revision_hash,
      automation_status = 'approved',
      apply_blocked_reason = NULL
  WHERE id = p_id
    AND stage = 'ready_to_submit'
    AND apply_preview IS NOT NULL
    AND approved_at IS NULL
    AND automation_status = 'ready_for_review'
    AND p_revision_hash IS NOT NULL
    AND preview_revision_hash = p_revision_hash;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'approve_application: row % is not approvable (already approved, not ready for review, or the preview changed since it was shown)', p_id;
  END IF;
END;
$$;

-- Only pre-click states can be reset. needs_confirmation/submitting/submitted are deliberately
-- excluded: the site may already have the application, and resetting would re-open the
-- Approve & Submit path to a duplicate real submission. Those resolve via resolve_confirmation.
CREATE OR REPLACE FUNCTION reset_approval(p_id BIGINT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET approved_at = NULL,
      approved_revision_hash = NULL,
      apply_blocked_reason = NULL,
      automation_status = 'ready_for_review'
  WHERE id = p_id
    AND stage = 'ready_to_submit'
    AND approved_at IS NOT NULL
    AND automation_status IN ('approved', 'failed_retryable')
    AND worker_lease_id IS NULL;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'reset_approval: row % is not in a resettable state (not approved/failed-before-submit, or a worker holds it)', p_id;
  END IF;
END;
$$;

-- A human checked the employer portal / inbox and says whether the ambiguous submit landed.
CREATE OR REPLACE FUNCTION resolve_confirmation(p_id BIGINT, p_submitted BOOLEAN)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF p_submitted THEN
    UPDATE job_applications
    SET automation_status = 'submitted',
        stage = 'applied',
        applied_date = coalesce(applied_date, current_date),
        source_channel = coalesce(source_channel, apply_preview->>'platform'),
        apply_blocked_reason = NULL,
        updated_at = now()
    WHERE id = p_id AND automation_status = 'needs_confirmation' AND worker_lease_id IS NULL;
  ELSE
    UPDATE job_applications
    SET automation_status = 'ready_for_review',
        approved_at = NULL,
        approved_revision_hash = NULL,
        apply_blocked_reason = NULL,
        updated_at = now()
    WHERE id = p_id AND automation_status = 'needs_confirmation' AND worker_lease_id IS NULL;
  END IF;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'resolve_confirmation: row % is not awaiting confirmation', p_id;
  END IF;
END;
$$;

REVOKE EXECUTE ON FUNCTION approve_application(BIGINT, TEXT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION approve_application(BIGINT, TEXT) TO anon;

REVOKE EXECUTE ON FUNCTION reset_approval(BIGINT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION reset_approval(BIGINT) TO anon;

REVOKE EXECUTE ON FUNCTION resolve_confirmation(BIGINT, BOOLEAN) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION resolve_confirmation(BIGINT, BOOLEAN) TO anon;
```

Before writing, confirm `job_applications.updated_at` exists (`grep -n updated_at supabase/migrations/20260826000000_create_job_applications.sql`). If it doesn't, drop the two `updated_at = now()` lines.

- [ ] **Step 4: Run the test; expect PASS**

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20261001000000_add_automation_status_and_revision_binding.sql tests/test_automation_status_migration.py
git commit -m "feat(db): automation_status, trigger-owned preview revision hash, revision-bound approval RPCs"
```

---

### Task 2: config constants

**Files:**
- Modify: `config.py` (next to `APPLY_AGENT_HAND_MAPPED_PLATFORMS`, ~line 573)
- Test: `tests/test_automation_status_migration.py` (append)

**Interfaces:**
- Produces: `config.AUTOMATION_STATUSES` (tuple of the 11 strings in the vocabulary table, in table order), `config.APPLY_AGENT_LEASE_STALE_SECONDS = 1800`, `config.APPLY_AGENT_PREVIEW_ELIGIBLE_STATUSES = ("idle", "failed_retryable")`.

- [ ] **Step 1: Append the failing test**

```python
import config


def test_config_statuses_match_migration_check_constraint():
    m = re.search(r"automation_status IN \(([^)]*)\)", SQL_NO_COMMENTS)
    assert sorted(re.findall(r"'([a-z_]+)'", m.group(1))) == sorted(config.AUTOMATION_STATUSES)


def test_lease_stale_threshold_exceeds_submit_workflow_timeout():
    # apply_agent_submit.yml has timeout-minutes: 15 -- a live submit must never look stale.
    assert config.APPLY_AGENT_LEASE_STALE_SECONDS > 15 * 60
```

- [ ] **Step 2: Run it; expect FAIL** (AttributeError)
- [ ] **Step 3: Add the constants with a one-line comment each** (the stale-lease comment must say *why* 1800: it exceeds the submit workflow's 15-minute timeout and the per-row preview budget, so only a dead worker's lease goes stale)
- [ ] **Step 4: Run it; expect PASS**
- [ ] **Step 5: Commit** `git add config.py tests/test_automation_status_migration.py` with message `feat(config): automation status vocabulary and lease staleness threshold`

---

### Task 3: db.py lease and lifecycle accessors

**Files:**
- Modify: `db.py`. Add a `# ── Application worker leases ──…` section after `set_apply_blocked` (~line 634). Modify `record_submission` (~583).
- Test: `tests/test_application_leases_db.py` (new; reuse the `fake_client` fixture pattern from `tests/test_job_applications_db.py`)

**Interfaces:**
- Consumes: `config.AUTOMATION_STATUSES`.
- Produces:
  - `claim_application(application_id, from_statuses, to_status) -> str | None`. Returns the lease UUID string on success, `None` when another worker or status holds it.
  - `heartbeat_application(application_id, lease_id) -> None`. Best-effort; never raises.
  - `release_application(application_id, lease_id, to_status, fields=None) -> dict | None`. Writes `automation_status=to_status`, clears both lease columns, merges `fields`. Conditional on `worker_lease_id == lease_id`.
  - `set_automation_status(application_id, status, reason=None) -> dict | None`. Unleased write, used for `unsupported`. Sets `apply_blocked_reason` when `reason` is not None.
  - `recover_stale_leases(stale_after_seconds) -> int`. Rows whose lease heartbeat is older than the cutoff: `submitting` becomes `needs_confirmation`, anything else becomes `failed_retryable`. The lease is cleared and the reason recorded. Returns the count.
  - `record_submission(application_id, source_channel, applied_date, lease_id=None)`. Unchanged single update, plus `automation_status='submitted'` and cleared lease columns. When `lease_id` is given, also `.eq("worker_lease_id", lease_id)`.
  - Every one raises `ValueError` if a passed status is not in `config.AUTOMATION_STATUSES`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for db.py's application worker lease accessors."""

from unittest.mock import MagicMock

import pytest

import db


@pytest.fixture
def fake_client(mocker):
    client = MagicMock(name="supabase_client")
    mocker.patch.object(db, "_client", client)
    mocker.patch.object(db, "get_client", return_value=client)
    return client


def _update_chain(client):
    return client.table.return_value.update.return_value


def test_claim_success_returns_locally_generated_lease(fake_client):
    chain = _update_chain(fake_client).eq.return_value.in_.return_value.is_.return_value
    chain.execute.return_value.data = [{"id": 7}]
    lease = db.claim_application(7, ("idle", "failed_retryable"), "preparing")
    assert isinstance(lease, str) and len(lease) == 36
    payload = fake_client.table.return_value.update.call_args[0][0]
    assert payload["automation_status"] == "preparing"
    assert payload["worker_lease_id"] == lease
    _update_chain(fake_client).eq.return_value.in_.assert_called_with(
        "automation_status", ["idle", "failed_retryable"])
    _update_chain(fake_client).eq.return_value.in_.return_value.is_.assert_called_with(
        "worker_lease_id", "null")


def test_claim_lost_to_another_worker_returns_none(fake_client):
    chain = _update_chain(fake_client).eq.return_value.in_.return_value.is_.return_value
    chain.execute.return_value.data = []
    fake_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"worker_lease_id": "someone-else"}]
    assert db.claim_application(7, ("idle",), "preparing") is None


def test_claim_whose_response_was_lost_is_recovered_by_reread(fake_client, mocker):
    chain = _update_chain(fake_client).eq.return_value.in_.return_value.is_.return_value
    chain.execute.side_effect = RuntimeError("connection reset after commit")
    captured = {}

    def reread(*a, **k):
        result = MagicMock()
        result.data = [{"worker_lease_id": captured["lease"]}]
        return result

    def capture_update(payload):
        captured["lease"] = payload["worker_lease_id"]
        return _update_chain(fake_client)

    fake_client.table.return_value.update.side_effect = capture_update
    fake_client.table.return_value.select.return_value.eq.return_value.execute.side_effect = reread
    lease = db.claim_application(7, ("approved",), "submitting")
    assert lease == captured["lease"]


def test_claim_is_never_wrapped_in_retry(fake_client, mocker):
    retry = mocker.spy(db, "_retry")
    chain = _update_chain(fake_client).eq.return_value.in_.return_value.is_.return_value
    chain.execute.return_value.data = [{"id": 7}]
    db.claim_application(7, ("idle",), "preparing")
    assert retry.call_count == 0


def test_claim_rejects_unknown_status(fake_client):
    with pytest.raises(ValueError):
        db.claim_application(7, ("idle",), "queued")


def test_release_is_conditional_on_own_lease_and_clears_it(fake_client):
    chain = _update_chain(fake_client).eq.return_value.eq.return_value
    chain.execute.return_value.data = [{"id": 7}]
    db.release_application(7, "lease-1", "ready_for_review", {"stage": "ready_to_submit"})
    payload = fake_client.table.return_value.update.call_args[0][0]
    assert payload["automation_status"] == "ready_for_review"
    assert payload["worker_lease_id"] is None and payload["worker_heartbeat_at"] is None
    assert payload["stage"] == "ready_to_submit"
    _update_chain(fake_client).eq.return_value.eq.assert_called_with("worker_lease_id", "lease-1")


def test_heartbeat_never_raises(fake_client):
    fake_client.table.side_effect = RuntimeError("down")
    db.heartbeat_application(7, "lease-1")


@pytest.mark.parametrize("held_status,expected", [
    ("submitting", "needs_confirmation"),
    ("preparing", "failed_retryable"),
])
def test_recover_stale_leases_never_makes_a_clicked_submit_retryable(fake_client, held_status, expected):
    select_chain = fake_client.table.return_value.select.return_value.not_.is_.return_value.lt.return_value
    select_chain.execute.return_value.data = [
        {"id": 7, "automation_status": held_status, "worker_lease_id": "old"}]
    upd = _update_chain(fake_client).eq.return_value.eq.return_value.lt.return_value
    upd.execute.return_value.data = [{"id": 7}]
    assert db.recover_stale_leases(1800) == 1
    payload = fake_client.table.return_value.update.call_args[0][0]
    assert payload["automation_status"] == expected
    assert payload["worker_lease_id"] is None
    assert held_status in payload["apply_blocked_reason"]


def test_record_submission_marks_submitted_and_clears_lease(fake_client):
    chain = _update_chain(fake_client).eq.return_value.eq.return_value
    chain.execute.return_value.data = [{"id": 7}]
    db.record_submission(7, "greenhouse", "2026-10-01", lease_id="lease-1")
    payload = fake_client.table.return_value.update.call_args[0][0]
    assert payload["stage"] == "applied"
    assert payload["automation_status"] == "submitted"
    assert payload["worker_lease_id"] is None
```

The mock chains above assume this query shape. If the implementation's builder order differs, adjust the **test's** chain to match, but keep every assertion:
- `recover_stale_leases` selects with `.select("id,automation_status,worker_lease_id").not_.is_("worker_lease_id", "null").lt("worker_heartbeat_at", cutoff)`.
- Its per-row update is `.update(...).eq("id", id).eq("worker_lease_id", old_lease).lt("worker_heartbeat_at", cutoff)`, so a heartbeat that lands between the select and the update makes the update match zero rows.

- [ ] **Step 2: Run them; expect FAIL**

Run: `/Users/kishoretheeraj/Documents/cold-email-agent/.venv/bin/python -m pytest tests/test_application_leases_db.py -v`

- [ ] **Step 3: Implement**

```python
# ── Application worker leases ──────────────────────────────────────────────────

def _check_status(status):
    if status not in config.AUTOMATION_STATUSES:
        raise ValueError(f"unknown automation_status: {status}")


def claim_application(application_id, from_statuses, to_status):
    """Atomically move a row from one of from_statuses to to_status under a fresh worker lease.
    Returns the lease id, or None if the row isn't claimable. Deliberately NOT wrapped in
    _retry: a retry after a committed-but-unacknowledged claim would match zero rows and
    report a lost claim -- so on an empty/failed response, re-read and compare lease ids."""
    for s in (*from_statuses, to_status):
        _check_status(s)
    lease_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    payload = {"automation_status": to_status, "worker_lease_id": lease_id,
               "worker_heartbeat_at": now, "updated_at": now}
    try:
        result = (get_client().table("job_applications").update(payload)
                  .eq("id", application_id).in_("automation_status", list(from_statuses))
                  .is_("worker_lease_id", "null").execute())
        if result.data:
            return lease_id
    except Exception as exc:
        log.warning(f"claim_application | {application_id} | claim call failed, re-reading: {exc}")
    row = _retry(lambda: get_client().table("job_applications").select("worker_lease_id")
                 .eq("id", application_id).execute())
    if row.data and row.data[0].get("worker_lease_id") == lease_id:
        return lease_id
    return None


def heartbeat_application(application_id, lease_id):
    """Refresh a held lease's heartbeat. Best-effort: never raises."""
    try:
        (get_client().table("job_applications")
         .update({"worker_heartbeat_at": datetime.utcnow().isoformat()})
         .eq("id", application_id).eq("worker_lease_id", lease_id).execute())
    except Exception as exc:
        log.warning(f"heartbeat_application | {application_id} | {exc}")


def release_application(application_id, lease_id, to_status, fields=None):
    """Write to_status (plus any extra fields) and drop the lease -- only if we still hold it."""
    _check_status(to_status)
    payload = {**(fields or {}), "automation_status": to_status, "worker_lease_id": None,
               "worker_heartbeat_at": None, "updated_at": datetime.utcnow().isoformat()}
    result = _retry(lambda: get_client().table("job_applications").update(payload)
                    .eq("id", application_id).eq("worker_lease_id", lease_id).execute())
    return result.data[0] if result.data else None


def set_automation_status(application_id, status, reason=None):
    """Unleased status write (e.g. 'unsupported' before any claim)."""
    _check_status(status)
    payload = {"automation_status": status, "updated_at": datetime.utcnow().isoformat()}
    if reason is not None:
        payload["apply_blocked_reason"] = reason
    result = _retry(lambda: get_client().table("job_applications").update(payload)
                    .eq("id", application_id).execute())
    return result.data[0] if result.data else None


def recover_stale_leases(stale_after_seconds):
    """Release leases whose heartbeat is older than the cutoff. A stale 'submitting' lease means
    a worker died somewhere around the Submit click -- it becomes needs_confirmation, never
    retryable, because the site may already have the application."""
    cutoff = (datetime.utcnow() - timedelta(seconds=stale_after_seconds)).isoformat()
    rows = _retry(lambda: get_client().table("job_applications")
                  .select("id,automation_status,worker_lease_id")
                  .not_.is_("worker_lease_id", "null").lt("worker_heartbeat_at", cutoff)
                  .execute()).data or []
    recovered = 0
    for row in rows:
        held = row.get("automation_status")
        to_status = "needs_confirmation" if held == "submitting" else "failed_retryable"
        payload = {"automation_status": to_status, "worker_lease_id": None,
                   "worker_heartbeat_at": None,
                   "apply_blocked_reason": f"worker lease expired while {held}; recovered as {to_status}",
                   "updated_at": datetime.utcnow().isoformat()}
        result = _retry(lambda: get_client().table("job_applications").update(payload)
                        .eq("id", row["id"]).eq("worker_lease_id", row["worker_lease_id"])
                        .lt("worker_heartbeat_at", cutoff).execute())
        if result.data:
            recovered += 1
            log.warning(f"recover_stale_leases | {row['id']} | {held} -> {to_status}")
    return recovered
```

Add `import uuid` to db.py's imports (stdlib, alphabetical with the others). Update `record_submission`:

```python
def record_submission(application_id, source_channel, applied_date, lease_id=None):
    """Atomically flip a row to 'applied'/'submitted' and record how/when it was actually filed.
    Must be ONE update, not stage-then-fields separately -- a partial failure between two calls
    would leave the row applied with no source_channel/applied_date, or vice versa."""
    payload = {"stage": "applied", "automation_status": "submitted", "source_channel": source_channel,
               "applied_date": applied_date, "worker_lease_id": None, "worker_heartbeat_at": None,
               "apply_blocked_reason": None, "updated_at": datetime.utcnow().isoformat()}

    def _do():
        q = get_client().table("job_applications").update(payload).eq("id", application_id)
        if lease_id is not None:
            q = q.eq("worker_lease_id", lease_id)
        return q.execute()

    result = _retry(_do)
    return result.data[0] if result.data else None
```

- [ ] **Step 4: Run the new file and `tests/test_job_applications_db.py`; expect PASS.** If an existing `record_submission` test asserts the exact payload, update it to include the new keys rather than weakening it.
- [ ] **Step 5: Commit** `git add db.py tests/test_application_leases_db.py tests/test_job_applications_db.py` with message `feat(db): atomic application leases with stale-lease recovery`

---

### Task 4: apply_agent.py — lease-held preview and submit, the clicked boundary

**Files:**
- Modify: `apply_agent.py` (`_process_one_preview`, `run_preview`, `submit`)
- Test: `tests/test_apply_agent.py` (update existing preview and submit tests to the new db calls; add the new cases below)

**Interfaces:**
- Consumes: Task 3's db functions and Task 2's constants.
- Produces: no new public API. Behavior:
  - **Preview.** `run_preview()` first calls `db.recover_stale_leases(config.APPLY_AGENT_LEASE_STALE_SECONDS)`. Eligible rows are `stage='saved'` with both file refs **and** `automation_status in config.APPLY_AGENT_PREVIEW_ELIGIBLE_STATUSES`; `.get("automation_status", "idle")` treats a missing key as `idle` for fixtures.
  - In `_process_one_preview`, Workday and aggregator rows go to `db.set_automation_status(id, "unsupported", reason)` and return `"blocked"`. Otherwise it claims `(eligible) → "preparing"` and returns `"skipped"` if the claim comes back `None`.
  - Between steps it calls `db.heartbeat_application`: after page launch, after the fill, after the attach, and after the screening answers.
  - On success it calls `db.release_application(id, lease, "ready_for_review", {"apply_preview": preview, "stage": "ready_to_submit", "apply_blocked_reason": None})`.
  - On exception it calls `db.release_application(id, lease, "failed_retryable", {"apply_blocked_reason": f"preview pass error: {exc}"})`, then re-raises. `run_preview()` gets a `skipped` counter.
  - **Submit.** Order:
    1. `recover_stale_leases`.
    2. `db.get_job_application`. If not found, raise `ValueError` (as today).
    3. Excluded platform: `db.set_apply_blocked` then raise `ValueError` (as today), before any claim.
    4. `lease = db.claim_application(job_id, ("approved",), "submitting")`. If `None`, raise `ValueError("submit() could not claim ...")` and write nothing.
    5. **Re-read** the row with `db.get_job_application` and run the gate on the re-read row. The gate is the existing three conditions verbatim **plus** `automation_status == "submitting"` (our own claim) **plus** `approved_revision_hash` truthy and equal to `preview_revision_hash`. If the gate fails, release to `failed_retryable` with the reason, then raise `ValueError`.
    6. Fill.
    7. Not armed: release back to `"approved"` and return.
    8. Set `clicked = True` **immediately before** `.click()`, then click and check confirmation.
    9. `db.record_submission(job_id, platform, date, lease_id=lease)`.
    10. On any exception after the claim: `to_status = "needs_confirmation" if clicked else "failed_retryable"`, then `db.release_application(job_id, lease, to_status, {"apply_blocked_reason": str(exc)})` inside its own try/except, then re-raise. Remove the old `db.set_apply_blocked` from that handler; the release carries the reason.

- [ ] **Step 1: Write the failing tests** (add to `tests/test_apply_agent.py`; reuse its existing fixtures/mocks for `_launch_page`, `ats_fillers`, `db`. Read that file's top first to match its helper names.)

```python
@pytest.fixture
def approved_job():
    return {"id": 9, "company": "Acme", "job_url": "https://boards.greenhouse.io/acme/jobs/1",
            "stage": "ready_to_submit", "apply_preview": {"platform": "greenhouse",
            "field_values": {}, "screening_answers": {}, "eligibility_answers": {}},
            "approved_at": "2026-10-01T00:00:00Z", "automation_status": "submitting",
            "preview_revision_hash": "h1", "approved_revision_hash": "h1",
            "resume_file_ref": "r", "cover_letter_file_ref": "c"}


def _arm_submit(mocker, job, confirmed=True, click_raises=None):
    mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": "1"})
    mocker.patch.object(apply_agent.db, "recover_stale_leases", return_value=0)
    mocker.patch.object(apply_agent.db, "get_job_application", return_value=job)
    mocker.patch.object(apply_agent.db, "claim_application", return_value="lease-1")
    release = mocker.patch.object(apply_agent.db, "release_application")
    record = mocker.patch.object(apply_agent.db, "record_submission")
    page = MagicMock()
    if click_raises:
        page.get_by_role.return_value.click.side_effect = click_raises
    mocker.patch.object(apply_agent, "_launch_page", return_value=page)
    mocker.patch.object(apply_agent, "_close_page")
    mocker.patch.object(apply_agent.ats_fillers, "fill_greenhouse")
    mocker.patch.object(apply_agent, "_attach_resume_and_cover_letter")
    mocker.patch.object(apply_agent, "_fill_screening_questions")
    mocker.patch.object(apply_agent, "_fill_eligibility_answers")
    mocker.patch.object(apply_agent, "_submission_confirmed", return_value=confirmed)
    return page, release, record


def test_submit_happy_path_records_with_lease(mocker, approved_job):
    page, release, record = _arm_submit(mocker, approved_job)
    apply_agent.submit(9)
    record.assert_called_once()
    assert record.call_args.kwargs.get("lease_id") == "lease-1"
    release.assert_not_called()


@pytest.mark.parametrize("confirmed,click_raises", [
    (False, None),                              # clicked, no confirmation on page
    (True, RuntimeError("navigation crashed")), # exception raised by .click() itself
])
def test_any_failure_from_the_click_onward_is_needs_confirmation(mocker, approved_job, confirmed, click_raises):
    _, release, _ = _arm_submit(mocker, approved_job, confirmed=confirmed, click_raises=click_raises)
    with pytest.raises(Exception):
        apply_agent.submit(9)
    assert release.call_args[0][2] == "needs_confirmation"


def test_record_submission_failure_after_confirmed_click_is_needs_confirmation(mocker, approved_job):
    _, release, record = _arm_submit(mocker, approved_job)
    record.side_effect = RuntimeError("supabase down")
    with pytest.raises(RuntimeError):
        apply_agent.submit(9)
    assert release.call_args[0][2] == "needs_confirmation"


def test_failure_before_click_is_failed_retryable(mocker, approved_job):
    _, release, _ = _arm_submit(mocker, approved_job)
    apply_agent.ats_fillers.fill_greenhouse.side_effect = RuntimeError("selector missing")
    with pytest.raises(RuntimeError):
        apply_agent.submit(9)
    assert release.call_args[0][2] == "failed_retryable"


def test_unarmed_submit_releases_back_to_approved_without_clicking(mocker, approved_job):
    page, release, record = _arm_submit(mocker, approved_job)
    mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": ""})
    apply_agent.submit(9)
    page.get_by_role.return_value.click.assert_not_called()
    assert release.call_args[0][2] == "approved"
    record.assert_not_called()


def test_unclaimable_row_raises_and_never_launches_browser(mocker, approved_job):
    _arm_submit(mocker, approved_job)
    apply_agent.db.claim_application.return_value = None
    with pytest.raises(ValueError):
        apply_agent.submit(9)
    apply_agent._launch_page.assert_not_called()


@pytest.mark.parametrize("override", [
    {"approved_revision_hash": "stale"},       # preview edited after approval
    {"approved_revision_hash": None},
    {"automation_status": "approved"},         # re-read shows we don't actually hold it
    {"approved_at": None},
    {"apply_preview": None},
    {"stage": "saved"},
])
def test_gate_on_reread_row_blocks_before_browser(mocker, approved_job, override):
    _, release, _ = _arm_submit(mocker, {**approved_job, **override})
    with pytest.raises(ValueError):
        apply_agent.submit(9)
    apply_agent._launch_page.assert_not_called()
    assert release.call_args[0][2] == "failed_retryable"


def test_preview_skips_rows_another_worker_claimed(mocker):
    job = {"id": 3, "job_url": "https://boards.greenhouse.io/x/jobs/1", "automation_status": "idle",
           "resume_file_ref": "r", "cover_letter_file_ref": "c", "stage": "saved"}
    mocker.patch.object(apply_agent.db, "claim_application", return_value=None)
    launch = mocker.patch.object(apply_agent, "_launch_page")
    assert apply_agent._process_one_preview(job) == "skipped"
    launch.assert_not_called()


def test_preview_marks_workday_unsupported_without_claiming(mocker):
    job = {"id": 3, "job_url": "https://acme.wd5.myworkdayjobs.com/x", "stage": "saved"}
    status = mocker.patch.object(apply_agent.db, "set_automation_status")
    claim = mocker.patch.object(apply_agent.db, "claim_application")
    assert apply_agent._process_one_preview(job) == "blocked"
    assert status.call_args[0][1] == "unsupported"
    claim.assert_not_called()


def test_run_preview_ignores_rows_not_in_eligible_statuses(mocker):
    rows = [{"id": i, "stage": "saved", "resume_file_ref": "r", "cover_letter_file_ref": "c",
             "automation_status": s} for i, s in enumerate(
             ["idle", "failed_retryable", "unsupported", "ready_for_review", "preparing"])]
    mocker.patch.object(apply_agent.db, "recover_stale_leases", return_value=0)
    mocker.patch.object(apply_agent.db, "get_job_applications", return_value=rows)
    proc = mocker.patch.object(apply_agent, "_process_one_preview", return_value="filled")
    apply_agent.run_preview()
    assert [c.args[0]["id"] for c in proc.call_args_list] == [0, 1]
```

Also update every existing submit and preview test in that file that asserts `db.set_apply_preview` or `db.set_apply_blocked` on a post-claim path. They should now assert `release_application`. Keep the armed and unarmed gate tests, adjusted to the new flow. **Do not delete any existing gate test.** If a test checks one of the three original conditions, keep it and make its fixture pass the new conditions.

- [ ] **Step 2: Run; expect the new tests to FAIL**

Run: `/Users/kishoretheeraj/Documents/cold-email-agent/.venv/bin/python -m pytest tests/test_apply_agent.py -v`

- [ ] **Step 3: Implement per the Interfaces block above.** The `clicked = True` assignment sits on the line directly above `page.get_by_role("button", name=_SUBMIT_BUTTON_NAME).click()`, with a comment: *from here on the site may have the application -- any failure is needs_confirmation, never retryable.* Keep the existing docstring and ARMED comments. Update the C1 comment block to describe the release-with-status behavior instead of `set_apply_blocked`.
- [ ] **Step 4: Run the full Python suite; expect PASS**

Run: `/Users/kishoretheeraj/Documents/cold-email-agent/.venv/bin/python -m pytest -q`

- [ ] **Step 5: Commit** `git add apply_agent.py tests/test_apply_agent.py` with message `feat(apply): lease-held preview/submit; post-click failures become needs_confirmation`

---

### Task 5: contact-manager — types, revision-bound approve, resolve-confirmation, UI

**Files:**
- Modify: `contact-manager/src/lib/types.ts` (+ `types.test.ts`)
- Modify: `contact-manager/src/app/api/applications/[id]/submit/route.ts` (+ add/extend `submit/route.test.ts`; check if one exists first)
- Create: `contact-manager/src/app/api/applications/[id]/resolve-confirmation/route.ts` + `route.test.ts` (mirror `reset-approval/route.test.ts`'s mocking pattern)
- Modify: `contact-manager/src/components/ApplicationsPage.tsx` (+ `ApplicationsPage.test.tsx`)
- Modify (if its mocks need new fields): `contact-manager/tests/e2e/18-applications.spec.ts`

**Interfaces:**
- Consumes: Task 1's RPC signatures. `approve_application({ p_id, p_revision_hash })` and `resolve_confirmation({ p_id, p_submitted })`.
- Produces (types.ts):
  ```ts
  export type AutomationStatus =
    | "idle" | "preparing" | "needs_input" | "ready_for_review" | "approved" | "submitting"
    | "submitted" | "needs_confirmation" | "failed_retryable" | "failed_terminal" | "unsupported";
  export const AUTOMATION_STATUSES: AutomationStatus[] = [/* same 11, same order */];
  export const AUTOMATION_STATUS_LABELS: Record<AutomationStatus, string> = {
    idle: "Idle", preparing: "Preparing", needs_input: "Needs your input",
    ready_for_review: "Ready for review", approved: "Approved / queued", submitting: "Submitting",
    submitted: "Submitted", needs_confirmation: "Needs confirmation",
    failed_retryable: "Failed (retryable)", failed_terminal: "Failed", unsupported: "Unsupported",
  };
  ```
  `JobApplication` gains `automation_status: AutomationStatus; preview_revision_hash: string | null; approved_revision_hash: string | null;`

Behavior:
1. **`POST /api/applications/[id]/submit`.** After the numeric-id check and **before** the token check, parse the JSON body and require `revision_hash` matching `/^[0-9a-f]{64}$/`. Otherwise return 400 `{ error: "revision_hash is required" }`. A malformed or missing body is also 400. Call `supabase.rpc("approve_application", { p_id: Number(id), p_revision_hash: revision_hash })`. Everything else is unchanged, including the `tryResetApproval` recovery on a dispatch failure. That recovery still works because the row is `approved` with no lease.
2. **`POST /api/applications/[id]/resolve-confirmation`.** Numeric id check gives 400. The body must be `{ submitted: boolean }`, otherwise 400. Call `supabase.rpc("resolve_confirmation", { p_id: Number(id), p_submitted: submitted })`. An RPC error gives 409 `{ error: message }`; success gives `{ ok: true }`. Use the same `getClient()` and `runtime = "nodejs"` shape as `reset-approval/route.ts`.
3. **`ApplicationsPage.tsx`:**
   - `doApprove`: if `!app.preview_revision_hash`, `toast.error("This preview has no revision hash yet -- reload and try again")` and return. Otherwise POST with `headers: {"Content-Type": "application/json"}, body: JSON.stringify({ revision_hash: app.preview_revision_hash })`.
   - `checkApplicationStatus`: read `automation_status` too. If it is `needs_confirmation`, return `"blocked"` after `toast.error("Submit outcome unknown -- confirm whether it went through")` and record the reason in `blockedReasons`, as the blocked branch does today. Check this **before** the generic `apply_blocked_reason` branch.
   - Action cell, inside the existing `stage === "ready_to_submit" && app.apply_preview` branch: add a branch **before** the blocked-reason branch for `app.automation_status === "needs_confirmation"`. It shows amber text "Submit may have gone through -- check the employer portal or your inbox, then confirm:" and two buttons. "It went through" posts `{submitted: true}`; "It didn't go through" posts `{submitted: false}`. Both go to `/resolve-confirmation`, then clear `blockedReasons[id]`/`timedOutIds` and call `load(stageFilter, sourceFilter)`. On failure: `toast.error("Could not record that -- try again")`.
   - The blocked-reason branch's "Try again" button renders **only** when `app.automation_status === "failed_retryable" || app.automation_status === "approved"`. Otherwise only the reason text shows. The timed-out branch's "Reset approval" button keeps working, and the RPC refuses unsafe states server-side (409 becomes the existing error toast).
   - In the blocked-reason column (the `{app.apply_blocked_reason ?? "—"}` cell), render `<Badge>{AUTOMATION_STATUS_LABELS[app.automation_status]}</Badge>` above the reason whenever `automation_status` is set and not `"idle"`.

- [ ] **Step 1: Write failing tests**
  - `types.test.ts`: `AUTOMATION_STATUSES` has 11 unique entries and every entry has a label.
  - Submit route:
    - A missing body gives 400.
    - `revision_hash: "abc"` gives 400.
    - A valid 64-hex hash calls `rpc("approve_application", { p_id: 5, p_revision_hash: <hash> })`.
    - An RPC error gives 409.
    - The 400 must happen without calling `rpc` or `fetch`.
    - Update existing submit-route tests that POST with no body so they send a valid hash.
  - Resolve route: a non-numeric id gives 400; `submitted: "yes"` gives 400; `true`/`false` pass through to the RPC; an RPC error gives 409.
  - `ApplicationsPage.test.tsx`:
    1. Approve sends `revision_hash` from the row in the POST body.
    2. A `needs_confirmation` row renders both resolve buttons and **no** "Approve & Submit" and **no** "Try again".
    3. Clicking "It went through" POSTs `{submitted:true}` to `/api/applications/<id>/resolve-confirmation`.
    4. A `failed_retryable` row with a reason renders "Try again".
    5. A row with a reason but `automation_status: "submitting"` renders no "Try again".
- [ ] **Step 2: Run; expect FAIL** with `cd contact-manager && npx vitest run src/lib/types.test.ts src/app/api/applications src/components/ApplicationsPage.test.tsx`
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run all of vitest (`npx vitest run`), `npx tsc --noEmit`, and `npx eslint src`; then `npx playwright test tests/e2e/18-applications.spec.ts`.** If an e2e mock fixture lacks `automation_status`/`preview_revision_hash`, add them (`"ready_for_review"` and a 64-char hex string) so the approve flow still passes. Do **not** commit regenerated screenshot PNGs. Restore them with `git checkout -- contact-manager/tests/e2e/screenshots`.
- [ ] **Step 5: Commit** the exact modified/created TS files with message `feat(ui): revision-bound approval, needs_confirmation resolution, automation status badges`

---

### Task 6: Docs and memory

**Files:**
- Modify: `CLAUDE.md` (Auto-apply agent section; Module/Tests notes)
- Modify: `docs/python/db-schema.md` (job_applications section)
- Modify: `docs/superpowers/plans/2026-09-28-application-reliability-plan.md`
- Memory: `/Users/kishoretheeraj/.claude/projects/-Users-kishoretheeraj-Documents-cold-email-agent/memory/project-application-reliability-plan.md` + `MEMORY.md` line

- [ ] **Step 1: CLAUDE.md.** Add a concise `**Execution lifecycle (automation_status, 2026-10-01)**` paragraph to the Auto-apply section covering:
  - The vocabulary table, by name only.
  - `stage` is still dual-written; `automation_status` is the gate.
  - The trigger owns `preview_revision_hash`, and `approved_revision_hash` is RPC-only. Neither is anon-granted.
  - Approval is bound to the client-sent hash.
  - Leases: claim without `_retry`, plus re-read.
  - `recover_stale_leases` maps `submitting` to `needs_confirmation`.
  - The `clicked` boundary.
  - `reset_approval` refuses post-click states; `resolve_confirmation` is the human path.
  - Any new `job_applications` column an anon writer needs requires an explicit `GRANT UPDATE/INSERT (col)`. Correct the earlier claim.

  Add `tests/test_application_leases_db.py` and `tests/test_automation_status_migration.py` to the Tests list. Update the "three conditions" text to say the gate now also checks `automation_status`/hash equality on a re-read after the claim.
- [ ] **Step 2: db-schema.md.** Add the five columns, the trigger, the three RPCs, and the grant correction.
- [ ] **Step 3: Strategic plan.**
  - In §4, replace the old execution-state list with the canonical vocabulary and the name mapping.
  - Mark §8 item 2 **Shipped (2026-10-01)**, linking this plan.
  - In Phase 1's first two bullets, note what shipped.
- [ ] **Step 4: Memory.** Update `project-application-reliability-plan.md`: item 2 is implemented, plus the non-obvious lessons (the grant-comment falsehood, the overload drop, the `clicked` boundary). Update its `MEMORY.md` line.
- [ ] **Step 5: Commit** `git add CLAUDE.md docs/python/db-schema.md docs/superpowers/plans/2026-09-28-application-reliability-plan.md docs/superpowers/plans/2026-10-01-automation-status-and-leases.md` with message `docs: automation_status lifecycle, revision-bound approval, leases`

---

### Task 7: Deploy and live verification (controller, not a subagent)

- [ ] **Step 1:** Run the full suites: pytest, vitest, tsc, and the applications e2e spec.
- [ ] **Step 2:** Push the branch and open a PR. Run a whole-branch review and fix any findings.
- [ ] **Step 3:** Merge. Then, from the main checkout, run `supabase db push` and the Vercel prod deploy back to back.
- [ ] **Step 4: Live SQL verification** (`supabase db query`), all inside `BEGIN; ... ROLLBACK;`:
  - `SELECT oid::regprocedure FROM pg_proc WHERE proname IN ('approve_application','reset_approval','resolve_confirmation');` should return exactly one row each, with `approve_application(bigint,text)`.
  - `SELECT c, has_column_privilege('anon','job_applications',c,'UPDATE') FROM unnest(ARRAY['automation_status','worker_lease_id','worker_heartbeat_at','preview_revision_hash','approved_revision_hash','approved_at']) c;` should give true, true, true, false, false, false.
  - `SET LOCAL ROLE anon; UPDATE job_applications SET approved_revision_hash='x' WHERE id=<the ready row>;` should fail with permission denied.
  - `SET LOCAL ROLE anon; UPDATE job_applications SET apply_preview = apply_preview || '{"_probe":1}' WHERE id=<ready row> RETURNING preview_revision_hash;` should return a hash different from the pre-update value.
  - `SELECT automation_status, count(*) FROM job_applications GROUP BY 1;` should show 442 idle and 1 ready_for_review.
- [ ] **Step 5:** In the browser, open `/applications` on the prod deployment. Confirm the ready row shows the "Ready for review" badge and an "Approve & Submit" button. **Do not click Approve.** That would dispatch a real submission.
