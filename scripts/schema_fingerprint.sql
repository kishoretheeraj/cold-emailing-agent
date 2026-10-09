-- One md5 over the public schema's shape: columns, functions (by definition), indexes, table
-- and column grants, triggers. db_migrate.yml's dryrun mode runs this before and after the
-- rolled-back dry run and fails if the two differ -- proof the dry run persisted nothing.
SELECT md5(coalesce(string_agg(x, '|' ORDER BY x), '')) AS schema_fingerprint FROM (
  SELECT 'c:' || table_name || '.' || column_name || ':' || data_type || ':' || coalesce(column_default, '')
    FROM information_schema.columns WHERE table_schema = 'public'
  UNION ALL
  SELECT 'f:' || p.oid::regprocedure::text || ':' || md5(pg_get_functiondef(p.oid))
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p')
  UNION ALL
  SELECT 'i:' || indexname || ':' || md5(indexdef) FROM pg_indexes WHERE schemaname = 'public'
  UNION ALL
  SELECT 'g:' || grantee || ':' || table_name || ':' || privilege_type
    FROM information_schema.role_table_grants WHERE table_schema = 'public'
  UNION ALL
  SELECT 'k:' || grantee || ':' || table_name || ':' || column_name || ':' || privilege_type
    FROM information_schema.column_privileges WHERE table_schema = 'public'
  UNION ALL
  SELECT 't:' || event_object_table || ':' || trigger_name || ':' || md5(action_statement)
    FROM information_schema.triggers WHERE trigger_schema = 'public'
) s(x);
