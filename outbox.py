"""
Outbox -- local, durable queue of pipeline events waiting to be synced to Supabase.

The pipeline NEVER calls Supabase inline. Instead it appends a row to the sync_outbox table in the
same SQLite database that holds the reply store (PIPELINE_DB_PATH), and supabase_sync.py drains
that table to PostgREST in the background. A Supabase outage therefore never slows or fails an
application attempt.

Contract:

- enqueue(conn, entity, entity_id, op, payload, version=1) -> bool. NEVER raises. Returns True when
  the row was inserted, False otherwise (unknown entity or op, unserializable payload, locked or
  missing database). Only the exception class name is logged, never the payload.
- Atomicity: when the caller already has a transaction open on `conn`, the outbox row joins it and
  is committed or rolled back with the caller's own writes. When no transaction is open, enqueue
  commits its own insert.
- Entities: 'job', 'attempt', 'submission', 'document', 'reply', 'status_event'.
- op is always 'upsert'. The remote side applies a row only when its version is HIGHER than the
  stored one, so replaying a row is harmless and a later state of the same entity must carry a
  higher version than the earlier one.
- Credentials are never synced. Callers must not put passwords, tokens, cookies, vault contents or
  applicant_eligibility values in a payload.
- Sent rows are kept (sent_at set) for audit and removed by supabase_sync.purge_sent().
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# ── Vocabulary ─────────────────────────────────────────────────────────────────

ENTITIES = ("job", "attempt", "submission", "document", "reply", "status_event")
OPS = ("upsert",)

# ── Schema ─────────────────────────────────────────────────────────────────────

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS sync_outbox (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        entity          TEXT NOT NULL,
        entity_id       TEXT NOT NULL,
        op              TEXT NOT NULL,
        payload_json    TEXT NOT NULL,
        version         INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT NOT NULL,
        attempts        INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT,
        last_error      TEXT,
        sent_at         TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sync_outbox_entity ON sync_outbox (entity, entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_sync_outbox_next ON sync_outbox (next_attempt_at)",
)

_INSERT_SQL = (
    "INSERT INTO sync_outbox (entity, entity_id, op, payload_json, version, created_at)"
    " VALUES (?, ?, ?, ?, ?, ?)"
)


def ensure_schema(conn):
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.commit()


def _create_tables(conn):
    # DDL only: does not commit, so it never ends a transaction the caller has open.
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── Stable identifiers ─────────────────────────────────────────────────────────

def derive_uuid(label, value):
    """Deterministic uuid5 for `label|value`, so a replayed event keeps the same key."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{label}|{value}"))


def derive_attempt_id(job_id, started_at):
    """Attempt key for result lines written without an attempt_id: uuid5(job_id|started_at)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{job_id}|{started_at}"))


def normalize_uuid(value):
    """`value` as a canonical uuid string; a value that is not a uuid is mapped to a stable uuid5."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return derive_uuid("attempt", value)


# ── Public entry point ─────────────────────────────────────────────────────────

def enqueue(conn, entity, entity_id, op, payload, version=1):
    """Append one event to the outbox. Returns True on success, False otherwise. Never raises."""
    started_tx = False
    try:
        if entity not in ENTITIES or op not in OPS:
            log.warning(f"[OUTBOX] | enqueue | rejected | entity={entity} op={op}")
            return False
        body = json.dumps(payload, sort_keys=True)
        started_tx = not conn.in_transaction
        params = (entity, str(entity_id), op, body, int(version), _now())
        try:
            conn.execute(_INSERT_SQL, params)
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            _create_tables(conn)
            conn.execute(_INSERT_SQL, params)
        if started_tx:
            conn.commit()
        return True
    except Exception as exc:
        log.warning(f"[OUTBOX] | enqueue | failed | entity={entity} reason={type(exc).__name__}")
        if started_tx:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
