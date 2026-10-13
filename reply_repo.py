"""
Reply repository -- local SQLite store for employer mail about submitted applications.

Two tables:

- replies: one row per employer message (confirmation, assessment, interview, offer, rejection,
  other), deduplicated on Message-ID. The snippet is redacted and capped at 200 characters.
- application_status_events: the append-only history of each application's status.

The status history is append-only by design. This module exposes NO update or delete function for
it, and none should be added: a correction is a new event, and get_current_status() derives the
present status from the whole history (highest rank wins, 'rejected' stands unless a later
interview/offer exists). Pure decision logic (advance, redact, correlate) does no I/O so it can be
tested without a database.

correlate() never guesses. Ties and unclear margins return (None, method, True) so a human reviews
the message; it is never attached to an application on a coin flip.
"""

import hashlib
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone

log = logging.getLogger(__name__)

PIPELINE_DB_PATH = os.environ.get("PIPELINE_DB_PATH", "/var/lib/jobagent24/pipeline.db")

# ── Vocabulary ─────────────────────────────────────────────────────────────────

STATUS_ORDER = {"submitted": 0, "confirmed": 1, "assessment": 2, "interview": 3, "offer": 4}
KIND_TO_STATUS = {
    "confirmation": "confirmed",
    "assessment": "assessment",
    "interview": "interview",
    "offer": "offer",
    "rejection": "rejected",
}
_REJECTED_RANK = 5
_OVERRIDE_REJECTION = ("interview", "offer")
_COMPANY_SUFFIXES = {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "technologies", "labs"}

# ── Schema ─────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
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
"""


def ensure_schema(conn):
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def connect(path=None):
    """Open the pipeline database (file 0600, parent directory 0700) with the schema in place."""
    path = path or PIPELINE_DB_PATH
    if path != ":memory:":
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, mode=0o700, exist_ok=True)
        if not os.path.exists(path):
            os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
        else:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


# ── Pure helpers ───────────────────────────────────────────────────────────────

def advance(current, kind):
    """The status a reply of `kind` moves an application to, or None when it does not move.
    `current` None is treated as 'submitted'."""
    new = KIND_TO_STATUS.get(kind)
    if new is None:
        return None
    current = current or "submitted"
    if new == "rejected":
        if current in _OVERRIDE_REJECTION or current == "rejected":
            return None
        return "rejected"
    if current == "rejected":
        return new if new in _OVERRIDE_REJECTION else None
    if STATUS_ORDER[new] > STATUS_ORDER.get(current, 0):
        return new
    return None


def redact(text):
    """Best-effort PII scrub for snippets: URLs (which can carry tokens), email addresses,
    phone-like numbers and runs of six or more digits (codes, account numbers) are replaced.
    Names and street addresses in prose cannot be reliably redacted without NER and are left
    as-is; snippets are capped at 200 chars and never leave the local database."""
    text = _URL.sub("[url]", text or "")
    text = _EMAIL.sub("[email]", text)
    text = _PHONE.sub("[phone]", text)
    return re.sub(r"\d{6,}", "[digits]", text)


_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_EMAIL = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b|\b\d{3}[-.\s]\d{4}\b")


def _words(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split()


def _normalize_company(company):
    tokens = _words(company)
    while tokens and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _aware(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value):
    when = _aware(value)
    if when is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return when.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _latest_submitted(cands, when):
    """The single application submitted most recently at or before `when`, or None on no
    eligible application or a tie for the latest."""
    stamped = [(a, _aware(a.get("submitted_at"))) for a in cands]
    eligible = [(a, s) for a, s in stamped if s is not None and (when is None or s <= when)]
    if not eligible:
        return None
    top = max(s for _, s in eligible)
    best = [a for a, s in eligible if s == top]
    return best[0] if len(best) == 1 else None


def _domain_within(host, domain):
    host = (host or "").lower().lstrip(".")
    domain = (domain or "").lower().lstrip(".")
    return bool(host and domain) and (host == domain or host.endswith("." + domain))


def _company_in(norm, message):
    """Does the normalized company name identify the sender? Matches the display name and the
    subject (word-boundary), or the domain labels (squashed) -- never the body, which the old
    _target() also ignored. A one- or two-letter single-token name ('as', 'it') only matches via
    domain labels: in prose it is indistinguishable from an ordinary word."""
    if not norm:
        return False
    domain = message.get("from_domain") or ""
    tokens = norm.split()
    text_match = False
    if not (len(tokens) == 1 and len(tokens[0]) < 3):
        text = " ".join(_words(f"{message.get('from_name', '')} {message.get('subject', '')}"))
        text_match = f" {norm} " in f" {text} "
    if text_match:
        return True
    squashed = norm.replace(" ", "")
    return any(label.replace("-", "") == squashed for label in domain.lower().split("."))


def _role_in(role, message):
    """The role words appear in the subject/body as an ordered phrase ('product manager' must read
    as a phrase, not as scattered words). Mirrors the old _target()'s _phrase_in check."""
    words = _words(role)
    if not words:
        return False
    text = " ".join(_words(f"{message.get('subject', '')} {message.get('body', '')}"))
    return f" {' '.join(words)} " in f" {text} "


