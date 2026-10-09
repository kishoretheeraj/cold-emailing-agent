"""
Finds postings worth applying to and saves them at stage 'saved' (spec 2026-10-08 fifty-a-day §3.3).
Runs on the Beelink every 2 hours (job-sourcing.timer); zero tokens, anon key only.

One run:
  1. backfills job identity onto rows saved before it existed (a batch per run);
  2. reads the Simplify new-grad feed, keeps the configured categories, and learns every company
     board its URLs point at (job_boards);
  3. sweeps the least recently scanned boards (Greenhouse/Ashby/Lever whole boards, Workday
     searched per title term);
  4. triages every posting with job_filters (title, seniority, location, age, sponsorship) --
     nothing that fails is saved;
  5. saves the rest through db.save_job_application, which drops the same job under another URL,
     the same role at the same company, and reposts of roles already applied to.

Never raises past run(); every source, board and posting is isolated. Log marker [SOURCING].
"""

import logging
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import config
import db
import job_filters
import job_identity
import job_sources

log = logging.getLogger(__name__)


def load_search_settings():
    """(preferences, applicant_eligibility) from the prompts table; defaults when unreachable."""
    try:
        prompts = db.load_prompts()
    except Exception as exc:
        log.warning(f"[SOURCING] | prompts unavailable, using default search preferences: {exc}")
        prompts = {}
    eligibility = {}
    try:
        import json
        parsed = json.loads(prompts.get("applicant_eligibility") or "{}")
        eligibility = parsed if isinstance(parsed, dict) else {}
    except ValueError:
        pass
    return job_filters.load_preferences(prompts), eligibility


def _snapshot(job):
    return {key: job.get(key) for key in ("description", "location", "sponsorship", "posted_at", "source")
            if job.get(key)}


def save(job, prefs, eligibility, stats, now=None):
    """Triage one normalized posting and save it if it passes. Returns the outcome it counted:
    'saved', 'skipped_<filter reason>', 'duplicate_<dedup reason>' or 'error'."""
    try:
        reason = job_filters.reject_reason(job, prefs, eligibility, now=now)
        if not reason and job_sources.needs_detail(job):
            # Workday and Greenhouse lists carry no description; fetch it only for postings that
            # passed the title and location cuts, then re-check age and sponsorship against it.
            job_sources.add_detail(job)
            reason = job_filters.reject_reason(job, prefs, eligibility, now=now)
        if reason:
            outcome = f"skipped_{reason}"
        else:
            row, duplicate = db.save_job_application(
                company=job["company"], role=job["title"], job_url=job["url"], source=job.get("source"),
                posting_snapshot=_snapshot(job), location=job.get("location") or None,
                posted_at=job.get("posted_at"))
            outcome = "saved" if row else f"duplicate_{duplicate}"
    except Exception as exc:
        log.warning(f"[SOURCING] | {job.get('company')} | {job.get('title')} | save error: {exc}")
        outcome = "error"
    stats[outcome] += 1
    return outcome


def _backfill(stats):
    try:
        rows = db.get_rows_missing_identity(config.SOURCING_BACKFILL_BATCH)
    except Exception as exc:
        log.warning(f"[SOURCING] | backfill read failed: {exc}")
        stats["error"] += 1
        return
    for row in rows:
        try:
            stats[f"backfill_{db.backfill_identity(row)}"] += 1
        except Exception as exc:
            log.warning(f"[SOURCING] | backfill | {row.get('id')} | {exc}")
            stats["error"] += 1


def _simplify(stats):
    every = job_sources.fetch_simplify([])
    stats["simplify_rows"] = len(every)
    wanted = {c.lower() for c in config.SOURCING_SIMPLIFY_CATEGORIES}
    boards = {}
    for job in every:
        board = job_sources.board_from_url(job["url"], job["company"])
        if board:
            boards[(board["platform"], board["board"])] = dict(board, source="simplify")
    if boards:
        try:
            db.add_job_boards(list(boards.values()))
        except Exception as exc:
            log.warning(f"[SOURCING] | could not record learned boards: {exc}")
            stats["error"] += 1
    stats["boards_learned"] = len(boards)
    return [j for j in every if (j.get("category") or "").lower() in wanted]


def _sweep(stats, now):
    jobs = []
    try:
        boards = db.get_sourcing_boards(config.SOURCING_MAX_BOARDS_PER_RUN)
    except Exception as exc:
        log.warning(f"[SOURCING] | board list unavailable: {exc}")
        stats["error"] += 1
        return jobs
    for board in boards:
        found, status = job_sources.fetch_board(board, search_terms=config.SOURCING_WORKDAY_SEARCH_TERMS, now=now)
        stats[f"boards_{status}"] += 1
        jobs += found
        try:
            db.record_board_scan(board, status, len(found), config.SOURCING_BOARD_DEAD_AFTER_FAILURES)
        except Exception as exc:
            log.warning(f"[SOURCING] | {board.get('platform')}:{board.get('board')} | scan not recorded: {exc}")
        time.sleep(config.SOURCING_BOARD_DELAY_SECONDS)
    return jobs


def run(now=None):
    """One sourcing pass. Returns the outcome counts (also logged and written to agent_runs)."""
    start = time.time()
    now = now or datetime.now(timezone.utc)
    stats = Counter()
    try:
        if db.get_pause_scope() in ("agent", "all"):
            log.info("[SOURCING] | PAUSED")
            return stats
        prefs, eligibility = load_search_settings()
        _backfill(stats)
        jobs = _simplify(stats) + _sweep(stats, now)
        seen = set()
        for job in jobs:
            key = job_identity.identify(job.get("url"))["job_key"] or job.get("url")
            if key in seen:
                stats["duplicate_in_run"] += 1
                continue
            seen.add(key)
            save(job, prefs, eligibility, stats, now=now)
    except Exception as exc:
        log.warning(f"[SOURCING] | run failed: {exc}")
        stats["error"] += 1
    summary = " | ".join(f"{k}={v}" for k, v in sorted(stats.items()))
    log.info(f"[SOURCING] | DONE | {summary}")
    try:
        skipped = sum(v for k, v in stats.items() if k.startswith(("skipped_", "duplicate_")))
        db.record_run("failure" if stats["error"] and not stats["saved"] else "success", stats["saved"],
                      skipped, stats["error"], round(time.time() - start), source="job_sourcing")
    except Exception as exc:
        log.warning(f"[SOURCING] | record_run failed: {exc}")
    return stats


if __name__ == "__main__":
    logging.basicConfig(
        filename="job_sourcing.log",
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M",
    )
    result = run()
    sys.exit(1 if result["error"] and not result["saved"] else 0)
