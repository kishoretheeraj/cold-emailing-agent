-- job_applications: application-tracking layer, separate from contacts.stage.
--
-- Sub-project: full-fledged buildout, Phase 1. contacts.stage tracks the
-- OUTREACH relationship lifecycle (new -> *_drafted -> *_sent); it has no
-- concept of "applied to req #X at Company Y, now onsite." This table is
-- that missing pipeline, deliberately independent so it never touches the
-- four mirrored first-touch stage/action sets documented in the root
-- CLAUDE.md (agent.py, emailer.py, monitor.detect_sent_drafts,
-- engagement_report._FIRST_TOUCH_DRAFTED_STAGES).
--
-- contact_id is nullable + ON DELETE SET NULL: an application can exist
-- without a known contact (e.g. applied cold via a job board before any
-- outreach contact exists for that company). contacts.id is INTEGER, not
-- UUID, so this column must match.
--
-- posting_snapshot is JSONB (not typed columns) so future scraped fields
-- (salary, location, description excerpt, source-specific ids) can be
-- added without another migration -- same reasoning as
-- draft_history.decision_context.

CREATE TABLE IF NOT EXISTS job_applications (
  id BIGSERIAL PRIMARY KEY,
  contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  company TEXT NOT NULL,
  role TEXT NOT NULL,
  job_url TEXT,
  source TEXT,
  stage TEXT NOT NULL DEFAULT 'saved'
    CHECK (stage IN ('saved','applied','phone_screen','onsite','offer','rejected','withdrawn','accepted')),
  applied_date DATE,
  notes TEXT,
  posting_snapshot JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_job_applications_stage ON job_applications(stage);
CREATE INDEX IF NOT EXISTS idx_job_applications_contact_id ON job_applications(contact_id);

COMMENT ON TABLE job_applications IS
  'Application-tracking pipeline (saved -> applied -> ... -> offer/rejected), independent of contacts.stage which tracks outreach only.';
