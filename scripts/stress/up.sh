#!/usr/bin/env bash
# Bring up the local stress stack: Postgres 16 (every migration applied), PostgREST 12 with
# Supabase's 1000-row cap, and rest_proxy.py serving it under /rest/v1. Local only; never touches
# Supabase. Needs: postgresql-16 binaries, curl, python3 with pyjwt. See README.md here.
set -euo pipefail
cd "$(dirname "$0")/../.."
STATE="${STRESS_STATE:-/tmp/job-agent-stress}"
PGPORT="${PGPORT:-54329}"
PG_BIN="${PG_BIN:-/usr/lib/postgresql/16/bin}"
PGRST_VERSION=v12.2.3
SECRET="${STRESS_JWT_SECRET:-local-stress-secret-local-stress-secret-0123456789}"
mkdir -p "$STATE"
RUN_AS=()
if [ "$(id -u)" = 0 ]; then chown postgres "$STATE"; RUN_AS=(su postgres -c); fi
as_pg() { if [ ${#RUN_AS[@]} -gt 0 ]; then "${RUN_AS[@]}" "$*"; else bash -c "$*"; fi; }

if [ ! -f "$STATE/data/PG_VERSION" ]; then
  as_pg "$PG_BIN/initdb -D $STATE/data -A trust -U postgres" >/dev/null
fi
if ! as_pg "$PG_BIN/pg_ctl -D $STATE/data status" >/dev/null 2>&1; then
  as_pg "$PG_BIN/pg_ctl -D $STATE/data -o '-p $PGPORT -k $STATE -c max_connections=200' -l $STATE/pg.log start" >/dev/null
  sleep 2
fi
export PGHOST="$STATE" PGPORT PGUSER=postgres
scripts/local_dryrun/build_db.sh stress
psql -q -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticator') THEN CREATE ROLE authenticator LOGIN NOINHERIT; END IF; END \$\$" \
     -c "GRANT anon, authenticated, service_role TO authenticator" >/dev/null

if [ ! -x "$STATE/postgrest" ]; then
  curl -sSL -o "$STATE/pgrst.tar.xz" "https://github.com/PostgREST/postgrest/releases/download/$PGRST_VERSION/postgrest-$PGRST_VERSION-linux-static-x64.tar.xz"
  tar -C "$STATE" -xf "$STATE/pgrst.tar.xz"
fi
cat > "$STATE/pgrst.conf" <<CONF
db-uri = "postgres://authenticator@/stress?host=$STATE&port=$PGPORT"
db-schemas = "public"
db-anon-role = "anon"
jwt-secret = "$SECRET"
server-port = 54330
db-pool = 20
db-max-rows = 1000
CONF
if ! curl -s -o /dev/null localhost:54330/; then
  nohup "$STATE/postgrest" "$STATE/pgrst.conf" > "$STATE/pgrst.log" 2>&1 &
  sleep 2
else
  psql -q -d stress -c "NOTIFY pgrst, 'reload schema'"
fi
if ! curl -s -o /dev/null localhost:54331/rest/v1/; then
  nohup python3 scripts/stress/rest_proxy.py 54331 54330 > "$STATE/proxy.log" 2>&1 &
  sleep 1
fi
echo "stress stack up. Run:"
echo "  PGHOST=$STATE PGPORT=$PGPORT STRESS_SUPABASE_URL=http://127.0.0.1:54331 python3 -m pytest tests/test_stress_local.py"
