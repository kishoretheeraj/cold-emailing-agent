"""
Application outcomes -- reads rejections and interview invites for submitted applications from the
receipt mailbox (the address forms are filled with) and moves the application's stage.

- A rejection moves applied/phone_screen/onsite -> rejected. That also stops applied-mode warm-path
  mail for the role (agent._application_gate).
- An interview invite moves applied -> phone_screen.

Every move stores the email as `outcome_evidence` (with the previous stage, so the queue's Undo can
restore it). A message already used for a row is never used again, so an undone outcome stays undone.
Precision over recall: matching is by exact phrases, only mail from the application's company (never
a job board), only after the application went in, only messages that are not replies in a human
thread, and only one open application at that company unless the email names the role. Anything
uncertain changes nothing. run() never raises.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

import config
import db
import gmail
from submission_reconciler import _company_matches, _normalize_company, _parse_ts, _phrase_in, _words

log = logging.getLogger(__name__)

# ── Classification ─────────────────────────────────────────────────────────────

_REJECTION = re.compile(
    r"\b(decided|chosen|elected) (not to|to not) (move|go|proceed) forward\b"
    r"|\bnot (be )?(moving|move|going|proceed(ing)?) forward with (your|you)\b"
    r"|\bwill not be (moving|proceeding|going) forward\b"
    r"|\b(move|moving|go|going|proceed|proceeding) forward with (other|another) (candidates?|applicants?)\b"
    r"|\bpursue (other|another) candidates?\b"
    r"|\bregret to inform\b"
    r"|\b(you were|you have|you've) not (been )?selected\b"
    r"|\bposition has (now )?(been filled|been closed|closed)\b"
    r"|\bno longer (under|being) consider",
    re.IGNORECASE,
)
_INTERVIEW = re.compile(
    r"\binvite you to (an? )?(\w+ )?(interview|phone screen|call|conversation|chat|video call)\b"
    r"|\b(share|send|provide|let us know) (us )?your availability\b"
    r"|\bschedule (a|an|your|the) (\w+ )?(call|interview|chat|conversation|phone screen)\b"
    r"|\b(first|initial)[- ]round interview\b"
    r"|\bnext step(s)? (in (our|the) (hiring )?process )?(is|will be|would be) (an? )?(\w+ )?(interview|call|phone screen|conversation)\b",
    re.IGNORECASE,
)
_BOOKING_LINK = re.compile(
    r"(calendly\.com/|goodtime\.io|cal\.com/|calendar\.app\.google|app\.gem\.com/|/schedul(e|ing)/)", re.IGNORECASE)
# Receipts often promise that "if your qualifications match, we will reach out to schedule an
# interview". Without a booking link that is not an invite.
_RECEIPT = re.compile(
    r"\bthank(s| you) for (applying|your application|submitting your application)\b"
    r"|\bwe(?:'ve| have)? received your application\b"
    r"|\bif (your|you|we|there)\b[^.]{0,80}\b(interview|call|reach out|contact)\b",
    re.IGNORECASE,
)

_JOB_BOARD_DOMAINS = ("linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "jobright.ai",
                      "simplify.jobs", "joinhandshake.com", "handshake.com", "monster.com", "dice.com",
                      "wellfound.com", "otta.com", "builtin.com", "careerbuilder.com")

_REJECTABLE_STAGES = ("applied", "phone_screen", "onsite")
_INTERVIEWABLE_STAGES = ("applied",)


def classify(text):
    """'rejection', 'interview', or None. A rejection wins over interview wording in the same email."""
    text = text or ""
    if _REJECTION.search(text):
        return "rejection"
    if _INTERVIEW.search(text) and (_BOOKING_LINK.search(text) or not _RECEIPT.search(text)):
        return "interview"
    if _BOOKING_LINK.search(text) and re.search(r"\b(interview|phone screen)\b", text, re.IGNORECASE):
        return "interview"
    return None


# ── Matching ───────────────────────────────────────────────────────────────────

def _from_job_board(msg):
    _, addr = parseaddr(msg.get("from", ""))
    domain = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
    return any(domain == d or domain.endswith("." + d) for d in _JOB_BOARD_DOMAINS)


def _window_start(app):
    started = _parse_ts(app.get("submit_attempted_at"))
    if started is not None:
        return started
    applied = app.get("applied_date")
    if applied:
        try:
            return datetime.fromisoformat(str(applied)[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _used_message(app):
    evidence = app.get("outcome_evidence")
    return evidence.get("message_id") if isinstance(evidence, dict) else None


def _evidence(msg, kind, app):
    return {"source": "gmail_outcome", "kind": kind, "message_id": msg["message_id"], "from": msg["from"],
            "subject": msg["subject"], "date": msg["date"].isoformat(), "previous_stage": app.get("stage")}


def _target(msg, apps):
    if msg.get("is_reply") or not msg.get("message_id") or _from_job_board(msg):
        return None
    candidates = []
    for app in apps:
        start = _window_start(app)
        if start is None or msg["date"] < start:
            continue
        if not _company_matches(_normalize_company(app.get("company")), msg):
            continue
        candidates.append(app)
    if len(candidates) <= 1:
        return candidates[0] if candidates else None
    text = f"{msg.get('subject', '')}\n{msg.get('body', '')}"
    named = [a for a in candidates if _phrase_in(" ".join(_words(a.get("role"))), text)]
    return named[0] if len(named) == 1 else None


def decide(apps, messages, now=None):
    """[(app, kind, evidence)] for the outcomes these messages establish, at most one per app (the
    latest message wins)."""
    latest = {}
    for msg in sorted(messages or [], key=lambda m: m["date"]):
        app = _target(msg, apps)
        if app is None or msg["message_id"] == _used_message(app):
            continue
        kind = classify(f"{msg.get('subject', '')}\n{msg.get('body', '')}")
        if kind is None:
            continue
        allowed = _REJECTABLE_STAGES if kind == "rejection" else _INTERVIEWABLE_STAGES
        if app.get("stage") not in allowed:
            continue
        latest[app["id"]] = (app, kind, _evidence(msg, kind, app))
    return list(latest.values())


# ── Public entry point ─────────────────────────────────────────────────────────

def run(now=None):
    """Apply outcomes found in the last APPLY_OUTCOME_LOOKBACK_DAYS of the receipt mailbox. Never raises."""
    counts = {"checked": 0, "rejected": 0, "interviews": 0, "errors": 0}
    now = now or datetime.now(timezone.utc)
    address, password = config.RECEIPT_IMAP_ADDRESS, config.RECEIPT_IMAP_APP_PASSWORD
    if not (address and password):
        log.info("[OUTCOME] | SKIP | receipt mailbox not configured")
        return counts
    try:
        apps = db.get_open_applications_for_outcomes(config.APPLY_OUTCOME_MAX_AGE_DAYS)
        counts["checked"] = len(apps)
        if not apps:
            return counts
        norms = {_normalize_company(a.get("company")) for a in apps}

        def want_body(msg):
            return (not msg.get("is_reply") and not _from_job_board(msg)
                    and any(_company_matches(n, msg) for n in norms))

        try:
            since = (now - timedelta(days=config.APPLY_OUTCOME_LOOKBACK_DAYS)).date()
            messages = gmail.fetch_inbox_since(since, address, password, want_body=want_body)
        except Exception as exc:
            counts["errors"] += 1
            log.warning(f"[OUTCOME] | IMAP | mailbox scan failed: {exc}")
            return counts

        for app, kind, evidence in decide(apps, messages, now=now):
            to_stage, from_stages = (("rejected", _REJECTABLE_STAGES) if kind == "rejection"
                                     else ("phone_screen", _INTERVIEWABLE_STAGES))
            try:
                if db.record_application_outcome(app["id"], to_stage, from_stages, evidence):
                    counts["rejected" if kind == "rejection" else "interviews"] += 1
                    log.info(f"[OUTCOME] | {app.get('company')} | {app.get('role')} | {kind} | {evidence['message_id']}")
                    db.log_agent_event("application_outcome", status="success",
                                       metadata={"application_id": app["id"], "kind": kind,
                                                 "subject": evidence["subject"], "to_stage": to_stage})
            except Exception as exc:
                counts["errors"] += 1
                log.warning(f"[OUTCOME] | {app.get('company')} | record failed: {exc}")
    except Exception as exc:
        counts["errors"] += 1
        log.warning(f"[OUTCOME] | run | error | {exc}")
    log.info(f"[OUTCOME] | DONE | {counts}")
    return counts
