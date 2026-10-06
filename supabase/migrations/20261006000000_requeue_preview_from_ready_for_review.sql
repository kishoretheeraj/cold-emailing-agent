-- requeue_preview also accepts a ready_for_review row ("Re-prepare" on an unapproved preview).
--
-- The first live preview pass (2026-10-06) put rows into ready_for_review with blank required
-- questions, and no RPC could move them back to idle to be prepared again by a fixed filler.
-- ready_for_review is unapproved by definition (approval moves it to 'approved'), and the reset
-- clears approval, the revision binding and the stored preview anyway, so it can never lead to a
-- submit of anything a human did not see. A held lease still blocks it. Every other status,
-- including approved/submitting/needs_confirmation/submitted, is still refused.
-- CREATE OR REPLACE with the same signature keeps the existing EXECUTE grants; restated below.

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
      updated_at = now()
  WHERE id = p_id
    AND automation_status IN ('needs_input', 'ready_for_review')
    AND worker_lease_id IS NULL;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'requeue_preview: row % is not awaiting input or review', p_id;
  END IF;
END;
$$;

REVOKE EXECUTE ON FUNCTION requeue_preview(BIGINT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION requeue_preview(BIGINT) TO anon;
