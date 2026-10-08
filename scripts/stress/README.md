# Local stress stack

`tests/test_stress_local.py` runs the real `db.py` against a disposable Postgres 16 with every
migration applied. PostgREST 12 sits in front with Supabase's 1000-row response cap, and the anon
role's real grants apply. `rest_proxy.py` serves it under `/rest/v1`, the prefix the
supabase-py client expects. Nothing here touches a real Supabase project.

```
scripts/stress/up.sh
PGHOST=/tmp/job-agent-stress PGPORT=54329 STRESS_SUPABASE_URL=http://127.0.0.1:54331 \
  python3 -m pytest tests/test_stress_local.py
```

Without `STRESS_SUPABASE_URL` the module is skipped, so the normal suite never needs the stack.

Each scenario reproduced a real failure before its fix. The failures are numbered F1 to F5 in
`docs/superpowers/specs/2026-10-08-fifty-a-day-design.md`.
