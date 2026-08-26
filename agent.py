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
from datetime import date, datetime, timezone

import anthropic

from config import ANTHROPIC_API_KEY, BATCH_POLL_INTERVAL, EMAIL_MODEL, FOLLOWUP_DAYS
from constants import TERMINAL_REPLY_STATUSES
from db import get_all_contacts, update_contact, close_contact, save_thread_info, get_thread_info, load_prompts, get_pause_scope, record_run, insert_email_message, log_drafted_email, update_message_id, update_latest_message_id
from emailer import generate_email, prepare_email, finalize_email, hash_prompt_set
from gmail import create_draft, apply_label_to_latest_draft, find_sent_by_thread_id, find_sent_by_subject

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
    if mode == "networking":
        return _decide_networking(contact, today)
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


def _decide_networking(contact, today):
    stage = contact.get("stage", "new")
    followup = _parse_date(contact.get("followup_date"))

    if stage == "new":
        return "send_networking_first_touch"
    if stage in ("networking_drafted", "networking_followup_drafted"):
        return "skip"
    if stage == "networking_followup_sent":
        return "skip"
    if followup and followup <= today:
        if stage == "networking_sent":
            return "send_networking_followup"
    return "skip"


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


# Actions that open a new thread — first email in a sequence.
_FIRST_TOUCH_ACTIONS = {"send_first_touch", "send_applied_intro", "send_networking_first_touch"}

_MODE_TAGS = {
    "outreach":   "[OUTREACH]",
    "applied":    "[APPLIED]",
    "networking": "[NETWORKING]",
}


def _resolve_thread_message_id(contact, thread_message_id):
    """
    Gmail rewrites the Message-ID when a draft is sent, making the stored
    draft ID stale. Search Sent Mail to find the actual sent Message-ID so
    In-Reply-To threads correctly. Falls back to thread_message_id on failure.
    """
    from datetime import timedelta

    since_date = _parse_date(contact.get("last_emailed")) or (date.today() - timedelta(days=90))

    if contact.get("gmail_thread_id"):
        try:
            actual = find_sent_by_thread_id(contact["gmail_thread_id"], since_date)
            if actual:
                return actual
        except Exception as exc:
            log.warning(f"[THREAD-RESOLVE] | {contact.get('name')} | thrid lookup failed: {exc}")

    original_subject = contact.get("original_subject", "")
    if original_subject and contact.get("email"):
        try:
            actual = find_sent_by_subject(original_subject, since_date, contact["email"])
            if actual:
                return actual
        except Exception as exc:
            log.warning(f"[THREAD-RESOLVE] | {contact.get('name')} | subject lookup failed: {exc}")

    return thread_message_id


# ── Stage transitions ──────────────────────────────────────────────────────────

NEXT_STAGE = {
    "send_first_touch":            "first_touch_drafted",
    "send_followup1":              "followup1_drafted",
    "send_followup2":              "followup2_drafted",
    "send_breakup":                "breakup_drafted",
    "send_applied_intro":          "applied_intro_drafted",
    "send_applied_followup":       "applied_followup_drafted",
    "send_networking_first_touch": "networking_drafted",
    "send_networking_followup":    "networking_followup_drafted",
}

DRAFTED_TO_SENT = {
    "first_touch_drafted":        "first_touch_sent",
    "followup1_drafted":          "followup1_sent",
    "followup2_drafted":          "followup2_sent",
    "breakup_drafted":            "breakup_sent",
    "applied_intro_drafted":      "applied_intro_sent",
    "applied_followup_drafted":   "applied_followup_sent",
    "networking_drafted":         "networking_sent",
    "networking_followup_drafted": "networking_followup_sent",
    "reply_drafted":              "reply_sent",
}

NEXT_TEMPLATE = {
    "send_first_touch":            "cold_intro",
    "send_followup1":              "follow_up_1",
    "send_followup2":              "follow_up_2",
    "send_breakup":                "breakup",
    "send_applied_intro":          "applied_intro",
    "send_applied_followup":       "applied_followup",
    "send_networking_first_touch": "networking_intro",
    "send_networking_followup":    "networking_followup",
}

