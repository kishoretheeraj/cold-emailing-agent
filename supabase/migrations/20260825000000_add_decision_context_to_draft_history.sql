-- decision_context: which live prompt configuration produced this draft.
--
-- Sub-project 3, Part B. One nullable JSONB column. Additive, no backfill,
-- no default, no index. Existing rows are unaffected and stay NULL forever --
-- the prompt snapshot behind a historical draft was never captured, so no
-- backfill is possible.
--
-- Governance, same posture as company_intel's funding columns: NULL means NOT
-- INSTRUMENTED, never "no context" and never zero. engagement_report.py renders
-- NULL as "unknown".
--
-- Shape today: {"prompt_hash": "3f9a1c2b7e0d4f6a"} -- SHA-256 of the live
-- prompts dict passed to that draft's generation call, first 16 hex chars.
-- JSONB rather than a typed column so a future signal (e.g. a stored critic
-- score) can be added without another migration -- the agent_events.metadata
-- precedent.

ALTER TABLE draft_history
  ADD COLUMN IF NOT EXISTS decision_context JSONB;

COMMENT ON COLUMN draft_history.decision_context IS
  'Prompt-set fingerprint in effect when this draft was generated: {"prompt_hash": "<16 hex>"}. NULL = not instrumented, never "no context".';
