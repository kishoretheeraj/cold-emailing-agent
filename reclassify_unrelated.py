"""
One-shot script: fix all misclassified or unclassified replies.

Three passes:
  1. Update the Supabase reply_classification_prompt to the improved version.
  2. Re-classify contacts with classifier_status='unrelated' using their
     stored reply body in email_messages (catches LLM false-positives).
  3. Run detect_replies() for contacts with null classifier_status so any
     reply that was missed (e.g. due to the old empty-prompt bug) gets
     picked up and classified with the improved prompt.

Run: python3 reclassify_unrelated.py
"""

import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

from config import REPLY_CLASSIFICATION_DEFAULT, REPLY_CLASSIFICATION_MODEL
from db import get_client, update_classifier_status, load_prompts, _retry
from emailer import _call_claude


# ── Helpers ────────────────────────────────────────────────────────────────────

def _update_supabase_prompt():
    result = _retry(lambda: (
        get_client()
        .table("prompts")
        .update({"value": REPLY_CLASSIFICATION_DEFAULT})
        .eq("key", "reply_classification_prompt")
        .execute()
    ))
    updated = result.data or []
    if updated:
        log.info(f"Supabase: reply_classification_prompt updated ({len(updated)} row)")
    else:
        log.warning("Supabase: reply_classification_prompt row not found — check prompts table")


def _get_contacts_by_classifier_status(status):
    """Fetch non-deleted contacts with a specific classifier_status (or null)."""
    q = get_client().table("contacts").select(
        "id, name, company, stage, classifier_status, message_id"
    ).is_("deleted_at", "null")
    if status is None:
        q = q.is_("classifier_status", "null").like("stage", "%_sent%")
    else:
        q = q.eq("classifier_status", status)
    return (_retry(lambda: q.execute()).data) or []


def _get_latest_incoming_body(contact_id):
    result = _retry(lambda: (
        get_client()
        .table("email_messages")
        .select("body")
        .eq("contact_id", contact_id)
        .eq("direction", "incoming")
        .order("sent_at", desc=True)
        .limit(1)
        .execute()
    ))
    rows = result.data or []
    return (rows[0].get("body") or "").strip() if rows else ""


def _classify(body_text, prompt_template, contact_id=None):
    try:
        prompt = prompt_template.format(reply_body=body_text[:1500])
        raw = _call_claude(prompt, model=REPLY_CLASSIFICATION_MODEL, max_tokens=100,
                            module="reclassify_unrelated", action="reply_classification", contact_id=contact_id)
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1].lstrip("json").strip()
        data = json.loads(text)
        return data.get("classifier_status")
    except Exception as exc:
        log.warning(f"classification error: {exc}")
        return None


# ── Pass 1: update Supabase prompt ────────────────────────────────────────────

def pass1_update_prompt():
    log.info("Pass 1: updating Supabase prompt")
    _update_supabase_prompt()


# ── Pass 2: re-classify 'unrelated' contacts using stored body ────────────────

def pass2_reclassify_unrelated():
    tpl = REPLY_CLASSIFICATION_DEFAULT
    contacts = _get_contacts_by_classifier_status("unrelated")
    log.info(f"Pass 2: {len(contacts)} contact(s) with classifier_status='unrelated'")

    changed = 0
    for c in contacts:
        cid, name, company = c["id"], c.get("name", "?"), c.get("company", "?")
        body = _get_latest_incoming_body(cid)
        if not body:
            log.warning(f"  SKIP | {name} | {company} | no stored reply body")
            continue
        new_status = _classify(body, tpl, contact_id=cid)
        if new_status is None:
            log.warning(f"  SKIP | {name} | {company} | classification failed")
            continue
        update_classifier_status(cid, new_status)
        log.info(f"  {'UPDATED' if new_status != 'unrelated' else 'UNCHANGED'} | {name} | {company} | unrelated → {new_status}")
        if new_status != "unrelated":
            changed += 1

    log.info(f"Pass 2 done: {changed} reclassified")
    return changed


# ── Pass 3: run detect_replies for null-status contacts ───────────────────────

def pass3_detect_null_status():
    """
    Re-run reply detection for contacts with null classifier_status.
    These are contacts the monitor may have missed or where classification
    failed (e.g. the old empty-prompt bug). detect_replies() skips contacts
    where classifier_status is not None, so this targets exactly the right set.
    """
    log.info("Pass 3: running detect_replies() for null-classifier_status contacts")
    # Import here to keep logging setup before project imports
    import monitor
    newly = monitor.detect_replies(prompts=load_prompts())
    log.info(f"Pass 3 done: {len(newly)} contact(s) newly classified")
    return len(newly)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    log.info("START | reclassify_unrelated")
    pass1_update_prompt()
    pass2_reclassify_unrelated()
    pass3_detect_null_status()
    log.info("DONE")


if __name__ == "__main__":
    main()
