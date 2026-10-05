-- Functional dry-run for 20261004000000_lifecycle_rpcs_form_signature_receipts.sql.
-- The controller prepends the migration's DDL, runs this whole script in one session, and the
-- script ROLLBACKs, so nothing persists. This file deliberately does NOT contain the migration.
-- Any failure raises 'DRYRUN FAIL: ...'; success prints DRYRUN OK.

BEGIN;

-- ── Helpers (pg_temp, rolled back with the transaction) ────────────────────────

CREATE FUNCTION pg_temp.did() RETURNS BIGINT LANGUAGE sql AS $$ SELECT current_setting('dry.id')::bigint $$;

CREATE FUNCTION pg_temp.dst() RETURNS TEXT LANGUAGE sql AS
$$ SELECT automation_status FROM job_applications WHERE id = pg_temp.did() $$;

CREATE FUNCTION pg_temp.expect_eq(actual TEXT, expected TEXT, label TEXT) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF actual IS DISTINCT FROM expected THEN
    RAISE EXCEPTION 'DRYRUN FAIL: % (expected %, got %)', label, expected, actual;
  END IF;
END;
$$;

CREATE FUNCTION pg_temp.expect_error(q TEXT, label TEXT) RETURNS void LANGUAGE plpgsql AS $$
DECLARE v_raised BOOLEAN := false;
BEGIN
  BEGIN
    EXECUTE q;
  EXCEPTION WHEN others THEN
    v_raised := true;
  END;
  IF NOT v_raised THEN
    RAISE EXCEPTION 'DRYRUN FAIL: expected an error but none was raised: %', label;
  END IF;
END;
$$;

-- Puts the row into a known state directly (as the migration owner, bypassing the RPCs).
CREATE FUNCTION pg_temp.dreset(p_status TEXT, p_stage TEXT, p_lease UUID DEFAULT NULL,
                               p_hb TIMESTAMPTZ DEFAULT NULL, p_attempted TIMESTAMPTZ DEFAULT NULL)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  UPDATE job_applications
  SET apply_preview = coalesce(apply_preview, '{"platform":"greenhouse","dry":true}'::jsonb),
      stage = p_stage,
      automation_status = p_status,
      worker_lease_id = p_lease,
      worker_heartbeat_at = p_hb,
      submit_attempted_at = p_attempted,
      approved_at = NULL,
      approved_revision_hash = NULL,
      apply_blocked_reason = NULL,
      applied_date = NULL,
      source_channel = NULL,
      submission_evidence = NULL
  WHERE id = pg_temp.did();

  IF p_status IN ('approved', 'submitting') THEN
    -- second statement: the trigger has already computed preview_revision_hash by now
    UPDATE job_applications
    SET approved_at = now(), approved_revision_hash = preview_revision_hash
    WHERE id = pg_temp.did();
  END IF;
END;
$$;

DO $$
DECLARE v BIGINT;
BEGIN
  INSERT INTO job_applications (company, role, stage) VALUES ('DRYRUN Co', 'DRYRUN Role', 'saved') RETURNING id INTO v;
  PERFORM set_config('dry.id', v::text, true);
END $$;

-- ── claim / heartbeat / complete_preview ───────────────────────────────────────

SELECT pg_temp.expect_eq(pg_temp.dst(), 'idle', 'new row starts idle');
SELECT pg_temp.expect_eq(claim_application(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'preparing')::text, 'true', 'claim preparing from idle');
SELECT pg_temp.expect_eq(claim_application(pg_temp.did(), '22222222-2222-2222-2222-222222222222'::uuid, 'preparing')::text, 'false', 'second claim loses');
SELECT pg_temp.expect_eq(heartbeat_application(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid)::text, 'true', 'heartbeat with lease');
SELECT pg_temp.expect_eq(heartbeat_application(pg_temp.did(), '22222222-2222-2222-2222-222222222222'::uuid)::text, 'false', 'heartbeat with wrong lease');
SELECT pg_temp.expect_error(format('SELECT claim_application(%s, %L::uuid, %L)', pg_temp.did(), '33333333-3333-3333-3333-333333333333', 'approved'), 'claim with unsupported target raises');
SELECT pg_temp.expect_eq(complete_preview(pg_temp.did(), '22222222-2222-2222-2222-222222222222'::uuid, '{"platform":"greenhouse"}'::jsonb, 'sig1')::text, 'false', 'complete_preview wrong lease');
SELECT pg_temp.expect_eq(complete_preview(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, '{"platform":"greenhouse"}'::jsonb, 'sig1')::text, 'true', 'complete_preview');
SELECT pg_temp.expect_eq(pg_temp.dst(), 'ready_for_review', 'status after preview');
SELECT pg_temp.expect_eq((SELECT stage || '|' || form_signature || '|' || (worker_lease_id IS NULL)::text FROM job_applications WHERE id = pg_temp.did()), 'ready_to_submit|sig1|true', 'preview side effects');

