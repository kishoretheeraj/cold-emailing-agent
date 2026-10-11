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
import tempfile
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import apply_agent
import ats_platform
import claude_subscription
import config
import db
import job_sourcing

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


# ── v2 documents ───────────────────────────────────────────────────────────────

def _prepare_documents(job):
    """With APPLY_RESUME_V2 on, tailor and upload documents for a row that has none: the Greenhouse/Lever
    posting comes from the ATS API (falling back to the saved snapshot), resume/v2 tailors the resume.
    Best-effort: anything but a Claude subscription error returns False and leaves the row to the
    existing resume_agent path. Returns True when documents were written."""
    if not config.APPLY_RESUME_V2 or job.get("resume_file_ref"):
        return False
    from ats_api import resolve as ats_resolve
    from resume.v2 import delivery, tailor_v2
    try:
        posting = ats_resolve.fetch_posting(job.get("job_url")) or {}
        description = posting.get("description") or (job.get("posting_snapshot") or {}).get("description") or ""
        if not description:
            log.info(f"[APPLY-WORKER] | {job.get('company')} | v2 documents skipped: no job description")
            return False
        profile = tailor_v2.load_candidate_profile()
        title = f"{job.get('company')} - Resume"
        with tempfile.TemporaryDirectory(prefix=f"resume-v2-{job['id']}-") as workdir:
            result = tailor_v2.tailor_resume(description, profile, os.path.join(workdir, "resume.pdf"))
            if not result.get("pdf_path"):
                log.warning(f"[APPLY-WORKER] | {job.get('company')} | v2 resume not usable: {result.get('violations')}")
                return False
            letter = delivery.generate_cover_letter(job, result["content_json"])
            letter_pdf = delivery.render_cover_letter_pdf(letter, workdir)
            resume_ref, resume_sha = delivery.upload_pdf(
                job["id"], result["pdf_path"], "resume", title=title, keywords=job.get("role", ""))
            letter_ref, letter_sha = delivery.upload_pdf(
                job["id"], letter_pdf, "cover_letter", title=f"{job.get('company')} - Cover Letter",
                keywords=job.get("role", ""))
        db.set_resume_files(job["id"], resume_file_ref=resume_ref, cover_letter_file_ref=letter_ref,
                            resume_sha256=resume_sha, cover_letter_sha256=letter_sha)
        return True
    except claude_subscription.ClaudeSubscriptionError:
        raise
    except Exception as exc:
        log.warning(f"[APPLY-WORKER] | {job.get('company')} | v2 documents failed, using existing path: {exc}")
        return False


# ── Document pass (v2) ─────────────────────────────────────────────────────────

def _finish_documents(job, lease, ok, reason=None):
    try:
        if not db.finish_document_generation(job["id"], lease, ok, reason):
            log.warning(f"[APPLY-WORKER] | {job.get('company')} | document lease was already lost")
    except Exception as exc:
        log.warning(f"[APPLY-WORKER] | {job.get('company')} | could not release the document lease: {exc}")


