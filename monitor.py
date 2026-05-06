"""
Reply Monitor — checks Gmail INBOX for replies from sent contacts.
Runs every 2 hours Mon-Fri via GitHub Actions.

Workflow:
  1. Fetch contacts where reply_status='no_reply' AND stage contains '_sent'
  2. For each contact, search INBOX for any email FROM their address
  3. If found: update reply_status='replied' in Supabase + label the email
  4. agent.py skips these contacts automatically next morning
"""

import imaplib
import sys
import logging

from config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD
from db import get_sent_contacts, update_reply_status
from gmail import create_gmail_label_if_not_exists

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

# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    contacts = get_sent_contacts()

    log.info(f"START | {len(contacts)} contacts to check")

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


if __name__ == "__main__":
    run()
