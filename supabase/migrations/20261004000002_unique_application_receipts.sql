-- One email can establish at most one application, across monitor runs and workers.
-- Keep previous applied migrations immutable. Existing duplicate evidence deliberately makes
-- this migration fail for investigation instead of choosing a submitted row silently.
CREATE UNIQUE INDEX IF NOT EXISTS job_applications_receipt_message_id_unique
ON public.job_applications ((btrim(submission_evidence->>'message_id')))
WHERE nullif(btrim(submission_evidence->>'message_id'), '') IS NOT NULL;

CREATE OR REPLACE FUNCTION record_receipt_evidence(p_id BIGINT, p_evidence JSONB)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_constraint TEXT;
BEGIN
  IF jsonb_typeof(p_evidence->'message_id') IS DISTINCT FROM 'string'
     OR nullif(btrim(p_evidence->>'message_id'), '') IS NULL THEN
    RAISE EXCEPTION 'record_receipt_evidence: evidence must carry a nonempty message_id';
  END IF;

  UPDATE job_applications
  SET automation_status = 'submitted',
      stage = 'applied',
      applied_date = coalesce(applied_date, submit_attempted_at::date, current_date),
      source_channel = coalesce(source_channel, apply_preview->>'platform'),
      submission_evidence = p_evidence || jsonb_build_object(
        'message_id', btrim(p_evidence->>'message_id'), 'source', 'gmail_receipt'),
      apply_blocked_reason = NULL,
      updated_at = now()
  WHERE id = p_id
    AND automation_status = 'needs_confirmation'
    AND worker_lease_id IS NULL;

  RETURN FOUND;
EXCEPTION WHEN unique_violation THEN
  GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
  IF v_constraint = 'job_applications_receipt_message_id_unique' THEN
    RETURN false;
  END IF;
  RAISE;
END;
$$;

REVOKE EXECUTE ON FUNCTION record_receipt_evidence(BIGINT, JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION record_receipt_evidence(BIGINT, JSONB) TO anon;
