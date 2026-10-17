-- Round 3 (Codex review of the 24/7 port).
--   1. record_dashboard_decision: audit row + withdrawal in ONE transaction, compare-and-swap on the
--      prior decision (P1-4, P2-5).
--   2. get_dashboard_decisions_page: latest decision per application, keyset-paginated (P2-6).
--   3. finish_document_generation: ends a document-only lease, so rows with no resume can be claimed
--      and tailored before any preview exists (P1-2).
-- A dashboard approval is intent only. Nothing here sets approved_at or approved_revision_hash; that
-- stays exclusively with the signed approve_application RPC (20261008000001).

CREATE OR REPLACE FUNCTION record_dashboard_decision(
  p_run_id UUID, p_application_id BIGINT, p_decision TEXT, p_reason TEXT DEFAULT NULL,
  p_host TEXT DEFAULT 'dashboard-api')
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_stage TEXT;
  v_status TEXT;
  v_approved TIMESTAMPTZ;
  v_prior TEXT;
  v_prior_id BIGINT;
  v_id BIGINT;
BEGIN
  IF p_decision NOT IN ('approved', 'rejected') THEN
    RAISE EXCEPTION 'record_dashboard_decision: unknown decision %', p_decision;
  END IF;
  IF length(coalesce(p_reason, '')) > 500 THEN
    RAISE EXCEPTION 'record_dashboard_decision: reason is too long';
  END IF;

  -- The row lock serializes concurrent decisions on one application.
  SELECT stage, automation_status, approved_at INTO v_stage, v_status, v_approved
  FROM job_applications WHERE id = p_application_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'dashboard_decision_conflict: application % not found', p_application_id;
  END IF;

  SELECT id, details->>'decision' INTO v_prior_id, v_prior
  FROM application_runs
  WHERE application_id = p_application_id AND adapter = 'dashboard' AND details->>'decision' IS NOT NULL
  ORDER BY ended_at DESC, id DESC LIMIT 1;
  IF v_prior IS NOT NULL THEN
    IF v_prior = p_decision THEN
      RETURN v_prior_id;  -- a retry of a decision that already committed
    END IF;
    RAISE EXCEPTION 'dashboard_decision_conflict: application % was already %', p_application_id, v_prior;
  END IF;

  IF v_stage <> 'ready_to_submit' OR v_status <> 'ready_for_review' OR v_approved IS NOT NULL THEN
    RAISE EXCEPTION 'dashboard_decision_conflict: application % is not awaiting approval', p_application_id;
  END IF;

  INSERT INTO application_runs (run_id, application_id, kind, adapter, host, started_at, outcome,
                                stop_reason, details)
  VALUES (p_run_id, p_application_id, 'submit', 'dashboard', p_host, now(), 'skipped',
          left(p_decision || ' via dashboard' || coalesce(': ' || p_reason, ''), 500),
          jsonb_build_object('decision', p_decision, 'reason', p_reason))
  RETURNING id INTO v_id;

  IF p_decision = 'rejected' THEN
    UPDATE job_applications SET stage = 'withdrawn', updated_at = now() WHERE id = p_application_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'record_dashboard_decision: withdrawal matched no row for %', p_application_id;
    END IF;
  END IF;

  RETURN v_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION record_dashboard_decision(UUID, BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION record_dashboard_decision(UUID, BIGINT, TEXT, TEXT, TEXT) TO anon;

-- Latest decision per application with application_id > p_after, ascending, at most 500 per call.
CREATE OR REPLACE FUNCTION get_dashboard_decisions_page(p_after BIGINT DEFAULT 0, p_limit INT DEFAULT 500)
RETURNS TABLE (application_id BIGINT, decision TEXT, reason TEXT)
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
  SELECT DISTINCT ON (r.application_id) r.application_id, r.details->>'decision', r.details->>'reason'
  FROM application_runs r
  WHERE r.adapter = 'dashboard'
    AND r.application_id > p_after
    AND r.details->>'decision' IN ('approved', 'rejected')
  ORDER BY r.application_id, r.ended_at DESC, r.id DESC
  LIMIT least(greatest(p_limit, 1), 500);
$$;

REVOKE EXECUTE ON FUNCTION get_dashboard_decisions_page(BIGINT, INT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION get_dashboard_decisions_page(BIGINT, INT) TO anon;

-- Ends a document-generation lease taken with claim_application(..., 'preparing'). Success returns the
-- row to 'idle' and refunds the attempt the claim counted (the preview's own claim counts again);
-- failure parks it in 'failed_retryable' with the reason, keeping the attempt so retries stay bounded.
CREATE OR REPLACE FUNCTION finish_document_generation(
  p_id BIGINT, p_lease UUID, p_ok BOOLEAN, p_reason TEXT DEFAULT NULL)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET automation_status = CASE WHEN p_ok THEN 'idle' ELSE 'failed_retryable' END,
      prepare_attempts = CASE WHEN p_ok THEN greatest(prepare_attempts - 1, 0) ELSE prepare_attempts END,
      apply_blocked_reason = CASE WHEN p_ok THEN NULL ELSE left(p_reason, 900) END,
      worker_lease_id = NULL,
      worker_heartbeat_at = NULL,
      updated_at = now()
  WHERE id = p_id
    AND worker_lease_id = p_lease
    AND automation_status = 'preparing'
    AND stage = 'saved'
    AND (NOT p_ok OR resume_file_ref IS NOT NULL);

  RETURN FOUND;
END;
$$;

REVOKE EXECUTE ON FUNCTION finish_document_generation(BIGINT, UUID, BOOLEAN, TEXT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION finish_document_generation(BIGINT, UUID, BOOLEAN, TEXT) TO anon;
