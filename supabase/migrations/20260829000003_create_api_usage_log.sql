-- System-wide Claude API cost/token tracking -- a single append-only ledger covering every
-- Claude call across the whole codebase (agent.py/emailer.py's outreach and critic calls,
-- monitor.py's reply classification, reply_drafter.py's reply generation, research.py's query
-- generation and curation, resume_agent.py's strategy and cover-letter calls), not just the
-- per-application accumulator columns Phase 3 added to job_applications.
--
-- Real, not estimated: input_tokens/output_tokens/cost_usd are always computed from the actual
-- Anthropic API response's usage field and config.MODEL_PRICING's verified per-model rates.
--
-- contact_id and job_application_id are both nullable and mutually exclusive in practice (a call
-- is either about a contact-based outreach/reply flow or a job_applications resume flow, never
-- both) -- kept as two separate nullable columns rather than one polymorphic pair so simple joins
-- against either table stay straightforward.
--
-- Additive only: new table, no changes to any existing table or row.

CREATE TABLE IF NOT EXISTS api_usage_log (
  id BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  module TEXT NOT NULL,
  action TEXT,
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  cost_usd NUMERIC(10,6) NOT NULL,
  contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  job_application_id INTEGER REFERENCES job_applications(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_api_usage_log_created_at ON api_usage_log (created_at);
CREATE INDEX IF NOT EXISTS idx_api_usage_log_module ON api_usage_log (module);