-- a submitting claim needs approval + hash binding
SELECT pg_temp.expect_eq(claim_application(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'submitting')::text, 'false', 'submitting claim refused before approval');
SELECT approve_application(pg_temp.did(), (SELECT preview_revision_hash FROM job_applications WHERE id = pg_temp.did()));
SELECT pg_temp.expect_eq(pg_temp.dst(), 'approved', 'approved');

-- ── submitting claim, renew, post-click release rules ──────────────────────────

UPDATE job_applications SET submit_attempted_at = now() - interval '1 day' WHERE id = pg_temp.did();
SELECT pg_temp.expect_eq(claim_application(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'submitting')::text, 'true', 'claim submitting');
SELECT pg_temp.expect_eq((SELECT (submit_attempted_at IS NULL)::text FROM job_applications WHERE id = pg_temp.did()), 'true', 'submitting claim clears submit_attempted_at');
SELECT pg_temp.expect_eq(renew_submission_lease(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'wrong-hash')::text, 'false', 'renew with wrong hash');
SELECT pg_temp.expect_eq(renew_submission_lease(pg_temp.did(), '22222222-2222-2222-2222-222222222222'::uuid, (SELECT preview_revision_hash FROM job_applications WHERE id = pg_temp.did()))::text, 'false', 'renew with wrong lease');
SELECT pg_temp.expect_eq(renew_submission_lease(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, (SELECT preview_revision_hash FROM job_applications WHERE id = pg_temp.did()))::text, 'true', 'renew');
SELECT pg_temp.expect_eq((SELECT (submit_attempted_at IS NOT NULL)::text FROM job_applications WHERE id = pg_temp.did()), 'true', 'renew records submit_attempted_at');
SELECT pg_temp.expect_error(format('SELECT release_application(%s, %L::uuid, %L)', pg_temp.did(), '11111111-1111-1111-1111-111111111111', 'failed_retryable'), 'post-click release to failed_retryable raises');
SELECT pg_temp.expect_error(format('SELECT release_application(%s, %L::uuid, %L)', pg_temp.did(), '11111111-1111-1111-1111-111111111111', 'approved'), 'post-click release to approved raises');
SELECT pg_temp.expect_error(format('SELECT release_application(%s, %L::uuid, %L)', pg_temp.did(), '11111111-1111-1111-1111-111111111111', 'needs_input'), 'post-click release to needs_input raises');
SELECT pg_temp.expect_eq(pg_temp.dst(), 'submitting', 'failed releases left the row submitting');
SELECT pg_temp.expect_eq(release_application(pg_temp.did(), '22222222-2222-2222-2222-222222222222'::uuid, 'needs_confirmation', 'x')::text, 'false', 'release with wrong lease is a plain false');
SELECT pg_temp.expect_eq(release_application(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'needs_confirmation', 'clicked submit; unconfirmed')::text, 'true', 'post-click release to needs_confirmation');
SELECT pg_temp.expect_eq(pg_temp.dst(), 'needs_confirmation', 'status needs_confirmation');
SELECT pg_temp.expect_eq((SELECT apply_blocked_reason FROM job_applications WHERE id = pg_temp.did()), 'clicked submit; unconfirmed', 'release reason stored');

-- ── needs_confirmation is a one-way street (only receipt evidence or a human) ──

SELECT pg_temp.expect_eq(claim_application(pg_temp.did(), '44444444-4444-4444-4444-444444444444'::uuid, 'preparing')::text, 'false', 'no claim out of needs_confirmation (preparing)');
SELECT pg_temp.expect_eq(claim_application(pg_temp.did(), '44444444-4444-4444-4444-444444444444'::uuid, 'submitting')::text, 'false', 'no claim out of needs_confirmation (submitting)');
SELECT pg_temp.expect_eq(mark_application_unsupported(pg_temp.did(), 'x')::text, 'false', 'unsupported refused from needs_confirmation');
SELECT pg_temp.expect_error(format('SELECT requeue_preview(%s)', pg_temp.did()), 'requeue refused from needs_confirmation');
SELECT pg_temp.expect_error(format('SELECT reset_approval(%s)', pg_temp.did()), 'reset_approval refused from needs_confirmation');
SELECT pg_temp.expect_error(format('SELECT record_receipt_evidence(%s, %L::jsonb)', pg_temp.did(), '{"nope":1}'), 'receipt evidence without message_id raises');
SELECT pg_temp.expect_eq(pg_temp.dst(), 'needs_confirmation', 'still needs_confirmation');
SELECT pg_temp.expect_eq(record_receipt_evidence(pg_temp.did(), '{"message_id":"<abc@x>","source":"gmail_receipt"}'::jsonb)::text, 'true', 'receipt evidence');
SELECT pg_temp.expect_eq((SELECT automation_status || '|' || stage || '|' || (applied_date IS NOT NULL)::text || '|' || coalesce(source_channel, 'NULL') || '|' || (submission_evidence->>'message_id') FROM job_applications WHERE id = pg_temp.did()), 'submitted|applied|true|greenhouse|<abc@x>', 'receipt side effects');
SELECT pg_temp.expect_eq(record_receipt_evidence(pg_temp.did(), '{"message_id":"<def@x>"}'::jsonb)::text, 'false', 'receipt evidence is a no-op once submitted');

