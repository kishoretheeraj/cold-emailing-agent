-- M2: approved_at is the third leg of the ARMED-gate design (see
-- docs/superpowers/specs/2026-09-17-beelink-24-7-automation-design.md, "The ARMED gate on a
-- persistent host"). approve_application() is the ONLY way it can ever be set -- this migration
-- makes that true at the database layer. IMPORTANT: this migration alone does NOT yet gate
-- apply_agent.py's submit() on approved_at -- that runtime enforcement (requiring
-- job.get("approved_at") truthy before ever clicking Submit) lands in Task 6. Until Task 6
-- lands, approved_at is set-only: nothing reads it yet, and the manual workflow_dispatch path
-- to apply_agent_submit.yml remains an open bypass of the intended three-condition gate.
--
-- This migration deliberately does NOT enable RLS (see root plan's Global Constraints --
-- this repo authenticates every reader/writer, Python and Next.js alike, with one shared
-- anon key, and RLS on a table four different Python scripts and three API routes all write
-- through would risk silently breaking one of them for a property that concerns one column).
--
-- Confirmed live against the production project before writing this migration: the anon role
-- already holds blanket TABLE-LEVEL UPDATE and INSERT grants on job_applications (Supabase's
-- own bootstrap: GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role).
-- A column-level REVOKE does NOT carve an exception out of a table-level grant in Postgres --
-- it only removes a column-level grant, so a naive "REVOKE UPDATE (approved_at) ... FROM anon"
-- would be a silent no-op and leave approved_at fully anon-writable. The real fix: revoke the
-- table-level UPDATE *and* INSERT entirely (INSERT too, not just UPDATE -- an anon INSERT can
-- otherwise preset approved_at on a brand-new row, forging an approval the moment Task 6 makes
-- approved_at actually load-bearing), then re-grant both privileges on every column EXCEPT
-- approved_at. The column list is derived dynamically from information_schema at migration
-- time below, not hand-enumerated, so it can never silently drift from the live schema (a
-- hand-typed list is exactly the kind of thing that goes stale the first time someone adds a
-- column and forgets this file exists). This is zero blast radius for every existing caller
-- (job_pick.py, apply_agent.py, cu_linkedin.py, resume_agent.py, and the PATCH/POST routes in
-- contact-manager) since every column any of them writes today stays in the re-grant list.
--
-- Deliberate, documented scope limit: this allowlist necessarily keeps `stage` anon-writable
-- (every Python writer needs it) even though the spec's original ask was to deny anon writes to
-- both approved_at and stage. approved_at is the property that actually needs protecting (it's
-- the ARMED gate); stage is a shared, heavily-written column with no single owner. See this
-- plan's "Judgment calls and deviations" section.
--
-- IMPORTANT for future schema changes: a new column added to job_applications needs no manual
-- edit here (the grant is derived live) -- but a future migration that ALSO touches
-- UPDATE/INSERT grants on this table must run AFTER this one, or it will re-grant a blanket
-- privilege and reopen the hole. See docs/python/db-schema.md.
--
-- Rollback: GRANT UPDATE, INSERT ON job_applications TO anon; (re-opens approved_at to the
-- anon key wholesale -- only use this to fully back out of the ARMED gate's column-level
-- protection, never as a partial fix.)

ALTER TABLE job_applications ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ NULL;

DO $$
DECLARE cols text;
BEGIN
  SELECT string_agg(quote_ident(column_name), ', ') INTO cols
  FROM information_schema.columns
  WHERE table_schema = 'public' AND table_name = 'job_applications' AND column_name <> 'approved_at';

  EXECUTE 'REVOKE UPDATE ON job_applications FROM anon';
  EXECUTE format('GRANT UPDATE (%s) ON job_applications TO anon', cols);

  EXECUTE 'REVOKE INSERT ON job_applications FROM anon';
  EXECUTE format('GRANT INSERT (%s) ON job_applications TO anon', cols);
END $$;

CREATE OR REPLACE FUNCTION approve_application(p_id BIGINT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET approved_at = now()
  WHERE id = p_id
    AND stage = 'ready_to_submit'
    AND apply_preview IS NOT NULL
    AND approved_at IS NULL;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'approve_application: row % is not in an approvable state (already approved, not ready_to_submit, or missing a preview)', p_id;
  END IF;
END;
$$;

-- Companion to approve_application: if a GitHub Actions dispatch fails after approval (502,
-- token expiry, outage), approved_at is already set and every retry would 409 forever, with no
-- role able to clear it (approved_at is excluded from anon's UPDATE grant above). Guarded so it
-- can NEVER un-approve an already-submitted row: only fires when the row is still
-- stage = 'ready_to_submit' (a submitted row has moved on to stage = 'applied') and is
-- currently approved.
CREATE OR REPLACE FUNCTION reset_approval(p_id BIGINT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET approved_at = NULL
  WHERE id = p_id
    AND stage = 'ready_to_submit'
    AND approved_at IS NOT NULL;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'reset_approval: row % is not in a resettable state (not ready_to_submit or not currently approved)', p_id;
  END IF;
END;
$$;

-- Postgres GRANTs EXECUTE to PUBLIC by default on function creation -- without these REVOKEs,
-- the GRANTs to anon below are no-ops that leave every role able to call these functions.
REVOKE EXECUTE ON FUNCTION approve_application(BIGINT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION approve_application(BIGINT) TO anon;

REVOKE EXECUTE ON FUNCTION reset_approval(BIGINT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION reset_approval(BIGINT) TO anon;