ACTION_LABEL = {
    "send_first_touch":            "Cold Outreach/First Touch",
    "send_followup1":              "Cold Outreach/Follow-up #1",
    "send_followup2":              "Cold Outreach/Follow-up #2",
    "send_breakup":                "Cold Outreach/Break-up",
    "send_applied_intro":          "Cold Outreach/Applied Intro",
    "send_applied_followup":       "Cold Outreach/Applied Follow-up",
    "send_networking_first_touch": "Networking/Intro",
    "send_networking_followup":    "Networking/Follow-up",
}

# ── Prompt validation ──────────────────────────────────────────────────────────

# Exact kwargs each prompt's .format() call receives (mirrors emailer.py / research.py).
_PROMPT_VALID_KEYS = {
    "outreach_prompt":           {"profile","name","company","role","detail","tier","tier_instruction","template","template_instruction","dartmouth_instruction"},
    "applied_intro_prompt":      {"profile","name","role","company","job_title","job_description","applied_date","dartmouth_instruction"},
    "applied_followup_prompt":   {"profile","name","role","company","job_title","dartmouth_instruction"},
    "networking_prompt":         {"profile","name","company","connection_context_instruction","dartmouth_instruction"},
    "networking_followup_prompt": {"profile","name","company","dartmouth_instruction"},
    "networking_subject_prompt": {"name","company","body"},
    "subject_prompt":            {"name","company","mode","job_title","body"},
    "critic_prompt":             {"sender_profile","contact_context","subject","body"},
    "research_injection":        {"brief_text"},
    "research_query_prompt":     {"sender_profile","name","company","role","detail","notes","dartmouth","tier"},
    "research_curate_prompt":    {"name","company","role","detail","raw_results"},
    "reply_response_prompt":     {"profile","name","company","role","reply_body"},
    "reply_classification_prompt": {"reply_body"},
}

def _validate_prompts(prompts):
    """Return list of error strings for any prompt that has an unknown {placeholder}."""
    import re
    problems = []
    for key, valid in _PROMPT_VALID_KEYS.items():
        value = prompts.get(key)
        if not value:
            continue
        placeholders = re.findall(r'(?<!\{)\{([^{}]+)\}(?!\})', value)
        unknown = [p for p in placeholders if p not in valid]
        if unknown:
            problems.append(f"{key}: unknown placeholder(s) {unknown}")
    return problems


# Keys the code reads from each JSON-output prompt's Claude response.
# If a key disappears from the prompt text it means the schema likely changed.
_PROMPT_OUTPUT_KEYS = {
    "critic_prompt": ["rewrite_required", "verdict", "killed_by", "failed_soft_criteria"],
    "reply_classification_prompt": ["classifier_status"],
}

def _validate_prompt_output_schemas(prompts):
    """Return list of error strings if a JSON-output prompt no longer mentions expected keys."""
    problems = []
    for key, required_keys in _PROMPT_OUTPUT_KEYS.items():
        value = prompts.get(key, "")
        if not value:
            continue  # empty → using config.py fallback, which is known-good
        missing = [k for k in required_keys if k not in value]
        if missing:
            problems.append(
                f"{key}: output schema may have changed — keys no longer in prompt: {missing}"
            )
    return problems


# ── Draft execution helper ─────────────────────────────────────────────────────

