-- Phase 2.5 (auto-apply agent) -- job_applications gets job-pick scoring columns and an
-- apply-preview column, plus a new 'ready_to_submit' stage.
--
-- pick_verdict / pick_score / pick_reasoning: job_pick.py's three-stage scoring output --
--   verdict is 'strong'/'maybe'/'no', score is Stage 2's embedding cosine similarity (null if
--   Stage 1 already rejected the row), reasoning is Stage 3's brief rationale text. Written once
--   per row by job_pick.py; never overwritten afterward (rescoring a stale verdict is a future
--   concern, not this phase's).
-- apply_preview: apply_agent.py --preview's filled-field-values + generated screening-question
--   answers, JSONB (schemaless by design, same reasoning as posting_snapshot -- the field set
--   varies per platform and per posting). Read by the contact-manager review UI, consumed again
--   by --submit at approval time.
-- apply_blocked_reason: set whenever job_pick.py or apply_agent.py --preview skips a row instead
--   of proceeding (Workday, an aggregator link, a CAPTCHA hit, an unrecognized field). Absence
--   means "not blocked," never "confirmed clean" -- same degrade-to-unknown posture as the H-1B
--   visa gate.
--
-- The stage CHECK constraint must be dropped and recreated (not just widened via a second
-- constraint) since Postgres has no "add one more allowed value" shorthand -- still additive in
-- effect: every existing allowed value stays allowed, only 'ready_to_submit' is added.
--
-- See docs/superpowers/specs/2026-08-30-phase2.5-auto-apply-design.md.

ALTER TABLE job_applications
  ADD COLUMN IF NOT EXISTS pick_verdict TEXT,
  ADD COLUMN IF NOT EXISTS pick_score NUMERIC,
  ADD COLUMN IF NOT EXISTS pick_reasoning TEXT,
  ADD COLUMN IF NOT EXISTS apply_preview JSONB,
  ADD COLUMN IF NOT EXISTS apply_blocked_reason TEXT;

ALTER TABLE job_applications DROP CONSTRAINT IF EXISTS job_applications_stage_check;

ALTER TABLE job_applications
  ADD CONSTRAINT job_applications_stage_check
  CHECK (stage IN ('saved','ready_to_submit','applied','phone_screen','onsite','offer','rejected','withdrawn','accepted'));
