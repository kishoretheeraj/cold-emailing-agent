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
