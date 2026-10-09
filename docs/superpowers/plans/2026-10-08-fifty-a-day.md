# Fifty a day: plan

Spec: [2026-10-08-fifty-a-day-design.md](../specs/2026-10-08-fifty-a-day-design.md). Each task follows the same TDD
loop: write the failing test (or the stress scenario that reproduces the failure), then the fix, then green.

- [x] 1. `job_identity.py`: hostname classification and canonical keys for every URL family; `ats_platform.classify` delegates to it (F1, F6).
- [x] 2. Migration `20261009000000`:
  - new columns `job_key`, `platform`, `company_key`, `title_key`, `location`, `posted_at`, `jd_fingerprint`, `prepare_attempts`, `pick_attempts`;
  - backfill plus unique `job_key` index, with anon grants for the new columns;
  - `claim_application` counts attempts and `requeue_preview` resets them;
  - the `job_boards` table.
- [x] 3. `db.create_job_application`: dedup by key, title identity and repost fingerprint; a unique violation returns None and is never retried (F1, F2).
- [x] 4. `job_filters.py` and the `job_search_preferences` prompt row: titles, seniority, locations (Career-Ops tiers), posting age, sponsorship (F7, F8).
- [x] 5. `job_sources.py`:
  - sources: Simplify feed, full Greenhouse/Ashby/Lever boards (filter before cap), Workday CXS;
  - board registry learned from the feed (F9);
  - `job_sourcing.py` runner.
- [x] 6. `job_liveness.py`: ATS-API liveness before the resume is built.
- [x] 7. Prepare queue: server-side `preview_candidates` ordered by score, attempts and backoff; `run_prepare` claims until `limit` (F3 to F5).
- [x] 8. Resume queue: preparable platforms first, then score; company cap; liveness gate (F15).
- [x] 9. `job_pick` on the subscription: batched judge, failures retried, Beelink timer; the workflow only pulls (F10).
- [x] 10. Applying:
  - attachment file names and temp cleanup (F11);
  - Submit resolved before the click (F12);
  - confirmation polling and the Greenhouse emailed code (F13);
  - daily and per-company caps (F14).
- [x] 11. Stress harness:
  - `tests/test_stress_local.py` against the local stack;
  - capacity model (`scripts/stress/capacity.py`, written 2026-10-08 after this box was first checked by mistake);
  - a 50-row real-browser preview load test.
- [x] 12. Contact-manager: the daily cap meter (`/api/applications/today`), location, source and posted date on queue cards, submitted list bounded to 14 days. (Each card also shows how many applications went to that company in the last 30 days.)
- [x] 13. Docs (`CLAUDE.md`, `db-schema.md`, `beelink-server.md`, RUNBOOK) and memory.
