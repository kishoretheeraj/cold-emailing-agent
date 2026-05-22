-- Add latest_message_id to contacts: tracks the most recently sent email's
-- Message-ID for sequential In-Reply-To chaining. Separate from message_id
-- (first-touch ID used for incoming reply detection) so reply detection is
-- not affected.
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS latest_message_id TEXT;
