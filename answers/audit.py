"""Answer audit trail.

Two sinks, both 0600 and written under an flock:
  - <artifacts_dir>/<job_id>/answers.json: JSON array, PRIVATE. The only place question TEXT and the
    raw field_ref (a selector that can embed page values) are stored.
  - a global jsonl (default /var/lib/jobagent24/answer_audit.jsonl): hashes only, never question
    text, values or the raw field_ref (field_ref_hash instead).
Write failures raise; they are never swallowed. Skip decisions are not logged.
"""
import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone

DEFAULT_AUDIT_PATH = "/var/lib/jobagent24/answer_audit.jsonl"
SOURCES = ("bank", "facts", "llm_grounded", "declined", "blank", "review")
ACTIONS = ("answer", "blank", "review", "skip")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


def _sha(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _job_file(artifacts_dir, job_id):
    jid = str(job_id)
    if not _JOB_ID_RE.match(jid) or ".." in jid:
        raise ValueError("unsafe job_id for an artifacts path")
    return os.path.join(os.fspath(artifacts_dir), jid, "answers.json")


@contextmanager
def _locked(path):
    fd = os.open(path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing releases the lock


def _append_job_entry(path, entry):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with _locked(path):
        entries = []
        if os.path.exists(path):
            with open(path, "rb") as fh:
                raw = fh.read()
            if raw.strip():
                entries = json.loads(raw.decode("utf-8"))
                if not isinstance(entries, list):
                    raise ValueError("answers.json is not a JSON array")
        entries.append(entry)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write((json.dumps(entries, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        dfd = os.open(os.path.dirname(path), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def _append_global(path, entry):
    os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
    line = (json.dumps(entry, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.fchmod(fd, 0o600)
        view = memoryview(line)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)


def log_answer(job_id, attempt_id, field_ref, question, qclass, rule_id, bank_path, source, action,
               reason_codes, value, artifacts_dir, audit_path=None, emit=None):
    """Record one field decision. Returns the hash-only entry (with job_id/attempt_id and field_ref_hash).
    The question text and raw field_ref go only into the private per-job file; `emit` (structured
    logging) never sees text or value."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    entry = {
        "question_hash": _sha(question),
        "class": qclass,
        "rule_id": rule_id,
        "bank_path": bank_path,
        "source": source,
        "action": action,
        "reason_codes": list(reason_codes or ()),
        "value_hash": None if value is None else _sha(value),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    global_entry = {**entry, "field_ref_hash": None if field_ref is None else _sha(field_ref),
                    "job_id": job_id, "attempt_id": attempt_id}
    if action == "skip":
        return global_entry
    _append_job_entry(_job_file(artifacts_dir, job_id), {**entry, "field_ref": field_ref, "question": question})
    _append_global(audit_path or DEFAULT_AUDIT_PATH, global_entry)
    if emit is not None:
        emit("answer_decided", job_id, question_hash=entry["question_hash"], action=action, rule_id=rule_id)
    return global_entry
