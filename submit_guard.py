"""Pure helpers for the live-submit path: key derivation, the submit ledger, outcome
classification, pacing config and evidence redaction. Stdlib only; no browser, no playwright."""
import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import time
from urllib.parse import urlsplit

DEFAULT_LEDGER_PATH = "/var/lib/jobagent24/submit_ledger.jsonl"
DEFAULT_MAX_PER_DAY, DEFAULT_SPACING_S = 10, 180
DAY_S = 86400
OUTCOME_RESULTS = frozenset(("confirmed", "rejected", "not_sent", "ambiguous"))
OVERRIDE_ACTIONS = frozenset(("submitted_confirmed", "not_sent_cleared"))
FREEING_OUTCOMES = frozenset(("rejected", "not_sent"))  # the only outcomes after which a retry is allowed


class LedgerError(Exception):
    """The ledger cannot be read, parsed or written. Callers must fail closed."""


class SubmitConflict(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# ── Keys ───────────────────────────────────────────────────────────────────────

def canonical_apply_url(url):
    """scheme://host/path (lowercase host, default port stripped, no query or fragment), or None."""
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parts = urlsplit(url.strip())
        scheme, host, port = parts.scheme.lower(), (parts.hostname or "").lower(), parts.port
    except ValueError:
        return None
    if scheme not in ("http", "https") or not host:
        return None
    if ":" in host:
        host = f"[{host}]"
    if port is not None and port != {"http": 80, "https": 443}[scheme]:
        host = f"{host}:{port}"
    return f"{scheme}://{host}{parts.path or '/'}"


def posting_key(url):
    canon = canonical_apply_url(url)
    return hashlib.sha256(canon.encode()).hexdigest() if canon else None


def idempotency_key(job_id, attempt_id, posting_key):
    for value in (job_id, attempt_id, posting_key):
        if not isinstance(value, str) or not value:
            raise ValueError("idempotency_key needs non-empty string inputs")
    return hashlib.sha256(f"{job_id}|{attempt_id}|{posting_key}".encode()).hexdigest()[:32]


# ── Redaction ──────────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\-\s().]{7,}\d")
_LONG_DIGITS_RE = re.compile(r"\d{6,}")
_TOKEN_RUN_RE = re.compile(r"[A-Za-z0-9_-]{20,}")


def _phone_sub(match):
    return "[phone]" if sum(c.isdigit() for c in match.group(0)) >= 7 else match.group(0)


def sanitize_path(path):
    """Replace long opaque runs (ids, tokens) in a URL path before it is stored as evidence."""
    return _TOKEN_RUN_RE.sub("[token]", path) if isinstance(path, str) else ""


def redact_text(text, limit=4000):
    try:
        if not isinstance(text, str):
            return ""
        out = _EMAIL_RE.sub("[email]", text)
        out = _PHONE_RE.sub(_phone_sub, out)
        out = _LONG_DIGITS_RE.sub("[redacted]", out)
        return out[:max(0, int(limit))]
    except Exception:
        return ""


# ── Pacing ─────────────────────────────────────────────────────────────────────

def _positive_int(raw, default):
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def submit_pacing_from_env():
    """(max_per_day, spacing_s); read at call time so a changed environment applies at once."""
    return (_positive_int(os.environ.get("APPLY_MAX_SUBMITS_PER_DAY"), DEFAULT_MAX_PER_DAY),
            _positive_int(os.environ.get("APPLY_SUBMIT_SPACING_S"), DEFAULT_SPACING_S))


# ── Ledger ─────────────────────────────────────────────────────────────────────

def is_live(entry):
    """An intent nobody resolved: no outcome yet and not cleared by a human."""
    return entry["outcome"] is None and not entry["cleared"]


def is_blocking(entry):
    """Stays blocked after confirmed, ambiguous and a human submitted_confirmed."""
    return entry["outcome"] not in FREEING_OUTCOMES and not entry["cleared"]


def is_counted(entry):
    """Counts toward the daily cap and the gap: every intent except not_sent / cleared ones."""
    return entry["outcome"] != "not_sent" and not entry["cleared"]


def _nonempty(value):
    return isinstance(value, str) and bool(value)


def _stamp(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def _valid_record(rec):
    """True when the record has every field the fold and the gates rely on. A record that fails is
    never skipped on read: a ledger with an unreadable record cannot prove a job was not submitted."""
    kind = rec.get("type")
    if not _nonempty(rec.get("key")) or not _stamp(rec.get("ts")):
        return False
    if kind == "intent":
        return (_nonempty(rec.get("job_id")) and _nonempty(rec.get("posting_key"))
                and isinstance(rec.get("attempt_id"), str))
    if kind == "outcome":
        # a string first: membership in a frozenset hashes the value, and a list or dict is unhashable
        return _nonempty(rec.get("result")) and rec["result"] in OUTCOME_RESULTS
    if kind == "human_override":
        return _nonempty(rec.get("action")) and rec["action"] in OVERRIDE_ACTIONS
    return kind == "dispatch"


def _parse_records(text):
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            raise LedgerError("ledger_malformed")
        if not isinstance(rec, dict) or not _valid_record(rec):
            raise LedgerError("ledger_malformed")
        out.append(rec)
    return out


def _fold(records):
    found = {}
    for rec in records:
        key, kind = rec["key"], rec["type"]
        if kind == "intent":
            if key not in found or not is_blocking(found[key]):  # a re-reservation after a freed attempt
                found[key] = {"job_id": rec["job_id"], "posting_key": rec["posting_key"],
                              "attempt_id": rec["attempt_id"], "ts": float(rec["ts"]),
                              "outcome": None, "cleared": False, "human_confirmed": False,
                              "dispatch_ts": None}
        elif key not in found:
            continue
        elif kind == "dispatch":
            prior = found[key]["dispatch_ts"]
            found[key]["dispatch_ts"] = float(rec["ts"]) if prior is None else max(prior, float(rec["ts"]))
        elif kind == "outcome":
            if not found[key]["human_confirmed"]:  # a hand-confirmed send is final
                found[key]["outcome"] = rec["result"]
        elif kind == "human_override":
            if rec["action"] == "not_sent_cleared":
                found[key]["cleared"] = True
            else:
                # authoritative: the send is real, so the job and posting block and it counts toward the cap
                found[key].update(outcome="confirmed", cleared=False, human_confirmed=True)
    return found


def _last_dispatch(records):
    stamps = [float(r["ts"]) for r in records if r["type"] == "dispatch"]
    return max(stamps) if stamps else None


def _fsync_dir(directory):
    with contextlib.suppress(OSError):
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


class SubmitLedger:
    """Append-only JSONL: intent -> dispatch -> outcome -> (human_override). Written under flock +
    fsync, 0600. Every record is validated on write and on read."""

    def __init__(self, path=None):
        self.path = path or os.environ.get("APPLY_SUBMIT_LEDGER_PATH") or DEFAULT_LEDGER_PATH

    def _append(self, record):
        if not _valid_record(record):  # never write what the reader would refuse: it would poison the ledger
            raise ValueError("invalid ledger record")
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, mode=0o700, exist_ok=True)
        existed = os.path.exists(self.path)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
            os.fchmod(fh.fileno(), 0o600)
        if not existed:
            _fsync_dir(directory)

    def append_intent(self, key, job_id, attempt_id, posting_key, ts=None):
        ts = time.time() if ts is None else float(ts)
        self._append({"type": "intent", "key": key, "job_id": str(job_id), "attempt_id": attempt_id,
                      "posting_key": posting_key, "ts": ts})
        return ts

    def append_dispatch(self, key, ts=None):
        """The moment the Submit click is dispatched. Pacing is measured from here, not from the
        reservation, which can be made long before a gap wait lets the click go. The live path uses
        recheck_and_dispatch, which does this under the same lock as its final checks."""
        self._append({"type": "dispatch", "key": key, "ts": time.time() if ts is None else float(ts)})

    def append_outcome(self, key, result, evidence, ts=None):
        if result not in OUTCOME_RESULTS:
            raise ValueError(f"unknown outcome {result!r}")
        self._append({"type": "outcome", "key": key, "result": result, "evidence": evidence,
                      "ts": time.time() if ts is None else float(ts)})

    def append_human_override(self, key, action, reason, ts=None):
        """The ONLY path that re-opens a job (not_sent_cleared) or records a hand-confirmed send."""
        if action not in OVERRIDE_ACTIONS:
            raise ValueError(f"unknown override {action!r}")
        self._append({"type": "human_override", "key": key, "action": action,
                      "reason": redact_text(reason, 300), "ts": time.time() if ts is None else float(ts)})

    def records(self):
        """Every record, in order. Raises LedgerError on an unreadable file or any malformed or
        structurally invalid record: a ledger that cannot be read in full cannot prove a job was not
        already submitted."""
        try:
            with open(self.path, encoding="utf-8") as fh:
                return _parse_records(fh.read())
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            raise LedgerError("ledger_unreadable")

    def entries(self):
        """{key: {job_id, posting_key, attempt_id, ts, outcome, cleared, human_confirmed, dispatch_ts}}."""
        return _fold(self.records())

    @contextlib.contextmanager
    def _locked(self):
        """The ledger open for read + append under an exclusive lock; yields (file, created)."""
        directory = os.path.dirname(os.path.abspath(self.path))
        try:
            os.makedirs(directory, mode=0o700, exist_ok=True)
            created = not os.path.exists(self.path)
            fd = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
            fh = os.fdopen(fd, "a+", encoding="utf-8")
        except OSError:
            raise LedgerError("ledger_unreadable")
        with fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX)
                os.fchmod(fh.fileno(), 0o600)
            except OSError:
                raise LedgerError("ledger_unreadable")
            yield fh, created  # closing the file releases the lock

    @staticmethod
    def _read_locked(fh):
        fh.seek(0)
        try:
            return _parse_records(fh.read())
        except (OSError, ValueError):
            raise LedgerError("ledger_unreadable")

    def reserve_intent(self, key, job_id, attempt_id, posting_key, max_per_day, spacing_s, now=None):
        """Check duplicates and the daily cap, then append the intent, all under ONE exclusive lock.
        Returns (ts, wait_s): wait_s is the gap the caller must still sleep off, measured from the last
        click DISPATCH (else the last counted intent); 0 when none, and never fatal here. Raises
        SubmitConflict (duplicate_submit_intent job|posting, rate_limit_daily) or LedgerError
        (unreadable / malformed / unwritable ledger)."""
        now = time.time() if now is None else float(now)
        job_id = str(job_id)
        record = {"type": "intent", "key": key, "job_id": job_id, "attempt_id": attempt_id,
                  "posting_key": posting_key, "ts": now}
        if not _valid_record(record):
            raise ValueError("invalid ledger record")
        with self._locked() as (fh, created):
            records = self._read_locked(fh)
            found = _fold(records)
            for entry in found.values():
                if is_blocking(entry) and entry["job_id"] == job_id:
                    raise SubmitConflict("duplicate_submit_intent job")
            for entry in found.values():
                if is_blocking(entry) and entry["posting_key"] == posting_key:
                    raise SubmitConflict("duplicate_submit_intent posting")
            counted = [e for e in found.values() if is_counted(e)]
            if sum(1 for e in counted if e["ts"] >= now - DAY_S) >= max_per_day:
                raise SubmitConflict("rate_limit_daily")
            last = _last_dispatch(records) or max((e["ts"] for e in counted), default=None)
            wait_s = 0.0 if last is None else max(0.0, last + spacing_s - now)
            try:
                fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            except OSError:
                raise LedgerError("ledger_unwritable")
        if created:
            _fsync_dir(os.path.dirname(os.path.abspath(self.path)))
        return now, wait_s

    def recheck_reservation(self, key, job_id, posting_key, spacing_s=None, now=None):
        """After the gap wait: raise SubmitConflict when a DIFFERENT key now blocks this job or
        posting, or (given spacing_s) when a click was dispatched less than spacing_s ago (rate_limit_gap;
        unreachable in the normal single-process flow, it guards against a concurrent dispatcher). The
        reservation's own key is ignored. Read-only: the click path uses recheck_and_dispatch, which
        repeats these checks and records the dispatch under one lock."""
        job_id = str(job_id)
        now = time.time() if now is None else float(now)
        with self._locked() as (fh, _created):
            records = self._read_locked(fh)
        found = _fold(records)
        for other, entry in found.items():
            if other == key or not is_blocking(entry):
                continue
            if entry["job_id"] == job_id:
                raise SubmitConflict("duplicate_submit_intent job")
            if entry["posting_key"] == posting_key:
                raise SubmitConflict("duplicate_submit_intent posting")
        last = _last_dispatch(records)
        if spacing_s is not None and last is not None and now - last < spacing_s:
            raise SubmitConflict("rate_limit_gap")

    def recheck_and_dispatch(self, key, job_id, posting_key, spacing_s, now=None):
        """The last gate before the click, atomic: under ONE exclusive lock, re-validate the whole
        ledger, refuse when a DIFFERENT key blocks this job or posting (duplicate_submit_intent
        job|posting) or a click was dispatched less than spacing_s ago (rate_limit_gap), otherwise
        append the dispatch record and fsync it. Returns the dispatch timestamp. Two workers cannot both
        pass the check and then both click inside the gap. Raises LedgerError on an unreadable,
        malformed or unwritable ledger."""
        job_id = str(job_id)
        now = time.time() if now is None else float(now)
        record = {"type": "dispatch", "key": key, "ts": now}
        if not _valid_record(record):
            raise ValueError("invalid ledger record")
        with self._locked() as (fh, _created):
            records = self._read_locked(fh)
            for other, entry in _fold(records).items():
                if other == key or not is_blocking(entry):
                    continue
                if entry["job_id"] == job_id:
                    raise SubmitConflict("duplicate_submit_intent job")
                if entry["posting_key"] == posting_key:
                    raise SubmitConflict("duplicate_submit_intent posting")
            last = _last_dispatch(records)
            if last is not None and now - last < spacing_s:
                raise SubmitConflict("rate_limit_gap")
            try:
                fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            except OSError:
                raise LedgerError("ledger_unwritable")
        return now

    def has_live_intent_for_job(self, job_id):
        return any(is_live(e) and e["job_id"] == str(job_id) for e in self.entries().values())

    def has_live_intent_for_posting(self, posting_key):
        return any(is_live(e) and e["posting_key"] == posting_key for e in self.entries().values())

    def is_blocked_for_job(self, job_id):
        return any(is_blocking(e) and e["job_id"] == str(job_id) for e in self.entries().values())

    def is_blocked_for_posting(self, posting_key):
        return any(is_blocking(e) and e["posting_key"] == posting_key for e in self.entries().values())

    def intents_last_24h(self, now=None):
        now = time.time() if now is None else now
        return sum(1 for e in self.entries().values() if is_counted(e) and e["ts"] >= now - DAY_S)

    def last_intent_ts(self):
        stamps = [e["ts"] for e in self.entries().values() if is_counted(e)]
        return max(stamps) if stamps else None

    def last_dispatch_ts(self):
        return _last_dispatch(self.records())

    def rate_ok(self, now=None):
        now = time.time() if now is None else now
        max_per_day, spacing_s = submit_pacing_from_env()
        if self.intents_last_24h(now) >= max_per_day:
            return False, "rate_limit_daily"
        last = self.last_intent_ts()
        if last is not None and now - last < spacing_s:
            return False, "rate_limit_gap"
        return True, None


