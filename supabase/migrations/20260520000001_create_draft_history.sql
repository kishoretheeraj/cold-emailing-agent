-- draft_history: tracks the lifecycle of every Gmail draft created by the agent.
-- Stores draft content + gmail_draft_id (needed by /api/send-draft to call drafts.send).
-- Also stores what was ultimately sent for edit-detection on the /queue page.
-- RLS disabled — consistent with all other tables in this project.

CREATE TABLE draft_history (
  id             SERIAL PRIMARY KEY,
  contact_id     INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  stage          TEXT NOT NULL,
  subject        TEXT,
  body           TEXT,
  message_id     TEXT,
  gmail_draft_id TEXT,
  drafted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  sent_subject   TEXT,
  sent_body      TEXT,
  sent_at        TIMESTAMPTZ,
  edit_detected  BOOLEAN
);

CREATE INDEX idx_draft_history_contact_id
  ON draft_history (contact_id, drafted_at DESC);

CREATE INDEX idx_draft_history_gmail_draft_id
  ON draft_history (gmail_draft_id)
  WHERE gmail_draft_id IS NOT NULL;
