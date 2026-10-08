#!/usr/bin/env bash
# Local rehearsal of db_migrate.yml's dryrun mode against a disposable Postgres (16+):
#   PGHOST=/tmp PGPORT=54329 PGUSER=postgres scripts/local_dryrun/run.sh <migration.sql> [<test.sql>]
# Builds a fresh database from setup_supabase.sql plus every migration older than the one under
# test, then runs sql_guard's composed BEGIN/migration/test/ROLLBACK. setup_supabase.sql does not
# create every base table, so a few unrelated old migrations fail and are listed, not fatal.
set -euo pipefail
cd "$(dirname "$0")/../.."
migration="$1"; test="${2:-}"
db="dryrun_$$"
psql -q -v ON_ERROR_STOP=1 -c "CREATE DATABASE $db" >/dev/null
trap 'psql -q -c "DROP DATABASE IF EXISTS $db" >/dev/null' EXIT
psql -q -v ON_ERROR_STOP=1 -d "$db" -f scripts/local_dryrun/supabase_stub.sql >/dev/null
psql -q -v ON_ERROR_STOP=1 -d "$db" -f setup_supabase.sql >/dev/null 2>&1
for f in $(ls supabase/migrations/*.sql | sort); do
  [[ "$(basename "$f")" < "$migration" ]] || continue
  psql -q -v ON_ERROR_STOP=1 -d "$db" -f "$f" >/dev/null 2>&1 || echo "skipped (base table missing locally): $(basename "$f")"
done
composed="$(mktemp)"
python3 scripts/sql_guard.py --migration "$migration" ${test:+--test "$test"} --out "$composed"
psql -q -t -v ON_ERROR_STOP=1 -d "$db" -f "$composed" 2>&1 | grep -E "DRYRUN|ERROR" || true
