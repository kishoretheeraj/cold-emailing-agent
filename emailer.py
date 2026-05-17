import json
import logging
import ssl
import time
import urllib.error
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
    CRITIC_PROMPT_DEFAULT, CRITIC_PASS_THRESHOLD,
    RESEARCH_TIERS, RESEARCH_INJECTION_DEFAULT,
)

log = logging.getLogger(__name__)

# ── Action → template name mapping ────────────────────────────────────────────
_FIRST_TOUCH_ACTIONS = {"send_first_touch", "send_applied_intro"}

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

def _call_claude(prompt, model=None, max_tokens=1000):
    _model = model or EMAIL_MODEL
    payload = json.dumps({
        "model": _model,
        "max_tokens": max_tokens,
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
    # Retry transient 429/529/5xx and network errors — Anthropic 529 (overload)
    # sank a full run before; same backoff covers DNS/TLS/connection blips.
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            try:
                text = data["content"][0]["text"].strip()
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError(f"Claude response malformed: {exc}") from exc
            if not text:
                raise ValueError("Claude returned empty text")
            return text
        except urllib.error.HTTPError as exc:
            if attempt < 2 and (exc.code in (429, 529) or 500 <= exc.code < 600):
                time.sleep(2 ** (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            raise

def _normalize_body(text):
    paragraphs = text.split("\n\n")
    normalized = []
    for para in paragraphs:
        lines = [l for l in para.splitlines() if l.strip()]
        if not lines:
            continue
        if any(l.lstrip().startswith(("•", "-", "*")) for l in lines):
            normalized.append("\n".join(lines))
        else:
            normalized.append(" ".join(l.strip() for l in lines))
    return "\n\n".join(normalized)


def generate_email(contact, action, original_subject=None, prompts=None):
    """
    Generate email body + subject for a contact based on the action.
    Returns (subject, body) tuple.
    For follow-up actions, skips Claude subject generation and returns
    'Re: {original_subject}' so follow-ups stay in the same thread.
    If prompts dict is provided, its values override the config.py defaults.
    """
    _prompts = prompts or {}
    mode = contact.get("mode", "outreach")
    dart = _is_dartmouth(contact)
    dart_instr = DARTMOUTH_INSTRUCTION if dart else ""

    # ── Research injection ─────────────────────────────────────────────────────
    research_brief = ""
    is_first_touch = action in _FIRST_TOUCH_ACTIONS
    tier = contact.get("tier")
    is_research_tier = tier in RESEARCH_TIERS

    if is_first_touch and is_research_tier:
        try:
            import research
            sender_profile_text = _prompts.get("sender_profile", SENDER_PROFILE)
            research_brief = research.get_research_brief(
                contact,
                sender_profile_text,
                _prompts,
            )
        except Exception as exc:
            log.warning(
                f"[RESEARCH] | {contact.get('name')} | "
                f"{contact.get('company')} | "
                f"unexpected pipeline failure: {exc}"
            )
            research_brief = ""

    research_block = ""
    if research_brief:
        injection_template = _prompts.get(
            "research_injection",
            RESEARCH_INJECTION_DEFAULT,
        )
        try:
            research_block = injection_template.format(brief_text=research_brief)
        except Exception as exc:
            log.warning(
                f"[RESEARCH] | {contact.get('name')} | "
                f"{contact.get('company')} | "
                f"injection template format failed: {exc}"
            )
            research_block = ""

    log.info(
        f"[OUTREACH] | {contact.get('name')} | "
        f"{contact.get('company')} | tier={tier} | "
        f"has_brief={bool(research_brief)}"
    )

    if action in ("send_first_touch", "send_followup1",
                  "send_followup2", "send_breakup"):
        body = _generate_outreach(contact, action, dart_instr, _prompts,
                                  research_block=research_block)
    elif action == "send_applied_intro":
        body = _generate_applied_intro(contact, dart_instr, _prompts,
                                       research_block=research_block)
    elif action == "send_applied_followup":
        body = _generate_applied_followup(contact, dart_instr, _prompts)
    else:
        raise ValueError(f"Unknown action: {action}")

    body = _normalize_body(body)

    # ── Pre-flight checks (all actions) ───────────────────────────────────────
    import preflight
    from db import log_agent_event as _log_event
    _name = contact.get("name", "")
    _company = contact.get("company", "")
    _pf_failures = preflight.check(body, contact, _prompts)
    if _pf_failures:
        _pf_extra = "Fix these issues before rewriting: " + "; ".join(_pf_failures)
        try:
            if action in ("send_first_touch", "send_followup1",
                          "send_followup2", "send_breakup"):
                body = _normalize_body(_generate_outreach(
                    contact, action, dart_instr, _prompts,
                    extra_instruction=_pf_extra, research_block=research_block))
            elif action == "send_applied_intro":
                body = _normalize_body(_generate_applied_intro(
                    contact, dart_instr, _prompts,
                    extra_instruction=_pf_extra, research_block=research_block))
            else:
                body = _normalize_body(_generate_applied_followup(
                    contact, dart_instr, _prompts, extra_instruction=_pf_extra))
            _pf_failures = preflight.check(body, contact, _prompts)
        except Exception as exc:
            log.warning(f"[PREFLIGHT] | {_name} | {_company} | retry error: {exc}")
    if _pf_failures:
        _log_event("preflight", contact_id=contact.get("id"),
                   status="blocked_preflight", blocked_checks=_pf_failures)
        log.warning(f"[PREFLIGHT] | {_name} | {_company} | BLOCKED | {_pf_failures}")
        raise ValueError(f"pre-flight blocked: {'; '.join(_pf_failures)}")
    _log_event("preflight", contact_id=contact.get("id"), status="success")

    if action in _FIRST_TOUCH_ACTIONS:
        subject = _generate_subject(contact, mode, body, _prompts)
    else:
        subject = "Re: " + (original_subject or "")

    if action in _FIRST_TOUCH_ACTIONS and contact.get("tier") == 1:
        critic_prompt_text = _prompts.get("critic_prompt", CRITIC_PROMPT_DEFAULT)
        sender_profile_text = _prompts.get("sender_profile", SENDER_PROFILE)

        def regenerate(feedback):
            if action == "send_first_touch":
                new_body = _normalize_body(
                    _generate_outreach(contact, action, dart_instr, _prompts,
                                       extra_instruction=feedback,
                                       research_block=research_block)
                )
            else:
                new_body = _normalize_body(
                    _generate_applied_intro(contact, dart_instr, _prompts,
                                            extra_instruction=feedback,
                                            research_block=research_block)
                )
            new_subject = _generate_subject(contact, mode, new_body, _prompts)
            return new_subject, new_body

        subject, body = critique_and_revise(
            subject, body, contact, sender_profile_text,
            critic_prompt_text, regenerate
        )

    return subject, body

def _generate_outreach(contact, action, dart_instr, prompts, extra_instruction=None, research_block=""):
    template = ACTION_TO_TEMPLATE[action]
    tier = str(contact.get("tier", 2))
    profile = prompts.get("sender_profile", SENDER_PROFILE)
    tpl = prompts.get("outreach_prompt", OUTREACH_PROMPT)
    prompt = tpl.format(
        profile=profile,
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
    if research_block:
        prompt += research_block
    if extra_instruction is not None:
        prompt += f"\nREVISION INSTRUCTION:\n{extra_instruction}"
    return _call_claude(prompt)

def _generate_applied_intro(contact, dart_instr, prompts, extra_instruction=None, research_block=""):
    applied = contact.get("applied_date") or str(date.today())
    profile = prompts.get("sender_profile", SENDER_PROFILE)
    tpl = prompts.get("applied_intro_prompt", APPLIED_INTRO_PROMPT)
    prompt = tpl.format(
        profile=profile,
        name=contact.get("name", ""),
        role=contact.get("role", ""),
        company=contact.get("company", ""),
        job_title=contact.get("job_title", "the role"),
        job_description=contact.get("job_description", ""),
        applied_date=applied,
        dartmouth_instruction=dart_instr,
    )
    if research_block:
        prompt += research_block
    if extra_instruction is not None:
        prompt += f"\nREVISION INSTRUCTION:\n{extra_instruction}"
    return _call_claude(prompt)

def _generate_applied_followup(contact, dart_instr, prompts, extra_instruction=None):
    profile = prompts.get("sender_profile", SENDER_PROFILE)
    tpl = prompts.get("applied_followup_prompt", APPLIED_FOLLOWUP_PROMPT)
    prompt = tpl.format(
        profile=profile,
        name=contact.get("name", ""),
        role=contact.get("role", ""),
        company=contact.get("company", ""),
        job_title=contact.get("job_title", "the role"),
        dartmouth_instruction=dart_instr,
    )
    if extra_instruction is not None:
        prompt += f"\nREVISION INSTRUCTION:\n{extra_instruction}"
    return _call_claude(prompt)

def _generate_subject(contact, mode, body, prompts):
    tpl = prompts.get("subject_prompt", SUBJECT_PROMPT)
    prompt = tpl.format(
        name=contact.get("name", ""),
        company=contact.get("company", ""),
        mode=mode,
        job_title=contact.get("job_title", ""),
        body=body[:500],
    )
    subject = _call_claude(prompt)
    return subject.strip().strip('"').strip("'")

# ── Critic loop ────────────────────────────────────────────────────────────────

def _run_critic(subject, body, contact, sender_profile, critic_prompt_text):
    parts = []
    for label, key in [
        ("Name", "name"), ("Company", "company"), ("Role", "role"),
        ("Detail", "detail"),
    ]:
        val = contact.get(key)
        if val:
            parts.append(f"{label}: {val}")
    tier = contact.get("tier")
    if tier:
        parts.append(f"Tier: {tier}")
    if contact.get("dartmouth"):
        parts.append("Dartmouth: yes")
    contact_context = "\n".join(parts)

    _fallback = {"score": 7, "failed_criteria": [], "feedback": ""}

    try:
        formatted = critic_prompt_text.format(
            sender_profile=sender_profile,
            contact_context=contact_context,
            subject=subject,
            body=body,
        )
    except KeyError as exc:
        log.warning(f"[CRITIC] prompt format error: {exc}")
        return _fallback

    try:
        raw = _call_claude(formatted)
    except Exception as exc:
        log.warning(f"[CRITIC] _call_claude error: {exc}")
        return _fallback

    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        return json.loads(text)
    except Exception as exc:
        log.warning(f"[CRITIC] JSON parse error: {exc}")
        return _fallback


def critique_and_revise(subject, body, contact, sender_profile,
                        critic_prompt_text, regenerate_fn):
    """
    Runs the critic on (subject, body). If score >= CRITIC_PASS_THRESHOLD,
    returns (subject, body) unchanged.
    Otherwise calls regenerate_fn(feedback) once to regenerate
    with the critic's feedback included, and returns the
    regenerated (subject, body).

    regenerate_fn is a callable that takes a string feedback
    and returns (new_subject, new_body). It is the caller's
    responsibility to compose the original prompt with the
    feedback appended.

    Never raises. Always returns a (subject, body) tuple. If
    regenerate_fn raises, returns the original (subject, body)
    and logs a warning.
    """
    name = contact.get("name", "")
    company = contact.get("company", "")
    result = _run_critic(subject, body, contact, sender_profile, critic_prompt_text)
    retried = False

    if result.get("score", 7) >= CRITIC_PASS_THRESHOLD:
        log.info(
            f"[CRITIC] | {name} | {company} | "
            f"score={result['score']} | "
            f"failed={result['failed_criteria']} | "
            f"retried={retried}"
        )
        return subject, body

    try:
        subject, body = regenerate_fn(result.get("feedback", ""))
        retried = True
    except Exception as exc:
        log.warning(f"[CRITIC] regenerate error for {name} | {company}: {exc}")

    log.info(
        f"[CRITIC] | {name} | {company} | "
        f"score={result['score']} | "
        f"failed={result['failed_criteria']} | "
        f"retried={retried}"
    )
    return subject, body
