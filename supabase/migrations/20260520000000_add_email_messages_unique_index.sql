-- Adds the unique index on email_messages.message_id required for
-- ON CONFLICT (message_id) upserts in db.insert_email_message().
-- Non-partial is safe because insert_email_message() only upserts when message_id IS NOT NULL.
CREATE UNIQUE INDEX IF NOT EXISTS email_messages_message_id_uniq
ON email_messages(message_id);
