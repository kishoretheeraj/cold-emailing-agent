"""
Backfill -- enqueue historical pipeline data for the Supabase sync.

Reads job_queue.json (a list of {id, company, role, url, platform, notes}) and
application_results.jsonl (one JSON object per line: job_id, started_at, finished_at, status,
detail, latency_s, dry_run, optional attempt_id) and appends 'job' and 'attempt' upserts to the
sync_outbox table, plus a 'reply' and 'status_event' upsert for every row already in the pipeline
database's replies and application_status_events tables. Nothing is sent from here;
supabase_sync.py drains the outbox.

Rerunnable: a row whose (entity, entity_id, version, payload) is already in the outbox is skipped,
and the remote upsert is idempotent on top of that. Attempt ids come from the line's attempt_id, or
uuid5(job_id|started_at) for older lines -- the same derivation apply_pipeline uses. A result line
whose job is not in the queue file gets a stub 'job' row (state 'unknown') so the foreign key holds.

--dry parses both files and prints counts only; it does not open or create the database.
Per-row errors are logged by class and skipped. main() returns 0, or 1 when any row or file failed.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import outbox
import reply_repo

log = logging.getLogger(__name__)

DEFAULT_QUEUE = os.environ.get("APPLY_QUEUE_PATH", "/opt/jobagent24/job_queue.json")
DEFAULT_RESULTS = os.environ.get("APPLY_RESULTS_PATH", "/opt/jobagent24/application_results.jsonl")


# ── Readers ────────────────────────────────────────────────────────────────────

def _mtime_iso(path):
    return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).replace(microsecond=0).isoformat()


def _job_events(queue_path, counts):
    """[(job_id, payload)] from the queue file."""
    events = []
    try:
        with open(queue_path) as fh:
            jobs = json.load(fh)
        queued_at = _mtime_iso(queue_path)
    except (OSError, ValueError) as exc:
        counts["errors"] += 1
        log.error(f"[BACKFILL] | queue | unreadable | {type(exc).__name__}")
        return events
    if not isinstance(jobs, list):
        counts["errors"] += 1
        log.error("[BACKFILL] | queue | unexpected shape | expected a list")
        return events
    for job in jobs:
        try:
            job_id = str(job["id"])
            # version 2: a full queued row supersedes a version-1 stub for the same job, and is
            # superseded in turn by the live claim (version 3).
            events.append((job_id, {
                "id": job_id, "company": job.get("company"), "role": job.get("role"),
                "url": job.get("url"), "platform": job.get("platform"),
                "queued_at": queued_at, "state": "queued", "version": 2}))
        except Exception as exc:
            counts["errors"] += 1
            log.warning(f"[BACKFILL] | queue | row skipped | {type(exc).__name__}")
    return events


def _attempt_events(results_path, counts):
    """[(attempt_id, payload)] from the results file."""
    events = []
    try:
        fh = open(results_path)
    except OSError as exc:
        counts["errors"] += 1
        log.error(f"[BACKFILL] | results | unreadable | {type(exc).__name__}")
        return events
    with fh:
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                job_id = str(rec["job_id"])
                attempt_id = (outbox.normalize_uuid(rec["attempt_id"]) if rec.get("attempt_id")
                              else outbox.derive_attempt_id(job_id, rec.get("started_at")))
                events.append((attempt_id, {
                    "attempt_id": attempt_id, "job_id": job_id, "started_at": rec.get("started_at"),
                    "finished_at": rec.get("finished_at"), "status": rec.get("status"),
                    "detail": rec.get("detail"), "latency_s": rec.get("latency_s"),
                    # version 100: a terminal row, like the live write_result enqueue. A backfill
                    # run can therefore repair a missing terminal state even if an in_progress
                    # phase row (version 11+) already synced.
                    "dry_run": bool(rec.get("dry_run", False)), "version": 100}))
            except Exception as exc:
                counts["errors"] += 1
                log.warning(f"[BACKFILL] | results | line {number} skipped | {type(exc).__name__}")
    return events


def _reply_events(conn):
    # Replies and status events recorded before record_reply queued its own sync rows (the
    # status events never synced at all). Read from the pipeline database itself.
    replies = [dict(r) for r in conn.execute("SELECT * FROM replies ORDER BY created_at, id")]
    events = [dict(r) for r in conn.execute("SELECT * FROM application_status_events ORDER BY at, id")]
    return ([(r["id"], reply_repo.reply_payload(r)) for r in replies],
            [(e["id"], reply_repo.status_event_payload(e)) for e in events])


# ── Enqueue ────────────────────────────────────────────────────────────────────

def _already_queued(conn, entity, entity_id, version, payload):
    row = conn.execute(
        "SELECT 1 FROM sync_outbox WHERE entity = ? AND entity_id = ? AND version = ? AND payload_json = ?",
        (entity, entity_id, version, json.dumps(payload, sort_keys=True))).fetchone()
    return row is not None


def _enqueue_once(conn, entity, entity_id, payload, counts, key):
    version = int(payload.get("version", 1))
    try:
        if _already_queued(conn, entity, entity_id, version, payload):
            counts["skipped"] += 1
        elif outbox.enqueue(conn, entity, entity_id, "upsert", payload, version=version):
            counts[key] += 1
        else:
            counts["errors"] += 1
    except Exception as exc:
        counts["errors"] += 1
        log.warning(f"[BACKFILL] | {entity} | skipped | {type(exc).__name__}")


# ── Public entry point ─────────────────────────────────────────────────────────

def run(queue_path=None, results_path=None, db_path=None, dry=False):
    """Returns {'jobs', 'attempts', 'replies', 'status_events', 'skipped', 'errors'}. Never raises."""
    counts = {"jobs": 0, "attempts": 0, "replies": 0, "status_events": 0, "skipped": 0, "errors": 0}
    conn = None
    try:
        jobs = _job_events(queue_path or DEFAULT_QUEUE, counts)
        attempts = _attempt_events(results_path or DEFAULT_RESULTS, counts)
        known = {job_id for job_id, _ in jobs}
        for _, payload in attempts:
            if payload["job_id"] not in known:
                known.add(payload["job_id"])
                # version 1: a stub carries no metadata, so any later row supersedes it.
                jobs.append((payload["job_id"], {"id": payload["job_id"], "state": "unknown",
                                                 "version": 1}))

        if dry:
            counts["jobs"], counts["attempts"] = len(jobs), len(attempts)
            return counts

        conn = reply_repo.connect(db_path)
        outbox.ensure_schema(conn)
        for job_id, payload in jobs:
            _enqueue_once(conn, "job", job_id, payload, counts, "jobs")
        for attempt_id, payload in attempts:
            _enqueue_once(conn, "attempt", attempt_id, payload, counts, "attempts")
        replies, events = _reply_events(conn)
        for reply_id, payload in replies:
            _enqueue_once(conn, "reply", reply_id, payload, counts, "replies")
        for event_id, payload in events:
            _enqueue_once(conn, "status_event", event_id, payload, counts, "status_events")
    except Exception as exc:
        counts["errors"] += 1
        log.error(f"[BACKFILL] | run | error | {type(exc).__name__}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description="Enqueue historical queue and results for Supabase sync.")
    parser.add_argument("--dry", action="store_true", help="print counts only; write nothing")
    parser.add_argument("--results", default=DEFAULT_RESULTS, help="application_results.jsonl path")
    parser.add_argument("--queue", default=DEFAULT_QUEUE, help="job_queue.json path")
    parser.add_argument("--db", default=None, help="pipeline database path (default PIPELINE_DB_PATH)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", force=True)
    counts = run(queue_path=args.queue, results_path=args.results, db_path=args.db, dry=args.dry)
    mode = "dry run, nothing written" if args.dry else "enqueued"
    print(f"backfill ({mode}): jobs={counts['jobs']} attempts={counts['attempts']}"
          f" replies={counts['replies']} status_events={counts['status_events']}"
          f" skipped={counts['skipped']} errors={counts['errors']}")
    return 1 if counts["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
