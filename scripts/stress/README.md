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

## Capacity model

`python3 scripts/stress/capacity.py [--target 50] [--json]` reads every Beelink unit's `OnCalendar` and
`TimeoutStartSec`, plus the batch sizes and caps in `config.py`. It walks the funnel backwards from the
target. For each stage it prints its daily ceiling, what the target demands of it, and its utilization.
It then names the bottleneck. Prepare and submit share display :1, so their seconds add up. The rates and
per-row seconds are labeled assumptions; override them (`--strong-rate 0.4` ...) with a week of
`apply_status` numbers. It exits 1 when the target is out of reach.

The first run (2026-10-08, assumptions as shipped) says the machine is not the limit. Resume building is
the busiest worker stage at 61%, and the display is busy about 4.7 h a day. The limit is supply: 50
submissions need about 700 new postings a day at the assumed funnel rates.

Measured the same day: the Simplify feed added 28 new Product postings in 14 days, and 23 passed the
default triage. That is about 1.6 a day. Its Software, AI/ML/Data, Quant and Hardware postings are
almost all engineering titles, and triage rejects them correctly. Board sweeps, JobRight and LinkedIn
(at most about 75 postings viewed a day) supply the rest. That supply has to be measured on the
Beelink, because this sandbox cannot reach the ATS APIs.
