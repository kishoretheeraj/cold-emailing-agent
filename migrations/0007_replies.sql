-- 0007_replies.sql
-- Local SQLite pipeline store (PIPELINE_DB_PATH, default /var/lib/jobagent24/pipeline.db):
-- employer replies about submitted applications, and the append-only application status history.
-- Identical to reply_repo.SCHEMA_SQL (ensure_schema applies it on connect); every statement is
-- idempotent. application_status_events is append-only: never UPDATE or DELETE its rows.

CREATE TABLE IF NOT EXISTS replies (
    id             TEXT PRIMARY KEY,
    application_id TEXT,
    job_id         TEXT,
    message_id     TEXT NOT NULL UNIQUE,
    thread_key     TEXT,
    source         TEXT NOT NULL CHECK (source IN ('imap', 'portal', 'manual')),
    from_addr      TEXT,
    from_domain    TEXT,
    subject        TEXT,
    received_at    TEXT,
    snippet        TEXT,
    kind           TEXT NOT NULL,
    status_after   TEXT,
    confidence     REAL,
    match_method   TEXT,
    needs_review   INTEGER NOT NULL DEFAULT 0 CHECK (needs_review IN (0, 1)),
    surfaced_at    TEXT,
    created_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_replies_application ON replies (application_id, received_at);
CREATE INDEX IF NOT EXISTS idx_replies_needs_review ON replies (needs_review) WHERE needs_review = 1;
CREATE INDEX IF NOT EXISTS idx_replies_from_domain ON replies (from_domain);

CREATE TABLE IF NOT EXISTS application_status_events (
    id             TEXT PRIMARY KEY,
    application_id TEXT NOT NULL,
    status         TEXT NOT NULL,
    reply_id       TEXT,
    at             TEXT NOT NULL,
    actor          TEXT NOT NULL CHECK (actor IN ('pipeline', 'monitor', 'user')),
    UNIQUE (application_id, status, reply_id)
);
CREATE INDEX IF NOT EXISTS idx_status_events_application
    ON application_status_events (application_id, at);
