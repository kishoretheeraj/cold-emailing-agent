-- Functional dry-run for 20261012000000_add_document_digests.sql (BEGIN/ROLLBACK via db_migrate.yml):
-- the revision hash covers the document digests and the destination, so changing any of them
-- after approval invalidates the approval; anon may write the digests (only invalidating).

BEGIN;

CREATE FUNCTION pg_temp.expect(ok BOOLEAN, label TEXT) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF NOT coalesce(ok, false) THEN
    RAISE EXCEPTION 'DRYRUN FAIL: %', label;
  END IF;
END;
$$;

DO $$
DECLARE v BIGINT;
BEGIN
  INSERT INTO job_applications (company, role, job_url, stage, automation_status, apply_preview,
                                resume_file_ref, resume_sha256, cover_letter_sha256)
  VALUES ('DRYRUN Co', 'DRYRUN APM', 'https://boards.greenhouse.io/dryrun/jobs/1', 'ready_to_submit',
          'ready_for_review', '{"platform":"greenhouse"}'::jsonb, 'r/1.pdf', 'aaa', 'bbb')
  RETURNING id INTO v;
  PERFORM set_config('dry.id', v::text, true);
END $$;

CREATE FUNCTION pg_temp.hash_now() RETURNS TEXT LANGUAGE sql AS $$
  SELECT preview_revision_hash FROM job_applications WHERE id = current_setting('dry.id')::bigint
$$;

DO $$ BEGIN PERFORM pg_temp.expect(pg_temp.hash_now() IS NOT NULL, 'hash computed on insert'); END $$;

-- Each of these, written by the public (anon) role, must change the hash.
DO $$
DECLARE before TEXT; col TEXT;
BEGIN
  FOREACH col IN ARRAY ARRAY['resume_sha256', 'cover_letter_sha256', 'job_url', 'company', 'role'] LOOP
    before := pg_temp.hash_now();
    EXECUTE format('SET LOCAL ROLE anon');
    EXECUTE format('UPDATE job_applications SET %I = %I || ''x'' WHERE id = %s', col, col, current_setting('dry.id'));
    EXECUTE 'RESET ROLE';
    PERFORM pg_temp.expect(pg_temp.hash_now() IS DISTINCT FROM before, col || ' changes the revision hash');
  END LOOP;
END $$;

-- Writing the same values back leaves the hash alone (the hash is a pure function of the row).
DO $$
DECLARE before TEXT;
BEGIN
  before := pg_temp.hash_now();
  UPDATE job_applications SET notes = 'touch' WHERE id = current_setting('dry.id')::bigint;
  PERFORM pg_temp.expect(pg_temp.hash_now() = before, 'an unrelated column keeps the hash');
END $$;

-- Only the 9-argument hash function remains; the 7-argument one from the edited 20261001000000 is gone.
DO $$
BEGIN
  PERFORM pg_temp.expect(NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'job_application_preview_revision_hash'
                                     AND pronargs = 7), 'no 7-arg overload');
  PERFORM pg_temp.expect(EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'job_application_preview_revision_hash'
                                 AND pronargs = 9), '9-arg function present');
END $$;

SELECT 'DRYRUN OK: document digests';
ROLLBACK;
