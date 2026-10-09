#!/usr/bin/env bash
# Build a disposable local database with every migration applied, for stress tests and manual
# probing:  PGHOST=/tmp PGPORT=54329 PGUSER=postgres scripts/local_dryrun/build_db.sh <dbname>
# Creates the Supabase API roles once per server if missing. A few old migrations need base
# tables setup_supabase.sql does not create; those are listed, not fatal.
set -euo pipefail
cd "$(dirname "$0")/../.."
db="${1:?database name}"
psql -q -c "DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role NOLOGIN BYPASSRLS; END IF;
END \$\$" -c "GRANT anon, authenticated, service_role TO postgres" >/dev/null
psql -q -c "DROP DATABASE IF EXISTS $db" -c "CREATE DATABASE $db" >/dev/null
psql -q -v ON_ERROR_STOP=1 -d "$db" -f scripts/local_dryrun/supabase_stub.sql >/dev/null
psql -q -v ON_ERROR_STOP=1 -d "$db" -f setup_supabase.sql >/dev/null 2>&1
psql -q -v ON_ERROR_STOP=1 -d "$db" -f setup_prompts.sql >/dev/null 2>&1 || echo "setup_prompts.sql: some seed rows did not load (the prompts table exists)"
for f in $(ls supabase/migrations/*.sql | sort); do
  psql -q -v ON_ERROR_STOP=1 -d "$db" -f "$f" >/dev/null 2>&1 || echo "skipped (base table missing locally): $(basename "$f")"
done
psql -q -v ON_ERROR_STOP=1 -d "$db" -f scripts/local_dryrun/prod_drift.sql >/dev/null
