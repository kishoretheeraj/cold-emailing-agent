-- Cold Email Agent — Supabase Schema
-- Paste this entire file into Supabase SQL Editor and click Run.

CREATE TABLE IF NOT EXISTS contacts (
  id                SERIAL PRIMARY KEY,
  name              TEXT NOT NULL,
  email             TEXT UNIQUE NOT NULL,
  company           TEXT,
  role              TEXT,
  detail            TEXT,
  tier              INT DEFAULT 2 CHECK (tier IN (1, 2, 3)),

  -- MODE: controls which pipeline runs
  -- 'outreach' = cold intro to new people (4-email sequence)
  -- 'applied'  = HM email after applying to a job (2-email max)
  mode              TEXT DEFAULT 'outreach' CHECK (mode IN ('outreach', 'applied')),

  -- SHARED TRACKING
  stage             TEXT DEFAULT 'new',
  reply_status      TEXT DEFAULT 'no_reply',
  dartmouth         BOOLEAN DEFAULT FALSE,
  template_current  TEXT DEFAULT 'cold_intro',
  followup_date     DATE,
  last_emailed      DATE,
  notes             TEXT,

  -- MODE B (applied) ONLY — leave NULL for outreach contacts
  job_title         TEXT,
  job_description   TEXT,
  company_applied   TEXT,
  applied_date      DATE,

  created_at        TIMESTAMPTZ DEFAULT NOW(),
  updated_at        TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for fast daily queries
CREATE INDEX IF NOT EXISTS idx_contacts_stage_followup ON contacts(stage, followup_date);
CREATE INDEX IF NOT EXISTS idx_contacts_mode ON contacts(mode);

-- Auto-update updated_at on any row change
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS contacts_updated_at ON contacts;
CREATE TRIGGER contacts_updated_at
  BEFORE UPDATE ON contacts
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ─────────────────────────────────────────────────────────────────────────────
-- STAGE REFERENCE
-- ─────────────────────────────────────────────────────────────────────────────
--
-- Mode A (outreach) stages in order:
--   new → first_touch_drafted → first_touch_sent
--       → followup1_drafted   → followup1_sent
--       → followup2_drafted   → followup2_sent
--       → breakup_drafted     → breakup_sent → closed
--
-- Mode B (applied) stages in order:
--   new → applied_intro_drafted    → applied_intro_sent
--       → applied_followup_drafted → applied_followup_sent → closed
--
-- Reply status values (you update these manually):
--   no_reply | replied | interested | call_scheduled | dead
--
-- ─────────────────────────────────────────────────────────────────────────────
-- SAMPLE ROWS (delete or modify before using in production)
-- ─────────────────────────────────────────────────────────────────────────────

-- Mode A sample
INSERT INTO contacts (name, email, company, role, detail, tier, mode)
VALUES (
  'Test Outreach',
  'test.outreach@example.com',
  'TestCo',
  'VP Product',
  'launched fintech lending product last quarter',
  2,
  'outreach'
);

-- Mode B sample (replace job_description with a real JD before running agent)
INSERT INTO contacts (
  name, email, company, role, mode,
  job_title, applied_date,
  job_description
) VALUES (
  'Test HiringManager',
  'test.hm@example.com',
  'TestCo',
  'Director of Product',
  'applied',
  'Senior PM, Financial Infrastructure',
  CURRENT_DATE,
  'We are looking for a Senior PM to lead our financial infrastructure products.
   Requirements: 3+ years PM experience, fintech background preferred,
   experience with APIs and developer products, strong data skills,
   track record of shipping products at scale.'
);
