-- Make the `authenticated` role's privileges on job_applications exactly equal anon's.
--
-- Why: email signup is enabled on this project, so anyone can mint an authenticated JWT with the
-- public anon key. 20260925000000 converted only anon to column-level grants; authenticated kept
-- table-level UPDATE/INSERT, including approved_at / approved_revision_hash. Combined with an
-- anon-writable apply_preview (and a readable trigger-owned preview_revision_hash), that let a
-- signed-up user edit an approved preview and re-forge the approval hash. 20261004000000 only
-- removed the three lifecycle columns for authenticated.
--
-- The app never uses the authenticated role (every writer uses the anon key), so mirroring anon
-- costs nothing. Derived dynamically so it stays exact whatever anon currently holds.

DO $$
DECLARE
  r RECORD;
BEGIN
  REVOKE ALL ON job_applications FROM authenticated;

  FOR r IN
    SELECT privilege_type FROM information_schema.table_privileges
    WHERE table_schema = 'public' AND table_name = 'job_applications' AND grantee = 'anon'
  LOOP
    EXECUTE format('GRANT %s ON job_applications TO authenticated', r.privilege_type);
  END LOOP;

  FOR r IN
    SELECT privilege_type, column_name FROM information_schema.column_privileges
    WHERE table_schema = 'public' AND table_name = 'job_applications' AND grantee = 'anon'
      AND privilege_type IN ('UPDATE', 'INSERT')
      AND NOT EXISTS (
        SELECT 1 FROM information_schema.table_privileges t
        WHERE t.table_schema = 'public' AND t.table_name = 'job_applications'
          AND t.grantee = 'anon' AND t.privilege_type = column_privileges.privilege_type)
  LOOP
    EXECUTE format('GRANT %s (%I) ON job_applications TO authenticated', r.privilege_type, r.column_name);
  END LOOP;
END;
$$;
