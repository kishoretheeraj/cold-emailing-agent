-- Signed approvals (spec 2026-10-08 §9.1).
--
-- approve_application is anon-callable and the anon key ships in the contact-manager's browser
-- bundle, so today anyone holding it can approve a row. Once a Beelink unit submits every
-- approved row on its own, that is enough to send an application. The contact-manager's submit
-- route (behind the operator login) now signs `approval:v1:<id>:<hash>:<signed_at_ms>` with
-- HMAC-SHA256 under APPROVAL_SIGNING_KEY, a server-only secret; the worker verifies it before
-- any browser opens (approval_signature.py). This RPC stores the signature, requires it to be
-- well-formed and its timestamp current, so an old signature (readable by anon) cannot be
-- replayed into a later approval. The database cannot check the HMAC itself: it never holds
-- the key.

ALTER TABLE job_applications
  ADD COLUMN IF NOT EXISTS approval_signature TEXT,
  ADD COLUMN IF NOT EXISTS approval_signed_at_ms BIGINT;

COMMENT ON COLUMN job_applications.approval_signature IS
  'Hex HMAC-SHA256 from the contact-manager submit route over approval:v1:<id>:<approved_revision_hash>:<approval_signed_at_ms>. Written only by approve_application; verified by the submit worker.';
COMMENT ON COLUMN job_applications.approval_signed_at_ms IS
  'Epoch milliseconds the submit route signed at. approve_application requires it within 2 minutes of now().';

REVOKE UPDATE (approval_signature, approval_signed_at_ms) ON job_applications FROM anon;
REVOKE INSERT (approval_signature, approval_signed_at_ms) ON job_applications FROM anon;
REVOKE UPDATE (approval_signature, approval_signed_at_ms) ON job_applications FROM authenticated;
REVOKE INSERT (approval_signature, approval_signed_at_ms) ON job_applications FROM authenticated;

-- A CREATE OR REPLACE with a new argument list would leave the unsigned 2-arg version callable.
DROP FUNCTION IF EXISTS approve_application(BIGINT, TEXT);

CREATE OR REPLACE FUNCTION approve_application(p_id BIGINT, p_revision_hash TEXT, p_signature TEXT, p_signed_at_ms BIGINT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF p_signature IS NULL OR p_signature !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'approve_application: a signature is required';
  END IF;
  IF p_signed_at_ms IS NULL OR abs(extract(epoch FROM now()) * 1000 - p_signed_at_ms) > 120000 THEN
    RAISE EXCEPTION 'approve_application: the signature timestamp is not current';
  END IF;

  UPDATE job_applications
  SET approved_at = now(),
      approved_revision_hash = preview_revision_hash,
      approval_signature = p_signature,
      approval_signed_at_ms = p_signed_at_ms,
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

REVOKE EXECUTE ON FUNCTION approve_application(BIGINT, TEXT, TEXT, BIGINT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION approve_application(BIGINT, TEXT, TEXT, BIGINT) TO anon;
