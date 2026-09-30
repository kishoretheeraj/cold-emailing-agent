-- Merge review 2026-09-28, finding 6: reset_approval only cleared approved_at, leaving a
-- separate anon-permitted UPDATE in the Next.js route to clear apply_blocked_reason. If that
-- second update failed, the row stayed visibly blocked forever -- a second "Try again" tap
-- would fail at this RPC's own guard (approved_at IS NOT NULL no longer holds after the first
-- call's partial success), so the advertised retry could never actually reach the cleanup step.
-- Fix: clear both columns in the one guarded UPDATE so the reset is atomic. CREATE OR REPLACE
-- on a SECURITY DEFINER function is safe to reapply against an already-shipped migration --
-- grants/search_path hardening carry over unchanged, only the function body changes.

CREATE OR REPLACE FUNCTION reset_approval(p_id BIGINT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET approved_at = NULL,
      apply_blocked_reason = NULL
  WHERE id = p_id
    AND stage = 'ready_to_submit'
    AND approved_at IS NOT NULL;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'reset_approval: row % is not in a resettable state (not ready_to_submit or not currently approved)', p_id;
  END IF;
END;
$$;
