-- Application outcomes (application_outcomes.py, run from monitor.py): a rejection or interview invite
-- read from the receipt mailbox moves job_applications.stage and stores the email here, with the stage
-- it replaced so the queue's Undo can restore it. A message already stored is never applied again, so
-- an undone outcome stays undone.
--
-- stage is already anon-writable (the UI's stage select and Skip); outcome_evidence needs its own
-- column grant, for anon (the monitor's key) and authenticated (kept identical to anon, see
-- 20261004000001). It is not part of preview_revision_hash, so it never touches an approval.

ALTER TABLE job_applications ADD COLUMN IF NOT EXISTS outcome_evidence JSONB NULL;

COMMENT ON COLUMN job_applications.outcome_evidence IS
  'Email that last moved this row''s stage (application_outcomes.py): source, kind (rejection|interview), '
  'message_id, from, subject, date, previous_stage. NULL means no outcome email was read, never "no outcome".';

GRANT UPDATE (outcome_evidence) ON job_applications TO anon, authenticated;
