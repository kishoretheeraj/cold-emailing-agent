-- Resume generation moves to the operator's Claude subscription (claude -p on the Beelink).
-- See docs/superpowers/specs/2026-10-04-resume-subscription-transport-design.md.

-- 1. api_usage_log.billing: 'subscription' rows carry real token counts and cost_usd = 0, so the
--    ledger's dollar totals keep meaning "API spend". Existing and API rows default to 'api'.
ALTER TABLE api_usage_log
  ADD COLUMN IF NOT EXISTS billing TEXT NOT NULL DEFAULT 'api'
  CHECK (billing IN ('api', 'subscription'));

-- 2. job_applications.resume_error: set by resume_agent.py --drain when a strong-verdict row's
--    propose/build fails for a non-transient reason, so the Beelink worker stops retrying it
--    against the subscription window. NULL = eligible. Clearing it re-queues the row.
ALTER TABLE job_applications ADD COLUMN IF NOT EXISTS resume_error TEXT;

-- job_applications uses column-level grants for anon (20260925000000), computed once, and
-- authenticated was made to mirror anon as a one-time snapshot (20261004000001). A new column has
-- no privilege for either role unless granted here. The worker writes it with the anon key.
GRANT UPDATE (resume_error) ON job_applications TO anon;
GRANT UPDATE (resume_error) ON job_applications TO authenticated;
