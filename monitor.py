"""
Reply Monitor — checks Gmail INBOX for replies from sent contacts.
Also detects when you send a draft from Gmail and flips the contact's stage
from *_drafted to *_sent automatically, with follow-up dates set using the
agent's normal cadence.
Runs every 2 hours Mon-Fri via GitHub Actions.

Workflow:
  1. Detect sent drafts: check Sent Mail for contacts in *_drafted stage.
  2. Fetch contacts where reply_status='no_reply' AND stage contains '_sent'
  3. For each contact, search INBOX for any email FROM their address
  4. If found: update reply_status='replied' in Supabase + label the email
  5. agent.py skips these contacts automatically next morning
"""

import imaplib
import sys
import logging
from datetime import date, timedelta

from agent import DRAFTED_TO_SENT, NEXT_STAGE, _parse_date
from config import FOLLOWUP_DAYS, GMAIL_ADDRESS, GMAIL_APP_PASSWORD
from constants import TERMINAL_DRAFTED_STAGES
from db import get_drafted_contacts, get_sent_contacts, update_contact, update_reply_status
from gmail import create_gmail_label_if_not_exists, find_sent_for_thread

# ── Logging setup ──────────────────────────────────────────────────────────────
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

REPLIED_LABEL = "Cold Outreach/Replied"

# ── Sent-draft detection ──────────────────────────────────────────────────────

def detect_sent_drafts():
    """
    For every contact in a *_drafted stage with a non-null
    message_id, check Sent Mail for evidence the user sent it.
    If found, advance the stage to *_sent and set followup_date
    using the same cadence the agent uses for that transition.
    Best-effort: failures log and continue.
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

        try:
            found = find_sent_for_thread(message_id, since_date, mode)
        except Exception as exc:
            log.warning(f"[SENT-CHECK] | {name} | {company} | unexpected error: {exc}")
            continue

        if not found:
            continue

        new_stage = DRAFTED_TO_SENT.get(stage)
        if new_stage is None:
            log.warning(f"[SENT-CHECK] | {name} | {company} | unknown stage: {stage}")
            continue

        # Look up follow-up days from FOLLOWUP_DAYS via the inverse of NEXT_STAGE.
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
        flipped += 1

    log.info(f"DONE | sent-detection | checked={checked} flipped={flipped}")


# ── Reply detection ────────────────────────────────────────────────────────────

def detect_replies():
    """
    Search Gmail INBOX for replies from contacts in *_sent stages.
    Updates reply_status to 'replied' and labels matching emails.
    """
    contacts = get_sent_contacts()

    log.info(f"START | reply-detection | {len(contacts)} contacts to check")

    if not contacts:
        log.info("DONE | 0 replies found | 0 contacts checked")
        print("\nSummary: 0 replies found, 0 contacts checked")
        return

    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        imap.select("INBOX")
        create_gmail_label_if_not_exists(imap, REPLIED_LABEL)

        replies = 0

        for contact in contacts:
            name    = contact.get("name", "Unknown")
            company = contact.get("company", "Unknown")
            email   = contact.get("email", "")

            status, data = imap.search(None, f'FROM "{email}"')

            if status == "OK" and data[0]:
                msg_nums = data[0].split()
                if msg_nums:
                    update_reply_status(contact["id"], "replied")

                    # Label every reply message so they're visible under
                    # Cold Outreach/Replied inside Gmail
                    for num in msg_nums:
                        try:
                            imap.copy(num.decode(), f'"{REPLIED_LABEL}"')
                        except Exception as exc:
                            log.warning(f"{name} | {company} | label warning: {exc}")

                    log.info(f"{name} | {company} | reply DETECTED")
                    replies += 1
                    continue

            log.info(f"{name} | {company} | no reply")

    finally:
        imap.logout()

    log.info(f"DONE | {replies} replies found | {len(contacts)} contacts checked")
    print(f"\nSummary: {replies} replies found, {len(contacts)} contacts checked")


# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    log.info("START | monitor run")

    try:
        detect_sent_drafts()
    except Exception as exc:
        log.warning(f"sent-detection | unexpected failure: {exc}")

    detect_replies()


if __name__ == "__main__":
    run()
