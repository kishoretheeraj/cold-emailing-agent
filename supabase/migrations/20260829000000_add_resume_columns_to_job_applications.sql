-- Phase 3 (resume intelligence) -- job_applications gets resume/cover-letter tracking columns.
--
-- resume_strategy: stage-4 output (section order, projects chosen, CL angle, named gaps) --
--   written by `resume_agent.py --propose`, reviewed by a human before any build happens.
-- resume_file_ref / cover_letter_file_ref: Supabase Storage paths for the built DOCX/PDF --
--   written by `resume_agent.py --build`.
-- resume_variant: which data snapshot/section-choices produced this build (traceability).
-- source_channel / response_date / outcome: outcome-tracking columns the corpus spec's own
--   analysis flagged as the single biggest gap across 30+ manually-tracked applications --
--   schema exists now for manual or future-phase use, not written by this plan's code.
--
-- Additive only: all nullable, no backfill, no destructive change to any existing row. See
-- docs/superpowers/specs/2026-08-29-phase3-resume-intelligence-design.md.

ALTER TABLE job_applications
  ADD COLUMN IF NOT EXISTS resume_strategy JSONB,
  ADD COLUMN IF NOT EXISTS resume_file_ref TEXT,
  ADD COLUMN IF NOT EXISTS cover_letter_file_ref TEXT,
  ADD COLUMN IF NOT EXISTS resume_variant TEXT,
  ADD COLUMN IF NOT EXISTS source_channel TEXT,
  ADD COLUMN IF NOT EXISTS response_date DATE,
  ADD COLUMN IF NOT EXISTS outcome TEXT;
