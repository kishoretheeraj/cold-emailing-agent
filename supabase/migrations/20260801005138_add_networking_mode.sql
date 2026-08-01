-- Networking mode: third contact track alongside outreach/applied.
-- See docs/python/db-schema.md and CLAUDE.md for the full mode/stage contract.

-- Widen the mode CHECK constraint without assuming its current name — live
-- schema has drifted from setup_supabase.sql before (e.g. gmail_thread_id/
-- resume_url exist live with no corresponding ALTER TABLE in the repo), so
-- discover the actual constraint via pg_constraint rather than hardcoding
-- "contacts_mode_check".
DO $$
DECLARE
  con_name text;
BEGIN
  SELECT conname INTO con_name
  FROM pg_constraint
  WHERE conrelid = 'contacts'::regclass
    AND contype = 'c'
    AND pg_get_constraintdef(oid) ILIKE '%mode%';
  IF con_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE contacts DROP CONSTRAINT %I', con_name);
  END IF;
END $$;

ALTER TABLE contacts ADD CONSTRAINT contacts_mode_check
  CHECK (mode IN ('outreach', 'applied', 'networking'));

ALTER TABLE contacts ADD COLUMN IF NOT EXISTS connection_context TEXT NULL;

-- Networking mode — seed prompts table rows.
-- Values are verbatim copies of config.py constants; default_value mirrors
-- value so the "Reset to default" button in /prompts restores agent behavior.

INSERT INTO prompts (key, value, display_title, description, default_value, sort_order, updated_at)
VALUES
  ('networking_prompt',
   'You are writing a warm networking email for a job seeker.
This is NOT a job pitch. Do not mention roles, openings, hiring, or applying.
The only goal is a genuine, low-pressure ask for a short conversation.
Generate ONLY the email body. No subject line, no sign-off name, no metadata.

SENDER PROFILE:
{profile}

RECIPIENT:
- Name: {name}
- Company: {company}

CONNECTION:
{connection_context_instruction}
{dartmouth_instruction}

RULES:
- Max 120 words
- No filler phrases ("I hope this finds you well", etc.)
- No em dashes
- Never mention a role, opening, application, or hiring
- No attachment references
- One clear, low-commitment ask: a 15-20 minute chat, with a couple of
  flexible time windows offered so it''s easy to say yes
- Simple, conversational, personal tone — not a pitch
- Sound human, not AI-generated',
   'Networking: First Touch',
   'First-touch networking email body. Leads with connection_context_instruction (a computed hook or degrade instruction), never a role pitch.',
   'You are writing a warm networking email for a job seeker.
This is NOT a job pitch. Do not mention roles, openings, hiring, or applying.
The only goal is a genuine, low-pressure ask for a short conversation.
Generate ONLY the email body. No subject line, no sign-off name, no metadata.

SENDER PROFILE:
{profile}

RECIPIENT:
- Name: {name}
- Company: {company}

CONNECTION:
{connection_context_instruction}
{dartmouth_instruction}

RULES:
- Max 120 words
- No filler phrases ("I hope this finds you well", etc.)
- No em dashes
- Never mention a role, opening, application, or hiring
- No attachment references
- One clear, low-commitment ask: a 15-20 minute chat, with a couple of
  flexible time windows offered so it''s easy to say yes
- Simple, conversational, personal tone — not a pitch
- Sound human, not AI-generated',
   41,
   NOW()),

  ('networking_followup_prompt',
   'You are writing a brief, gentle follow-up to a
networking email sent about a week ago. This is the only follow-up that will
be sent — do not imply there will be another. Keep it low-pressure; the
recipient owes nothing.

SENDER PROFILE:
{profile}

RECIPIENT:
- Name: {name}
- Company: {company}
{dartmouth_instruction}

RULES:
- Max 60 words
- Briefly reference that a previous note was sent
- Restate the same low-commitment ask (15-20 minute chat), do not escalate it
- No role, opening, application, or hiring language
- No em dashes, no filler
- End with a soft, easy-out close (e.g. "No worries at all if timing''s not right")
- Do NOT include subject line or name sign-off',
   'Networking: Follow-up',
   'The single networking follow-up nudge, sent once ~6 days after the first touch if no reply.',
   'You are writing a brief, gentle follow-up to a
networking email sent about a week ago. This is the only follow-up that will
be sent — do not imply there will be another. Keep it low-pressure; the
recipient owes nothing.

SENDER PROFILE:
{profile}

RECIPIENT:
- Name: {name}
- Company: {company}
{dartmouth_instruction}

RULES:
- Max 60 words
- Briefly reference that a previous note was sent
- Restate the same low-commitment ask (15-20 minute chat), do not escalate it
- No role, opening, application, or hiring language
- No em dashes, no filler
- End with a soft, easy-out close (e.g. "No worries at all if timing''s not right")
- Do NOT include subject line or name sign-off',
   42,
   NOW()),

  ('networking_subject_prompt',
   'Generate a short email subject line for a warm
networking email (not a job pitch).

To: {name} at {company}
Email body:
{body}

RULES:
- Max 8 words
- Casual, personal feel — never mention role, application, or hiring
- Lowercase preferred
- No clickbait
- Return ONLY the subject line, nothing else',
   'Networking: Subject Line',
   'Subject line for networking first-touch emails. Casual, never job-flavored.',
   'Generate a short email subject line for a warm
networking email (not a job pitch).

To: {name} at {company}
Email body:
{body}

RULES:
- Max 8 words
- Casual, personal feel — never mention role, application, or hiring
- Lowercase preferred
- No clickbait
- Return ONLY the subject line, nothing else',
   43,
   NOW());
