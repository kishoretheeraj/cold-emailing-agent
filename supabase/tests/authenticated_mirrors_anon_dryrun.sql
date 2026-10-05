-- Run against the linked DB as: BEGIN; <migration 20261004000001>; <this file>  (ends in ROLLBACK)
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM (
    (SELECT column_name, privilege_type FROM information_schema.column_privileges WHERE table_name='job_applications' AND grantee='anon'
     EXCEPT SELECT column_name, privilege_type FROM information_schema.column_privileges WHERE table_name='job_applications' AND grantee='authenticated')
    UNION ALL
    (SELECT column_name, privilege_type FROM information_schema.column_privileges WHERE table_name='job_applications' AND grantee='authenticated'
     EXCEPT SELECT column_name, privilege_type FROM information_schema.column_privileges WHERE table_name='job_applications' AND grantee='anon')) d;
  IF n <> 0 THEN RAISE EXCEPTION 'DRYRUN FAIL: % column-privilege differences', n; END IF;
  IF has_column_privilege('authenticated','job_applications','approved_revision_hash','UPDATE')
     OR has_column_privilege('authenticated','job_applications','approved_at','UPDATE') THEN
    RAISE EXCEPTION 'DRYRUN FAIL: authenticated can still write approval columns'; END IF;
  IF NOT has_column_privilege('authenticated','job_applications','notes','UPDATE') THEN
    RAISE EXCEPTION 'DRYRUN FAIL: authenticated lost notes UPDATE (anon has it)'; END IF;
END $$;
SET LOCAL ROLE authenticated;
DO $$ BEGIN
  UPDATE job_applications SET approved_revision_hash = 'x' WHERE id = -1;
  RAISE EXCEPTION 'DRYRUN FAIL: authenticated UPDATE approved_revision_hash succeeded';
EXCEPTION WHEN insufficient_privilege THEN NULL; END $$;
RESET ROLE;
SELECT 'AUTH DRYRUN OK';
ROLLBACK;
