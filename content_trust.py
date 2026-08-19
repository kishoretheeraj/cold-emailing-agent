"""
Detects prompt-injection patterns in externally-sourced text (web research
briefs, inbound reply bodies) before it is placed into a Claude prompt.

Flag-only by design: callers annotate and proceed. Nothing here blocks a
draft, strips text, or rewrites input -- stripping destroys the evidence and
silently changes what the model sees.

Pure module: no I/O, no Claude call, no project imports.
"""

import re

# ── Patterns ───────────────────────────────────────────────────────────────────

# Deliberately narrow. Phrases like "forward this to your team" were considered
# and excluded: they are ordinary business copy and would flag constantly.
# A guardrail that cries wolf gets ignored, which is worse than not having one.
_PATTERNS = (
    ("instruction_override", re.compile(
        r"\b(?:ignore|disregard|forget|override)\s+"
        r"(?:all\s+|any\s+)?"
        r"(?:the\s+)?"
        r"(?:previous|prior|above|earlier|preceding|your)\s+"
        r"(?:\w+\s+){0,2}?instructions?\b",
        re.IGNORECASE)),
    ("role_injection", re.compile(
        r"(?:^|\n)\s*(?:system|assistant|user)\s*:"
        r"|<\|im_(?:start|end)\|>"
        r"|\[/?INST\]",
        re.IGNORECASE)),
    ("exfiltration", re.compile(
        r"\b(?:reveal|print|repeat|show|output|disclose)\s+"
        r"(?:me\s+)?(?:your|the)\s+"
        r"(?:system\s+)?(?:prompt|instructions?|rules)\b",
        re.IGNORECASE)),
)

PATTERN_LABELS = tuple(label for label, _ in _PATTERNS)


# ── Public interface ───────────────────────────────────────────────────────────

def scan(text):
    """
    Return a sorted list of injection-pattern labels found in text.
    Empty list means clean. Never raises -- invalid input scans clean.
    """
    try:
        if not text or not isinstance(text, str):
            return []
        found = set()
        for label, pattern in _PATTERNS:
            if pattern.search(text):
                found.add(label)
        return sorted(found)
    except Exception:
        return []
