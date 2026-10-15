"""
Application replies -- polls the receipt mailbox for employer mail about submitted applications and
stores each message in the local SQLite reply store (reply_repo).

Complements application_outcomes.py (which moves job_applications stages on a rejection or an
interview invite): this module records every application-related message, including confirmations,
assessments, offers and mail it cannot attach, with the status it implies.

Application adaptation. db.get_open_applications_for_outcomes() returns rows with id, company, role,
stage, applied_date, submit_attempted_at and outcome_evidence. Each row becomes a correlate() dict:
submitted_at is application_outcomes._window_start(row); thread_keys are any message-id fields the
row carries (message_id, thread_key, receipt_message_id, thread_keys, and the message_id inside
outcome_evidence / receipt_evidence); tenant_domains come from ats_sessions.tenant_key applied to
job_url / apply_url / url when present. Shared hosts (tenant keys with a path, e.g. a Greenhouse or
Lever board) are skipped: many employers share them, so the host says nothing about the sender. With
the current select the URL and message-id fields are absent, so correlation falls to company-name
matching; selecting them in db.py makes the thread and domain steps live with no change here.

Threading headers. gmail.fetch_inbox_since returns only an is_reply flag, not In-Reply-To or
References. For replies this module does one extra read-only imaplib pass (BODY.PEEK of
MESSAGE-ID / IN-REPLY-TO / REFERENCES, located by a HEADER Message-ID search) so thread correlation
works. Nothing is ever marked read, moved or deleted. run() never raises.
"""

import imaplib
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.utils import parseaddr

import config
import db
import gmail
import reply_repo
from application_outcomes import _BOOKING_LINK, _INTERVIEW, _RECEIPT, _REJECTION, _from_job_board, _window_start
from ats_sessions import tenant_key
from submission_reconciler import _company_matches, _normalize_company

log = logging.getLogger(__name__)

# ── Classification ─────────────────────────────────────────────────────────────

_CONFIRMATION = re.compile(
    r"\bthank(s| you) for (applying|your application|submitting your application|your interest in)\b"
    r"|\bwe(?:'ve| have)? received your application\b"
    r"|\byour application (has been |was )?(received|submitted)\b"
    r"|\bapplication (received|submitted|confirmation)\b",
    re.IGNORECASE,
)
_ASSESSMENT = re.compile(
    r"\b(online|coding|technical|take[- ]home|skills?|written|case study) (assessment|test|challenge|exercise)\b"
    r"|\bcomplete (the|an?|your) (following )?(assessment|questionnaire|exercise)\b"
    r"|\bassessment (link|invitation|invite)\b"
    r"|\b(hackerrank|codility|codesignal|hirevue|testgorilla)\b",
    re.IGNORECASE,
)
_OFFER = re.compile(
    r"\b(pleased|happy|excited|delighted|thrilled) to (extend|offer)\b"
    r"|\boffer of employment\b"
    r"|\b(formal|written|official) offer\b"
    r"|\boffer letter\b"
    r"|\bextend(ing)? (you )?an offer\b",
    re.IGNORECASE,
)
_VERIFICATION = re.compile(
    r"\b(verification|verify|security|one[- ]time|login|sign[- ]in|confirmation|access) (code|pin|passcode)\b"
    r"|\bverify your (email|e-mail|account|identity)\b"
    r"|\bconfirm your (email|e-mail|account)\b"
    r"|\b(otp|one[- ]time password)\b"
    r"|\bpassword reset\b|\breset your password\b",
    re.IGNORECASE,
)

MONITOR_NUDGE_KINDS = frozenset(
    k.strip() for k in os.environ.get("MONITOR_NUDGE_KINDS", "interview,offer,assessment").split(",") if k.strip())

_CONFIDENCE = {"thread": 0.95, "domain": 0.9, "domain_role": 0.75, "company_role": 0.7, "company": 0.6, "none": 0.0}

# Bounded threading-header pass: one IMAP search per reply is expensive, so cap the ids and the
# total time; messages past the cap simply correlate without thread headers.
_HEADER_FETCH_CAP = 100
_HEADER_FETCH_DEADLINE_S = 120


