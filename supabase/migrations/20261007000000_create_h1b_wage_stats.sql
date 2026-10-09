-- Offered H-1B wages per employer, role family and worksite state, for salary answers on
-- application forms (salary_estimate.py).
--
-- Extends the existing DOL OFLC LCA ingestion (ingest_oflc_lca.py): the same quarterly files that
-- feed employer_h1b_stats carry each filing's JOB_TITLE, WAGE_RATE_OF_PAY_FROM and
-- WAGE_UNIT_OF_PAY, which were previously dropped. normalized_name uses the same key as
-- employer_h1b_stats.normalized_name. '*' is the market row: normalized_name '*' pools every
-- employer, worksite_state '*' pools every state.
--
-- Decision support only, like the rest of the visa data: a missing row means "no filings
-- observed", never a statement about what an employer pays.

CREATE TABLE IF NOT EXISTS h1b_wage_stats (
  id              BIGSERIAL PRIMARY KEY,
  normalized_name TEXT NOT NULL,
  role_family     TEXT NOT NULL,
  worksite_state  TEXT NOT NULL,
  filings         INTEGER NOT NULL,
  wage_p25        INTEGER NOT NULL,
  wage_median     INTEGER NOT NULL,
  wage_p75        INTEGER NOT NULL,
  fiscal_years    INTEGER[] NOT NULL DEFAULT '{}',
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_h1b_wage_stats_key
  ON h1b_wage_stats (normalized_name, role_family, worksite_state);

-- Reads use the anon key (salary_estimate.py). Writes use the service-role key, which bypasses
-- RLS, so anon and authenticated get no INSERT/UPDATE/DELETE.
ALTER TABLE h1b_wage_stats ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS h1b_wage_stats_anon_read ON h1b_wage_stats;
CREATE POLICY h1b_wage_stats_anon_read ON h1b_wage_stats FOR SELECT TO anon USING (true);
GRANT SELECT ON h1b_wage_stats TO anon;
GRANT USAGE, SELECT ON SEQUENCE h1b_wage_stats_id_seq TO anon, authenticated;
