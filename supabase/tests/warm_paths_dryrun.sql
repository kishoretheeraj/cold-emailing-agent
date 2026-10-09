-- Functional dry-run for 20261010000000_warm_paths.sql.
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

CREATE FUNCTION pg_temp.app() RETURNS BIGINT LANGUAGE sql AS $$ SELECT current_setting('dry.app')::bigint $$;
CREATE FUNCTION pg_temp.closed_app() RETURNS BIGINT LANGUAGE sql AS $$ SELECT current_setting('dry.closed')::bigint $$;

DO $$
DECLARE v BIGINT; w BIGINT;
BEGIN
  INSERT INTO job_applications (company, role, stage, automation_status, apply_preview)
  VALUES ('DRYRUN Co', 'DRYRUN APM', 'ready_to_submit', 'ready_for_review', '{"platform":"greenhouse"}'::jsonb)
  RETURNING id INTO v;
  INSERT INTO job_applications (company, role, stage) VALUES ('DRYRUN Co', 'DRYRUN PM', 'rejected') RETURNING id INTO w;
  PERFORM set_config('dry.app', v::text, true);
  PERFORM set_config('dry.closed', w::text, true);
END $$;

SET LOCAL ROLE anon;

-- Linking at creation, up to three people per application.
INSERT INTO contacts (name, email, company, mode, relationship, job_application_id)
SELECT 'Dry ' || n, 'dry' || n || '@dryrun.example', 'DRYRUN Co', 'applied', 'hiring_manager', pg_temp.app()
FROM generate_series(1, 3) AS n;
SELECT pg_temp.expect_error(format($q$INSERT INTO contacts (name, email, company, mode, job_application_id)
                                     VALUES ('Dry 4', 'dry4@dryrun.example', 'DRYRUN Co', 'applied', %s)$q$, pg_temp.app()),
                            'a fourth person on one application');

-- A soft-deleted contact frees its place; restoring it re-checks the cap.
UPDATE contacts SET deleted_at = now() WHERE email = 'dry3@dryrun.example';
INSERT INTO contacts (name, email, company, mode, job_application_id) VALUES ('Dry 5', 'dry5@dryrun.example', 'DRYRUN Co', 'networking', pg_temp.app());
SELECT pg_temp.expect_error($q$UPDATE contacts SET deleted_at = NULL WHERE email = 'dry3@dryrun.example'$q$,
                            'restoring a fourth linked person');

-- Only contacts not yet emailed can be linked; unlinking always works.
INSERT INTO contacts (name, email, company, mode, stage) VALUES ('Mid', 'mid@dryrun.example', 'DRYRUN Co', 'networking', 'networking_sent');
INSERT INTO contacts (name, email, company, mode, reply_status) VALUES ('Replied', 'replied@dryrun.example', 'DRYRUN Co', 'applied', 'interested');
INSERT INTO contacts (name, email, company, mode) VALUES ('Free', 'free@dryrun.example', 'DRYRUN Co', 'networking');
INSERT INTO contacts (name, email, company) VALUES ('Cold', 'cold@dryrun.example', 'DRYRUN Co');
UPDATE contacts SET job_application_id = NULL WHERE email = 'dry5@dryrun.example';
SELECT pg_temp.expect_error(format($q$UPDATE contacts SET job_application_id = %s WHERE email = 'mid@dryrun.example'$q$, pg_temp.app()),
                            'linking a contact mid-thread');
SELECT pg_temp.expect_error(format($q$UPDATE contacts SET job_application_id = %s, stage = 'new' WHERE email = 'mid@dryrun.example'$q$, pg_temp.app()),
                            'resetting the stage to slip a link through');
SELECT pg_temp.expect_error(format($q$UPDATE contacts SET job_application_id = %s WHERE email = 'replied@dryrun.example'$q$, pg_temp.app()),
                            'linking a contact who replied');
SELECT pg_temp.expect_error(format($q$UPDATE contacts SET job_application_id = %s WHERE email = 'cold@dryrun.example'$q$, pg_temp.app()),
                            'linking an outreach-mode contact');
