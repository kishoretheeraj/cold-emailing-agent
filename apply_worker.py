"""
Beelink apply worker (first-ten-applications Phase D). Each mode is a oneshot started by its own
systemd timer on the Beelink:

  --prepare  fills eligible rows and stops before Submit (apply_agent's preview pass). Never armed.
  --submit   submits rows the operator approved and signed (apply_agent.submit). Run only by
             apply-submit.service, the only armed Beelink unit.

Both drive the same headful Chrome display (:1), serialized by an flock. A submit waits for the
lock; a prepare skips the run when the lock is busy and stops between rows as soon as an approved
row is waiting, so an approval never sits behind a long preparation pass. Every row's outcome is
written to application_runs, which is how the cloud sees what happened here.
"""

import argparse
import contextlib
import fcntl
import logging
import os
import socket
import sys
import time
from datetime import datetime, timezone

import apply_agent
import claude_subscription
import config
import db

log = logging.getLogger(__name__)

_STATUS_TO_OUTCOME = {
    "ready_for_review": "ready",
    "needs_input": "needs_input",
    "unsupported": "unsupported",
    "failed_retryable": "failed_retryable",
    "failed_terminal": "failed_terminal",
    "submitted": "submitted",
    "needs_confirmation": "needs_confirmation",
}


# ── Shared guards ──────────────────────────────────────────────────────────────

def _paused():
    return db.get_pause_scope() in ("agent", "all")


@contextlib.contextmanager
def display_lock(blocking, timeout_seconds=0):
    """Hold the display :1 lock for the duration of the block. Yields False when it could not be
    acquired (immediately for non-blocking, after timeout_seconds otherwise)."""
    os.makedirs(os.path.dirname(config.APPLY_DISPLAY_LOCK) or ".", exist_ok=True)
    with open(config.APPLY_DISPLAY_LOCK, "a") as handle:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not blocking or time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(1)
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _outcome(job_id, default):
    try:
        row = db.get_job_application(job_id) or {}
    except Exception:
        return default
    return _STATUS_TO_OUTCOME.get(row.get("automation_status"), default)


def _log_run(kind, job, started, outcome, error=None):
    db.log_application_run(
        application_id=job.get("id"), kind=kind, adapter=config.APPLY_WORKER_ADAPTER,
        host=socket.gethostname(), started_at=started, outcome=outcome,
        stop_reason=str(error) if error else None,
        error_class=type(error).__name__ if error else None)


# ── Prepare ────────────────────────────────────────────────────────────────────

def run_prepare(limit):
    """Prepare up to `limit` eligible rows. Returns the number of rows that errored."""
    if _paused():
        log.info("[APPLY-WORKER] | PAUSED | prepare")
        return 0
    with display_lock(blocking=False) as acquired:
        if not acquired:
            log.info("[APPLY-WORKER] | prepare | display busy, skipping this run")
            return 0
        db.recover_stale_leases(config.APPLY_AGENT_LEASE_STALE_SECONDS)
        jobs = apply_agent.preview_candidates()[:limit]
        log.info(f"[APPLY-WORKER] | START | prepare | candidates={len(jobs)}")
        errors = 0
        for job in jobs:
            if _paused():
                log.info("[APPLY-WORKER] | PAUSED | prepare stopped between rows")
                break
            if db.get_approved_application_ids():
                log.info("[APPLY-WORKER] | prepare | an approval is waiting, yielding the display")
                break
            started = datetime.now(timezone.utc)
            try:
                apply_agent._process_one_preview(job)
            except claude_subscription.ClaudeSubscriptionError as exc:
                errors += 1
                _log_run("prepare", job, started, _outcome(job["id"], "failed_retryable"), exc)
                log.warning(f"[APPLY-WORKER] | {job.get('company')} | Claude subscription unavailable, stopping: {exc}")
                break
            except Exception as exc:
                errors += 1
                _log_run("prepare", job, started, _outcome(job["id"], "failed_retryable"), exc)
                log.warning(f"[APPLY-WORKER] | {job.get('company')} | prepare error: {exc}")
                continue
            _log_run("prepare", job, started, _outcome(job["id"], "skipped"))
        log.info(f"[APPLY-WORKER] | DONE | prepare | errors={errors}")
        return errors


# ── Submit ─────────────────────────────────────────────────────────────────────

def run_submit(limit):
    """Submit up to `limit` approved rows, oldest approval first. Returns the number that errored."""
    if os.environ.get("APPLY_AGENT_ARMED") != "1":
        log.warning("[APPLY-WORKER] | submit | not armed, refusing (only apply-submit.service is)")
        return 1
    if _paused():
        log.info("[APPLY-WORKER] | PAUSED | submit")
        return 0
    ids = db.get_approved_application_ids()[:limit]
    if not ids:
        return 0
    with display_lock(blocking=True, timeout_seconds=config.APPLY_SUBMIT_LOCK_WAIT_SECONDS) as acquired:
        if not acquired:
            log.warning("[APPLY-WORKER] | submit | display still busy, will retry next run")
            return 0
        errors = 0
        for job_id in ids:
            if _paused():
                log.info("[APPLY-WORKER] | PAUSED | submit stopped between rows")
                break
            started = datetime.now(timezone.utc)
            try:
                apply_agent.submit(job_id)
            except Exception as exc:
                errors += 1
                _log_run("submit", {"id": job_id}, started, _outcome(job_id, "failed_retryable"), exc)
                log.warning(f"[APPLY-WORKER] | {job_id} | submit error: {exc}")
                continue
            _log_run("submit", {"id": job_id}, started, _outcome(job_id, "submitted"))
        return errors


def main(argv=None):
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--submit", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    if args.prepare:
        return run_prepare(args.limit or config.APPLY_PREPARE_BATCH)
    return run_submit(args.limit or config.APPLY_SUBMIT_BATCH)


if __name__ == "__main__":
    logging.basicConfig(
        filename="apply_worker.log",
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M",
    )
    sys.exit(1 if main() else 0)