def _eligible(app, when):
    """An application submitted after the message was sent cannot be what the message is about.
    Unknown dates (either side) do not disqualify."""
    submitted = _aware(app.get("submitted_at"))
    if submitted is not None and when is not None and submitted > when:
        return False
    return True


def _single(app, when, method):
    if _eligible(app, when):
        return app, method, False
    return None, method, False


def _disambiguate_domain(cands, when, message):
    eligible = [a for a in cands if _eligible(a, when)]
    if not eligible:
        return None, "domain", False
    named = [a for a in eligible if _role_in(a.get("role"), message)]
    if len(named) == 1:
        return named[0], "domain_role", False
    pool = named if named else eligible
    pick = _latest_submitted(pool, when)
    return (pick, "domain_role", False) if pick is not None else (None, "domain", True)


def _disambiguate_company(cands, when, message):
    eligible = [a for a in cands if _eligible(a, when)]
    if not eligible:
        return None, "company", False
    named = [a for a in eligible if _role_in(a.get("role"), message)]
    if len(named) == 1:
        return named[0], "company_role", False
    if named:
        pick = _latest_submitted(named, when)
        return (pick, "company_role", False) if pick is not None else (None, "company", True)
    return None, "company", True


def correlate(message, applications):
    """(application dict or None, method, needs_review) for one employer message.

    Order: thread headers, then sender domain (a tenant host not shared with other employers),
    then company name in the sender display name / subject / domain labels (never the body).
    Candidates submitted after the message are ineligible. When several remain, the role words
    narrow them first; a tenant-domain match then falls back to the latest submitted, while a
    company-name match without a role phrase goes to review. Ambiguity is never resolved by
    guessing: the result is (None, method, True). An optional message key `from_name` (the
    display name) also counts as company text.
    """
    when = _aware(message.get("date"))

    ids = [message.get("in_reply_to")] + list(message.get("references") or [])
    ids = {str(i).strip() for i in ids if i and str(i).strip()}
    if ids:
        hits = [a for a in applications if ids & {str(k).strip() for k in a.get("thread_keys") or []}]
        if len(hits) == 1:
            return _single(hits[0], when, "thread")
        if len(hits) > 1:
            return None, "thread", True

    from_domain = (message.get("from_domain") or "").lower()
    by_domain = [a for a in applications
                 if any(_domain_within(from_domain, d) for d in a.get("tenant_domains") or [])]
    if len(by_domain) == 1:
        return _single(by_domain[0], when, "domain")
    if len(by_domain) > 1:
        return _disambiguate_domain(by_domain, when, message)

    by_company = [a for a in applications if _company_in(_normalize_company(a.get("company")), message)]
    if len(by_company) == 1:
        return _single(by_company[0], when, "company")
    if len(by_company) > 1:
        return _disambiguate_company(by_company, when, message)

    return None, "none", False


