-- Lifecycle RPCs with server-enforced transitions (docs/superpowers/plans/2026-10-04-lifecycle-rpcs-and-submission-reconciliation.md, Task 1).
--
-- Closes the known limitation from 20261001000000: automation_status / worker_lease_id /
-- worker_heartbeat_at were anon-writable, so anyone holding the public key could rewrite the
-- execution lifecycle directly (e.g. flip a post-click row back to failed_retryable and open a
-- duplicate-application path). Every lifecycle write now goes through a SECURITY DEFINER RPC that
-- enforces the allowed transitions; the columns stay SELECT-able by anon. The lease id is
-- therefore a concurrency token only, never authorization.
--
-- GRANTS: 20260925000000 replaced the table-level UPDATE/INSERT grants on job_applications with
-- per-column grants for anon, so a column-level REVOKE from anon is effective. `authenticated`
-- was never converted: it may still hold table-level UPDATE/INSERT, against which a column
-- REVOKE is a silent no-op, so it gets the same revoke-then-regrant-by-column treatment.
-- The three new columns below are deliberately never granted to anon (receipt evidence and the
-- form signature must not be forgeable); they are only written by the RPCs.

-- ── New columns ────────────────────────────────────────────────────────────────

ALTER TABLE job_applications
  ADD COLUMN IF NOT EXISTS form_signature TEXT,
  ADD COLUMN IF NOT EXISTS submit_attempted_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS submission_evidence JSONB;

COMMENT ON COLUMN job_applications.form_signature IS
  'Hash of the application form structure (name/label/aria-label/placeholder, never id) captured at preview; submit compares it to detect form drift. NULL on rows previewed before this column existed (check is skipped with a warning).';
COMMENT ON COLUMN job_applications.submit_attempted_at IS
  'Set by renew_submission_lease immediately BEFORE the Submit click (submission intent); NULL again at each submitting claim. Non-NULL on a stale submitting row means the click may have landed.';
COMMENT ON COLUMN job_applications.submission_evidence IS
  'How a submission was established: {source: page_confirmation, at} from the page, or the Gmail receipt evidence (must carry message_id) from the reconciler.';

-- ── Revoke anon / authenticated direct lifecycle writes ────────────────────────

DO $$
DECLARE cols text;
BEGIN
  SELECT string_agg(quote_ident(column_name), ', ') INTO cols
  FROM information_schema.columns
  WHERE table_schema = 'public' AND table_name = 'job_applications'
    AND column_name NOT IN ('automation_status', 'worker_lease_id', 'worker_heartbeat_at');

  EXECUTE 'REVOKE UPDATE ON job_applications FROM authenticated';
  EXECUTE format('GRANT UPDATE (%s) ON job_applications TO authenticated', cols);
  EXECUTE 'REVOKE INSERT ON job_applications FROM authenticated';
  EXECUTE format('GRANT INSERT (%s) ON job_applications TO authenticated', cols);
END $$;

REVOKE UPDATE (automation_status, worker_lease_id, worker_heartbeat_at) ON job_applications FROM anon;
REVOKE INSERT (automation_status, worker_lease_id, worker_heartbeat_at) ON job_applications FROM anon;
REVOKE UPDATE (automation_status, worker_lease_id, worker_heartbeat_at) ON job_applications FROM authenticated;
REVOKE INSERT (automation_status, worker_lease_id, worker_heartbeat_at) ON job_applications FROM authenticated;

-- ── RPCs ───────────────────────────────────────────────────────────────────────

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

