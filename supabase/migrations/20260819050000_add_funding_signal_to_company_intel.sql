-- Form D funding signal (sub-project 4).
--
-- Adds a "most recent observed private raise" signal to company_intel, sourced
-- from SEC Form D exempt-offering filings via ingest_form_d.py.
--
-- Governance, same as the H-1B sponsorship column: NULL means NOT OBSERVED, not
-- "did not raise". A company may raise through a route that does not file Form D,
-- or file under a different legal entity name than the one on the contact row.
-- The UI must never render a NULL here as a negative claim.
--
-- All columns nullable, no defaults, no backfill. Existing rows are unaffected.

ALTER TABLE company_intel
  ADD COLUMN IF NOT EXISTS last_funding_date       DATE,
  ADD COLUMN IF NOT EXISTS last_funding_amount     BIGINT,
  ADD COLUMN IF NOT EXISTS last_funding_source     TEXT,
  ADD COLUMN IF NOT EXISTS last_funding_checked_at TIMESTAMPTZ;

COMMENT ON COLUMN company_intel.last_funding_date IS
  'Filing date of the most recent observed Form D raise. NULL = not observed, never "did not raise".';
COMMENT ON COLUMN company_intel.last_funding_amount IS
  'TOTALAMOUNTSOLD from the matching Form D offering, in whole US dollars.';
COMMENT ON COLUMN company_intel.last_funding_source IS
  'Provenance of the funding signal. Currently always ''sec_form_d''.';
COMMENT ON COLUMN company_intel.last_funding_checked_at IS
  'When the matcher last evaluated this company, whether or not a raise was found.';

-- Partial index: the review/prioritisation queries only ever ask for companies
-- that HAVE an observed raise, so the large NULL majority stays out of the index.
CREATE INDEX IF NOT EXISTS company_intel_last_funding_date_idx
  ON company_intel (last_funding_date DESC)
  WHERE last_funding_date IS NOT NULL;
