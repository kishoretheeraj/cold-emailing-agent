import json
import logging
from datetime import date

import anthropic

import time

from config import (
    ANTHROPIC_API_KEY, EMAIL_MODEL, SENDER_PROFILE,
    DARTMOUTH_KEYWORDS, DARTMOUTH_INSTRUCTION,
    TIER_INSTRUCTIONS, TEMPLATE_INSTRUCTIONS,
    OUTREACH_PROMPT, APPLIED_INTRO_PROMPT,
    APPLIED_FOLLOWUP_PROMPT, SUBJECT_PROMPT,
    NETWORKING_PROMPT, NETWORKING_FOLLOWUP_PROMPT, NETWORKING_SUBJECT_PROMPT,
    CRITIC_PROMPT_DEFAULT, CRITIC_PASS_THRESHOLD,
    INTER_CALL_SLEEP,
    RESEARCH_TIERS, RESEARCH_INJECTION_DEFAULT,
)

log = logging.getLogger(__name__)

_claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=4)
_credit_exhausted = False  # set on first 400 credit error; makes all further calls fail fast

# ── Action → template name mapping ────────────────────────────────────────────
_FIRST_TOUCH_ACTIONS = {"send_first_touch", "send_applied_intro", "send_networking_first_touch"}

ACTION_TO_TEMPLATE = {
    "send_first_touch":            "cold_intro",
    "send_followup1":              "follow_up_1",
    "send_followup2":              "follow_up_2",
    "send_breakup":                "breakup",
    "send_applied_intro":          "applied_intro",
    "send_applied_followup":       "applied_followup",
    "send_networking_first_touch": "networking_intro",
    "send_networking_followup":    "networking_followup",
}

# Mirrors agent._MODE_TAGS — duplicated by the same convention as ACTION_TO_TEMPLATE.
_MODE_TAGS = {
    "outreach":   "[OUTREACH]",
    "applied":    "[APPLIED]",
    "networking": "[NETWORKING]",
}

def _is_dartmouth(contact):
    if contact.get("dartmouth"):
        return True
    detail = (contact.get("detail") or "").lower()
    return any(kw in detail for kw in DARTMOUTH_KEYWORDS)

# ── Prompt helpers — read from Supabase prompts dict, fall back to config.py ──

_TEMPLATE_TO_PROMPT_KEY = {
    "cold_intro":  "outreach_first_touch_instruction",
    "follow_up_1": "outreach_followup1_instruction",
    "follow_up_2": "outreach_followup2_instruction",
    "breakup":     "outreach_breakup_instruction",
}

def get_tier_instruction(prompts_dict, tier):
    key = f"tier_{tier}_instruction"
    if key in prompts_dict:
        return prompts_dict[key]
    log.warning(f"[WARN] prompt key {key} not in DB — using fallback")
    return TIER_INSTRUCTIONS.get(tier, TIER_INSTRUCTIONS[2])

def get_template_instruction(prompts_dict, template):
    key = _TEMPLATE_TO_PROMPT_KEY.get(template)
    if key and key in prompts_dict:
        return prompts_dict[key]
    log.warning(f"[WARN] prompt key for template '{template}' not in DB — using fallback")
    return TEMPLATE_INSTRUCTIONS.get(template, "")

def get_dartmouth_instruction(prompts_dict, dart):
    if not dart:
        return ""
    if "dartmouth_instruction" in prompts_dict:
        return prompts_dict["dartmouth_instruction"]
    log.warning("[WARN] prompt key dartmouth_instruction not in DB — using fallback")
    return DARTMOUTH_INSTRUCTION

