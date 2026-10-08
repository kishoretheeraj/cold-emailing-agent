-- Fifty a day (spec docs/superpowers/specs/2026-10-08-fifty-a-day-design.md §3.1, §3.3, §3.5).
--
-- 1. Job identity. job_key is the canonical key job_identity.py derives from any URL spelling of a
--    job (greenhouse:<id>, lever:<uuid>, ashby:<uuid>, workday:<tenant>:<site>:<req>, url:<canonical>).
--    A unique partial index makes "same job, second source" an insert conflict instead of a second
--    row; before it, ten spellings of four jobs made ten rows. Existing rows keep job_key NULL until
--    job_sourcing.py backfills them in Python (the canonicalization lives in one place, not in SQL).
--    company_key/title_key/location/posted_at/jd_fingerprint feed cross-source and repost dedup.
-- 2. prepare_attempts counts preview claims. The preview queue skips a row after
--    APPLY_PREPARE_MAX_ATTEMPTS and backs off between attempts, so one row that always fails can no
--    longer take a slot on every run. Only claim_application (increment) and requeue_preview
--    (reset) write it; no API role is granted the column.
-- 3. pick_attempts counts failed fit-judge calls, so an unparseable answer is retried instead of
--    being recorded as "no", and is not retried forever either.
-- 4. job_boards: the company boards job_sourcing.py sweeps, learned from the Simplify feed and
--    probing, with failure counting so a dead board stops costing requests.
--
-- New job_applications columns get explicit anon/authenticated grants: 20260925000000 granted a
-- fixed column list, so a later column has no API privilege until it is granted here.

ALTER TABLE job_applications
  ADD COLUMN IF NOT EXISTS job_key TEXT,
  ADD COLUMN IF NOT EXISTS platform TEXT,
  ADD COLUMN IF NOT EXISTS company_key TEXT,
  ADD COLUMN IF NOT EXISTS title_key TEXT,
  ADD COLUMN IF NOT EXISTS location TEXT,
  ADD COLUMN IF NOT EXISTS posted_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS jd_fingerprint TEXT,
  ADD COLUMN IF NOT EXISTS prepare_attempts INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS pick_attempts INTEGER NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS idx_job_applications_job_key_unique
  ON job_applications (job_key) WHERE job_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_job_applications_company_title
  ON job_applications (company_key, title_key);
CREATE INDEX IF NOT EXISTS idx_job_applications_queue
  ON job_applications (stage, automation_status, pick_verdict);

GRANT INSERT (job_key, platform, company_key, title_key, location, posted_at, jd_fingerprint, pick_attempts)
  ON job_applications TO anon, authenticated;
GRANT UPDATE (job_key, platform, company_key, title_key, location, posted_at, jd_fingerprint, pick_attempts)
  ON job_applications TO anon, authenticated;
GRANT SELECT (job_key, platform, company_key, title_key, location, posted_at, jd_fingerprint,
              prepare_attempts, pick_attempts)
  ON job_applications TO anon, authenticated;

-- Same body as 20261004000000's claim_application plus the attempt counter on a preview claim.
CREATE OR REPLACE FUNCTION claim_application(p_id BIGINT, p_lease UUID, p_to TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF p_to = 'preparing' THEN
    UPDATE job_applications
    SET automation_status = 'preparing',
        worker_lease_id = p_lease,
        worker_heartbeat_at = now(),
        prepare_attempts = prepare_attempts + 1,
        updated_at = now()
    WHERE id = p_id
      AND automation_status IN ('idle', 'failed_retryable')
      AND stage = 'saved'
      AND worker_lease_id IS NULL;
  ELSIF p_to = 'submitting' THEN
    -- submit_attempted_at is cleared so a prior attempt's value cannot leak into this attempt
    UPDATE job_applications
    SET automation_status = 'submitting',
        worker_lease_id = p_lease,
        worker_heartbeat_at = now(),
        submit_attempted_at = NULL,
        updated_at = now()
    WHERE id = p_id
      AND automation_status = 'approved'
      AND stage = 'ready_to_submit'
      AND approved_at IS NOT NULL
      AND approved_revision_hash = preview_revision_hash
      AND worker_lease_id IS NULL;
  ELSE
    RAISE EXCEPTION 'claim_application: unsupported target status %', p_to;
  END IF;

  RETURN FOUND;
END;
$$;

REVOKE EXECUTE ON FUNCTION claim_application(BIGINT, UUID, TEXT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION claim_application(BIGINT, UUID, TEXT) TO anon;

-- requeue_preview also accepts failed_retryable ("Prepare again" after the attempts ran out) and
-- resets the attempt counter. Every in-flight or post-approval status is still refused.
CREATE OR REPLACE FUNCTION requeue_preview(p_id BIGINT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET automation_status = 'idle',
      stage = 'saved',
      apply_blocked_reason = NULL,
      apply_preview = NULL,
      approved_at = NULL,
      approved_revision_hash = NULL,
      form_signature = NULL,
      prepare_attempts = 0,
      updated_at = now()
  WHERE id = p_id
    AND automation_status IN ('needs_input', 'ready_for_review', 'failed_retryable')
    AND worker_lease_id IS NULL;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'requeue_preview: row % is not awaiting input or review', p_id;
  END IF;
END;
$$;

REVOKE EXECUTE ON FUNCTION requeue_preview(BIGINT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION requeue_preview(BIGINT) TO anon;

CREATE TABLE IF NOT EXISTS job_boards (
  id BIGSERIAL PRIMARY KEY,
  platform TEXT NOT NULL CHECK (platform IN ('greenhouse', 'lever', 'ashby', 'workday')),
  -- greenhouse/lever/ashby: the board slug; workday: "<tenant host>/<site>".
  board TEXT NOT NULL CHECK (length(board) BETWEEN 1 AND 300),
  company TEXT,
  source TEXT,
  enabled BOOLEAN NOT NULL DEFAULT true,
  last_scanned_at TIMESTAMPTZ,
  last_job_count INTEGER,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  dead_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (platform, board)
);

COMMENT ON TABLE job_boards IS
  'Company job boards job_sourcing.py sweeps (spec 2026-10-08 fifty-a-day §3.3). dead_at is set after 3 consecutive failures.';

-- Supabase's default privileges grant ALL on new tables; sourcing needs read, insert and update only.
REVOKE ALL ON job_boards FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON job_boards TO anon, authenticated;
GRANT USAGE ON SEQUENCE job_boards_id_seq TO anon, authenticated;

-- The API roles still held table-level DELETE and TRUNCATE on job_applications from Supabase's
-- bootstrap grants (20260925000000 narrowed only INSERT/UPDATE). Nothing deletes application rows,
-- and a delete is not harmless: removing a submitted row frees its job_key, so the same job could
-- be sourced and applied to again. Rows leave the pipeline through stage 'withdrawn' instead.
REVOKE DELETE, TRUNCATE, TRIGGER ON job_applications FROM anon, authenticated;
