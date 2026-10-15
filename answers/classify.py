"""Coarse question classification: which decision path owns this question text.

classify() returns one of the QuestionClass constants. It decides routing only; resolve_field()
(answers/__init__.py) makes the actual decision. Pattern lists below are module-level and
documented; first match wins in the order skip, ambiguity, work_auth, identity, free_text.
"""
import re

from . import workauth


class QuestionClass:
    WORK_AUTH = "work_auth"
    IDENTITY = "identity"
    FREE_TEXT = "free_text"
    UNKNOWN = "unknown"
    SKIP = "skip"


# Button labels, navigation and section headings: not questions. A text ending in "?" is never
# skipped by these patterns.
_SKIP_PATTERNS = (
    re.compile(
        r"^(?:submit(?:\s+(?:your\s+)?application)?|apply(?:\s+now)?|upload(?:\s+\w+){0,3}|attach(?:\s+\w+){0,3}|"
        r"next(?:\s+step)?|back|previous|continue|save(?:\s+(?:and|&)\s+continue|\s+draft|\s+for\s+later)?|"
        r"cancel|close|done|finish|review(?:\s+(?:and\s+submit|application))?|add\s+another|remove|edit|"
        r"choose\s+file|browse(?:\s+files?)?|drag\s+(?:and|&)\s+drop.*)\s*[.!:]?$", re.I),
    re.compile(
        r"^(?:personal|contact|additional|application|voluntary|demographic|education|employment|"
        r"work\s+experience|equal\s+(?:employment\s+)?opportunity|self[- ]identification)\s+"
        r"(?:information|details|questions|survey|history|section|disclosures?|data)$", re.I),
    re.compile(r"^(?:step\s+\d+(?:\s+of\s+\d+)?|\d+\s*/\s*\d+)$", re.I),
)

# Who the candidate is and where they will work: name, contact channels, profiles, location prefs.
_IDENTITY_PATTERNS = (
    re.compile(r"\b(?:full|first|last|legal|given|family|preferred|middle)\s*name\b|^name\b|\byour\s+name\b|\bsurname\b", re.I),
    re.compile(r"e-?mail", re.I),
    re.compile(r"\b(?:phone|mobile|telephone|cell)\b", re.I),
    re.compile(r"linked\s*in|git\s*hub|\bportfolio\b|\bwebsite\b|\bpersonal\s+(?:site|url)\b", re.I),
    re.compile(
        r"\b(?:location|city|state|province|country|zip|postal|address)\b|"
        r"where\s+(?:are\s+you\s+(?:located|based)|do\s+you\s+(?:live|reside))|"
        r"relocat|commut|in[- ]?person|on[- ]?site|\bhybrid\b|\bnyc\b|new\s+york", re.I),
)

# Essay-style prompts.
_FREE_TEXT_PATTERNS = (
    re.compile(
        r"\bwhy\b|\bdescribe\b|tell\s+us\s+(?:about|more)|cover\s+letter|in\s+your\s+own\s+words|\bexplain\b|"
        r"accomplish\w*|\bproud\b|achievement|additional\s+information|anything\s+else|"
        r"what\s+(?:interests|excites|motivates|attracts)\s+you", re.I),
)
# Any prompt longer than this (after the patterns above found nothing) is treated as an essay prompt.
FREE_TEXT_MIN_CHARS = 200


def _normalize(text):
    return " ".join((text or "").split())


def _is_skip(q):
    if not q:
        return True
    if q.endswith("?"):
        return False
    return any(p.search(q) for p in _SKIP_PATTERNS)


def classify(text):
    """Return the QuestionClass constant for one question string."""
    from . import ambiguity  # lazy: ambiguity imports workauth, keep this module import-light

    q = _normalize(text)
    if _is_skip(q):
        return QuestionClass.SKIP
    ambiguous, reasons = ambiguity.detect(q)
    # "truncated" alone must not hide short labels like "Email" from the identity check.
    if ambiguous and any(r != "truncated" for r in reasons):
        return QuestionClass.UNKNOWN
    if workauth._AUTH_RE.search(q) or workauth._SPONSOR_RE.search(q):
        return QuestionClass.WORK_AUTH
    if any(p.search(q) for p in _IDENTITY_PATTERNS):
        return QuestionClass.IDENTITY
    if any(p.search(q) for p in _FREE_TEXT_PATTERNS) or len(q) > FREE_TEXT_MIN_CHARS:
        return QuestionClass.FREE_TEXT
    return QuestionClass.UNKNOWN
