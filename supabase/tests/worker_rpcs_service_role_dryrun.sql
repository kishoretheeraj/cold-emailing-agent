-- Functional dry-run for 20261015000000_worker_rpcs_service_role_only.sql.
-- The controller prepends the migration's DDL, runs this whole script in one session, and the
-- script ROLLBACKs, so nothing persists. Any failure raises 'DRYRUN FAIL: ...'; success prints DRYRUN OK.

BEGIN;

-- ── Privileges: worker RPCs are service_role-only, UI RPCs stay anon ───────────

DO $$
DECLARE
  f TEXT;
  r TEXT;
  worker TEXT[] := ARRAY[
    'claim_application(bigint, uuid, text)',
    'heartbeat_application(bigint, uuid)',
    'renew_submission_lease(bigint, uuid, text)',
    'complete_preview(bigint, uuid, jsonb, text)',
    'release_application(bigint, uuid, text, text)',
    'record_submission(bigint, uuid, text, date, jsonb)',
    'mark_application_unsupported(bigint, text)',
    'recover_stale_leases(integer)',
    'record_receipt_evidence(bigint, jsonb)',
    'request_takeover(bigint, uuid, text, text)',
    'clear_takeover(bigint, uuid)',
    'log_application_run(uuid, bigint, text, text, text, timestamptz, text, text, integer, integer, integer, integer, text, jsonb)'
  ];
  ui TEXT[] := ARRAY[
    'reset_approval(bigint)',
    'requeue_preview(bigint)',
    'resolve_confirmation(bigint, boolean)',
    'takeover_continue(bigint)',
    'release_referral_hold(bigint)'
  ];
BEGIN
  FOREACH f IN ARRAY worker LOOP
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
      IF has_function_privilege(r, f, 'EXECUTE') THEN
        RAISE EXCEPTION 'DRYRUN FAIL: % can still execute %', r, f;
      END IF;
    END LOOP;
    IF NOT has_function_privilege('service_role', f, 'EXECUTE') THEN
      RAISE EXCEPTION 'DRYRUN FAIL: service_role cannot execute %', f;
    END IF;
  END LOOP;
  FOREACH f IN ARRAY ui LOOP
    IF NOT has_function_privilege('anon', f, 'EXECUTE') THEN
      RAISE EXCEPTION 'DRYRUN FAIL: anon lost the UI RPC %', f;
    END IF;
  END LOOP;
END $$;

-- Every overload in the live database, not only the signatures the repo defines: an overload
-- created elsewhere (Supabase grants new functions to anon directly) would stay callable.
DO $$
DECLARE
  r RECORD;
  bad TEXT := '';
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS sig
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.proname = ANY (ARRAY['claim_application', 'heartbeat_application', 'renew_submission_lease',
        'complete_preview', 'release_application', 'record_submission', 'mark_application_unsupported',
        'recover_stale_leases', 'record_receipt_evidence', 'request_takeover', 'clear_takeover',
        'log_application_run'])
  LOOP
    IF has_function_privilege('anon', r.sig, 'EXECUTE') OR has_function_privilege('authenticated', r.sig, 'EXECUTE') THEN
      bad := bad || ' ' || r.sig::text;
    END IF;
  END LOOP;
  IF bad <> '' THEN
    RAISE EXCEPTION 'DRYRUN FAIL: overloads still executable by anon/authenticated:%', bad;
  END IF;
END $$;

-- ── Behaviour: the forgeries from the audit are refused for anon ───────────────

DO $$
DECLARE v BIGINT;
BEGIN
  INSERT INTO job_applications (company, role, stage) VALUES ('DRYRUN Co', 'DRYRUN Role', 'applied') RETURNING id INTO v;
  PERFORM set_config('dry.id', v::text, true);
  UPDATE job_applications SET automation_status = 'needs_confirmation', submit_attempted_at = now(),
         apply_preview = '{"platform":"greenhouse","dry":true}'::jsonb
  WHERE id = v;
END $$;

SET LOCAL ROLE anon;
DO $$
DECLARE v_raised BOOLEAN;
BEGIN
  v_raised := false;
  BEGIN
    PERFORM record_receipt_evidence(current_setting('dry.id')::bigint, '{"message_id":"<forged@x>"}'::jsonb);
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: anon record_receipt_evidence was allowed'; END IF;

  v_raised := false;
  BEGIN
    PERFORM complete_preview(current_setting('dry.id')::bigint, gen_random_uuid(), '{"forged":true}'::jsonb, 'sig');
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: anon complete_preview was allowed'; END IF;

  v_raised := false;
  BEGIN
    PERFORM recover_stale_leases(1800);
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: anon recover_stale_leases was allowed'; END IF;
END $$;
RESET ROLE;

SET LOCAL ROLE authenticated;
DO $$
DECLARE v_raised BOOLEAN := false;
BEGIN
  BEGIN
    PERFORM record_receipt_evidence(current_setting('dry.id')::bigint, '{"message_id":"<forged@x>"}'::jsonb);
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: authenticated record_receipt_evidence was allowed'; END IF;
END $$;
RESET ROLE;

-- The worker's role still drives the same transition.
SET LOCAL ROLE service_role;
DO $$
BEGIN
  IF NOT record_receipt_evidence(current_setting('dry.id')::bigint,
                                 '{"message_id":"<dryrun-receipt@x>","from":"no-reply@greenhouse.io"}'::jsonb) THEN
    RAISE EXCEPTION 'DRYRUN FAIL: service_role record_receipt_evidence returned false';
  END IF;
END $$;
RESET ROLE;

DO $$
BEGIN
  IF (SELECT automation_status FROM job_applications WHERE id = current_setting('dry.id')::bigint) <> 'submitted' THEN
    RAISE EXCEPTION 'DRYRUN FAIL: service_role receipt did not move the row to submitted';
  END IF;
END $$;

SELECT 'DRYRUN OK';
ROLLBACK;