def classify_kind(subject, body, from_domain):
    """'confirmation', 'assessment', 'interview', 'offer', 'rejection', 'other', or None for
    verification mail (codes, sign-in links), which is never stored."""
    text = f"{subject or ''}\n{body or ''}"
    if _VERIFICATION.search(text):
        return None
    if _REJECTION.search(text):
        return "rejection"
    if _OFFER.search(text):
        return "offer"
    if _INTERVIEW.search(text) and (_BOOKING_LINK.search(text) or not _RECEIPT.search(text)):
        return "interview"
    if _BOOKING_LINK.search(text) and re.search(r"\b(interview|phone screen)\b", text, re.IGNORECASE):
        return "interview"
    if _ASSESSMENT.search(text):
        return "assessment"
    if _CONFIRMATION.search(text):
        return "confirmation"
    return "other"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sender_domain(msg):
    _, addr = parseaddr(msg.get("from", ""))
    return addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""


def _adapt_application(row):
    keys = []
    for field in ("message_id", "thread_key", "receipt_message_id"):
        if row.get(field):
            keys.append(str(row[field]).strip())
    keys.extend(str(k).strip() for k in (row.get("thread_keys") or []) if k)
    for field in ("outcome_evidence", "receipt_evidence"):
        evidence = row.get(field)
        if isinstance(evidence, dict) and evidence.get("message_id"):
            keys.append(str(evidence["message_id"]).strip())
    domains = []
    for field in ("job_url", "apply_url", "url"):
        key = tenant_key(row.get(field))
        if key and "/" not in key:
            domains.append(key)
    return {
        "id": str(row["id"]),
        "job_id": row.get("job_key") or row.get("job_id"),
        "company": row.get("company"),
        "role": row.get("role"),
        "submitted_at": _window_start(row),
        "thread_keys": keys,
        "tenant_domains": domains,
    }


def _fetch_threading_headers(address, password, message_ids):
    """{message_id: {'in_reply_to': str|None, 'references': [str]}} via a read-only IMAP pass.
    Never raises; ids it cannot resolve are simply absent. Bounded: at most _HEADER_FETCH_CAP ids,
    and the whole pass runs in a daemon thread joined on _HEADER_FETCH_DEADLINE_S -- a hung IMAP
    call cannot stall the run past the deadline (the abandoned thread dies with the process)."""
    out = {}
    ids = list(message_ids)[:_HEADER_FETCH_CAP]
    if not ids:
        return out

    def work():
        imap = None
        try:
            imap = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
            imap.login(address, password)
            status, _ = imap.select('"[Gmail]/All Mail"', readonly=True)
            if status != "OK":
                return
            for mid in ids:
                try:
                    safe = str(mid).replace('"', "").replace("\\", "")
                    status, data = imap.search(None, "HEADER", "Message-ID", f'"{safe}"')
                    if status != "OK" or not data or not data[0]:
                        continue
                    raw = gmail._fetch_raw(
                        imap, data[0].split()[0], "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES)])")
                    if raw is None:
                        continue
                    hdr = message_from_bytes(raw)
                    in_reply_to = (hdr.get("In-Reply-To") or "").strip() or None
                    refs = re.findall(r"<[^>]+>", hdr.get("References") or "")
                    out[mid] = {"in_reply_to": in_reply_to, "references": refs}
                except Exception:
                    log.warning("[REPLIES] | IMAP | header fetch failed")
        except Exception:
            log.warning("[REPLIES] | IMAP | header pass failed")
        finally:
            if imap is not None:
                try:
                    imap.logout()
                except Exception:
                    pass

    thread = threading.Thread(target=work, daemon=True, name="reply-headers")
    thread.start()
    thread.join(_HEADER_FETCH_DEADLINE_S)
    if thread.is_alive():
        log.warning("[REPLIES] | IMAP | header pass exceeded deadline; continuing without thread headers")
    return out


def _nudge(app, kind, notify):
    log.info(f"[REPLIES] | {app.get('company')} | {app.get('role')} | {kind} | needs your attention")
    if notify is None:
        return
    try:
        notify(app.get("company"), app.get("role"), kind)
    except Exception as exc:
        log.warning(f"[REPLIES] | notify failed: {type(exc).__name__}")


# ── Public entry point ─────────────────────────────────────────────────────────

