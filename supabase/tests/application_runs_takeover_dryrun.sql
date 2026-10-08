-- Functional dry-run for 20261008000000_application_runs_takeover_evidence.sql.
-- db_migrate.yml (mode=dryrun) prepends the migration, runs this in one session and ROLLBACKs,
-- so nothing persists. This file deliberately does NOT contain the migration.
-- Any failure raises 'DRYRUN FAIL: ...'; success prints DRYRUN OK.

BEGIN;

-- ── Helpers (pg_temp, rolled back with the transaction) ────────────────────────

CREATE FUNCTION pg_temp.did() RETURNS BIGINT LANGUAGE sql AS $$ SELECT current_setting('dry.id')::bigint $$;

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

CREATE FUNCTION pg_temp.takeover() RETURNS JSONB LANGUAGE sql AS
$$ SELECT takeover FROM job_applications WHERE id = pg_temp.did() $$;

DO $$
DECLARE v BIGINT;
BEGIN
  INSERT INTO job_applications (company, role, stage) VALUES ('DRYRUN Co', 'DRYRUN Role', 'saved') RETURNING id INTO v;
  PERFORM set_config('dry.id', v::text, true);
END $$;

-- ── Takeover ──────────────────────────────────────────────────────────────────

SELECT pg_temp.expect_eq(request_takeover(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'captcha', 'x')::text, 'false', 'no takeover without a lease');
SELECT pg_temp.expect_eq(claim_application(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'preparing')::text, 'true', 'claim preparing');
SELECT pg_temp.expect_eq(request_takeover(pg_temp.did(), '22222222-2222-2222-2222-222222222222'::uuid, 'captcha', 'x')::text, 'false', 'takeover with wrong lease');
SELECT pg_temp.expect_error(format('SELECT request_takeover(%s, %L::uuid, %L, %L)', pg_temp.did(), '11111111-1111-1111-1111-111111111111', 'solve_it', 'x'), 'unknown takeover kind raises');
SELECT pg_temp.expect_error(format('SELECT request_takeover(%s, %L::uuid, %L, %L)', pg_temp.did(), '11111111-1111-1111-1111-111111111111', 'captcha', repeat('x', 501)), 'overlong reason raises');
SELECT pg_temp.expect_eq(takeover_continue(pg_temp.did())::text, 'false', 'continue with no open request');
SELECT pg_temp.expect_eq(request_takeover(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'captcha', 'hCaptcha on Lever')::text, 'true', 'request takeover');
SELECT pg_temp.expect_eq((SELECT (t->>'kind') || '|' || (t->>'lease') || '|' || (t->>'reason') || '|' || ((t->'continue_at') = 'null'::jsonb)::text FROM (SELECT pg_temp.takeover() AS t) s),
                         'captcha|11111111-1111-1111-1111-111111111111|hCaptcha on Lever|true', 'takeover contents');
SELECT pg_temp.expect_eq(takeover_continue(pg_temp.did())::text, 'true', 'continue');
SELECT pg_temp.expect_eq((pg_temp.takeover()->>'continue_at' IS NOT NULL)::text, 'true', 'continue_at set');
SELECT pg_temp.expect_eq(takeover_continue(pg_temp.did())::text, 'false', 'second continue is a no-op');
SELECT pg_temp.expect_eq((SELECT automation_status FROM job_applications WHERE id = pg_temp.did()), 'preparing', 'continue does not touch status');
SELECT pg_temp.expect_eq(clear_takeover(pg_temp.did(), '22222222-2222-2222-2222-222222222222'::uuid)::text, 'false', 'clear with wrong lease');
SELECT pg_temp.expect_eq(clear_takeover(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid)::text, 'true', 'clear');
SELECT pg_temp.expect_eq((pg_temp.takeover() IS NULL)::text, 'true', 'takeover cleared');

-- a request left behind by an earlier lease is stale and cannot be continued
SELECT request_takeover(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'login', 'sign in');
SELECT pg_temp.expect_eq(release_application(pg_temp.did(), '11111111-1111-1111-1111-111111111111'::uuid, 'failed_retryable', 'takeover timeout')::text, 'true', 'release');
SELECT pg_temp.expect_eq(takeover_continue(pg_temp.did())::text, 'false', 'continue after release');
SELECT pg_temp.expect_eq(claim_application(pg_temp.did(), '33333333-3333-3333-3333-333333333333'::uuid, 'preparing')::text, 'true', 'reclaim');
SELECT pg_temp.expect_eq(takeover_continue(pg_temp.did())::text, 'false', 'stale request from the earlier lease cannot be continued');

-- a lease alone is not enough: only an active run (preparing/submitting) may ask for a human
UPDATE job_applications SET automation_status = 'approved' WHERE id = pg_temp.did();
SELECT pg_temp.expect_eq(request_takeover(pg_temp.did(), '33333333-3333-3333-3333-333333333333'::uuid, 'captcha', 'x')::text, 'false', 'no takeover outside preparing/submitting');
UPDATE job_applications SET automation_status = 'preparing' WHERE id = pg_temp.did();

