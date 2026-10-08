-- First-ten-applications foundation (spec 2026-10-08 §4.2, §7, §8).
--
-- 1. application_runs: append-only record of every prepare/submit/bake-off run, written only
--    through log_application_run. It is how the cloud (apply_status.yml) sees Beelink results.
-- 2. job_applications.takeover: a worker's request for a human (CAPTCHA, SMS code, login),
--    written only through request_takeover / takeover_continue / clear_takeover.
-- 3. application-evidence: private Storage bucket for filled-form and confirmation screenshots.
-- 4. record_submission gains p_evidence (confirmation URL/text/screenshot path).
--
-- Every RPC follows 20261004000000: SECURITY DEFINER, pinned search_path, EXECUTE revoked from
-- PUBLIC and granted to anon (the only role the app uses).

-- ── 1. application_runs ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS application_runs (
  id BIGSERIAL PRIMARY KEY,
  run_id UUID NOT NULL,
  application_id BIGINT REFERENCES job_applications(id) ON DELETE SET NULL,
  kind TEXT NOT NULL CHECK (kind IN ('prepare', 'submit', 'bakeoff', 'dryrun')),
  adapter TEXT NOT NULL,
  host TEXT,
  started_at TIMESTAMPTZ NOT NULL,
  ended_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  outcome TEXT NOT NULL CHECK (outcome IN ('ready', 'needs_input', 'takeover_timeout', 'submitted',
                                           'needs_confirmation', 'failed_retryable', 'failed_terminal',
                                           'unsupported', 'skipped', 'scored')),
  stop_reason TEXT,
  fields_filled INT,
  fields_missing INT,
  takeovers INT NOT NULL DEFAULT 0,
  model_calls INT NOT NULL DEFAULT 0,
  error_class TEXT,
  details JSONB
);

CREATE INDEX IF NOT EXISTS idx_application_runs_application ON application_runs (application_id, ended_at DESC);
CREATE INDEX IF NOT EXISTS idx_application_runs_ended ON application_runs (ended_at DESC);

COMMENT ON TABLE application_runs IS
  'Append-only run log for the apply workers and the adapter bake-off. Written only by log_application_run; '
  'readable by the API roles so the cloud can see Beelink outcomes. Never holds answers, credentials or page HTML.';

-- Supabase's default privileges grant ALL on new public tables to the API roles.
REVOKE ALL ON application_runs FROM anon, authenticated;
GRANT SELECT ON application_runs TO anon, authenticated;
REVOKE ALL ON SEQUENCE application_runs_id_seq FROM anon, authenticated;

CREATE OR REPLACE FUNCTION log_application_run(p_run_id UUID, p_application_id BIGINT, p_kind TEXT, p_adapter TEXT, p_host TEXT, p_started_at TIMESTAMPTZ, p_outcome TEXT, p_stop_reason TEXT DEFAULT NULL, p_fields_filled INT DEFAULT NULL, p_fields_missing INT DEFAULT NULL, p_takeovers INT DEFAULT 0, p_model_calls INT DEFAULT 0, p_error_class TEXT DEFAULT NULL, p_details JSONB DEFAULT NULL)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_id BIGINT;
BEGIN
  IF p_run_id IS NULL OR p_adapter IS NULL OR p_started_at IS NULL THEN
    RAISE EXCEPTION 'log_application_run: run_id, adapter and started_at are required';
  END IF;
  IF length(p_adapter) > 64 OR length(coalesce(p_host, '')) > 255
     OR length(p_stop_reason) > 500 OR length(p_error_class) > 128 THEN
    RAISE EXCEPTION 'log_application_run: a text argument is too long';
  END IF;
  IF p_details IS NOT NULL AND (jsonb_typeof(p_details) <> 'object' OR length(p_details::text) > 16384) THEN
    RAISE EXCEPTION 'log_application_run: details must be a JSON object under 16 KB';
  END IF;
  IF p_started_at > now() + interval '5 minutes' THEN
    RAISE EXCEPTION 'log_application_run: started_at is in the future';
  END IF;
  IF p_takeovers IS NULL OR p_model_calls IS NULL
     OR least(coalesce(p_fields_filled, 0), coalesce(p_fields_missing, 0), p_takeovers, p_model_calls) < 0 THEN
    RAISE EXCEPTION 'log_application_run: counts must be non-negative';
  END IF;

  INSERT INTO application_runs (run_id, application_id, kind, adapter, host, started_at, outcome,
                                stop_reason, fields_filled, fields_missing, takeovers, model_calls,
                                error_class, details)
  VALUES (p_run_id, p_application_id, p_kind, p_adapter, p_host, p_started_at, p_outcome,
          p_stop_reason, p_fields_filled, p_fields_missing, p_takeovers, p_model_calls,
          p_error_class, p_details)
  RETURNING id INTO v_id;

  RETURN v_id;
END;
$$;

-- ── 2. takeover ───────────────────────────────────────────────────────────────

ALTER TABLE job_applications
  ADD COLUMN IF NOT EXISTS takeover JSONB;

COMMENT ON COLUMN job_applications.takeover IS
  'Open request for a human: {kind, reason, lease, requested_at, continue_at}. Meaningful only while '
  'takeover->>''lease'' equals worker_lease_id; a request from an earlier lease is stale. RPC-only.';

