"""
Supabase sync service -- drains the local sync_outbox table to Supabase PostgREST.

The pipeline never talks to Supabase itself (see outbox.py). This service reads due rows from
sync_outbox, groups them by entity, and POSTs each group to the pipeline_upsert RPC:

    POST {SUPABASE_URL}/rest/v1/rpc/pipeline_upsert    body {"target": <table>, "rows": [<payload>, ...]}

Configuration comes from the environment only: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY. When
either is missing the service logs that once and exits 0. Neither value is ever logged or
interpolated into a log line, and no log line contains a payload.

Outcome handling, per group of rows:

- 2xx: sent_at is set. The row is kept for audit until purge_sent() removes it.
- 429 and 5xx, a network error, 3xx, 401, 403 and 408: retryable. attempts += 1, last_error is the
  HTTP class only (for example 'HTTP429'), next_attempt_at = now + min(2^attempts * 30s, 900s).
  401/403 are retryable on purpose: a wrong or rotated key is fixed by the operator, and poisoning
  the queue over it would silently discard good events.
- 409: treated as retryable until attempts reaches MAX_CONFLICT_ATTEMPTS, then poisoned. A child row
  can arrive before its parent (foreign key); the parent is usually already queued with a backoff.
- Any other 4xx: POISONED. attempts += 1, last_error is set and sent_at is set so the queue keeps
  moving. The row is never retried; it stays in sync_outbox with its last_error for inspection.
  When a multi-row group is rejected, its rows are re-sent one at a time so a single bad row cannot
  poison its neighbours.

A retryable failure ends the current batch (later entities depend on earlier ones through foreign
keys, and entities are sent in dependency order: job, attempt, submission, document, reply,
status_event). Rows not yet attempted stay due.

SIGTERM and SIGINT set a stop flag. The run loop finishes the batch in progress, then exits 0.
"""

import argparse
import logging
import json
import os
import signal
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone

import requests

import outbox
import reply_repo

log = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

ENTITY_RPC_TARGET = {
    "job": "pipeline_jobs",
    "attempt": "pipeline_attempts",
    "submission": "pipeline_submissions",
    "document": "pipeline_documents",
    "reply": "pipeline_replies",
    "status_event": "pipeline_status_events",
}
ENTITY_ORDER = tuple(ENTITY_RPC_TARGET)

BATCH_SIZE = 50
REQUEST_TIMEOUT_S = 30
BACKOFF_BASE_S = 30
BACKOFF_CAP_S = 900
MAX_CONFLICT_ATTEMPTS = 8
_RETRY_4XX = frozenset({401, 403, 408, 429})

STOP = threading.Event()


def _config():
    return os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()


# ── HTTP ───────────────────────────────────────────────────────────────────────

def _post(url, headers, body):
    """The only place that touches the network. Returns the HTTP status code."""
    resp = requests.post(url, headers=headers, data=body.encode("utf-8"), timeout=REQUEST_TIMEOUT_S)
    try:
        return resp.status_code
    finally:
        resp.close()


_default_post = _post


# ── Helpers ────────────────────────────────────────────────────────────────────

def _utcnow():
    return datetime.now(timezone.utc)


def _iso(when):
    return when.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _classify(status):
    if status is None:
        return "retry"
    if 200 <= status < 300:
        return "ok"
    if status == 409:
        return "conflict"
    if 400 <= status < 500 and status not in _RETRY_4XX:
        return "poison"
    return "retry"


def _load_payload(row):
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["version"] = row["version"]
    return payload


def _call(post, url, headers, entity, payloads):
    body = json.dumps({"target": ENTITY_RPC_TARGET[entity], "rows": payloads}, sort_keys=True)
    try:
        resp = post(url, headers, body)
        return int(getattr(resp, "status_code", resp)), None
    except Exception as exc:
        return None, type(exc).__name__


def _mark_sent(conn, ids, now):
    conn.executemany("UPDATE sync_outbox SET sent_at = ?, last_error = NULL WHERE id = ?",
                     [(_iso(now), i) for i in ids])


def _mark_retry(conn, row, now, error):
    attempts = row["attempts"] + 1
    delay = min(BACKOFF_BASE_S * (2 ** attempts), BACKOFF_CAP_S)
    conn.execute("UPDATE sync_outbox SET attempts = ?, next_attempt_at = ?, last_error = ? WHERE id = ?",
                 (attempts, _iso(now + timedelta(seconds=delay)), error, row["id"]))


def _mark_poison(conn, row, now, error):
    conn.execute("UPDATE sync_outbox SET attempts = ?, last_error = ?, sent_at = ? WHERE id = ?",
                 (row["attempts"] + 1, error, _iso(now), row["id"]))