-- ── pre-click release and page-confirmed submission ────────────────────────────

SELECT pg_temp.dreset('approved', 'ready_to_submit');
SELECT pg_temp.expect_eq(claim_application(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'submitting')::text, 'true', 'claim submitting (pre-click path)');
SELECT pg_temp.expect_eq(release_application(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'approved', 'drift')::text, 'true', 'pre-click release to approved');
SELECT pg_temp.expect_eq(pg_temp.dst(), 'approved', 'back to approved');
SELECT pg_temp.expect_eq((SELECT (approved_at IS NOT NULL)::text FROM job_applications WHERE id = pg_temp.did()), 'true', 'approval survives a pre-click release to approved');
SELECT pg_temp.expect_eq(claim_application(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'submitting')::text, 'true', 're-claim submitting');
SELECT pg_temp.expect_eq(release_application(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'needs_input', 'form changed')::text, 'true', 'pre-click release to needs_input');
SELECT pg_temp.expect_eq((SELECT automation_status || '|' || (approved_at IS NULL)::text || '|' || (approved_revision_hash IS NULL)::text FROM job_applications WHERE id = pg_temp.did()), 'needs_input|true|true', 'needs_input clears approval');

SELECT pg_temp.dreset('approved', 'ready_to_submit');
SELECT claim_application(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'submitting');
SELECT pg_temp.expect_eq(record_submission(pg_temp.did(), '22222222-2222-2222-2222-222222222222'::uuid, 'greenhouse', current_date)::text, 'false', 'record_submission wrong lease');
SELECT pg_temp.expect_eq(record_submission(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'greenhouse', current_date)::text, 'true', 'record_submission');
SELECT pg_temp.expect_eq((SELECT automation_status || '|' || stage || '|' || source_channel || '|' || (submission_evidence->>'source') || '|' || (worker_lease_id IS NULL)::text FROM job_applications WHERE id = pg_temp.did()), 'submitted|applied|greenhouse|page_confirmation|true', 'record_submission side effects');

-- ── preparing release pairs, requeue, unsupported ──────────────────────────────

SELECT pg_temp.dreset('failed_retryable', 'saved');
SELECT pg_temp.expect_eq(claim_application(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'preparing')::text, 'true', 'claim preparing from failed_retryable');
SELECT pg_temp.expect_error(format('SELECT release_application(%s, %L::uuid, %L)', pg_temp.did(), '11111111-1111-1111-1111-111111111111', 'approved'), 'preparing cannot be released to approved');
SELECT pg_temp.expect_error(format('SELECT release_application(%s, %L::uuid, %L)', pg_temp.did(), '11111111-1111-1111-1111-111111111111', 'needs_confirmation'), 'preparing cannot be released to needs_confirmation');
SELECT pg_temp.expect_eq(release_application(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'needs_input', 'missing field')::text, 'true', 'preparing release to needs_input');
SELECT pg_temp.expect_eq(pg_temp.dst(), 'needs_input', 'status needs_input');
SELECT requeue_preview(pg_temp.did());
SELECT pg_temp.expect_eq((SELECT automation_status || '|' || stage || '|' || coalesce(form_signature, 'NULL') || '|' || coalesce(apply_blocked_reason, 'NULL') FROM job_applications WHERE id = pg_temp.did()), 'idle|saved|NULL|NULL', 'requeue side effects');
SELECT pg_temp.expect_error(format('SELECT requeue_preview(%s)', pg_temp.did()), 'requeue refused when not needs_input');
SELECT pg_temp.expect_eq(mark_application_unsupported(pg_temp.did(), 'workday')::text, 'true', 'mark unsupported from idle');
SELECT pg_temp.expect_eq((SELECT automation_status || '|' || apply_blocked_reason FROM job_applications WHERE id = pg_temp.did()), 'unsupported|workday', 'unsupported side effects');
SELECT pg_temp.dreset('preparing', 'saved', '55555555-5555-5555-5555-555555555555'::uuid, now());
SELECT pg_temp.expect_eq(mark_application_unsupported(pg_temp.did(), 'x')::text, 'false', 'unsupported refused while a lease is held');

