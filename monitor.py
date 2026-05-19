"""
Reply Monitor — runs every 2 hours Mon-Fri via GitHub Actions.

Workflow:
  1. detect_sent_drafts  — flip *_drafted → *_sent when Sent Mail evidence found
  2. detect_replies      — scan INBOX via In-Reply-To/References header matching
  3. _classify_replies   — Claude Haiku classifies each new reply; writes classifier_status
  4. _draft_reply_responses — creates Gmail drafts for positive_reply/soft_yes contacts
"""

import email
import email.policy
import imaplib
import json
import sys
import logging
from datetime import date, timedelta, datetime, timezone

# ── Logging setup ──────────────────────────────────────────────────────────────
# Must be called before any project imports — agent.py also calls basicConfig
# at module level (for agent.log), and basicConfig is a no-op if root already
# has handlers. Importing agent first would silently redirect all monitor logs
# to agent.log instead of monitor.log.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s EST | %(message)s",
    datefmt="%Y-%m-%d %H:%M",
    handlers=[
        logging.FileHandler("monitor.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

from agent import DRAFTED_TO_SENT, NEXT_STAGE, _parse_date
from config import (
    FOLLOWUP_DAYS, GMAIL_ADDRESS, GMAIL_APP_PASSWORD,
    REPLY_CLASSIFICATION_MODEL, REPLY_CLASSIFICATION_DEFAULT,
)
from constants import TERMINAL_DRAFTED_STAGES
from db import (
    get_drafted_contacts, get_sent_contacts, update_contact, update_reply_status,
    log_agent_event, update_classifier_status, insert_email_message, load_prompts,
    update_message_id,
)
from gmail import (
    create_gmail_label_if_not_exists, find_sent_for_thread,
    find_sent_by_subject, find_sent_by_thread_id,
)
from emailer import _call_claude

REPLIED_LABEL = "Cold Outreach/Replied"

# ── Sent-draft detection ───────────────────────────────────────────────────────

def detect_sent_drafts():
    """
    For every contact in a *_drafted stage with a non-null message_id, check
    Sent Mail for evidence the user sent it. Best-effort per contact.
    """
    contacts = get_drafted_contacts()
    checked = 0
    flipped = 0

    for contact in contacts:
        name       = contact.get("name", "Unknown")
        company    = contact.get("company", "Unknown")
        stage      = contact.get("stage", "")
        message_id = contact.get("message_id")
        checked += 1

        if not message_id:
            log.info(f"[SENT-CHECK] | {name} | {company} | skip: no message_id")
            continue

        mode = "first_touch" if stage in {"first_touch_drafted", "applied_intro_drafted"} else "followup"

        since_date = _parse_date(contact.get("last_emailed"))
        if since_date is None:
            since_date = date.today() - timedelta(days=60)

        actual_mid = None

        # Priority 1: X-GM-THRID — Gmail's stable thread ID, survives message_id rewrites.
        if not actual_mid and contact.get("gmail_thread_id"):
            try:
                actual_mid = find_sent_by_thread_id(contact["gmail_thread_id"], since_date)
            except Exception as exc:
                log.warning(f"[SENT-CHECK] | {name} | {company} | thrid error: {exc}")

        # Priority 2: Message-ID header search.
        if not actual_mid:
            try:
                actual_mid = find_sent_for_thread(message_id, since_date, mode)
            except Exception as exc:
                log.warning(f"[SENT-CHECK] | {name} | {company} | unexpected error: {exc}")
                continue

        # Priority 3: subject fragment fallback (first_touch only — last resort).
        if not actual_mid and mode == "first_touch":
            original_subject = contact.get("original_subject", "")
            if original_subject:
                actual_mid = find_sent_by_subject(original_subject, since_date, contact.get("email", ""))

        if not actual_mid:
            continue

        new_stage = DRAFTED_TO_SENT.get(stage)
        if new_stage is None:
            log.warning(f"[SENT-CHECK] | {name} | {company} | unknown stage: {stage}")
            continue

        action = next((a for a, s in NEXT_STAGE.items() if s == stage), None)
        followup_days = FOLLOWUP_DAYS.get(action) if action else None
        terminal = stage in TERMINAL_DRAFTED_STAGES
        clear_fd = terminal and followup_days is None

        try:
            update_contact(
                contact["id"], new_stage,
                followup_days=followup_days,
                clear_followup_date=clear_fd,
            )
        except Exception as exc:
            log.warning(f"[SENT-DETECTED] | {name} | {company} | db error: {exc}")
            continue

        new_followup_date = (
            None if followup_days is None
            else str(date.today() + timedelta(days=followup_days))
        )
        log.info(
            f"[SENT-DETECTED] | {name} | {company} | "
            f"{stage} -> {new_stage} | followup_date={new_followup_date}"
        )

        # If Gmail rewrote the Message-ID on send, update it so follow-ups thread correctly.
        if mode == "first_touch" and actual_mid != message_id:
            try:
                update_message_id(contact["id"], actual_mid)
                log.info(f"[SENT-DETECTED] | {name} | {company} | message_id updated: {actual_mid}")
            except Exception as exc:
                log.warning(f"[SENT-DETECTED] | {name} | {company} | message_id update failed: {exc}")

        flipped += 1

    log.info(f"DONE | sent-detection | checked={checked} flipped={flipped}")


# ── IMAP helpers ───────────────────────────────────────────────────────────────

def _fetch_headers(imap, num):
    """Fetch and parse headers for a single INBOX message number."""
    status, data = imap.fetch(num, "(BODY[HEADER])")
    if status != "OK" or not data or not data[0]:
        return {}
    raw = data[0][1] if isinstance(data[0], tuple) else b""
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    return msg


def _header_val(msg, name):
    """Return a decoded header value or empty string."""
    val = msg.get(name, "")
    return str(val).strip() if val else ""


def _is_auto_reply(msg):
    """Return True if the message is an automated reply."""
    auto_sub = _header_val(msg, "Auto-Submitted").lower()
    if auto_sub and auto_sub != "no":
        return True
    if _header_val(msg, "X-Auto-Response-Suppress"):
        return True
    return False


def _fetch_body_text(imap, num):
    """Return the plain-text body of a message, truncated to 2000 chars."""
    status, data = imap.fetch(num, "(BODY[TEXT])")
    if status != "OK" or not data or not data[0]:
        return ""
    raw = data[0][1] if isinstance(data[0], tuple) else b""
    try:
        return raw.decode("utf-8", errors="replace")[:2000]
    except Exception:
        return ""


def _match_message(msg, by_message_id):
    """
    Return the matched contact for an INBOX message, or None.
    Checks In-Reply-To first, then walks the References chain.
    """
    in_reply_to = _header_val(msg, "In-Reply-To").strip("<>")
    if in_reply_to and in_reply_to in by_message_id:
        return by_message_id[in_reply_to]

    references = _header_val(msg, "References")
    for ref in references.split():
        ref_id = ref.strip("<>")
        if ref_id and ref_id in by_message_id:
            return by_message_id[ref_id]

    return None


# ── Reply classification ───────────────────────────────────────────────────────

def _classify_reply(body_text, contact, prompts):
    """Call Claude Haiku to classify a reply. Returns classifier_status string."""
    _fallback = "unrelated"
    tpl = prompts.get("reply_classification_prompt", REPLY_CLASSIFICATION_DEFAULT)
    try:
        prompt = tpl.format(reply_body=body_text[:1500])
        raw = _call_claude(prompt, model=REPLY_CLASSIFICATION_MODEL, max_tokens=100)
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1].lstrip("json").strip()
        data = json.loads(text)
        return data.get("classifier_status", _fallback)
    except Exception as exc:
        log.warning(f"[CLASSIFY] error: {exc}")
        return _fallback


# ── Reply detection ────────────────────────────────────────────────────────────

def detect_replies(prompts=None):
    """
    Scan INBOX for replies to our sent emails, matching via In-Reply-To and
    References headers against stored message_ids. Classifies each match.
    Best-effort per message.
    """
    _prompts = prompts or {}
    contacts = get_sent_contacts()

    log.info(f"START | reply-detection | {len(contacts)} contacts to check")
    if not contacts:
        log.info("DONE | reply-detection | 0 contacts")
        return []

    # Build lookup: stored message_id → contact
    by_message_id = {}
    for c in contacts:
        mid = (c.get("message_id") or "").strip("<>")
        if mid:
            by_message_id[mid] = c

    if not by_message_id:
        log.info("DONE | reply-detection | no contacts with message_id")
        return []

    newly_classified = []

    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        imap.select("INBOX", readonly=True)
        create_gmail_label_if_not_exists(imap, REPLIED_LABEL)

        since_str = (date.today() - timedelta(days=60)).strftime("%d-%b-%Y")
        seen_nums = set()

        for mid, contact in by_message_id.items():
            if contact.get("classifier_status") is not None:
                continue

            name = contact.get("name", "Unknown")
            company = contact.get("company", "Unknown")

            # Targeted HEADER search — server-side filter, not a full inbox scan.
            matched_nums = set()
            for header in ("In-Reply-To", "References"):
                status, data = imap.search(
                    None,
                    f'SINCE "{since_str}" HEADER "{header}" "{mid}"',
                )
                if status == "OK" and data[0]:
                    matched_nums.update(data[0].split())

            log.info(
                f"[REPLY-DETECTION] | {name} | {company} | "
                f"candidates={len(matched_nums)}"
            )

            for num in sorted(matched_nums):
                if num in seen_nums:
                    continue
                seen_nums.add(num)
                try:
                    msg = _fetch_headers(imap, num)
                    if _match_message(msg, {mid: contact}) is None:
                        continue

                    is_auto = _is_auto_reply(msg)
                    body_text = "" if is_auto else _fetch_body_text(imap, num)

                    # Store in email_messages (idempotent)
                    incoming_mid = _header_val(msg, "Message-ID").strip("<>")
                    in_reply_to_hdr = _header_val(msg, "In-Reply-To").strip("<>")
                    date_hdr = _header_val(msg, "Date")
                    try:
                        sent_at = email.utils.parsedate_to_datetime(date_hdr).isoformat()
                    except Exception:
                        sent_at = datetime.now(timezone.utc).isoformat()

                    insert_email_message(
                        contact_id=contact["id"],
                        direction="incoming",
                        sent_at=sent_at,
                        subject=_header_val(msg, "Subject"),
                        body=body_text,
                        message_id=incoming_mid or None,
                        in_reply_to=in_reply_to_hdr or None,
                        stage_at_send=contact.get("stage"),
                        raw_headers={"auto_reply": is_auto},
                    )

                    # Classify
                    if is_auto:
                        status_val = "auto_reply"
                        log.info(f"[REPLY] | {name} | {company} | auto-reply header detected")
                    else:
                        status_val = _classify_reply(body_text, contact, _prompts)

                    update_classifier_status(contact["id"], status_val)
                    log_agent_event("classify_reply", contact_id=contact["id"],
                                    status="success")

                    # Copy to label (best-effort)
                    try:
                        imap.select("INBOX")
                        imap.copy(num.decode() if isinstance(num, bytes) else num,
                                  f'"{REPLIED_LABEL}"')
                    except Exception as exc:
                        log.warning(f"[REPLY] | {name} | {company} | label warning: {exc}")

                    # Update contact's classifier_status in memory for draft phase
                    contact["classifier_status"] = status_val
                    newly_classified.append(contact)

                    log.info(
                        f"[REPLY] | {name} | {company} | "
                        f"classifier_status={status_val}"
                    )

                except Exception as exc:
                    log.warning(f"[REPLY] | message {num} | error: {exc}")
                    continue

    finally:
        imap.logout()

    log.info(f"DONE | reply-detection | {len(newly_classified)} new replies classified")
    return newly_classified


# ── Reply draft responses ──────────────────────────────────────────────────────

def _draft_reply_responses(classified_contacts, prompts):
    """Create Gmail reply drafts for positive_reply and soft_yes contacts."""
    import reply_drafter

    for contact in classified_contacts:
        name = contact.get("name", "Unknown")
        company = contact.get("company", "Unknown")
        cs = contact.get("classifier_status", "")
        if cs not in reply_drafter.DRAFTABLE_STATUSES:
            continue
        try:
            # Retrieve body from email_messages for the latest incoming message
            from db import get_email_messages
            messages = get_email_messages(contact["id"])
            incoming = [m for m in messages if m.get("direction") == "incoming"]
            reply_body_text = incoming[-1]["body"] if incoming else ""
            reply_drafter.draft_reply(contact, reply_body_text, prompts)
        except Exception as exc:
            log.warning(f"[REPLY-DRAFT] | {name} | {company} | error: {exc}")


# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    log.info("START | monitor run")

    prompts = {}
    try:
        prompts = load_prompts() or {}
    except Exception as exc:
        log.warning(f"[MONITOR] prompts load failed, using defaults: {exc}")

    try:
        detect_sent_drafts()
    except Exception as exc:
        log.warning(f"sent-detection | unexpected failure: {exc}")

    try:
        classified = detect_replies(prompts=prompts)
    except Exception as exc:
        log.warning(f"reply-detection | unexpected failure: {exc}")
        classified = []

    try:
        _draft_reply_responses(classified, prompts)
    except Exception as exc:
        log.warning(f"reply-drafting | unexpected failure: {exc}")


if __name__ == "__main__":
    run()