def _send_rows(conn, post, url, headers, entity, rows, now, counts):
    """Send one group. Returns 'ok', 'retry' or 'poison'."""
    parsed = []
    for row in rows:
        payload = _load_payload(row)
        if payload is None:
            _mark_poison(conn, row, now, "BadPayload")
            counts["poisoned"] += 1
            log.error(f"[SYNC] | postgrest | poisoned | entity={entity} outbox_id={row['id']} reason=BadPayload")
        else:
            parsed.append((row, payload))
    conn.commit()
    if not parsed:
        return "poison"

    status, err = _call(post, url, headers, entity, [p for _, p in parsed])
    kind = _classify(status)
    label = err or f"HTTP{status}"

    if kind in ("poison", "conflict") and len(parsed) > 1:
        # A single bad row (or a single FK orphan) must not poison/retry its neighbours:
        # re-send one at a time so each row is judged on its own outcome.
        outcome = "ok"
        for row, _ in parsed:
            if _send_rows(conn, post, url, headers, entity, [row], now, counts) == "retry":
                outcome = "retry"
        return outcome

    if kind == "ok":
        _mark_sent(conn, [r["id"] for r, _ in parsed], now)
        counts["sent"] += len(parsed)
        conn.commit()
        return "ok"

    outcome = "poison" if kind == "poison" else "retry"
    for row, _ in parsed:
        if kind == "poison" or (kind == "conflict" and row["attempts"] + 1 >= MAX_CONFLICT_ATTEMPTS):
            _mark_poison(conn, row, now, label)
            counts["poisoned"] += 1
            log.error(f"[SYNC] | postgrest | poisoned | entity={entity} outbox_id={row['id']} reason={label}")
        else:
            _mark_retry(conn, row, now, label)
            counts["retried"] += 1
            outcome = "retry"
    conn.commit()
    return outcome


# ── Drain ──────────────────────────────────────────────────────────────────────

def drain(conn, post, now, url="", key=""):
    """Send one batch of due rows (at most BATCH_SIZE). Returns a counts dict; 'rows' is how many
    were due, 'halted' is True when a retryable failure ended the batch early."""
    conn.row_factory = sqlite3.Row
    counts = {"rows": 0, "sent": 0, "retried": 0, "poisoned": 0, "halted": False}
    due = conn.execute(
        "SELECT * FROM sync_outbox WHERE sent_at IS NULL AND (next_attempt_at IS NULL OR next_attempt_at <= ?)"
        " ORDER BY id LIMIT ?", (_iso(now), BATCH_SIZE)).fetchall()
    counts["rows"] = len(due)
    if not due:
        return counts

    endpoint = f"{url.rstrip('/')}/rest/v1/rpc/pipeline_upsert"
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    groups = {}
    for row in due:
        groups.setdefault(row["entity"], []).append(row)
    for entity, rows in groups.items():
        if entity not in ENTITY_RPC_TARGET:
            for row in rows:
                _mark_poison(conn, row, now, "UnknownEntity")
                counts["poisoned"] += 1
            conn.commit()

    for entity in ENTITY_ORDER:
        rows = groups.get(entity)
        if not rows:
            continue
        if _send_rows(conn, post, endpoint, headers, entity, rows, now, counts) == "retry":
            counts["halted"] = True
            break
    return counts


def purge_sent(conn, older_than_days=30, now=None):
    """Delete audit rows that were sent more than `older_than_days` ago. Returns the number removed."""
    cutoff = _iso((now or _utcnow()) - timedelta(days=older_than_days))
    cur = conn.execute("DELETE FROM sync_outbox WHERE sent_at IS NOT NULL AND sent_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


# ── Run loop ───────────────────────────────────────────────────────────────────

def run(poll_seconds=30, once=False, _post=None, _now=None, db_path=None):
    """Drain the outbox until stopped (or once). Returns a counts dict. Never raises past its own
    boundary except for a database that cannot be opened at all."""
    totals = {"batches": 0, "rows": 0, "sent": 0, "retried": 0, "poisoned": 0}
    STOP.clear()
    url, key = _config()
    if _post is None and not (url and key):
        log.error("[SYNC] | CONFIG | SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set; nothing to do")
        return totals
    post = _post if _post is not None else _default_post
    clock = _now or _utcnow

    conn = reply_repo.connect(db_path)
    try:
        outbox.ensure_schema(conn)
        log.info("[SYNC] | START | postgrest")
        while True:
            while not STOP.is_set():
                counts = drain(conn, post, clock(), url, key)
                if counts["rows"] == 0:
                    break
                totals["batches"] += 1
                for name in ("rows", "sent", "retried", "poisoned"):
                    totals[name] += counts[name]
                log.info(f"[SYNC] | postgrest | batch | rows={counts['rows']} sent={counts['sent']}"
                         f" retried={counts['retried']} poisoned={counts['poisoned']}")
                if counts["halted"]:
                    break
            if once or STOP.is_set():
                break
            STOP.wait(poll_seconds)
    finally:
        conn.close()
    log.info(f"[SYNC] | DONE | {totals}")
    return totals


def main(argv=None):
    parser = argparse.ArgumentParser(description="Drain the pipeline outbox to Supabase.")
    parser.add_argument("--once", action="store_true", help="drain what is due, then exit")
    parser.add_argument("--poll", type=int, default=30, help="seconds between polls")
    parser.add_argument("--db", default=None, help="pipeline database path (default PIPELINE_DB_PATH)")
    parser.add_argument("--purge-days", type=int, default=30, help="delete sent rows older than this on exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", force=True)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    try:
        run(poll_seconds=args.poll, once=args.once, db_path=args.db)
        conn = reply_repo.connect(args.db)
        try:
            outbox.ensure_schema(conn)
            removed = purge_sent(conn, args.purge_days)
            if removed:
                log.info(f"[SYNC] | PURGE | removed={removed}")
        finally:
            conn.close()
    except Exception as exc:
        log.error(f"[SYNC] | FATAL | {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
