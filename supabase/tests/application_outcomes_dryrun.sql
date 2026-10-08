-- Functional dry-run for 20261011000000_application_outcomes.sql (BEGIN/ROLLBACK via db_migrate.yml).

BEGIN;

CREATE FUNCTION pg_temp.expect_eq(actual TEXT, expected TEXT, label TEXT) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF actual IS DISTINCT FROM expected THEN
    RAISE EXCEPTION 'DRYRUN FAIL: % (expected %, got %)', label, expected, actual;
  END IF;
END;
$$;

DO $$
DECLARE v BIGINT;
BEGIN
  INSERT INTO job_applications (company, role, stage, automation_status, apply_preview)
  VALUES ('DRYRUN Co', 'DRYRUN APM', 'applied', 'submitted', '{"platform":"greenhouse"}'::jsonb) RETURNING id INTO v;
  PERFORM set_config('dry.id', v::text, true);
END $$;

DO $$
DECLARE before TEXT; after TEXT;
BEGIN
  SELECT preview_revision_hash INTO before FROM job_applications WHERE id = current_setting('dry.id')::bigint;
  PERFORM set_config('dry.hash', coalesce(before, ''), true);
END $$;

SET LOCAL ROLE anon;
UPDATE job_applications
SET stage = 'rejected',
    outcome_evidence = '{"kind": "rejection", "message_id": "<m@x>", "previous_stage": "applied"}'::jsonb
WHERE id = current_setting('dry.id')::bigint AND stage IN ('applied', 'phone_screen', 'onsite');
RESET ROLE;

SET LOCAL ROLE authenticated;
UPDATE job_applications SET outcome_evidence = outcome_evidence WHERE id = current_setting('dry.id')::bigint;
RESET ROLE;

SELECT pg_temp.expect_eq((SELECT stage || '|' || (outcome_evidence->>'message_id') FROM job_applications
                          WHERE id = current_setting('dry.id')::bigint), 'rejected|<m@x>', 'anon wrote the outcome');
SELECT pg_temp.expect_eq((SELECT coalesce(preview_revision_hash, '') FROM job_applications
                          WHERE id = current_setting('dry.id')::bigint), current_setting('dry.hash'),
                         'outcome leaves preview_revision_hash unchanged');

SELECT 'DRYRUN OK';
ROLLBACK;