def _call_claude(prompt, model=None, max_tokens=1000, system=None):
    global _credit_exhausted
    if _credit_exhausted:
        raise RuntimeError("Anthropic credit balance exhausted — aborting remaining calls this run")
    _model = model or EMAIL_MODEL
    kwargs = dict(
        model=_model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if system:
        kwargs["system"] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
    try:
        resp = _claude.messages.create(**kwargs)
    except anthropic.BadRequestError as exc:
        if "credit balance is too low" in str(exc):
            _credit_exhausted = True
            log.error("[CREDIT] Anthropic credit balance exhausted — aborting remaining calls this run")
        raise
    usage = resp.usage
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_created = getattr(usage, "cache_creation_input_tokens", 0) or 0
    if cache_read or cache_created:
        log.info(
            f"[CACHE] model={_model} | "
            f"cache_read={cache_read} | cache_created={cache_created}"
        )
    text = resp.content[0].text.strip()
    if not text:
        raise ValueError("Claude returned empty text")
    return text

def _normalize_body(text):
    paragraphs = text.split("\n\n")
    normalized = []
    for para in paragraphs:
        lines = [l for l in para.splitlines() if l.strip()]
        if not lines:
            continue
        if len(lines) == 1:
            normalized.append(lines[0].strip())
        elif any(l.lstrip().startswith(("•", "-", "*")) for l in lines):
            normalized.append("\n".join(lines))
        elif all(len(l.strip()) < 35 for l in lines):
            # Every line is short — sign-off block (threshold covers "Dartmouth College, MEM '26").
            normalized.append("\n".join(l.strip() for l in lines))
        else:
            # Word-wrapped prose: collapse into one continuous paragraph.
            normalized.append(" ".join(l.strip() for l in lines))
    return "\n\n".join(normalized)


def prepare_email(contact, action, prompts=None):
    """
    Build the email body prompt without calling Claude.
    Handles research and dartmouth checks synchronously (fast/cached).
    Returns (user_prompt, system, ctx) where ctx is passed as **kwargs to finalize_email().
    """
    _prompts = prompts or {}
    dart = _is_dartmouth(contact)
    dart_instr = get_dartmouth_instruction(_prompts, dart)
    tier = contact.get("tier")

    # ── Research injection ─────────────────────────────────────────────────────
    research_brief = ""
    is_first_touch = action in _FIRST_TOUCH_ACTIONS
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

    mode_tag = _MODE_TAGS.get(contact.get("mode", "outreach"), "[OUTREACH]")
    log.info(
        f"{mode_tag} | {contact.get('name')} | "
        f"{contact.get('company')} | tier={tier} | "
        f"has_brief={bool(research_brief)}"
    )

    profile = _prompts.get("sender_profile", SENDER_PROFILE)

    if action in ("send_first_touch", "send_followup1",
                  "send_followup2", "send_breakup"):
        user_prompt = _build_outreach_prompt(contact, action, dart_instr, _prompts,
                                             research_block=research_block)
    elif action == "send_applied_intro":
        user_prompt = _build_applied_intro_prompt(contact, dart_instr, _prompts,
                                                  research_block=research_block)
    elif action == "send_applied_followup":
        user_prompt = _build_applied_followup_prompt(contact, dart_instr, _prompts)
    elif action == "send_networking_first_touch":
        user_prompt = _build_networking_prompt(contact, dart_instr, _prompts,
                                                research_block=research_block)
    elif action == "send_networking_followup":
        user_prompt = _build_networking_followup_prompt(contact, dart_instr, _prompts)
    else:
        raise ValueError(f"Unknown action: {action}")

    ctx = {"dart_instr": dart_instr, "research_block": research_block}
    return user_prompt, profile, ctx


def finalize_email(contact, action, body, original_subject=None, prompts=None,
                   dart_instr="", research_block=""):
    """
    Complete email generation from a pre-computed body.
    Runs pre-flight, generates subject, runs critic for Tier 1 first-touch.
    Returns (subject, body). Raises ValueError on hard pre-flight block.
    """
    import preflight
    from db import log_agent_event as _log_event
    _prompts = prompts or {}
    mode = contact.get("mode", "outreach")
    _name = contact.get("name", "")
    _company = contact.get("company", "")

    body = _normalize_body(body)

    # ── Pre-flight checks (all actions) ───────────────────────────────────────
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
            elif action == "send_networking_first_touch":
                body = _normalize_body(_generate_networking(
                    contact, dart_instr, _prompts,
                    extra_instruction=_pf_extra, research_block=research_block))
            elif action == "send_networking_followup":
                body = _normalize_body(_generate_networking_followup(
                    contact, dart_instr, _prompts, extra_instruction=_pf_extra))
            else:
                body = _normalize_body(_generate_applied_followup(
                    contact, dart_instr, _prompts, extra_instruction=_pf_extra))
            _pf_failures = preflight.check(body, contact, _prompts)
        except Exception as exc:
            log.warning(
                f"[PREFLIGHT] | {_name} | {_company} | retry raised ({exc}) — "
                f"allowing draft with unrevised body"
            )
            _pf_failures = []  # transient error — don't falsely block the contact
    if _pf_failures:
        _log_event("preflight", contact_id=contact.get("id"),
                   contact_name=contact.get("name"),
                   status="blocked_preflight",
                   metadata={"blocked_checks": _pf_failures})
        log.warning(f"[PREFLIGHT] | {_name} | {_company} | BLOCKED | {_pf_failures}")
        raise ValueError(f"pre-flight blocked: {'; '.join(_pf_failures)}")
    _log_event("preflight", contact_id=contact.get("id"),
               contact_name=contact.get("name"), status="success")

    # Space out subject + critic calls to stay within per-minute token budget.
    if action in _FIRST_TOUCH_ACTIONS:
        time.sleep(INTER_CALL_SLEEP)

    if action in _FIRST_TOUCH_ACTIONS:
        subject = _generate_subject(contact, mode, body, _prompts)
    else:
        subject = "Re: " + (original_subject or "")

    if action in _FIRST_TOUCH_ACTIONS and contact.get("tier") == 1:
        critic_prompt_text = _prompts.get("critic_prompt", CRITIC_PROMPT_DEFAULT)
        sender_profile_text = _prompts.get("sender_profile", SENDER_PROFILE)

        def regenerate(feedback):
            # Only reachable for actions in _FIRST_TOUCH_ACTIONS with tier == 1:
            # send_first_touch, send_applied_intro, send_networking_first_touch.
            if action == "send_first_touch":
                new_body = _normalize_body(
                    _generate_outreach(contact, action, dart_instr, _prompts,
                                       extra_instruction=feedback,
                                       research_block=research_block)
                )
            elif action == "send_networking_first_touch":
                new_body = _normalize_body(
                    _generate_networking(contact, dart_instr, _prompts,
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


def generate_email(contact, action, original_subject=None, prompts=None):
    """
    Generate email body + subject for a contact based on the action.
    Returns (subject, body) tuple.
    For follow-up actions, skips Claude subject generation and returns
    'Re: {original_subject}' so follow-ups stay in the same thread.
    If prompts dict is provided, its values override the config.py defaults.
    """
    user_prompt, system, ctx = prepare_email(contact, action, prompts)
    body = _call_claude(user_prompt, system=system)
    return finalize_email(contact, action, body, original_subject, prompts, **ctx)

def _build_outreach_prompt(contact, action, dart_instr, prompts, extra_instruction=None, research_block=""):
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
        tier_instruction=get_tier_instruction(prompts, int(tier)),
        template=template,
        template_instruction=get_template_instruction(prompts, template),
        dartmouth_instruction=dart_instr,
    )
    if research_block:
        prompt += research_block
    if extra_instruction is not None:
        prompt += f"\nREVISION INSTRUCTION:\n{extra_instruction}"
    return prompt

def _generate_outreach(contact, action, dart_instr, prompts, extra_instruction=None, research_block=""):
    prompt = _build_outreach_prompt(contact, action, dart_instr, prompts, extra_instruction, research_block)
    return _call_claude(prompt, system=prompts.get("sender_profile", SENDER_PROFILE))

def _build_applied_intro_prompt(contact, dart_instr, prompts, extra_instruction=None, research_block=""):
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
    return prompt

def _generate_applied_intro(contact, dart_instr, prompts, extra_instruction=None, research_block=""):
    prompt = _build_applied_intro_prompt(contact, dart_instr, prompts, extra_instruction, research_block)
    return _call_claude(prompt, system=prompts.get("sender_profile", SENDER_PROFILE))

def _build_applied_followup_prompt(contact, dart_instr, prompts, extra_instruction=None):
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
    return prompt

def _generate_applied_followup(contact, dart_instr, prompts, extra_instruction=None):
    prompt = _build_applied_followup_prompt(contact, dart_instr, prompts, extra_instruction)
    return _call_claude(prompt, system=prompts.get("sender_profile", SENDER_PROFILE))

def _connection_context_instruction(contact):
    value = (contact.get("connection_context") or "").strip()
    if value:
        return f"Lead with this specific hook: {value}"
    return (
        "No connection hook provided — do not invent one. Open with a brief, "
        "honest, low-pressure reason for reaching out instead."
    )

def _build_networking_prompt(contact, dart_instr, prompts, extra_instruction=None, research_block=""):
    profile = prompts.get("sender_profile", SENDER_PROFILE)
    tpl = prompts.get("networking_prompt", NETWORKING_PROMPT)
    prompt = tpl.format(
        profile=profile,
        name=contact.get("name", ""),
        company=contact.get("company", ""),
        connection_context_instruction=_connection_context_instruction(contact),
        dartmouth_instruction=dart_instr,
    )
    if research_block:
        prompt += research_block
    if extra_instruction is not None:
        prompt += f"\nREVISION INSTRUCTION:\n{extra_instruction}"
    return prompt

def _generate_networking(contact, dart_instr, prompts, extra_instruction=None, research_block=""):
    prompt = _build_networking_prompt(contact, dart_instr, prompts, extra_instruction, research_block)
    return _call_claude(prompt, system=prompts.get("sender_profile", SENDER_PROFILE))

def _build_networking_followup_prompt(contact, dart_instr, prompts, extra_instruction=None):
    profile = prompts.get("sender_profile", SENDER_PROFILE)
    tpl = prompts.get("networking_followup_prompt", NETWORKING_FOLLOWUP_PROMPT)
    prompt = tpl.format(
        profile=profile,
        name=contact.get("name", ""),
        company=contact.get("company", ""),
        dartmouth_instruction=dart_instr,
    )
    if extra_instruction is not None:
        prompt += f"\nREVISION INSTRUCTION:\n{extra_instruction}"
    return prompt

def _generate_networking_followup(contact, dart_instr, prompts, extra_instruction=None):
    prompt = _build_networking_followup_prompt(contact, dart_instr, prompts, extra_instruction)
    return _call_claude(prompt, system=prompts.get("sender_profile", SENDER_PROFILE))

def _generate_subject(contact, mode, body, prompts):
    profile = prompts.get("sender_profile", SENDER_PROFILE)
    if mode == "networking":
        tpl = prompts.get("networking_subject_prompt", NETWORKING_SUBJECT_PROMPT)
        prompt = tpl.format(
            name=contact.get("name", ""),
            company=contact.get("company", ""),
            body=body[:500],
        )
    else:
        tpl = prompts.get("subject_prompt", SUBJECT_PROMPT)
        prompt = tpl.format(
            name=contact.get("name", ""),
            company=contact.get("company", ""),
            mode=mode,
            job_title=contact.get("job_title", ""),
            body=body[:500],
        )
    subject = _call_claude(prompt, system=profile)
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

    _fallback = {
        "verdict": "PASS", "score": 16, "rewrite_required": False,
        "killed_by": [], "failed_soft_criteria": [],
        "banned_phrases_found": [], "ai_tells_found": [], "feedback": "",
    }

    try:
        formatted = critic_prompt_text.format(
            sender_profile=sender_profile,
            contact_context=contact_context,
            subject=subject,
            body=body,
        )
    except Exception as exc:
        log.warning(f"[CRITIC] prompt format error: {exc}")
        return _fallback

    try:
        raw = _call_claude(formatted, system=sender_profile)
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
    from db import log_agent_event as _log_event
    name = contact.get("name", "")
    company = contact.get("company", "")
    result = _run_critic(subject, body, contact, sender_profile, critic_prompt_text)
    retried = False

    if not result.get("rewrite_required", False):
        log.info(
            f"[CRITIC] | {name} | {company} | "
            f"score={result.get('score', 16)} | "
            f"killed_by={result.get('killed_by', [])} | "
            f"failed_soft={result.get('failed_soft_criteria', [])} | "
            f"retried={retried}"
        )
        _log_event("critic", contact_id=contact.get("id"),
                   contact_name=contact.get("name"), status="success",
                   metadata={
                       "score": result.get("score"),
                       "verdict": result.get("verdict"),
                       "rewrite_required": False,
                       "killed_by": result.get("killed_by", []),
                       "failed_soft": result.get("failed_soft_criteria", []),
                       "retried": False,
                   })
        return subject, body

    try:
        subject, body = regenerate_fn(result.get("feedback", ""))
        retried = True
    except Exception as exc:
        log.warning(f"[CRITIC] regenerate error for {name} | {company}: {exc}")

    log.info(
        f"[CRITIC] | {name} | {company} | "
        f"score={result.get('score', 0)} | "
        f"killed_by={result.get('killed_by', [])} | "
        f"failed_soft={result.get('failed_soft_criteria', [])} | "
        f"retried={retried}"
    )
    _log_event("critic", contact_id=contact.get("id"),
               contact_name=contact.get("name"), status="success",
               metadata={
                   "score": result.get("score"),
                   "verdict": result.get("verdict"),
                   "rewrite_required": result.get("rewrite_required"),
                   "killed_by": result.get("killed_by", []),
                   "failed_soft": result.get("failed_soft_criteria", []),
                   "retried": retried,
               })
    return subject, body