-- ── record_submission with evidence ───────────────────────────────────────────

SELECT pg_temp.expect_eq((SELECT count(*) FROM pg_proc WHERE proname = 'record_submission' AND pronamespace = 'public'::regnamespace)::text, '1', 'record_submission has exactly one overload');

DO $$
BEGIN
  -- straight to a submitting lease, as the migration owner, bypassing the approval RPCs
  UPDATE job_applications
  SET apply_preview = '{"platform":"greenhouse","dry":true}'::jsonb, stage = 'ready_to_submit',
      automation_status = 'submitting', worker_lease_id = '44444444-4444-4444-4444-444444444444'::uuid,
      worker_heartbeat_at = now(), takeover = NULL
  WHERE id = current_setting('dry.id')::bigint;
END $$;
SELECT request_takeover(pg_temp.did(), '44444444-4444-4444-4444-444444444444'::uuid, 'captcha', 'after click');
SELECT pg_temp.expect_error(format('SELECT record_submission(%s, %L::uuid, %L, current_date, %L::jsonb)', pg_temp.did(), '44444444-4444-4444-4444-444444444444', 'greenhouse', '["not","an","object"]'), 'array evidence raises');
SELECT pg_temp.expect_error(format('SELECT record_submission(%s, %L::uuid, %L, current_date, %L::jsonb)', pg_temp.did(), '44444444-4444-4444-4444-444444444444', 'greenhouse', json_build_object('text', repeat('x', 17000))), 'oversized evidence raises');
SELECT pg_temp.expect_eq(record_submission(pg_temp.did(), '55555555-5555-5555-5555-555555555555'::uuid, 'greenhouse', current_date, '{}'::jsonb)::text, 'false', 'record_submission wrong lease');
SELECT pg_temp.expect_eq(record_submission(pg_temp.did(), '44444444-4444-4444-4444-444444444444'::uuid, 'greenhouse', current_date,
                         '{"url":"https://x/thanks","text":"Thank you for applying","source":"spoof","at":"1999","message_id":"forged"}'::jsonb)::text, 'true', 'record_submission with evidence');
SELECT pg_temp.expect_eq((SELECT automation_status || '|' || stage || '|' || (submission_evidence->>'source') || '|' || (submission_evidence->>'url') || '|'
                                 || (submission_evidence ? 'message_id')::text || '|' || ((submission_evidence->>'at') <> '1999')::text || '|'
                                 || (takeover IS NULL)::text || '|' || (worker_lease_id IS NULL)::text
                          FROM job_applications WHERE id = pg_temp.did()),
                         'submitted|applied|page_confirmation|https://x/thanks|false|true|true|true', 'evidence merged, server keys win, takeover and lease cleared');

-- the 4-argument call the current worker makes still resolves (p_evidence defaults to NULL)
DO $$
BEGIN
  UPDATE job_applications
  SET automation_status = 'submitting', worker_lease_id = '66666666-6666-6666-6666-666666666666'::uuid,
      stage = 'ready_to_submit', submission_evidence = NULL
  WHERE id = current_setting('dry.id')::bigint;
END $$;
SELECT pg_temp.expect_eq(record_submission(p_id => pg_temp.did(), p_lease => '66666666-6666-6666-6666-666666666666'::uuid,
                                           p_source_channel => 'greenhouse', p_applied_date => current_date)::text, 'true', 'named 4-arg call');
SELECT pg_temp.expect_eq((SELECT submission_evidence->>'source' FROM job_applications WHERE id = pg_temp.did()), 'page_confirmation', 'default evidence');

-- ── application_runs ──────────────────────────────────────────────────────────

SELECT pg_temp.expect_eq((log_application_run('77777777-7777-7777-7777-777777777777'::uuid, pg_temp.did(), 'prepare', 'deterministic', 'boards.greenhouse.io',
                          now() - interval '2 minutes', 'ready', NULL, 9, 0, 0, 0, NULL, '{"fill_report":{"email":true}}'::jsonb) > 0)::text, 'true', 'log a run');
SELECT pg_temp.expect_eq((SELECT kind || '|' || adapter || '|' || outcome || '|' || fields_filled FROM application_runs WHERE run_id = '77777777-7777-7777-7777-777777777777'::uuid),
                         'prepare|deterministic|ready|9', 'run row');
