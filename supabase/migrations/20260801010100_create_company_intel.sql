-- company_intel: one row per normalized target company (a value seen in
-- contacts.company), joined to employer_h1b_stats via entity resolution.
-- This is the "what do we know about this target company" row, deliberately
-- not named after any single signal — typical_wage_level and
-- cap_exempt_likely are extension points for Stage 2/3 of the visa & wage
-- intelligence gate, always NULL until those stages ship.
--
-- Governance rule (enforced in application code, not the DB): Stage 1
-- ingestion/matching code only ever writes sponsors_h1b as NULL or TRUE.
-- FALSE requires an explicit human "confirmed" decision via the match
-- review screen. NULL is the permanent, safe "unknown" state — a missed
-- or excluded match must never present as a false negative.
--
-- RLS disabled — consistent with all other tables in this project.

CREATE TABLE IF NOT EXISTS company_intel (
  id                  SERIAL PRIMARY KEY,
  normalized_name     TEXT NOT NULL,
  raw_company_names   TEXT[] NOT NULL DEFAULT '{}',
  matched_employer_id INTEGER NULL REFERENCES employer_h1b_stats(id) ON DELETE SET NULL,
  match_confidence    NUMERIC(5,2) NULL,
  match_status        TEXT NOT NULL DEFAULT 'unknown'
    CHECK (match_status IN ('unknown', 'auto', 'needs_review', 'confirmed', 'rejected')),
  top_candidates      JSONB NULL,
  sponsors_h1b        BOOLEAN NULL,
  h1b_recent_count    INTEGER NULL,
  latest_filing_fy    INTEGER NULL,
  approval_rate       NUMERIC(5,4) NULL,
  typical_wage_level  TEXT NULL,
  cap_exempt_likely   BOOLEAN NULL,
  source_vintages     JSONB NULL,
  reviewed_by_user_at TIMESTAMPTZ NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_company_intel_normalized_name
  ON company_intel (normalized_name);

CREATE INDEX IF NOT EXISTS idx_company_intel_needs_review
  ON company_intel (match_status)
  WHERE match_status = 'needs_review';
