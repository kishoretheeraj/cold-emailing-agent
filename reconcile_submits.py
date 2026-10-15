#!/usr/bin/env python3
"""Operator tool: resolve submits the pipeline could not prove.

  --list                                   jobs that need a human decision (state + ledger, no URLs)
  --mark submitted --job ID --reason TEXT  the application was sent: job -> done, stays blocked
  --mark not_sent  --job ID --reason TEXT --i-verified-by-hand
                                           you checked by hand that nothing was sent: job -> pending,
                                           its ledger intents are cleared (the ONLY way a possibly-sent
                                           job can be retried)

Every action is appended to the ledger. State comes from APPLY_STATE_PATH, the ledger from
APPLY_SUBMIT_LEDGER_PATH. Stop the pipeline before marking anything. An unreadable or malformed
ledger stops every action (exit 3): repair or restore it first.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import submit_guard

DEFAULT_STATE_PATH = "/var/lib/jobagent24/pipeline_state.json"
ATTENTION = ("needs_reconciliation", "submitted_unconfirmed", "needs_review")


class ReconcileError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


# ── State file ─────────────────────────────────────────────────────────────────

def _state_path():
    return os.environ.get("APPLY_STATE_PATH", DEFAULT_STATE_PATH)


def _read_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except FileNotFoundError:
        raise ReconcileError(f"state file not found: {path}", 3)
    except (OSError, ValueError):
        raise ReconcileError("state file is unreadable or not JSON", 3)
    if not isinstance(state, dict) or not all(
            isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("status"), str)
            for k, v in state.items()):
        raise ReconcileError("state file has the wrong shape", 3)
    return state


def _write_state(path, state):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        os.fchmod(fh.fileno(), 0o600)
        json.dump(state, fh, indent=1, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


@contextlib.contextmanager
def _locked_state(path):
    lock_fd = os.open(path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(lock_fd, "a+") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = _read_state(path)
        yield state  # an exception raised by the caller skips the write-back
        _write_state(path, state)


# ── Actions ────────────────────────────────────────────────────────────────────

def _job_entries(ledger, job):
    return [(key, entry) for key, entry in ledger.entries().items() if entry["job_id"] == job]


def _clear_attention(rec):
    for key in ("owner", "lease_expires", "reconcile_reason"):
        rec.pop(key, None)
    rec["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mark_submitted(ledger, state, job, reason):
    entries = _job_entries(ledger, job)
    if not entries:  # sent by hand without any intent on record: add one so the job stays blocked
        key = hashlib.sha256(f"manual|{job}".encode()).hexdigest()[:32]
        ledger.append_intent(key, job, "manual", hashlib.sha256(f"manual-posting|{job}".encode()).hexdigest())
        ledger.append_outcome(key, "confirmed", {"manual": True})
        entries = _job_entries(ledger, job)
    for key, entry in entries:
        # a cleared entry must be re-confirmed: submitted -> not_sent -> submitted has to block again
        if not entry["human_confirmed"] or entry["cleared"]:
            ledger.append_human_override(key, "submitted_confirmed", reason)
    rec = state[job]
    rec["status"] = "done"
    _clear_attention(rec)


def mark_not_sent(ledger, state, job, reason):
    for key, entry in _job_entries(ledger, job):
        if submit_guard.is_blocking(entry):
            ledger.append_human_override(key, "not_sent_cleared", reason)
    rec = state[job]
    rec["status"], rec["phases"] = "pending", []
    rec.pop("attempt_id", None)
    _clear_attention(rec)


def cmd_list(ledger, path):
    state = _read_state(path)
    by_job = {}
    for entry in ledger.entries().values():
        by_job.setdefault(entry["job_id"], []).append(entry)
    shown = 0
    for job in sorted(set(by_job) | set(state)):
        rec, entries = state.get(job, {}), by_job.get(job, [])
        unresolved = [e for e in entries if submit_guard.is_blocking(e)
                      and not e["human_confirmed"] and e["outcome"] != "confirmed"]
        if rec.get("status") not in ATTENTION and not unresolved:
            continue
        last = max((e["ts"] for e in entries), default=None)
        when = datetime.fromtimestamp(last, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if last else "-"
        outcomes = ",".join(str(e["outcome"]) for e in entries) or "-"
        print(f"job={job} state={rec.get('status', '-')} reason={rec.get('reconcile_reason', '-')} "
              f"outcomes={outcomes} last_intent={when}")
        shown += 1
    if not shown:
        print("nothing to reconcile")
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(description="Resolve submits the pipeline could not prove.")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--mark", choices=("submitted", "not_sent"))
    parser.add_argument("--job")
    parser.add_argument("--reason")
    parser.add_argument("--i-verified-by-hand", action="store_true", dest="verified")
    args = parser.parse_args(argv)
    if bool(args.list) == bool(args.mark):
        parser.error("give exactly one of --list or --mark")
    if args.list and (args.job or args.reason or args.verified):
        parser.error("--list takes no other arguments")
    if args.mark and not (args.job and args.reason and args.reason.strip()):
        parser.error("--mark needs --job and --reason")
    if args.mark == "not_sent" and not args.verified:
        parser.error("--mark not_sent needs --i-verified-by-hand")

    ledger = submit_guard.SubmitLedger()
    path = _state_path()
    try:
        if args.list:
            return cmd_list(ledger, path)
        with _locked_state(path) as state:
            if args.job not in state:
                raise ReconcileError(f"unknown job: {args.job}")
            if state[args.job].get("status") == "in_progress":
                raise ReconcileError("job is in progress; stop the pipeline first")
            (mark_submitted if args.mark == "submitted" else mark_not_sent)(ledger, state, args.job, args.reason)
        print(f"job={args.job} marked {args.mark}")
        return 0
    except ReconcileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except submit_guard.LedgerError as exc:  # nothing was written to the state file either
        print(f"error: submit ledger unusable ({exc}); repair or restore it before reconciling", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