SELECT pg_temp.expect_error($q$SELECT log_application_run(gen_random_uuid(), NULL, 'prepare', 'x', NULL, now(), 'nonsense')$q$, 'unknown outcome raises');
SELECT pg_temp.expect_error($q$SELECT log_application_run(gen_random_uuid(), NULL, 'deploy', 'x', NULL, now(), 'ready')$q$, 'unknown kind raises');
SELECT pg_temp.expect_error($q$SELECT log_application_run(gen_random_uuid(), NULL, 'prepare', repeat('x', 65), NULL, now(), 'ready')$q$, 'overlong adapter raises');
SELECT pg_temp.expect_error($q$SELECT log_application_run(gen_random_uuid(), NULL, 'prepare', 'x', NULL, now() + interval '1 hour', 'ready')$q$, 'future start raises');
SELECT pg_temp.expect_error($q$SELECT log_application_run(gen_random_uuid(), NULL, 'prepare', 'x', NULL, now(), 'ready', NULL, -1)$q$, 'negative count raises');
SELECT pg_temp.expect_error($q$SELECT log_application_run(gen_random_uuid(), NULL, 'prepare', 'x', NULL, now(), 'ready', NULL, NULL, NULL, 0, 0, NULL, '[1]'::jsonb)$q$, 'array details raise');
SELECT pg_temp.expect_eq((log_application_run(gen_random_uuid(), NULL, 'bakeoff', 'ats_agent', NULL, now(), 'scored') > 0)::text, 'true', 'bake-off run without an application');

-- ── Evidence bucket ───────────────────────────────────────────────────────────

SELECT pg_temp.expect_eq((SELECT public::text || '|' || file_size_limit FROM storage.buckets WHERE id = 'application-evidence'), 'false|5242880', 'evidence bucket private and size-limited');
SELECT pg_temp.expect_eq((SELECT count(*) FROM pg_policies WHERE schemaname = 'storage' AND tablename = 'objects'
                          AND (qual LIKE '%application-evidence%' OR with_check LIKE '%application-evidence%'))::text, '1', 'one evidence policy');
SELECT pg_temp.expect_eq((SELECT cmd FROM pg_policies WHERE schemaname = 'storage' AND policyname = 'application-evidence anon insert'), 'INSERT', 'evidence policy is insert-only');

-- ── API roles: read runs, write nothing directly ──────────────────────────────

DO $$
DECLARE r TEXT; p TEXT;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
    IF NOT has_table_privilege(r, 'application_runs', 'SELECT') THEN
      RAISE EXCEPTION 'DRYRUN FAIL: % cannot SELECT application_runs', r;
    END IF;
    FOREACH p IN ARRAY ARRAY['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'] LOOP
      IF has_table_privilege(r, 'application_runs', p) THEN
        RAISE EXCEPTION 'DRYRUN FAIL: % holds % on application_runs', r, p;
      END IF;
    END LOOP;
    IF has_sequence_privilege(r, 'application_runs_id_seq', 'USAGE') THEN
      RAISE EXCEPTION 'DRYRUN FAIL: % holds USAGE on application_runs_id_seq', r;
    END IF;
  END LOOP;
END $$;

SET LOCAL ROLE anon;
DO $$
DECLARE v_raised BOOLEAN;
BEGIN
  v_raised := false;
  BEGIN
    INSERT INTO application_runs (run_id, kind, adapter, started_at, outcome) VALUES (gen_random_uuid(), 'prepare', 'forged', now(), 'submitted');
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: anon INSERT into application_runs was allowed'; END IF;

  v_raised := false;
  BEGIN
    DELETE FROM application_runs;
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: anon DELETE from application_runs was allowed'; END IF;

  v_raised := false;
  BEGIN
    UPDATE job_applications SET takeover = '{"kind":"captcha"}'::jsonb WHERE id = current_setting('dry.id')::bigint;
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: anon UPDATE takeover was allowed'; END IF;

  IF (SELECT count(*) FROM application_runs WHERE run_id = '77777777-7777-7777-7777-777777777777'::uuid) <> 1 THEN
    RAISE EXCEPTION 'DRYRUN FAIL: anon cannot read application_runs';
  END IF;
  IF log_application_run(gen_random_uuid(), NULL, 'dryrun', 'deterministic', NULL, now(), 'skipped') IS NULL THEN
    RAISE EXCEPTION 'DRYRUN FAIL: anon could not call log_application_run';
  END IF;
  IF takeover_continue(current_setting('dry.id')::bigint) IS DISTINCT FROM false THEN
    RAISE EXCEPTION 'DRYRUN FAIL: anon could not call takeover_continue';
  END IF;
END $$;
RESET ROLE;

SET LOCAL ROLE authenticated;
DO $$
DECLARE v_raised BOOLEAN;
BEGIN
  v_raised := false;
  BEGIN
    INSERT INTO application_runs (run_id, kind, adapter, started_at, outcome) VALUES (gen_random_uuid(), 'prepare', 'forged', now(), 'submitted');
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: authenticated INSERT into application_runs was allowed'; END IF;

  v_raised := false;
  BEGIN
    UPDATE job_applications SET takeover = '{"kind":"captcha"}'::jsonb WHERE id = current_setting('dry.id')::bigint;
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: authenticated UPDATE takeover was allowed'; END IF;
END $$;
RESET ROLE;

SELECT 'DRYRUN OK';
ROLLBACK;