SELECT pg_temp.expect_error(format($q$UPDATE contacts SET job_application_id = %s WHERE email = 'free@dryrun.example'$q$, pg_temp.closed_app()),
                            'linking to a rejected application');
SELECT pg_temp.expect_error(format($q$INSERT INTO contacts (name, email, mode, stage, job_application_id)
                                     VALUES ('Late', 'late@dryrun.example', 'applied', 'applied_intro_sent', %s)$q$, pg_temp.app()),
                            'creating a linked contact that is already mid-thread');
UPDATE contacts SET job_application_id = pg_temp.app() WHERE email = 'free@dryrun.example';
SELECT pg_temp.expect_eq((SELECT count(*) FROM contacts WHERE job_application_id = pg_temp.app() AND deleted_at IS NULL)::text,
                         '3', 'three live people linked');

-- A linked contact moves through its stages freely; the guard only looks at new links.
UPDATE contacts SET stage = 'applied_intro_drafted' WHERE email = 'dry1@dryrun.example';
SELECT pg_temp.expect_error($q$INSERT INTO contacts (name, email, relationship) VALUES ('Bad', 'bad@dryrun.example', 'friend')$q$,
                            'an unknown relationship');

-- The hold: no direct writes, only the clamped RPC, only on an unapproved preview.
SELECT pg_temp.expect_error(format($q$UPDATE job_applications SET referral_hold_until = now() + interval '1 year' WHERE id = %s$q$, pg_temp.app()),
                            'anon writes referral_hold_until');
SELECT pg_temp.expect_eq((hold_for_referral(pg_temp.app(), 99) <= now() + interval '14 days')::text, 'true', 'hold clamped to 14 days');
SELECT pg_temp.expect_eq((hold_for_referral(pg_temp.app(), 0) >= now() + interval '1 day')::text, 'true', 'hold of at least a day');
SELECT pg_temp.expect_eq((hold_for_referral(pg_temp.app(), NULL) >= now() + interval '10 days')::text, 'true', 'default hold of 10 days');
SELECT pg_temp.expect_error(format('SELECT hold_for_referral(%s, 5)', pg_temp.closed_app()), 'holding a row that is not ready for review');
RESET ROLE;

SET LOCAL ROLE authenticated;
SELECT pg_temp.expect_error(format($q$UPDATE job_applications SET referral_hold_until = NULL WHERE id = %s$q$, pg_temp.app()),
                            'authenticated writes referral_hold_until');
SELECT release_referral_hold(pg_temp.app());
RESET ROLE;
SELECT pg_temp.expect_eq((SELECT referral_hold_until FROM job_applications WHERE id = pg_temp.app())::text, NULL, 'hold released');

-- The hold is outside the revision hash and survives a re-preview.
DO $$
DECLARE before TEXT; after TEXT;
BEGIN
  SELECT preview_revision_hash INTO before FROM job_applications WHERE id = pg_temp.app();
  PERFORM hold_for_referral(pg_temp.app(), 7);
  SELECT preview_revision_hash INTO after FROM job_applications WHERE id = pg_temp.app();
  PERFORM pg_temp.expect_eq(after, before, 'hold leaves preview_revision_hash unchanged');
END $$;
SELECT requeue_preview(pg_temp.app());
SELECT pg_temp.expect_eq((SELECT (referral_hold_until IS NOT NULL)::text FROM job_applications WHERE id = pg_temp.app()), 'true',
                         'hold survives requeue_preview');

-- Deleting an application unlinks its people.
DELETE FROM job_applications WHERE id = pg_temp.app();
SELECT pg_temp.expect_eq((SELECT count(*) FROM contacts WHERE email LIKE 'dry%@dryrun.example' AND job_application_id IS NOT NULL)::text,
                         '0', 'ON DELETE SET NULL');
SELECT pg_temp.expect_eq((SELECT count(*) FROM contacts WHERE email LIKE 'dry%@dryrun.example')::text,
                         '4', 'the people outlive the application');

SELECT 'DRYRUN OK';
ROLLBACK;
