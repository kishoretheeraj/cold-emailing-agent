-- Links contacts to their resolved company_intel row so list filters and
-- badges can use a plain Supabase embedded-resource select instead of a
-- client-side join. NULL is the default and permanent "not yet matched"
-- state — see company_intel migration for the governance rule this
-- depends on.

ALTER TABLE contacts ADD COLUMN IF NOT EXISTS company_intel_id INTEGER NULL
  REFERENCES company_intel(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_contacts_company_intel_id
  ON contacts (company_intel_id)
  WHERE company_intel_id IS NOT NULL;
