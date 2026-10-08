# Warm paths: plan

Spec: [2026-10-08-warm-paths-design.md](../specs/2026-10-08-warm-paths-design.md), reviewed twice by an
advisor (APPROVE WITH CHANGES both times; §7 and §8 map every finding). Each task follows the same loop: write the
failing test, make the change, get to green.

- [x] 1. Agent gates I1 to I4: `_application_gate`, `_attach_applications` (fails closed), `_with_application_fields` (applied mode only), plus the `run()` wiring.
- [x] 2. `db.get_application_states` (chunked) and paged `db.get_all_contacts` (I8).
- [x] 3. Migration `20261010000000`:
  - link columns;
  - the `contacts_link_guard` trigger (definer, row lock, restore check);
  - the hold RPCs.
  - Dry run plus 12 mutations; static tests.
- [x] 4. `warmPaths.ts` (pure) and `warmPathsData.ts` (queries); the shared `company_keys.json` fixture.
- [x] 5. Routes: people GET/POST, link, unlink, hold; queue people counts; PATCH `applied_date`.
- [x] 6. UI:
  - `PeoplePanel`;
  - queue card (People, Ask for a referral first, held badge, counts);
  - detail sheet section.
- [x] 7. Reply drafts carry the application (first attempt and retry; closed means no ask).
- [x] 8. `engagement_report` warm-path section with caveats.
- [x] 9. Stress stack:
  - TypeScript queries against PostgREST (1000-row cap, 6-way race);
  - Python decision pass over 1,350 linked and unlinked contacts plus 50 applications;
  - 12-thread link race.
- [x] 10. Playwright `22-warm-paths.spec.ts` with screenshot; docs (`CLAUDE.md` x2, `db-schema.md`) and memory.

Later, deliberately not in this change:
- a sponsorship signal on queue cards (needs a TypeScript `entity_resolution` mirror);
- copyable LinkedIn connection notes.
