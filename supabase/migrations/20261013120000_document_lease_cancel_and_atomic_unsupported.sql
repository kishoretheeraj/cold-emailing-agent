-- Round 6 (Codex): a machine-wide Claude failure must not spend a row's preview budget, and marking a
-- row unsupported while holding its document lease must be one atomic step.

-- Ends a document-generation lease without counting the attempt: the claim's prepare_attempts increment
-- is refunded and the row returns to 'idle' with the reason, so quota/auth outages cannot exhaust
-- APPLY_PREPARE_MAX_ATTEMPTS. Per-row failures still use finish_document_generation(ok => false).
CREATE OR REPLACE FUNCTION cancel_document_generation(p_id BIGINT, p_lease UUID, p_reason TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET automation_status = 'idle',
      prepare_attempts = greatest(prepare_attempts - 1, 0),
      apply_blocked_reason = left(p_reason, 900),
      worker_lease_id = NULL,
      worker_heartbeat_at = NULL,
      updated_at = now()
  WHERE id = p_id
    AND worker_lease_id = p_lease
    AND automation_status = 'preparing'
    AND stage = 'saved';

  RETURN FOUND;
END;
$$;

REVOKE EXECUTE ON FUNCTION cancel_document_generation(BIGINT, UUID, TEXT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION cancel_document_generation(BIGINT, UUID, TEXT) TO anon;

-- Marks a row 'unsupported' in the same statement that ends its 'preparing' lease, so no other worker
-- can claim the row between the release and the mark. Approval columns are cleared with it.
CREATE OR REPLACE FUNCTION mark_unsupported_under_lease(p_id BIGINT, p_lease UUID, p_reason TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET automation_status = 'unsupported',
      apply_blocked_reason = left(p_reason, 900),
      approved_at = NULL,
      approved_revision_hash = NULL,
      worker_lease_id = NULL,
      worker_heartbeat_at = NULL,
      updated_at = now()
  WHERE id = p_id
    AND worker_lease_id = p_lease
    AND automation_status = 'preparing';

  RETURN FOUND;
END;
$$;

REVOKE EXECUTE ON FUNCTION mark_unsupported_under_lease(BIGINT, UUID, TEXT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION mark_unsupported_under_lease(BIGINT, UUID, TEXT) TO anon;
