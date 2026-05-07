"""
Cold Email Agent — Dual Mode
Runs daily via GitHub Actions at 8am EST Monday-Friday.

Mode A (outreach): Cold intro to new contacts, 4-email sequence.
Mode B (applied):  Email to hiring managers after applying, 2-email max.

User workflow:
  1. Add contact to Supabase (set mode, fill relevant fields)
  2. Agent generates Gmail draft overnight
  3. User reviews draft in Gmail and hits Send
  4. User updates stage to *_sent in Supabase
  5. Agent generates follow-up when followup_date arrives
  6. User updates reply_status when someone responds
"""

import sys
import time
import logging
from datetime import date

from config import FOLLOWUP_DAYS
from constants import TERMINAL_REPLY_STATUSES
from db import get_all_contacts, update_contact, close_contact, save_thread_info, get_thread_info
from emailer import generate_email
from gmail import create_draft, apply_label_to_latest_draft

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s EST | %(message)s",
    datefmt="%Y-%m-%d %H:%M",
    handlers=[
        logging.FileHandler("agent.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── Decision logic ─────────────────────────────────────────────────────────────

def decide_action(contact, today):
    mode = contact.get("mode", "outreach")
    reply = contact.get("reply_status", "no_reply")
    stage = contact.get("stage", "new")

    # Global skips
    if reply in TERMINAL_REPLY_STATUSES:
        return "skip"
    if stage == "closed":
        return "skip"

    if mode == "outreach":
        return _decide_outreach(contact, today)
    if mode == "applied":
        return _decide_applied(contact, today)
    return "skip"


def _decide_outreach(contact, today):
    stage = contact.get("stage", "new")
    followup = _parse_date(contact.get("followup_date"))

    if stage == "new":
        return "send_first_touch"
    if stage in ("first_touch_drafted", "followup1_drafted",
                 "followup2_drafted", "breakup_drafted"):
        return "skip"
    if stage == "breakup_sent":
        return "skip"
    if followup and followup <= today:
        if stage == "first_touch_sent":  return "send_followup1"
        if stage == "followup1_sent":    return "send_followup2"
        if stage == "followup2_sent":    return "send_breakup"
    return "skip"


def _decide_applied(contact, today):
    stage = contact.get("stage", "new")
    followup = _parse_date(contact.get("followup_date"))

    if stage == "new":
        return "send_applied_intro"
    if stage in ("applied_intro_drafted", "applied_followup_drafted"):
        return "skip"
    if stage == "applied_followup_sent":
        return "skip"
    if followup and followup <= today:
        if stage == "applied_intro_sent":
            return "send_applied_followup"
    return "skip"


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


# Actions that open a new thread — first email in a sequence.
_FIRST_TOUCH_ACTIONS = {"send_first_touch", "send_applied_intro"}

# ── Stage transitions ──────────────────────────────────────────────────────────

NEXT_STAGE = {
    "send_first_touch":      "first_touch_drafted",
    "send_followup1":        "followup1_drafted",
    "send_followup2":        "followup2_drafted",
    "send_breakup":          "breakup_drafted",
    "send_applied_intro":    "applied_intro_drafted",
    "send_applied_followup": "applied_followup_drafted",
}

NEXT_TEMPLATE = {
    "send_first_touch":      "cold_intro",
    "send_followup1":        "follow_up_1",
    "send_followup2":        "follow_up_2",
    "send_breakup":          "breakup",
    "send_applied_intro":    "applied_intro",
    "send_applied_followup": "applied_followup",
}

ACTION_LABEL = {
    "send_first_touch":      "Cold Outreach/First Touch",
    "send_followup1":        "Cold Outreach/Follow-up #1",
    "send_followup2":        "Cold Outreach/Follow-up #2",
    "send_breakup":          "Cold Outreach/Break-up",
    "send_applied_intro":    "Cold Outreach/Applied Intro",
    "send_applied_followup": "Cold Outreach/Applied Follow-up",
}

# ── Main loop ──────────────────────────────────────────────────────────────────

def run():
    today = date.today()
    start = time.time()

    contacts = get_all_contacts()
    outreach_count = sum(1 for c in contacts if c.get("mode", "outreach") == "outreach")
    applied_count  = sum(1 for c in contacts if c.get("mode") == "applied")

    log.info(f"START | {len(contacts)} contacts | {outreach_count} outreach | {applied_count} applied")

    drafted = 0
    skipped = 0
    errors  = 0

    for contact in contacts:
        name    = contact.get("name", "Unknown")
        company = contact.get("company", "Unknown")
        mode    = contact.get("mode", "outreach")
        mode_tag = "[OUTREACH]" if mode == "outreach" else "[APPLIED] "

        # Idempotency: skip contacts already processed today
        last_emailed = _parse_date(contact.get("last_emailed"))
        if last_emailed == today:
            log.info(f"{mode_tag} {name} | {company} | skip | already processed today")
            skipped += 1
            continue

        action = decide_action(contact, today)

        if action == "skip":
            reason = _skip_reason(contact, today)
            log.info(f"{mode_tag} {name} | {company} | skip | {reason}")
            skipped += 1
            continue

        try:
            # Fetch stored thread info for follow-ups
            thread_message_id = None
            original_subject = None
            if action not in _FIRST_TOUCH_ACTIONS:
                thread_info = get_thread_info(contact["id"])
                thread_message_id = thread_info.get("message_id")
                original_subject = thread_info.get("original_subject")

            # Generate email
            subject, body = generate_email(contact, action, original_subject)

            # Create Gmail draft (with threading headers for follow-ups).
            # Returns None if a duplicate draft already exists for today.
            current_stage = contact.get("stage")
            if thread_message_id:
                message_id = create_draft(
                    contact["email"], subject, body,
                    in_reply_to=thread_message_id,
                    contact_id=contact["id"], stage=current_stage,
                )
            else:
                message_id = create_draft(
                    contact["email"], subject, body,
                    contact_id=contact["id"], stage=current_stage,
                )

            if message_id is None:
                log.info(f"{mode_tag} {name} | {company} | {action} | draft already exists, skipping")
                skipped += 1
                time.sleep(2)
                continue

            # Persist thread info after the first email so follow-ups can thread
            if action in _FIRST_TOUCH_ACTIONS and message_id:
                save_thread_info(contact["id"], message_id, subject)

            # Apply Gmail label to the draft (best-effort — never blocks)
            label = ACTION_LABEL.get(action)
            if label:
                try:
                    apply_label_to_latest_draft(label)
                except Exception as exc:
                    log.warning(f"{mode_tag} {name} | {company} | label warning: {exc}")

            # Update Supabase — conditional on current stage to guard against races
            next_stage = NEXT_STAGE[action]
            followup_days = FOLLOWUP_DAYS.get(action)
            next_template = NEXT_TEMPLATE.get(action)
            update_contact(
                contact["id"], next_stage, followup_days, next_template,
                expected_stage=current_stage,
            )

            extra = ""
            if mode == "applied":
                extra = f"| job: {contact.get('job_title', 'N/A')}"
            if followup_days:
                from datetime import timedelta
                fu_date = today + timedelta(days=followup_days)
                extra += f" | followup: {fu_date}"

            log.info(f"{mode_tag} {name} | {company} | {action} | DRAFTED {extra}")
            drafted += 1

            # Small pause between contacts to avoid rate limits
            time.sleep(2)

        except Exception as exc:
            log.error(f"{mode_tag} {name} | {company} | {action} | ERROR: {exc}")
            errors += 1

    elapsed = round(time.time() - start)
    log.info(f"DONE | {drafted} drafted | {skipped} skipped | {errors} errors | {elapsed}s")

    if errors > 0:
        sys.exit(1)


def _skip_reason(contact, today):
    reply = contact.get("reply_status", "no_reply")
    stage = contact.get("stage", "new")
    followup = _parse_date(contact.get("followup_date"))

    if reply in ("replied", "interested", "call_scheduled"):
        return f"replied ({reply})"
    if reply == "dead":
        return "marked dead"
    if stage == "closed":
        return "closed"
    if "drafted" in stage:
        return "draft pending — mark as sent in Supabase to continue"
    if followup and followup > today:
        return f"followup not due until {followup}"
    return "no action needed"


if __name__ == "__main__":
    run()
