-- Functional dry-run for 20261009000000_job_identity_queue_and_boards.sql.
-- db_migrate.yml (mode=dryrun) prepends the migration, runs this in one session and ROLLBACKs.
-- Any failure raises 'DRYRUN FAIL: ...'; success prints DRYRUN OK.

BEGIN;

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

CREATE FUNCTION pg_temp.did() RETURNS BIGINT LANGUAGE sql AS $$ SELECT current_setting('dry.id')::bigint $$;

DO $$
DECLARE v BIGINT;
BEGIN
  INSERT INTO job_applications (company, role, stage, job_key, platform)
  VALUES ('DRYRUN Co', 'DRYRUN Role', 'saved', 'dryrun:job:1', 'greenhouse')
  RETURNING id INTO v;
  PERFORM set_config('dry.id', v::text, true);
END $$;

-- One row per job key.
SELECT pg_temp.expect_error($q$INSERT INTO job_applications (company, role, job_key) VALUES ('DRYRUN Co', 'Other', 'dryrun:job:1')$q$,
                            'a second row with the same job_key');
INSERT INTO job_applications (company, role, job_key) VALUES ('DRYRUN Co', 'NULL key 1', NULL), ('DRYRUN Co', 'NULL key 2', NULL);

-- A preview claim counts an attempt; a submit claim does not; requeue resets the count.
SELECT pg_temp.expect_eq(claim_application(pg_temp.did(), gen_random_uuid(), 'preparing')::text, 'true', 'first claim');
SELECT pg_temp.expect_eq((SELECT prepare_attempts FROM job_applications WHERE id = pg_temp.did())::text, '1', 'attempt counted');
SELECT release_application(pg_temp.did(), (SELECT worker_lease_id FROM job_applications WHERE id = pg_temp.did()), 'failed_retryable', 'dryrun');
SELECT pg_temp.expect_eq(claim_application(pg_temp.did(), gen_random_uuid(), 'preparing')::text, 'true', 'retry claim');
SELECT pg_temp.expect_eq((SELECT prepare_attempts FROM job_applications WHERE id = pg_temp.did())::text, '2', 'second attempt counted');
SELECT release_application(pg_temp.did(), (SELECT worker_lease_id FROM job_applications WHERE id = pg_temp.did()), 'failed_retryable', 'dryrun');
SELECT requeue_preview(pg_temp.did());
SELECT pg_temp.expect_eq((SELECT automation_status || '|' || prepare_attempts FROM job_applications WHERE id = pg_temp.did()),
                         'idle|0', 'requeue from failed_retryable resets attempts');
SELECT pg_temp.expect_error(format('SELECT requeue_preview(%s)', pg_temp.did()), 'requeue of an idle row');

-- API roles: identity columns writable, the attempt counter and deletes are not.
SET LOCAL ROLE anon;
UPDATE job_applications SET location = 'Boston, MA', pick_attempts = 1 WHERE id = pg_temp.did();
SELECT pg_temp.expect_error(format('UPDATE job_applications SET prepare_attempts = 0 WHERE id = %s', pg_temp.did()), 'anon writes prepare_attempts');
SELECT pg_temp.expect_error(format('DELETE FROM job_applications WHERE id = %s', pg_temp.did()), 'anon deletes an application');
SELECT pg_temp.expect_error('TRUNCATE job_applications', 'anon truncates applications');
INSERT INTO job_boards (platform, board, company, source) VALUES ('greenhouse', 'dryrunboard', 'DRYRUN Co', 'dryrun');
UPDATE job_boards SET consecutive_failures = 1 WHERE board = 'dryrunboard';
SELECT pg_temp.expect_error($q$DELETE FROM job_boards WHERE board = 'dryrunboard'$q$, 'anon deletes a board');
SELECT pg_temp.expect_error($q$INSERT INTO job_boards (platform, board) VALUES ('greenhouse', 'dryrunboard')$q$, 'duplicate board');
SELECT pg_temp.expect_error($q$INSERT INTO job_boards (platform, board) VALUES ('indeed', 'x')$q$, 'unknown board platform');
RESET ROLE;
SET LOCAL ROLE authenticated;
SELECT pg_temp.expect_error(format('DELETE FROM job_applications WHERE id = %s', pg_temp.did()), 'authenticated deletes an application');
SELECT pg_temp.expect_error(format('UPDATE job_applications SET prepare_attempts = 0 WHERE id = %s', pg_temp.did()), 'authenticated writes prepare_attempts');
RESET ROLE;
SELECT pg_temp.expect_eq((SELECT location || '|' || pick_attempts FROM job_applications WHERE id = pg_temp.did()), 'Boston, MA|1', 'anon identity write landed');

SELECT 'DRYRUN OK';
ROLLBACK;
