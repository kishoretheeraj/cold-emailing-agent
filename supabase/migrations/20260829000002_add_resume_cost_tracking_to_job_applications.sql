-- Phase 3 (resume intelligence) -- token/cost tracking per job_applications row.
--
-- resume_tokens_input / resume_tokens_output: cumulative Claude API token counts across every
--   resume_agent.py call for this row (--propose's strategy call, --build's cover-letter call
--   and any lint-failure retry).
-- resume_cost_usd: cumulative USD cost computed from those tokens at config.py's
--   RESUME_MODEL_COST_PER_MTOK_INPUT/OUTPUT rates.
--
-- Additive only: all nullable, no backfill, no destructive change to any existing row.

ALTER TABLE job_applications
  ADD COLUMN IF NOT EXISTS resume_tokens_input INTEGER,
  ADD COLUMN IF NOT EXISTS resume_tokens_output INTEGER,
  ADD COLUMN IF NOT EXISTS resume_cost_usd NUMERIC(10,6);