# ── Outcome classification ─────────────────────────────────────────────────────

_SUCCESS_RE = re.compile(
    r"thank you for applying|application (has been )?submitted|successfully submitted|application received",
    re.IGNORECASE)
_SUBMIT_PATH_RE = re.compile(r"submit|apply|application|candidate", re.IGNORECASE)
_NOT_SUBMIT_PATH_RE = re.compile(r"draft|autosave|analytics|track|beacon|ping", re.IGNORECASE)


def _path(url):
    try:
        return urlsplit(str(url or "")).path or ""
    except ValueError:
        return ""


def is_plausible_submit_path(path):
    """A path that could be the application POST: submit/apply-shaped and not a draft save, analytics
    or tracking call, whose status says nothing about whether the application was accepted."""
    if not isinstance(path, str):
        return False
    return bool(_SUBMIT_PATH_RE.search(path)) and not _NOT_SUBMIT_PATH_RE.search(path)


def classify_submit_outcome(submit_requests, final_url, url_changed, page_text, form_present, platform):
    """('confirmed' | 'rejected' | 'provably_not_sent' | 'ambiguous', evidence).

    Only requests on a plausible submit path count toward the outcome; the rest stay in the evidence.
    form_present is tri-state: True (a submit control is still there), False (affirmatively gone),
    None (unknown); None satisfies neither condition below.
      conflicting: both a 2xx/3xx and a 4xx submit request were seen -> ambiguous, whatever else the
        page shows (one of them may be the real application).
      confirmed: a 2xx/3xx submit request, the form gone, and a confirmation signal: a confirmation
        path on an ok request or on the final URL, a /thanks final URL, or success text after the URL
        changed. Text alone never confirms.
      rejected: a 4xx submit request, the form still present, and no 2xx/3xx submit request.
    Incomplete signals are ambiguous. 'provably_not_sent' is a pure predicate over what
    was seen: the pipeline never trusts it after a click, because silence does not prove nothing was
    sent."""
    requests = []
    for item in submit_requests or []:
        try:
            method, url, status = item
            requests.append((str(method).upper(), _path(url), int(status)))
        except (TypeError, ValueError):
            continue
    final_path = _path(final_url)
    text_hit = bool(_SUCCESS_RE.search(page_text)) if isinstance(page_text, str) else False
    form = form_present if isinstance(form_present, bool) else None
    evidence = {"requests": [(m, sanitize_path(p), s) for m, p, s in requests],
                "final_url_path": sanitize_path(final_path), "url_changed": bool(url_changed),
                "form_present": form, "platform": platform, "text_hit": text_hit}

    submit_reqs = [(m, p, s) for m, p, s in requests if is_plausible_submit_path(p)]
    ok = [(m, p, s) for m, p, s in submit_reqs if 200 <= s <= 399]
    bad = [(m, p, s) for m, p, s in submit_reqs if 400 <= s <= 499]
    if ok and bad:  # conflicting responses never confirm and never reject
        return "ambiguous", evidence
    if ok and form is False:
        by_path = any("confirmation" in p.lower() for _m, p, _s in ok) or "confirmation" in final_path.lower()
        by_thanks = final_path.lower().rstrip("/").endswith("/thanks")
        by_text = text_hit and bool(url_changed)
        if by_path or by_thanks or by_text:
            return "confirmed", evidence
    if bad and form is True and not ok:
        return "rejected", evidence
    if not requests and not url_changed and form is True:
        return "provably_not_sent", evidence
    return "ambiguous", evidence