def _run_document_pass(limit):
    """With APPLY_RESUME_V2 on, claim saved rows that have no documents yet (their own query: the preview
    queue only holds rows that already have them), tailor each under a 'preparing' lease and hand it on:
    success -> idle (so the preview below can claim it), failure -> failed_retryable with a backoff and
    the existing resume worker still free to build it. Returns the number of errors."""
    if not config.APPLY_RESUME_V2:
        return 0
    now = datetime.now(timezone.utc)
    excluded = sorted(set(ats_platform.unpreparable_platforms()))
    pool = db.get_document_candidates(config.APPLY_PREVIEW_POOL, exclude_platforms=excluded)
    jobs = [j for j in pool if apply_agent._backoff_elapsed(j, now)][:limit]
    errors = 0
    for job in jobs:
        if _paused() or db.get_approved_application_ids():
            break
        lease = db.claim_application(job["id"], "preparing")
        if lease is None:
            continue
        try:
            ok = _prepare_documents(job)
            reason = None if ok else "v2 documents were not produced"
        except claude_subscription.ClaudeSubscriptionError as exc:
            _finish_documents(job, lease, False, f"Claude subscription unavailable: {exc}")
            log.warning(f"[APPLY-WORKER] | {job.get('company')} | Claude subscription unavailable, stopping: {exc}")
            return errors + 1
        except Exception as exc:
            errors += 1
            ok, reason = False, f"v2 documents failed: {exc}"
            log.warning(f"[APPLY-WORKER] | {job.get('company')} | document error: {exc}")
        _finish_documents(job, lease, ok, reason)
    return errors


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
        errors = _run_document_pass(limit)
        jobs = apply_agent.preview_candidates()
        log.info(f"[APPLY-WORKER] | START | prepare | candidates={len(jobs)}")
        prepared = 0
        for job in jobs:
            # The limit counts rows actually worked on; a row this host has to skip costs nothing.
            if prepared >= limit:
                break
            if _paused():
                log.info("[APPLY-WORKER] | PAUSED | prepare stopped between rows")
                break
            if db.get_approved_application_ids():
                log.info("[APPLY-WORKER] | prepare | an approval is waiting, yielding the display")
                break
            started = datetime.now(timezone.utc)
            try:
                if _prepare_documents(job):
                    job = db.get_job_application(job["id"]) or job
                status = apply_agent._process_one_preview(job)
            except claude_subscription.ClaudeSubscriptionError as exc:
                errors += 1
                _log_run("prepare", job, started, _outcome(job["id"], "failed_retryable"), exc)
                log.warning(f"[APPLY-WORKER] | {job.get('company')} | Claude subscription unavailable, stopping: {exc}")
                break
            except Exception as exc:
                errors += 1
                prepared += 1
                _log_run("prepare", job, started, _outcome(job["id"], "failed_retryable"), exc)
                log.warning(f"[APPLY-WORKER] | {job.get('company')} | prepare error: {exc}")
                continue
            if status == "skipped":
                continue
            prepared += 1
            _log_run("prepare", job, started, _outcome(job["id"], "skipped"))
        log.info(f"[APPLY-WORKER] | DONE | prepare | errors={errors}")
        return errors


# ── Submit ─────────────────────────────────────────────────────────────────────

def _daily_room():
    # Submissions left today (America/New_York) under daily_submit_cap, or None for no cap. An
    # unreadable count stops the run rather than guessing: the cap is the one limit on volume.
    cap = job_sourcing.load_search_settings()[0]["daily_submit_cap"]
    if not cap:
        return None
    start = datetime.now(ZoneInfo("America/New_York")).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        used = db.count_submit_attempts_since(start.astimezone(timezone.utc))
    except Exception as exc:
        log.warning(f"[APPLY-WORKER] | submit | could not count today's submissions, not submitting: {exc}")
        return 0
    return cap - used


def _note_unsigned_intents():
    # Dashboard approvals are intent only. They are surfaced here (ids only) and never turned into a
    # submission: the signed approve_application RPC (contact-manager tap) is the one gate.
    try:
        waiting = db.get_unsigned_approval_intents()
    except Exception as exc:
        log.warning(f"[APPLY-WORKER] | submit | could not read dashboard approval intents: {exc}")
        return
    if waiting:
        log.info(f"[APPLY-WORKER] | submit | {len(waiting)} dashboard approval(s) await the signed "
                 f"approve_application step: {waiting}")


def run_submit(limit):
    """Submit up to `limit` approved rows, oldest approval first. Returns the number that errored."""
    if os.environ.get("APPLY_AGENT_ARMED") != "1":
        log.warning("[APPLY-WORKER] | submit | not armed, refusing (only apply-submit.service is)")
        return 1
    if _paused():
        log.info("[APPLY-WORKER] | PAUSED | submit")
        return 0
    _note_unsigned_intents()
    ids = db.get_approved_application_ids()[:limit]
    if not ids:
        return 0
    room = _daily_room()
    if room is not None and room <= 0:
        log.info("[APPLY-WORKER] | submit | daily submit cap reached; approvals wait for tomorrow")
        return 0
    if room is not None:
        ids = ids[:room]
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
