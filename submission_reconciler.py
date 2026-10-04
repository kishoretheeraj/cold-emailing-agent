"""
Submission reconciler -- resolves 'needs_confirmation' job_applications rows from Gmail receipts.

A row lands in needs_confirmation when the Submit click may have happened but the page never
confirmed it. This module looks in the receipt mailbox (the address forms are filled with, which
is not GMAIL_ADDRESS) for the employer's "we received your application" email. It only ever moves
needs_confirmation -> submitted, via the record_receipt_evidence RPC; it never retries a
submission, and run() never raises.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

import config
import db
import gmail

log = logging.getLogger(__name__)

# ── Matching constants ─────────────────────────────────────────────────────────

# Uncalibrated against real receipts. "thank you for your interest" alone is deliberately absent:
# rejections and ordinary human mail use it.
_RECEIPT_PHRASE = re.compile(
    r"\bthank(s| you) for (applying|your application|submitting)\b"
    r"|\b(application|submission) (has been |was )?(received|submitted)\b"
    r"|\bwe(?:'ve| have)? received your application\b"
    r"|\byour application (to|for|at)\b",
    re.IGNORECASE,
)
_COMPANY_SUFFIXES = {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "technologies", "labs"}
_WINDOW_SLACK = timedelta(minutes=2)
_WINDOW_MAX = timedelta(hours=72)
_ESCALATE_AFTER = timedelta(minutes=15)
_ESCALATION_PREFIX = "No receipt email"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _words(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split()


def _normalize_company(company):
    tokens = _words(company)
    while tokens and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _phrase_in(phrase, text):
    return bool(phrase) and f" {phrase} " in f" {' '.join(_words(text))} "


def _parse_ts(value):
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _window_start(app):
    for key in ("submit_attempted_at", "approved_at", "updated_at"):
        ts = _parse_ts(app.get(key))
        if ts is not None:
            return ts
    return None


def _company_matches(norm, msg):
    if not norm:
        return False
    display, addr = parseaddr(msg.get("from", ""))
    if _phrase_in(norm, display) or _phrase_in(norm, msg.get("subject", "")):
        return True
    domain = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
    squashed = norm.replace(" ", "")
    return any(label.replace("-", "") == squashed for label in domain.split("."))


def _qualifying(app, messages):
    start = _window_start(app)
    norm = _normalize_company(app.get("company"))
    if start is None:
        return []
    out = []
    for msg in messages:
        if msg.get("is_reply") or not msg.get("message_id"):
            continue
        if not (start - _WINDOW_SLACK <= msg["date"] <= start + _WINDOW_MAX):
            continue
        if not _company_matches(norm, msg):
            continue
        if not _RECEIPT_PHRASE.search(f"{msg.get('subject', '')}\n{msg.get('body', '')}"):
            continue
        out.append(msg)
    return sorted(out, key=lambda m: m["date"])


def _evidence(msg):
    return {"source": "gmail_receipt", "message_id": msg["message_id"], "from": msg["from"],
            "subject": msg["subject"], "date": msg["date"].isoformat()}


# ── Public API ─────────────────────────────────────────────────────────────────

def match_receipt(app, messages, now):
    """Earliest message that looks like this row's application receipt, as an evidence dict, or None."""
    found = _qualifying(app, messages)
    return _evidence(found[0]) if found else None


def run(now=None):
    """Resolve needs_confirmation rows from receipt emails. Never raises."""
    counts = {"checked": 0, "resolved": 0, "escalated": 0, "errors": 0}
    now = now or datetime.now(timezone.utc)
    address, password = config.RECEIPT_IMAP_ADDRESS, config.RECEIPT_IMAP_APP_PASSWORD
    if not (address and password):
        log.info("[RECONCILE] | SKIP | receipt mailbox not configured")
        return counts
    try:
        rows = db.get_applications_needing_confirmation()
        counts["checked"] = len(rows)
        if not rows:
            return counts
        starts = {row["id"]: _window_start(row) for row in rows}
        searchable = [r for r in rows if starts[r["id"]] and now - starts[r["id"]] <= _WINDOW_MAX]
        messages = []
        if searchable:
            norms = [_normalize_company(r.get("company")) for r in searchable]
            since = min(starts[r["id"]] for r in searchable).date()

            def want_body(msg):
                return not msg.get("is_reply") and any(_company_matches(n, msg) for n in norms)

            try:
                messages = gmail.fetch_inbox_since(since, address, password, want_body=want_body)
            except Exception as exc:
                log.warning(f"[RECONCILE] | IMAP | receipt mailbox scan failed: {exc}")
                counts["errors"] += 1
                return counts

        candidates = {r["id"]: _qualifying(r, messages) for r in searchable}
        claimants = {}
        for rid, msgs in candidates.items():
            for m in msgs:
                claimants.setdefault(m["message_id"], []).append(rid)
        by_id = {r["id"]: r for r in rows}
        consumed = set()
        for row in rows:
            rid = row["id"]
            try:
                chosen = None
                for m in candidates.get(rid, []):
                    if m["message_id"] in consumed:
                        continue
                    rivals = claimants[m["message_id"]]
                    if len(rivals) > 1:
                        named = [x for x in rivals
                                 if _phrase_in(" ".join(_words(by_id[x].get("role"))), m["subject"])]
                        if named != [rid]:
                            continue
                    chosen = m
                    break
                if chosen is not None:
                    if db.record_receipt_evidence(rid, _evidence(chosen)):
                        consumed.add(chosen["message_id"])
                        counts["resolved"] += 1
                        log.info(f"[RECONCILE] | {row.get('company')} | resolved | {chosen['message_id']}")
                    continue
                reason = row.get("apply_blocked_reason") or ""
                start = starts[rid]
                if start and now - start > _ESCALATE_AFTER and not reason.startswith(_ESCALATION_PREFIX):
                    db.set_apply_blocked(
                        rid, f"{_ESCALATION_PREFIX} found 15+ min after submit -- check the employer portal. "
                             f"Original: {reason or 'unknown'}")
                    counts["escalated"] += 1
                    log.info(f"[RECONCILE] | {row.get('company')} | escalated | no receipt after 15 min")
            except Exception as exc:
                counts["errors"] += 1
                log.warning(f"[RECONCILE] | {row.get('company')} | error | {exc}")
    except Exception as exc:
        counts["errors"] += 1
        log.warning(f"[RECONCILE] | run | error | {exc}")
    return counts