def _execute_draft(contact, action, subject, body, thread_message_id,
                   mode_tag, today, prompts):
    """
    Create Gmail draft, persist thread info, store in email_messages, apply
    label, update Supabase. Returns True if a new draft was created, False if
    a duplicate was detected. Raises on hard errors so callers can count them.
    """
    name    = contact.get("name", "Unknown")
    company = contact.get("company", "Unknown")
    mode    = contact.get("mode", "outreach")
    current_stage = contact.get("stage")

    # For follow-ups, verify stored message_id is the actual sent ID.
    # Gmail rewrites Message-IDs on send; resolve the real ID so In-Reply-To
    # points to a message that exists in Sent Mail and Gmail can thread it.
    if action not in _FIRST_TOUCH_ACTIONS and thread_message_id:
        resolved = _resolve_thread_message_id(contact, thread_message_id)
        if resolved and resolved != thread_message_id:
            log.info(f"{mode_tag} {name} | {company} | thread_message_id resolved for threading")
            try:
                update_message_id(contact["id"], resolved)
            except Exception as exc:
                log.warning(f"{mode_tag} {name} | {company} | message_id update failed: {exc}")
            thread_message_id = resolved

    if thread_message_id:
        result = create_draft(
            contact["email"], subject, body,
            in_reply_to=thread_message_id,
            contact_id=contact["id"], stage=current_stage,
        )
    else:
        result = create_draft(
            contact["email"], subject, body,
            contact_id=contact["id"], stage=current_stage,
        )

    message_id = result.message_id
    gmail_draft_id = result.gmail_draft_id
    thread_id = result.gmail_thread_id

    if message_id is None:
        log.info(f"{mode_tag} {name} | {company} | {action} | draft already exists, skipping")
        return False

    if action in _FIRST_TOUCH_ACTIONS and message_id:
        save_thread_info(contact["id"], message_id, subject, gmail_thread_id=thread_id)

    insert_email_message(
        contact_id=contact["id"],
        direction="outgoing",
        sent_at=datetime.now(timezone.utc).isoformat(),
        subject=subject,
        body=body,
        message_id=message_id,
        in_reply_to=thread_message_id,
        stage_at_send=current_stage,
    )

    # Decision-context tagging: which live prompt set produced this draft.
    # Wrapped because a fingerprint bug must never cost a draft_history row.
    try:
        decision_context = {"prompt_hash": hash_prompt_set(prompts)}
    except Exception as exc:
        decision_context = None
        log.warning(f"{mode_tag} {name} | {company} | decision_context skipped: {exc}")

    log_drafted_email(
        contact["id"], current_stage, subject, body,
        message_id=message_id, gmail_draft_id=gmail_draft_id,
        decision_context=decision_context,
    )

    label = ACTION_LABEL.get(action)
    if label:
        try:
            apply_label_to_latest_draft(label, gmail_draft_id=gmail_draft_id, message_id=message_id)
        except Exception as exc:
            log.warning(f"{mode_tag} {name} | {company} | label warning: {exc}")

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
    return True


# ── Main loop ──────────────────────────────────────────────────────────────────