# ── Persistence ────────────────────────────────────────────────────────────────

def get_current_status(conn, application_id):
    """Present status derived from the event history, or None when there is none."""
    rows = conn.execute(
        "SELECT status, at FROM application_status_events WHERE application_id = ?",
        (str(application_id),)).fetchall()
    if not rows:
        return None
    rejected_at = max((r["at"] for r in rows if r["status"] == "rejected"), default=None)
    if rejected_at is not None:
        revived = any(r["status"] in _OVERRIDE_REJECTION and r["at"] > rejected_at for r in rows)
        if not revived:
            return "rejected"
    ranked = [(STATUS_ORDER[r["status"]], r["at"], r["status"]) for r in rows if r["status"] in STATUS_ORDER]
    return max(ranked)[2] if ranked else None


def _revival_is_fresh(conn, application_id, received_at):
    """A post-rejection interview/offer only revives the application when it is newer than the
    latest rejection; a stale message ingested out of order must not undo a newer rejection.
    Timestamps here are the UTC-normalized ISO values record_reply writes, so string comparison
    is chronological."""
    row = conn.execute(
        "SELECT MAX(at) FROM application_status_events WHERE application_id = ? AND status = 'rejected'",
        (str(application_id),)).fetchone()
    return not row[0] or received_at > row[0]


def _row_by_message_id(conn, message_id):
    row = conn.execute("SELECT * FROM replies WHERE message_id = ?", (message_id,)).fetchone()
    return dict(row) if row else None


def record_reply(conn, *, application_id, job_id, message_id, thread_key, source, from_addr, from_domain,
                 subject, received_at, snippet, kind, confidence, match_method, needs_review):
    """Store one reply and, when it is attached to an application, append the status it causes.
    A message already stored returns the existing row with deduped=True and changes nothing."""
    received = _iso(received_at)
    if not message_id:
        digest = hashlib.sha1(f"{from_addr}|{subject}|{received}".encode()).hexdigest()
        message_id = "noid:" + digest[:32]
    application_id = str(application_id) if application_id is not None else None
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    reply_id = uuid.uuid4().hex

    cur = conn.execute(
        "INSERT OR IGNORE INTO replies (id, application_id, job_id, message_id, thread_key, source, from_addr,"
        " from_domain, subject, received_at, snippet, kind, status_after, confidence, match_method,"
        " needs_review, surfaced_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, ?)",
        (reply_id, application_id, job_id, message_id, thread_key, source, from_addr, from_domain, subject,
         received, redact(snippet)[:200], kind, confidence, match_method, 1 if needs_review else 0, now))
    if cur.rowcount == 0:
        conn.commit()
        row = _row_by_message_id(conn, message_id)
        row["deduped"] = True
        return row

    status_after = None
    if application_id:
        current = get_current_status(conn, application_id)
        status_after = advance(current, kind)
        if (status_after in _OVERRIDE_REJECTION and current == "rejected"
                and not _revival_is_fresh(conn, application_id, received)):
            status_after = None
        event_status = status_after
        if event_status is None and kind == "rejection" and current == "rejected":
            # A repeat rejection changes nothing, but it still belongs in the timeline: the
            # revival guard judges a later out-of-order interview against the newest rejection.
            event_status = "rejected"
        if event_status:
            conn.execute(
                "INSERT OR IGNORE INTO application_status_events (id, application_id, status, reply_id, at, actor)"
                " VALUES (?, ?, ?, ?, ?, 'monitor')",
                (uuid.uuid4().hex, application_id, event_status, reply_id, received))
            conn.execute("UPDATE replies SET status_after = ? WHERE id = ?", (event_status, reply_id))
            status_after = event_status
    conn.commit()
    row = _row_by_message_id(conn, message_id)
    row["deduped"] = False
    return row
