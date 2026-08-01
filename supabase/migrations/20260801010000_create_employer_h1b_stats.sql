-- employer_h1b_stats: aggregated per-employer H-1B filing statistics from
-- DOL OFLC LCA disclosure files + USCIS H-1B Employer Data Hub.
-- One row per resolved/consolidated employer identity, NOT one row per raw
-- filer name — see entity_resolution.py for the normalization/consolidation
-- pass that produces normalized_name.
--
-- Materiality-filtered on ingestion (only employers with lca_recent_2fy >= 1
-- are written, hard-capped by row count) to stay well under the Supabase
-- free-tier storage limit. An employer absent from this table means
-- "unknown / not in the filtered corpus", never "confirmed non-sponsor" —
-- see company_intel.sponsors_h1b for the governance rule that depends on
-- this distinction.
--
-- RLS disabled — consistent with all other tables in this project.

CREATE TABLE IF NOT EXISTS employer_h1b_stats (
  id                SERIAL PRIMARY KEY,
  normalized_name   TEXT NOT NULL,
  display_name      TEXT NOT NULL,
  aliases           TEXT[] NOT NULL DEFAULT '{}',
  lca_total         INTEGER NOT NULL DEFAULT 0,
  lca_recent_2fy    INTEGER NOT NULL DEFAULT 0,
  distinct_socs     INTEGER NOT NULL DEFAULT 0,
  latest_filing_fy  INTEGER NULL,
  worksite_states   TEXT[] NOT NULL DEFAULT '{}',
  wage_level_dist   JSONB NOT NULL DEFAULT '{}',
  uscis_approvals   INTEGER NULL,
  uscis_denials     INTEGER NULL,
  approval_rate     NUMERIC(5,4) NULL,
  naics_code        TEXT NULL,
  source_vintages   JSONB NOT NULL DEFAULT '{}',
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_employer_h1b_stats_normalized_name
  ON employer_h1b_stats (normalized_name);