CREATE OR REPLACE FUNCTION heartbeat_application(p_id BIGINT, p_lease UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET worker_heartbeat_at = now(), updated_at = now()
  WHERE id = p_id AND worker_lease_id = p_lease;

  RETURN FOUND;
END;
$$;

-- Runs BEFORE the Submit click: records submission intent and re-validates lease + approval.
CREATE OR REPLACE FUNCTION renew_submission_lease(p_id BIGINT, p_lease UUID, p_revision_hash TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET worker_heartbeat_at = now(),
      submit_attempted_at = now(),
      updated_at = now()
  WHERE id = p_id
    AND worker_lease_id = p_lease
    AND automation_status = 'submitting'
    AND stage = 'ready_to_submit'
    AND p_revision_hash IS NOT NULL
    AND preview_revision_hash = p_revision_hash
    AND approved_revision_hash = p_revision_hash
    AND approved_at IS NOT NULL;

  RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION complete_preview(p_id BIGINT, p_lease UUID, p_preview JSONB, p_form_signature TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET automation_status = 'ready_for_review',
      apply_preview = p_preview,
      stage = 'ready_to_submit',
      form_signature = p_form_signature,
      apply_blocked_reason = NULL,
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

CREATE OR REPLACE FUNCTION release_application(p_id BIGINT, p_lease UUID, p_to TEXT, p_reason TEXT DEFAULT NULL)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_status TEXT;
  v_attempted BOOLEAN;
BEGIN
  SELECT automation_status, (submit_attempted_at IS NOT NULL) INTO v_status, v_attempted
  FROM job_applications
  WHERE id = p_id AND worker_lease_id = p_lease
  FOR UPDATE;

  IF NOT FOUND THEN
    RETURN false;
  END IF;

  IF v_status = 'preparing' THEN
    IF p_to NOT IN ('failed_retryable', 'needs_input', 'unsupported') THEN
      RAISE EXCEPTION 'release_application: preparing cannot be released to %', p_to;
    END IF;
  ELSIF v_status = 'submitting' THEN
    -- The click may have happened once submit_attempted_at IS NOT NULL. The lease id is SELECT-able by
    -- anon, so without this rule the public key could release a post-click row to a retryable
    -- state and open a duplicate-application path.
    IF v_attempted THEN
      IF p_to <> 'needs_confirmation' THEN
        RAISE EXCEPTION 'release_application: a submit was attempted; only needs_confirmation is allowed, not %', p_to;
      END IF;
    ELSIF p_to NOT IN ('approved', 'failed_retryable', 'needs_confirmation', 'needs_input') THEN
      RAISE EXCEPTION 'release_application: submitting cannot be released to %', p_to;
    END IF;
  ELSE
    RAISE EXCEPTION 'release_application: status % cannot be released', v_status;
  END IF;

  UPDATE job_applications
  SET automation_status = p_to,
      apply_blocked_reason = p_reason,
      approved_at = CASE WHEN p_to = 'needs_input' THEN NULL ELSE approved_at END,
      approved_revision_hash = CASE WHEN p_to = 'needs_input' THEN NULL ELSE approved_revision_hash END,
      worker_lease_id = NULL,
      worker_heartbeat_at = NULL,
      updated_at = now()
  WHERE id = p_id AND worker_lease_id = p_lease;

  RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION record_submission(p_id BIGINT, p_lease UUID, p_source_channel TEXT, p_applied_date DATE)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET automation_status = 'submitted',
      stage = 'applied',
      source_channel = p_source_channel,
      applied_date = p_applied_date,
      apply_blocked_reason = NULL,
      submission_evidence = jsonb_build_object('source', 'page_confirmation', 'at', now()),
      worker_lease_id = NULL,
      worker_heartbeat_at = NULL,
      updated_at = now()
  WHERE id = p_id
    AND worker_lease_id = p_lease
    AND automation_status = 'submitting';

  RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION mark_application_unsupported(p_id BIGINT, p_reason TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET automation_status = 'unsupported',
      apply_blocked_reason = p_reason,
      approved_at = NULL,
      approved_revision_hash = NULL,
      updated_at = now()
  WHERE id = p_id
    AND worker_lease_id IS NULL
    AND automation_status NOT IN ('preparing', 'submitting', 'submitted', 'needs_confirmation');

  RETURN FOUND;
END;
$$;

-- The floor means a caller passing 0 cannot steal a live lease: the submit workflow's timeout is
-- 15 minutes, so nothing under 30 minutes of silence is ever stale.
CREATE OR REPLACE FUNCTION recover_stale_leases(p_stale_seconds INT)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_count INT;
BEGIN
  UPDATE job_applications
  SET automation_status = CASE
        WHEN automation_status = 'submitting' AND submit_attempted_at IS NOT NULL THEN 'needs_confirmation'
        ELSE 'failed_retryable'
      END,
      apply_blocked_reason = 'worker lease expired while ' || automation_status || '; recovered as ' ||
        CASE
          WHEN automation_status = 'submitting' AND submit_attempted_at IS NOT NULL THEN 'needs_confirmation'
          ELSE 'failed_retryable'
        END,
      worker_lease_id = NULL,
      worker_heartbeat_at = NULL,
      updated_at = now()
  WHERE worker_lease_id IS NOT NULL
    AND worker_heartbeat_at < now() - make_interval(secs => GREATEST(p_stale_seconds, 1800));

  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count;
END;
$$;

CREATE OR REPLACE FUNCTION record_receipt_evidence(p_id BIGINT, p_evidence JSONB)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF p_evidence IS NULL OR NOT (p_evidence ? 'message_id') THEN
    RAISE EXCEPTION 'record_receipt_evidence: evidence must carry a message_id';
  END IF;

  UPDATE job_applications
  SET automation_status = 'submitted',
      stage = 'applied',
      applied_date = coalesce(applied_date, submit_attempted_at::date, current_date),
      source_channel = coalesce(source_channel, apply_preview->>'platform'),
      submission_evidence = p_evidence,
      apply_blocked_reason = NULL,
      updated_at = now()
  WHERE id = p_id
    AND automation_status = 'needs_confirmation'
    AND worker_lease_id IS NULL;

  RETURN FOUND;
END;
$$;

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
      approved_at = NULL,
      approved_revision_hash = NULL,
      form_signature = NULL,
      updated_at = now()
  WHERE id = p_id
    AND automation_status = 'needs_input'
    AND worker_lease_id IS NULL;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'requeue_preview: row % is not awaiting input', p_id;
  END IF;
END;
$$;

-- ── Execute grants (Postgres grants EXECUTE to PUBLIC by default) ──────────────

REVOKE EXECUTE ON FUNCTION claim_application(BIGINT, UUID, TEXT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION claim_application(BIGINT, UUID, TEXT) TO anon;

REVOKE EXECUTE ON FUNCTION heartbeat_application(BIGINT, UUID) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION heartbeat_application(BIGINT, UUID) TO anon;

REVOKE EXECUTE ON FUNCTION renew_submission_lease(BIGINT, UUID, TEXT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION renew_submission_lease(BIGINT, UUID, TEXT) TO anon;

REVOKE EXECUTE ON FUNCTION complete_preview(BIGINT, UUID, JSONB, TEXT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION complete_preview(BIGINT, UUID, JSONB, TEXT) TO anon;

REVOKE EXECUTE ON FUNCTION release_application(BIGINT, UUID, TEXT, TEXT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION release_application(BIGINT, UUID, TEXT, TEXT) TO anon;

REVOKE EXECUTE ON FUNCTION record_submission(BIGINT, UUID, TEXT, DATE) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION record_submission(BIGINT, UUID, TEXT, DATE) TO anon;

REVOKE EXECUTE ON FUNCTION mark_application_unsupported(BIGINT, TEXT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION mark_application_unsupported(BIGINT, TEXT) TO anon;

REVOKE EXECUTE ON FUNCTION recover_stale_leases(INT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION recover_stale_leases(INT) TO anon;

REVOKE EXECUTE ON FUNCTION record_receipt_evidence(BIGINT, JSONB) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION record_receipt_evidence(BIGINT, JSONB) TO anon;

REVOKE EXECUTE ON FUNCTION requeue_preview(BIGINT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION requeue_preview(BIGINT) TO anon;
