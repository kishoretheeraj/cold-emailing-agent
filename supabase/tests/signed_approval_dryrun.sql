-- Functional dry-run for 20261008000001_signed_approval.sql.
-- db_migrate.yml (mode=dryrun) prepends the migration, runs this in one session and ROLLBACKs.
-- Any failure raises 'DRYRUN FAIL: ...'; success prints DRYRUN OK.

BEGIN;

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

CREATE FUNCTION pg_temp.now_ms() RETURNS BIGINT LANGUAGE sql AS $$ SELECT (extract(epoch FROM now()) * 1000)::bigint $$;
CREATE FUNCTION pg_temp.hash() RETURNS TEXT LANGUAGE sql AS $$ SELECT preview_revision_hash FROM job_applications WHERE id = pg_temp.did() $$;

DO $$
DECLARE v BIGINT;
BEGIN
  INSERT INTO job_applications (company, role, stage, apply_preview, automation_status)
  VALUES ('DRYRUN Co', 'DRYRUN Role', 'ready_to_submit', '{"platform":"greenhouse"}'::jsonb, 'ready_for_review')
  RETURNING id INTO v;
  PERFORM set_config('dry.id', v::text, true);
END $$;

SELECT pg_temp.expect_eq((SELECT count(*) FROM pg_proc WHERE proname = 'approve_application' AND pronamespace = 'public'::regnamespace)::text, '1', 'approve_application has exactly one overload');
SELECT pg_temp.expect_error(format('SELECT approve_application(%s, %L)', pg_temp.did(), pg_temp.hash()), 'unsigned 2-arg call no longer exists');
SELECT pg_temp.expect_error(format('SELECT approve_application(%s, %L, NULL, %s)', pg_temp.did(), pg_temp.hash(), pg_temp.now_ms()), 'missing signature');
SELECT pg_temp.expect_error(format('SELECT approve_application(%s, %L, %L, %s)', pg_temp.did(), pg_temp.hash(), 'xyz', pg_temp.now_ms()), 'malformed signature');
SELECT pg_temp.expect_error(format('SELECT approve_application(%s, %L, %L, %s)', pg_temp.did(), pg_temp.hash(), upper(repeat('ab', 32)), pg_temp.now_ms()), 'uppercase signature');
SELECT pg_temp.expect_error(format('SELECT approve_application(%s, %L, %L, %s)', pg_temp.did(), pg_temp.hash(), repeat('ab', 32), pg_temp.now_ms() - 121000), 'stale timestamp');
SELECT pg_temp.expect_error(format('SELECT approve_application(%s, %L, %L, %s)', pg_temp.did(), pg_temp.hash(), repeat('ab', 32), pg_temp.now_ms() + 121000), 'future timestamp');
SELECT pg_temp.expect_error(format('SELECT approve_application(%s, %L, %L, NULL)', pg_temp.did(), pg_temp.hash(), repeat('ab', 32)), 'missing timestamp');
SELECT pg_temp.expect_error(format('SELECT approve_application(%s, %L, %L, %s)', pg_temp.did(), 'wrong-hash', repeat('ab', 32), pg_temp.now_ms()), 'wrong hash');
SELECT pg_temp.expect_eq((SELECT automation_status FROM job_applications WHERE id = pg_temp.did()), 'ready_for_review', 'failed approvals changed nothing');

SELECT approve_application(pg_temp.did(), pg_temp.hash(), repeat('ab', 32), pg_temp.now_ms() - 30000);
SELECT pg_temp.expect_eq((SELECT automation_status || '|' || approval_signature || '|' || (approval_signed_at_ms = pg_temp.now_ms() - 30000)::text
                                 || '|' || (approved_revision_hash = preview_revision_hash)::text || '|' || (approved_at IS NOT NULL)::text
                          FROM job_applications WHERE id = pg_temp.did()),
                         'approved|' || repeat('ab', 32) || '|true|true|true', 'signed approval stored');
SELECT pg_temp.expect_error(format('SELECT approve_application(%s, %L, %L, %s)', pg_temp.did(), pg_temp.hash(), repeat('cd', 32), pg_temp.now_ms()), 'second approval refused');
-- approved_at alone also blocks a second approval, even if the status were moved back by hand
UPDATE job_applications SET automation_status = 'ready_for_review' WHERE id = pg_temp.did();
SELECT pg_temp.expect_error(format('SELECT approve_application(%s, %L, %L, %s)', pg_temp.did(), pg_temp.hash(), repeat('cd', 32), pg_temp.now_ms()), 'approval refused while approved_at is set');
SELECT pg_temp.expect_eq((SELECT approval_signature FROM job_applications WHERE id = pg_temp.did()), repeat('ab', 32), 'first signature kept');

SET LOCAL ROLE anon;
DO $$
DECLARE v_raised BOOLEAN := false;
BEGIN
  BEGIN
    UPDATE job_applications SET approval_signature = repeat('ef', 32) WHERE id = current_setting('dry.id')::bigint;
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: anon UPDATE approval_signature was allowed'; END IF;
  v_raised := false;
  BEGIN
    UPDATE job_applications SET approval_signed_at_ms = 1 WHERE id = current_setting('dry.id')::bigint;
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: anon UPDATE approval_signed_at_ms was allowed'; END IF;
END $$;
RESET ROLE;

SET LOCAL ROLE authenticated;
DO $$
DECLARE v_raised BOOLEAN := false;
BEGIN
  BEGIN
    UPDATE job_applications SET approval_signature = repeat('ef', 32) WHERE id = current_setting('dry.id')::bigint;
  EXCEPTION WHEN insufficient_privilege THEN v_raised := true;
  END;
  IF NOT v_raised THEN RAISE EXCEPTION 'DRYRUN FAIL: authenticated UPDATE approval_signature was allowed'; END IF;
END $$;
RESET ROLE;

SELECT 'DRYRUN OK';
ROLLBACK;