def run():
    today = date.today()
    start = time.time()

    scope = get_pause_scope()
    if scope in ("agent", "all"):
        log.info("PAUSED | agent is paused — exiting without drafting")
        return

    # Load live prompts and sender profile from Supabase; fall back to config.py defaults.
    try:
        prompts = load_prompts()
        log.info(f"Loaded prompts from Supabase ({len(prompts)} keys)")
    except Exception as exc:
        prompts = {}
        log.warning(f"Using default prompts from config.py (load failed: {exc})")

    # Abort before any API calls if a prompt has an unknown placeholder.
    prompt_errors = _validate_prompts(prompts)
    if prompt_errors:
        for err in prompt_errors:
            log.error(f"[PROMPT-VALIDATION] {err}")
        raise ValueError(f"Prompt validation failed: {prompt_errors}")

    # Abort if a JSON-output prompt's expected keys are no longer present in its text.
    schema_errors = _validate_prompt_output_schemas(prompts)
    if schema_errors:
        for err in schema_errors:
            log.error(f"[OUTPUT-SCHEMA] {err}")
        raise ValueError(f"Output schema validation failed: {schema_errors}")

    contacts = get_all_contacts()
    outreach_count   = sum(1 for c in contacts if c.get("mode", "outreach") == "outreach")
    applied_count    = sum(1 for c in contacts if c.get("mode") == "applied")
    networking_count = sum(1 for c in contacts if c.get("mode") == "networking")

    log.info(
        f"START | {len(contacts)} contacts | {outreach_count} outreach | "
        f"{applied_count} applied | {networking_count} networking"
    )

    drafted = 0
    skipped = 0
    errors  = 0

    # ── Phase 1: Collect batch requests ───────────────────────────────────────
    # (contact, action, thread_message_id, original_subject, custom_id, ctx)
    batch_items = []
    batch_requests = []

    for contact in contacts:
        name    = contact.get("name", "Unknown")
        company = contact.get("company", "Unknown")
        mode    = contact.get("mode", "outreach")
        mode_tag = _MODE_TAGS.get(mode, "[OUTREACH]")

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

        thread_message_id = None
        original_subject = None
        if action not in _FIRST_TOUCH_ACTIONS:
            thread_info = get_thread_info(contact["id"])
            # Prefer latest_message_id (most recently sent email) for sequential
            # In-Reply-To chaining; fall back to first-touch message_id.
            thread_message_id = thread_info.get("latest_message_id") or thread_info.get("message_id")
            original_subject = thread_info.get("original_subject")

        try:
            user_prompt, system, ctx = prepare_email(contact, action, prompts=prompts)
        except Exception as exc:
            log.error(f"{mode_tag} {name} | {company} | {action} | prepare error: {exc}")
            errors += 1
            continue

        custom_id = f"{contact['id']}-{action}"
        batch_requests.append({
            "custom_id": custom_id,
            "params": {
                "model": EMAIL_MODEL,
                "max_tokens": 1000,
                "system": [{"type": "text", "text": system,
                             "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user_prompt}],
            },
        })
        batch_items.append((contact, action, thread_message_id, original_subject,
                             custom_id, ctx, mode_tag))

    if not batch_requests:
        elapsed = round(time.time() - start)
        log.info(f"DONE | {drafted} drafted | {skipped} skipped | {errors} errors | {elapsed}s")
        try:
            record_run("success", drafted, skipped, errors, elapsed)
        except Exception as exc:
            log.warning(f"Failed to record run metadata: {exc}")
        return

    # ── Phases 2–4: Submit batch, poll, collect results ───────────────────────
    retry_items = []  # contacts to re-attempt sequentially

    try:
        _batch_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        batch = _batch_client.messages.batches.create(requests=batch_requests)
        log.info(f"BATCH | submitted {len(batch_requests)} requests | batch_id={batch.id}")

        while batch.processing_status == "in_progress":
            time.sleep(BATCH_POLL_INTERVAL)
            batch = _batch_client.messages.batches.retrieve(batch.id)
            log.info(f"BATCH | {batch.processing_status} | {batch.request_counts}")

        log.info(f"BATCH | complete | {batch.request_counts}")

        results_map = {r.custom_id: r
                       for r in _batch_client.messages.batches.results(batch.id)}

        for contact, action, thread_message_id, original_subject, custom_id, ctx, mode_tag in batch_items:
            name    = contact.get("name", "Unknown")
            company = contact.get("company", "Unknown")

            result = results_map.get(custom_id)
            if result is None or result.result.type != "succeeded":
                log.warning(
                    f"{mode_tag} {name} | {company} | {action} | "
                    f"batch errored — queued for sequential retry"
                )
                retry_items.append(
                    (contact, action, thread_message_id, original_subject, mode_tag)
                )
                continue

            try:
                raw_body = result.result.message.content[0].text
                subject, body = finalize_email(
                    contact, action, raw_body, original_subject, prompts, **ctx
                )
                created = _execute_draft(
                    contact, action, subject, body, thread_message_id, mode_tag, today, prompts
                )
                if created:
                    drafted += 1
                else:
                    skipped += 1
            except Exception as exc:
                log.error(f"{mode_tag} {name} | {company} | {action} | ERROR: {exc}")
                errors += 1

    except Exception as exc:
        # Batch submission or polling failed — retry all contacts sequentially
        log.warning(
            f"BATCH | failed ({exc}) — falling back to sequential "
            f"for all {len(batch_items)} contacts"
        )
        retry_items = [
            (c, a, tmid, orig_subj, mtag)
            for c, a, tmid, orig_subj, _, _, mtag in batch_items
        ]

    # ── Phase 5: Sequential retry (partial failures + catastrophic fallback) ──
    for contact, action, thread_message_id, original_subject, mode_tag in retry_items:
        name    = contact.get("name", "Unknown")
        company = contact.get("company", "Unknown")
        try:
            subject, body = generate_email(
                contact, action, original_subject, prompts=prompts
            )
            created = _execute_draft(
                contact, action, subject, body, thread_message_id, mode_tag, today, prompts
            )
            if created:
                drafted += 1
            else:
                skipped += 1
        except Exception as exc:
            log.error(f"{mode_tag} {name} | {company} | {action} | retry ERROR: {exc}")
            errors += 1

    elapsed = round(time.time() - start)
    log.info(f"DONE | {drafted} drafted | {skipped} skipped | {errors} errors | {elapsed}s")

    status = "failure" if errors > 0 else "success"
    failure_reason = f"{errors} contact error(s)" if errors > 0 else None
    try:
        record_run(status, drafted, skipped, errors, elapsed, failure_reason)
    except Exception as exc:
        log.warning(f"Failed to record run metadata: {exc}")

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
    try:
        run()
    except SystemExit:
        raise
    except Exception as exc:
        try:
            record_run("failure", 0, 0, 0, 0, str(exc))
        except Exception:
            pass
        raise
