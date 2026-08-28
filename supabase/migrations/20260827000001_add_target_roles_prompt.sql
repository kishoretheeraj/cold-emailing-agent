-- target_roles prompts row — full-fledged buildout, Phase 2 (job & company discovery).
--
-- Every existing "role" key in config.py/prompts means the CONTACT's role (used to personalize
-- outreach). This is the first key that means the USER's own target role(s) — job_discovery.py
-- filters scanned postings against it. Newline-delimited, same convention as
-- guardrail_company_list/forbidden_phrases (sort_orders 62-63); this is sort_order 65, one past
-- voice_dna (64), the highest existing key.
--
-- Seeded with a single reasonable default. Edit via the contact-manager's Prompts page — no code
-- change needed to change it, same as every other schema-driven prompt.

INSERT INTO prompts (key, value, display_title, description, default_value, sort_order, updated_at)
VALUES
  ('target_roles',
   'Product Manager',
   'Target roles (job discovery)',
   'Newline-delimited list of role titles job_discovery.py matches scanned ATS postings against. A posting matches if its title shares any word with any line here. Empty means match everything (no filtering).',
   'Product Manager',
   65,
   now())
ON CONFLICT (key) DO NOTHING;
