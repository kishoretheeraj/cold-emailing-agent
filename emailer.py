import json
import ssl
import urllib.request
from datetime import date

import certifi

_ssl_ctx = ssl.create_default_context(cafile=certifi.where())

from config import (
    ANTHROPIC_API_KEY, EMAIL_MODEL, SENDER_PROFILE,
    DARTMOUTH_KEYWORDS, DARTMOUTH_INSTRUCTION,
    TIER_INSTRUCTIONS, TEMPLATE_INSTRUCTIONS,
    OUTREACH_PROMPT, APPLIED_INTRO_PROMPT,
    APPLIED_FOLLOWUP_PROMPT, SUBJECT_PROMPT,
)

# ── Action → template name mapping ────────────────────────────────────────────
ACTION_TO_TEMPLATE = {
    "send_first_touch":      "cold_intro",
    "send_followup1":        "follow_up_1",
    "send_followup2":        "follow_up_2",
    "send_breakup":          "breakup",
    "send_applied_intro":    "applied_intro",
    "send_applied_followup": "applied_followup",
}

def _is_dartmouth(contact):
    if contact.get("dartmouth"):
        return True
    detail = (contact.get("detail") or "").lower()
    return any(kw in detail for kw in DARTMOUTH_KEYWORDS)

def _call_claude(prompt):
    payload = json.dumps({
        "model": EMAIL_MODEL,
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["content"][0]["text"].strip()

def generate_email(contact, action):
    """
    Generate email body + subject for a contact based on the action.
    Returns (subject, body) tuple.
    """
    mode = contact.get("mode", "outreach")
    dart = _is_dartmouth(contact)
    dart_instr = DARTMOUTH_INSTRUCTION if dart else ""

    if action in ("send_first_touch", "send_followup1",
                  "send_followup2", "send_breakup"):
        body = _generate_outreach(contact, action, dart_instr)
    elif action == "send_applied_intro":
        body = _generate_applied_intro(contact, dart_instr)
    elif action == "send_applied_followup":
        body = _generate_applied_followup(contact, dart_instr)
    else:
        raise ValueError(f"Unknown action: {action}")

    subject = _generate_subject(contact, mode, body)
    return subject, body

def _generate_outreach(contact, action, dart_instr):
    template = ACTION_TO_TEMPLATE[action]
    tier = str(contact.get("tier", 2))
    prompt = OUTREACH_PROMPT.format(
        profile=SENDER_PROFILE,
        name=contact.get("name", ""),
        company=contact.get("company", ""),
        role=contact.get("role", ""),
        detail=contact.get("detail", ""),
        tier=tier,
        tier_instruction=TIER_INSTRUCTIONS.get(int(tier), TIER_INSTRUCTIONS[2]),
        template=template,
        template_instruction=TEMPLATE_INSTRUCTIONS.get(template, ""),
        dartmouth_instruction=dart_instr,
    )
    return _call_claude(prompt)

def _generate_applied_intro(contact, dart_instr):
    applied = contact.get("applied_date") or str(date.today())
    prompt = APPLIED_INTRO_PROMPT.format(
        profile=SENDER_PROFILE,
        name=contact.get("name", ""),
        role=contact.get("role", ""),
        company=contact.get("company", ""),
        job_title=contact.get("job_title", "the role"),
        job_description=contact.get("job_description", ""),
        applied_date=applied,
        dartmouth_instruction=dart_instr,
    )
    return _call_claude(prompt)

def _generate_applied_followup(contact, dart_instr):
    prompt = APPLIED_FOLLOWUP_PROMPT.format(
        profile=SENDER_PROFILE,
        name=contact.get("name", ""),
        role=contact.get("role", ""),
        company=contact.get("company", ""),
        job_title=contact.get("job_title", "the role"),
        dartmouth_instruction=dart_instr,
    )
    return _call_claude(prompt)

def _generate_subject(contact, mode, body):
    prompt = SUBJECT_PROMPT.format(
        name=contact.get("name", ""),
        company=contact.get("company", ""),
        mode=mode,
        job_title=contact.get("job_title", ""),
        body=body[:500],
    )
    subject = _call_claude(prompt)
    return subject.strip().strip('"').strip("'")