def run(now=None, notify=None):
    """Store application-related mail from the last APPLY_OUTCOME_LOOKBACK_DAYS of the receipt
    mailbox. `notify(company, role, kind)` is called for new interview/offer/assessment mail.
    Never raises."""
    counts = {"checked": 0, "stored": 0, "needs_review": 0, "errors": 0}
    now = now or datetime.now(timezone.utc)
    address, password = config.RECEIPT_IMAP_ADDRESS, config.RECEIPT_IMAP_APP_PASSWORD
    if not (address and password):
        log.info("[REPLIES] | SKIP | receipt mailbox not configured")
        return counts
    conn = None
    try:
        rows = db.get_open_applications_for_outcomes(config.APPLY_OUTCOME_MAX_AGE_DAYS)
        if not rows:
            return counts
        apps = [_adapt_application(r) for r in rows]
        norms = {_normalize_company(a["company"]) for a in apps}
        domains = {d for a in apps for d in a["tenant_domains"]}
        thread_keys = {k for a in apps for k in a["thread_keys"]}

        def relevant(msg):
            if _from_job_board(msg):
                return False
            sender = _sender_domain(msg)
            if any(sender == d or sender.endswith("." + d) for d in domains):
                return True
            return any(_company_matches(n, msg) for n in norms)

        def want_body(msg):
            # Bodies are needed for classification. Threaded replies are fetched too: a reply in
            # a known thread can come from an address that matches no company or tenant domain,
            # and without its body it would be permanently stored as kind 'other'.
            return bool(msg.get("is_reply")) or relevant(msg)

        try:
            since = (now - timedelta(days=config.APPLY_OUTCOME_LOOKBACK_DAYS)).date()
            messages = gmail.fetch_inbox_since(since, address, password, want_body=want_body)
        except Exception as exc:
            counts["errors"] += 1
            log.warning(f"[REPLIES] | IMAP | mailbox scan failed: {type(exc).__name__}")
            return counts

        reply_ids = [m["message_id"] for m in messages if m.get("is_reply") and m.get("message_id")]
        headers = _fetch_threading_headers(address, password, reply_ids) if reply_ids else {}

        conn = reply_repo.connect()
        for msg in sorted(messages, key=lambda m: m["date"]):
            counts["checked"] += 1
            try:
                hdr = headers.get(msg.get("message_id"), {})
                in_reply_to = hdr.get("in_reply_to")
                references = hdr.get("references") or []
                threaded = bool(thread_keys & ({in_reply_to} | set(references)))
                if not (threaded or relevant(msg)):
                    continue
                name, addr = parseaddr(msg.get("from", ""))
                domain = _sender_domain(msg)
                body = msg.get("body") or ""
                kind = classify_kind(msg.get("subject"), body, domain)
                if kind is None:
                    continue
                view = {"message_id": msg.get("message_id"), "in_reply_to": in_reply_to,
                        "references": references, "from_domain": domain, "from_name": name,
                        "subject": msg.get("subject", ""), "body": body, "date": msg["date"]}
                app, method, needs_review = reply_repo.correlate(view, apps)
                if app is None and not needs_review:
                    continue
                row = reply_repo.record_reply(
                    conn,
                    application_id=app["id"] if app else None,
                    job_id=app.get("job_id") if app else None,
                    message_id=msg.get("message_id"),
                    thread_key=(references[0] if references else in_reply_to or msg.get("message_id")),
                    source="imap", from_addr=addr, from_domain=domain, subject=msg.get("subject", ""),
                    received_at=msg["date"], snippet=body or msg.get("subject", ""), kind=kind,
                    confidence=_CONFIDENCE.get(method, 0.0), match_method=method, needs_review=needs_review)
                if row.get("deduped"):
                    continue
                counts["stored"] += 1
                if needs_review:
                    counts["needs_review"] += 1
                if app is not None and not needs_review and kind in MONITOR_NUDGE_KINDS:
                    _nudge(app, kind, notify)
            except Exception as exc:
                counts["errors"] += 1
                log.warning(f"[REPLIES] | message | error | {type(exc).__name__}: {exc}")
    except Exception as exc:
        counts["errors"] += 1
        log.warning(f"[REPLIES] | run | error | {type(exc).__name__}: {exc}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    log.info(f"[REPLIES] | DONE | {counts}")
    return counts
