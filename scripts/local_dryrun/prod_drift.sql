-- Columns that exist on the live project but were added outside the migrations (see the note at
-- the top of 20260801005138_add_networking_mode.sql). Local stacks only; never applied to Supabase.
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS classifier_status TEXT;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS resume_url TEXT;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
GRANT SELECT, INSERT, UPDATE ON prompts TO anon, authenticated;
