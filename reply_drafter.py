"""
Generates suggested reply drafts for contacts where classifier_status is
positive_reply or soft_yes. Called from monitor.run() after classification.
Never sends. Creates Gmail drafts only.
"""

import logging

from config import (
    ANTHROPIC_API_KEY, REPLY_RESPONSE_MODEL, SENDER_PROFILE,
    REPLY_RESPONSE_DEFAULT,
)
from db import log_agent_event, update_contact, insert_email_message
from emailer import _call_claude, _normalize_body
from gmail import create_draft, apply_label_to_latest_draft
import preflight

log = logging.getLogger(__name__)

REPLY_LABEL = "Cold Outreach/Reply"
DRAFTABLE_STATUSES = {"positive_reply", "soft_yes"}

# ── Body generation ────────────────────────────────────────────────────────────

def _generate_reply_body(contact, reply_body_text, prompts):
    profile = prompts.get("sender_profile", SENDER_PROFILE)
    tpl = prompts.get("reply_response_prompt", REPLY_RESPONSE_DEFAULT)
    prompt = tpl.format(
        profile=profile,
        name=contact.get("name", ""),
        company=contact.get("company", ""),
        role=contact.get("role", ""),
        reply_body=reply_body_text,
    )
    return _call_claude(prompt, model=REPLY_RESPONSE_MODEL, system=profile)


# ── Public interface ───────────────────────────────────────────────────────────

def draft_reply(contact, reply_body_text, prompts):
    """
    Generate a reply draft for a contact, run pre-flight, create Gmail draft,
    update stage to reply_drafted, apply label. Logs to agent_events.
    Never calls the critic loop. Best-effort: logs warning on any failure.
    """
    name = contact.get("name", "Unknown")
    company = contact.get("company", "Unknown")
    contact_id = contact.get("id")
    classifier_status = contact.get("classifier_status", "")

    if classifier_status not in DRAFTABLE_STATUSES:
        return

    if contact.get("stage") in ("reply_drafted", "reply_sent"):
        log.info(f"[REPLY-DRAFT] | {name} | {company} | skip: already in {contact.get('stage')}")
        return

    try:
        body = _normalize_body(_generate_reply_body(contact, reply_body_text, prompts))

        # Pre-flight: one retry on failure; no critic
        failures = preflight.check(body, contact, prompts)
        if failures:
            extra = "Fix these issues: " + "; ".join(failures)
            prompt_retry = (prompts.get("reply_response_prompt", REPLY_RESPONSE_DEFAULT)
                            + f"\nREVISION INSTRUCTION:\n{extra}")
            from config import SENDER_PROFILE as _SP
            profile = prompts.get("sender_profile", _SP)
            retry_prompt = prompts.get("reply_response_prompt", REPLY_RESPONSE_DEFAULT)
            retry_full = retry_prompt.format(
                profile=profile,
                name=contact.get("name", ""),
                company=contact.get("company", ""),
                role=contact.get("role", ""),
                reply_body=reply_body_text,
            ) + f"\nREVISION INSTRUCTION:\n{'; '.join(failures)}"
            body = _normalize_body(_call_claude(retry_full, model=REPLY_RESPONSE_MODEL, system=profile))
            failures = preflight.check(body, contact, prompts)

        if failures:
            log_agent_event("draft_reply", contact_id=contact_id,
                            contact_name=name, status="blocked_preflight",
                            metadata={"blocked_checks": failures})
            log.warning(f"[REPLY-DRAFT] | {name} | {company} | BLOCKED | {failures}")
            return

        # Subject: Re: original subject
        original_subject = contact.get("original_subject") or ""
        subject = ("Re: " + original_subject) if original_subject else "Re: your message"

        thread_message_id = contact.get("message_id")
        message_id, _thread_id = create_draft(
            contact.get("email"),
            subject,
            body,
            in_reply_to=thread_message_id,
            contact_id=contact_id,
            stage="reply_drafted",
        )

        if message_id is None:
            log.info(f"[REPLY-DRAFT] | {name} | {company} | duplicate draft, skipping")
            return

        # Store in email_messages
        from datetime import datetime, timezone
        insert_email_message(
            contact_id=contact_id,
            direction="outgoing",
            sent_at=datetime.now(timezone.utc).isoformat(),
            subject=subject,
            body=body,
            message_id=message_id,
            in_reply_to=thread_message_id,
            stage_at_send="reply_drafted",
        )

        # Gmail label (best-effort)
        try:
            apply_label_to_latest_draft(REPLY_LABEL)
        except Exception as exc:
            log.warning(f"[REPLY-DRAFT] | {name} | {company} | label warning: {exc}")

        update_contact(contact_id, "reply_drafted", clear_followup_date=True)

        log_agent_event("draft_reply", contact_id=contact_id, contact_name=name, status="success")
        log.info(f"[REPLY-DRAFT] | {name} | {company} | DRAFTED | classifier_status={classifier_status}")

    except Exception as exc:
        log_agent_event("draft_reply", contact_id=contact_id, contact_name=name,
                        status="failed", error_message=str(exc))
        log.warning(f"[REPLY-DRAFT] | {name} | {company} | error: {exc}")