-- 20260925000000 granted anon per-column privileges computed once, so a new column has none;
-- these revokes make the intent explicit and survive any later re-grant script.
REVOKE UPDATE (takeover) ON job_applications FROM anon;
REVOKE INSERT (takeover) ON job_applications FROM anon;
REVOKE UPDATE (takeover) ON job_applications FROM authenticated;
REVOKE INSERT (takeover) ON job_applications FROM authenticated;

CREATE OR REPLACE FUNCTION request_takeover(p_id BIGINT, p_lease UUID, p_kind TEXT, p_reason TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF p_kind IS NULL OR p_kind NOT IN ('captcha', 'sms_code', 'email_verification', 'login', 'unrecognized_page', 'other') THEN
    RAISE EXCEPTION 'request_takeover: unsupported kind %', p_kind;
  END IF;
  IF length(p_reason) > 500 THEN
    RAISE EXCEPTION 'request_takeover: reason is too long';
  END IF;

  UPDATE job_applications
  SET takeover = jsonb_build_object('kind', p_kind, 'reason', p_reason, 'lease', p_lease,
                                    'requested_at', now(), 'continue_at', NULL),
      worker_heartbeat_at = now(),
      updated_at = now()
  WHERE id = p_id
    AND worker_lease_id = p_lease
    AND automation_status IN ('preparing', 'submitting');

  RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION takeover_continue(p_id BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET takeover = takeover || jsonb_build_object('continue_at', now()),
      updated_at = now()
  WHERE id = p_id
    AND worker_lease_id IS NOT NULL
    AND takeover IS NOT NULL
    AND takeover->>'lease' = worker_lease_id::text
    AND takeover->>'continue_at' IS NULL;

  RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION clear_takeover(p_id BIGINT, p_lease UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET takeover = NULL,
      updated_at = now()
  WHERE id = p_id
    AND worker_lease_id = p_lease;

  RETURN FOUND;
END;
$$;

-- ── 3. evidence bucket ────────────────────────────────────────────────────────

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('application-evidence', 'application-evidence', false, 5242880,
        ARRAY['image/png', 'image/jpeg', 'text/plain', 'application/json'])
ON CONFLICT (id) DO NOTHING;

-- Insert only: workers upload under fresh per-run paths. No read policy: evidence is shown only
-- through signed URLs minted by an authenticated server route (spec §9.1), never to the anon key.
DROP POLICY IF EXISTS "application-evidence anon insert" ON storage.objects;
CREATE POLICY "application-evidence anon insert" ON storage.objects
  FOR INSERT TO anon
  WITH CHECK (bucket_id = 'application-evidence');

-- ── 4. record_submission with evidence ────────────────────────────────────────

-- A CREATE OR REPLACE with a new argument list would add an overload beside the old one.
DROP FUNCTION IF EXISTS record_submission(BIGINT, UUID, TEXT, DATE);

CREATE OR REPLACE FUNCTION record_submission(p_id BIGINT, p_lease UUID, p_source_channel TEXT, p_applied_date DATE, p_evidence JSONB DEFAULT NULL)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF p_evidence IS NOT NULL AND (jsonb_typeof(p_evidence) <> 'object' OR length(p_evidence::text) > 16384) THEN
    RAISE EXCEPTION 'record_submission: evidence must be a JSON object under 16 KB';
  END IF;

  UPDATE job_applications
  SET automation_status = 'submitted',
      stage = 'applied',
      source_channel = p_source_channel,
      applied_date = p_applied_date,
      apply_blocked_reason = NULL,
      -- server-owned keys win; message_id is reserved for Gmail receipt proof
      submission_evidence = coalesce(p_evidence, '{}'::jsonb) - 'source' - 'at' - 'message_id'
                            || jsonb_build_object('source', 'page_confirmation', 'at', now()),
      takeover = NULL,
      worker_lease_id = NULL,
      worker_heartbeat_at = NULL,
      updated_at = now()
  WHERE id = p_id
    AND worker_lease_id = p_lease
    AND automation_status = 'submitting';

  RETURN FOUND;
END;
$$;

-- ── Grants ────────────────────────────────────────────────────────────────────

REVOKE EXECUTE ON FUNCTION log_application_run(UUID, BIGINT, TEXT, TEXT, TEXT, TIMESTAMPTZ, TEXT, TEXT, INT, INT, INT, INT, TEXT, JSONB) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION log_application_run(UUID, BIGINT, TEXT, TEXT, TEXT, TIMESTAMPTZ, TEXT, TEXT, INT, INT, INT, INT, TEXT, JSONB) TO anon;

REVOKE EXECUTE ON FUNCTION request_takeover(BIGINT, UUID, TEXT, TEXT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION request_takeover(BIGINT, UUID, TEXT, TEXT) TO anon;

REVOKE EXECUTE ON FUNCTION takeover_continue(BIGINT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION takeover_continue(BIGINT) TO anon;

REVOKE EXECUTE ON FUNCTION clear_takeover(BIGINT, UUID) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION clear_takeover(BIGINT, UUID) TO anon;

REVOKE EXECUTE ON FUNCTION record_submission(BIGINT, UUID, TEXT, DATE, JSONB) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION record_submission(BIGINT, UUID, TEXT, DATE, JSONB) TO anon;