-- ── recover_stale_leases ───────────────────────────────────────────────────────

-- a caller passing 0 must not steal a lease that is only 10 minutes old (floor is 1800s)
SELECT pg_temp.dreset('submitting', 'ready_to_submit', '66666666-6666-6666-6666-666666666666'::uuid, now() - interval '10 minutes', now() - interval '10 minutes');
SELECT recover_stale_leases(0);
SELECT pg_temp.expect_eq(pg_temp.dst(), 'submitting', 'p_stale_seconds=0 does not steal a 10-minute-old lease');

SELECT pg_temp.dreset('submitting', 'ready_to_submit', '66666666-6666-6666-6666-666666666666'::uuid, now() - interval '2 hours', now() - interval '2 hours');
SELECT pg_temp.expect_eq((recover_stale_leases(0) >= 1)::text, 'true', 'recover returns a count');
SELECT pg_temp.expect_eq(pg_temp.dst(), 'needs_confirmation', 'stale submitting with an attempt -> needs_confirmation');
SELECT pg_temp.expect_eq((SELECT (worker_lease_id IS NULL)::text || '|' || (apply_blocked_reason LIKE 'worker lease expired while submitting; recovered as needs_confirmation')::text FROM job_applications WHERE id = pg_temp.did()), 'true|true', 'recover clears lease and records reason');

SELECT pg_temp.dreset('submitting', 'ready_to_submit', '66666666-6666-6666-6666-666666666666'::uuid, now() - interval '2 hours', NULL);
SELECT recover_stale_leases(0);
SELECT pg_temp.expect_eq(pg_temp.dst(), 'failed_retryable', 'stale submitting with no attempt -> failed_retryable');

SELECT pg_temp.dreset('preparing', 'saved', '66666666-6666-6666-6666-666666666666'::uuid, now() - interval '2 hours');
SELECT recover_stale_leases(0);
SELECT pg_temp.expect_eq(pg_temp.dst(), 'failed_retryable', 'stale preparing -> failed_retryable');

-- ── Direct lifecycle writes are denied for anon and authenticated ──────────────

SELECT pg_temp.dreset('idle', 'saved');

SET LOCAL ROLE anon;
DO $$
DECLARE v_raised BOOLEAN;
BEGIN
  v_raised := false;
  BEGIN
    UPDATE job_applications SET automation_status = 'submitted' WHERE id = current_setting('dry.id')::bigint;
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: anon UPDATE automation_status was allowed'; END IF;

  v_raised := false;
  BEGIN
    UPDATE job_applications SET worker_lease_id = gen_random_uuid() WHERE id = current_setting('dry.id')::bigint;
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: anon UPDATE worker_lease_id was allowed'; END IF;

  v_raised := false;
  BEGIN
    UPDATE job_applications SET worker_heartbeat_at = now() WHERE id = current_setting('dry.id')::bigint;
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: anon UPDATE worker_heartbeat_at was allowed'; END IF;

  v_raised := false;
  BEGIN
    INSERT INTO job_applications (company, role, automation_status) VALUES ('DRYRUN anon', 'x', 'submitted');
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: anon INSERT of automation_status was allowed'; END IF;

  v_raised := false;
  BEGIN
    UPDATE job_applications SET submission_evidence = '{"message_id":"forged"}'::jsonb WHERE id = current_setting('dry.id')::bigint;
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: anon UPDATE submission_evidence was allowed'; END IF;

  -- regression: anon must still write ordinary columns and call the RPCs
  UPDATE job_applications SET notes = 'dryrun anon note' WHERE id = current_setting('dry.id')::bigint;
  IF heartbeat_application(current_setting('dry.id')::bigint, gen_random_uuid()) IS DISTINCT FROM false THEN
    RAISE EXCEPTION 'DRYRUN FAIL: anon could not call heartbeat_application';
  END IF;
END $$;
RESET ROLE;

SET LOCAL ROLE authenticated;
DO $$
DECLARE v_raised BOOLEAN;
BEGIN
  v_raised := false;
  BEGIN
    UPDATE job_applications SET automation_status = 'submitted' WHERE id = current_setting('dry.id')::bigint;
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: authenticated UPDATE automation_status was allowed'; END IF;

  v_raised := false;
  BEGIN
    UPDATE job_applications SET worker_lease_id = gen_random_uuid() WHERE id = current_setting('dry.id')::bigint;
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: authenticated UPDATE worker_lease_id was allowed'; END IF;

  v_raised := false;
  BEGIN
    INSERT INTO job_applications (company, role, automation_status) VALUES ('DRYRUN auth', 'x', 'submitted');
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: authenticated INSERT of automation_status was allowed'; END IF;
END $$;
RESET ROLE;

SELECT 'DRYRUN OK';
ROLLBACK;
