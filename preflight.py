import re
from datetime import date

# ── Check implementations ──────────────────────────────────────────────────────

def check_placeholder_braces(body, contact, prompts):
    matches = re.findall(r'\{[A-Z][A-Z0-9 _]{1,}\}', body)
    if matches:
        return f"unfilled_braces: {', '.join(sorted(set(matches)))}"
    return None


def check_unfilled_brackets(body, contact, prompts):
    matches = re.findall(r'\[[A-Z][A-Za-z0-9 ]{1,30}\]', body)
    if matches:
        return f"unfilled_brackets: {', '.join(sorted(set(matches)))}"
    return None


def check_first_name_presence(body, contact, prompts):
    full_name = (contact.get("name") or "").strip()
    if not full_name:
        return None
    first_name = full_name.split()[0]
    if first_name.lower() not in body.lower():
        return f"first_name_missing: '{first_name}' not found in body"
    return None


def check_wrong_company(body, contact, prompts):
    watchlist_raw = (prompts or {}).get("guardrail_company_list", "")
    if not watchlist_raw:
        return None
    contact_company = (contact.get("company") or "").lower()
    body_lower = body.lower()
    flagged = []
    for line in watchlist_raw.splitlines():
        watchword = line.strip()
        if not watchword:
            continue
        if watchword.lower() in body_lower and watchword.lower() not in contact_company:
            flagged.append(watchword)
    if flagged:
        return f"wrong_company: {', '.join(flagged)}"
    return None


_FUTURE_PHRASES = [
    "looking forward to",
    "looking forward",
    "chat in",
    "meet in",
    "catch up",
    "talk in",
    "connect in",
    "will",
]

def check_stale_year(body, contact, prompts):
    current_year = date.today().year
    body_lower = body.lower()
    for m in re.finditer(r'\b(20\d{2})\b', body):
        if int(m.group(1)) >= current_year:
            continue
        start = max(0, m.start() - 50)
        end = min(len(body), m.end() + 50)
        window = body_lower[start:end]
        for phrase in _FUTURE_PHRASES:
            if phrase in window:
                return f"stale_year: '{m.group(1)}' with future-tense context ('{phrase}')"
    return None


def check_forbidden_phrases(body, contact, prompts):
    forbidden_raw = (prompts or {}).get("forbidden_phrases", "")
    if not forbidden_raw:
        return None
    body_lower = body.lower()
    flagged = [
        phrase.strip() for phrase in forbidden_raw.splitlines()
        if phrase.strip() and phrase.strip().lower() in body_lower
    ]
    if flagged:
        return f"forbidden_phrases: {', '.join(repr(p) for p in flagged)}"
    return None


# ── Public interface ───────────────────────────────────────────────────────────

_CHECKS = [
    check_placeholder_braces,
    check_unfilled_brackets,
    check_first_name_presence,
    check_wrong_company,
    check_stale_year,
    check_forbidden_phrases,
]

def check(body, contact, prompts):
    """Run all six pre-flight checks. Returns list of failure strings (empty = pass)."""
    return [r for fn in _CHECKS for r in [fn(body, contact, prompts)] if r]
