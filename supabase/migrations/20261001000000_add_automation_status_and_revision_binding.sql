-- Application reliability plan, packaging item 2 (docs/superpowers/plans/2026-10-01-automation-status-and-leases.md).
--
-- 1. automation_status: execution lifecycle, decoupled from the recruiting `stage` (which is kept,
--    unrenamed, and still dual-written -- see the strategic plan §4's recorded deviation).
-- 2. preview_revision_hash: computed ONLY by the trigger below, on every insert/update, from
--    apply_preview + resume/cover-letter refs + the destination (company, role, job_url), so an
--    approved row cannot be retargeted at another job without invalidating its approval.
--    Because the trigger overwrites it on every write,
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
  ADD COLUMN IF NOT EXISTS worker_heartbeat_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS documents_version TEXT;

ALTER TABLE job_applications DROP CONSTRAINT IF EXISTS job_applications_automation_status_check;
ALTER TABLE job_applications
  ADD CONSTRAINT job_applications_automation_status_check
  CHECK (automation_status IN ('idle', 'preparing', 'needs_input', 'ready_for_review', 'approved',
                               'submitting', 'submitted', 'needs_confirmation', 'failed_retryable',
                               'failed_terminal', 'unsupported'));

-- ── Preview revision hash (trigger-owned) ──────────────────────────────────────

-- documents_version: resume_agent.py --build re-uploads to FIXED storage paths, so a rebuild after
-- approval leaves the file refs (and thus a refs-only hash) unchanged. db.set_resume_files writes a
-- fresh uuid here on every build, which is hashed in, so a rebuild invalidates any approval.
-- Destination identity (company, role, job_url) is hashed too: job_url is anon-writable, and any
-- change to where the application goes changes the hash, which invalidates the approval.
CREATE OR REPLACE FUNCTION job_application_preview_revision_hash(p_preview JSONB, p_resume TEXT, p_cover TEXT, p_docs_version TEXT, p_company TEXT, p_role TEXT, p_job_url TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT CASE
    WHEN p_preview IS NULL THEN NULL
    -- jsonb::text is canonical (keys sorted, whitespace normalized), so equal previews hash equal
    ELSE encode(sha256(convert_to(p_preview::text || '|' || coalesce(p_resume, '') || '|' || coalesce(p_cover, '') || '|' || coalesce(p_docs_version, '') || '|' || coalesce(p_company, '') || '|' || coalesce(p_role, '') || '|' || coalesce(p_job_url, ''), 'UTF8')), 'hex')
  END
$$;

CREATE OR REPLACE FUNCTION set_job_application_preview_revision_hash()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.preview_revision_hash := job_application_preview_revision_hash(NEW.apply_preview, NEW.resume_file_ref, NEW.cover_letter_file_ref, NEW.documents_version, NEW.company, NEW.role, NEW.job_url);
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
  WHEN approved_at IS NOT NULL AND stage NOT IN ('saved', 'ready_to_submit') THEN 'submitted'
  ELSE 'idle'
END;

-- Carry any pre-existing approval over to the hash it was granted on (live data on 2026-10-01
-- had zero approved rows; this is for correctness on any other environment).
UPDATE job_applications SET approved_revision_hash = preview_revision_hash
WHERE approved_at IS NOT NULL AND automation_status IN ('approved', 'failed_retryable', 'needs_confirmation');

-- ── Anon column grants (see GRANTS note above) ─────────────────────────────────

-- documents_version is anon-writable on purpose: bumping it only ever CHANGES preview_revision_hash,
-- which invalidates an approval (the safe direction). It can never create or preserve one.
GRANT UPDATE (automation_status, worker_lease_id, worker_heartbeat_at, documents_version) ON job_applications TO anon;
GRANT INSERT (automation_status, worker_lease_id, worker_heartbeat_at, documents_version) ON job_applications TO anon;

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

-- approved_at may be NULL here (a failed_retryable row can lack it); it must still be resettable.
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
