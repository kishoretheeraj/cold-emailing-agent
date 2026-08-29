"""
Deterministic, pure-function lint checks for generated resume/cover-letter text --
distilled from RESUME_AGENT_SPEC.md's Parts 3, 5, and 9 (rules that failed
repeatedly even when stated directly in a prompt, so they became checks instead).

No I/O, no logging, no swallowed exceptions -- these are pure functions, text in,
a list of violation strings out. Empty list means pass. See
docs/superpowers/specs/2026-08-29-phase3-resume-intelligence-design.md.
"""

import re

_NUMBER_PATTERN = re.compile(r"\$?\d[\d,]*(?:\.\d+)?[KMB%x]?", re.IGNORECASE)


# ── Humanizer ──────────────────────────────────────────────────────────────────

def check_em_dashes(text):
    return ["em dash (—) found in text"] if "—" in text else []


# ── Jargon ─────────────────────────────────────────────────────────────────────

def check_jargon(text, jargon_map):
    violations = []
    lowered = text.lower()
    for banned, allowed in jargon_map.items():
        if banned.lower() in lowered:
            violations.append(f"'{banned}' found -- use '{allowed}' instead")
    return violations


# ── Metrics whitelist ──────────────────────────────────────────────────────────

def _extract_numbers(text):
    return _NUMBER_PATTERN.findall(text)


def check_metrics_whitelist(text, metrics):
    violations = []
    candidate_numbers = set(_extract_numbers(text))
    for metric in metrics:
        if metric.get("resolved") is not None:
            continue
        banned_numbers = set(_extract_numbers(metric.get("text", "")))
        for cv in metric.get("conflicting_values", []):
            banned_numbers |= set(_extract_numbers(cv))
        overlap = candidate_numbers & banned_numbers
        if overlap:
            violations.append(
                f"unresolved metric conflict '{metric['id']}': found {sorted(overlap)} -- "
                f"resolve resume/data/metrics.json before this can be used"
            )
    return violations


# ── Cover letter (corpus spec Part 9) ──────────────────────────────────────────

_BANNED_OPENERS = ("i am writing to apply", "i am excited to apply")
_HEDGE_WORDS = ("might", "could", "perhaps", "possibly", "may", "hopefully", "i believe", "i think", "i hope")


def _ngrams(text, n):
    words = re.findall(r"[A-Za-z0-9']+", text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def check_cover_letter_number_overlap(cl_text, resume_text):
    overlap = set(_extract_numbers(cl_text)) & set(_extract_numbers(resume_text))
    return [f"number '{n}' also appears in the resume" for n in sorted(overlap)]


def check_cover_letter_ngram_overlap(cl_text, resume_text, n=6):
    overlap = _ngrams(cl_text, n) & _ngrams(resume_text, n)
    return [f"shared {n}-word phrase: {' '.join(g)}" for g in sorted(overlap)]


def check_cover_letter_capability_enumeration(text):
    lowered = text.lower()
    if all(marker in lowered for marker in ("first,", "second,", "third,")):
        return ["enumerates exactly three capabilities (First/Second/Third pattern)"]
    return []


def check_cover_letter_banned_opener(text):
    stripped = text.strip().lower()
    for opener in _BANNED_OPENERS:
        if stripped.startswith(opener):
            return [f"opens with banned phrase '{opener}'"]
    return []


def check_cover_letter_word_count(text, max_words=300):
    count = len(text.split())
    return [f"word count {count} exceeds {max_words}"] if count > max_words else []


def check_cover_letter_closing_hedges(text, max_hedges=1):
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    if not sentences:
        return []
    closing = sentences[-1].lower()
    count = sum(closing.count(h) for h in _HEDGE_WORDS)
    return [f"closing sentence has {count} hedge words (max {max_hedges})"] if count > max_hedges else []


def check_cover_letter(cl_text, resume_text):
    violations = []
    violations += check_cover_letter_number_overlap(cl_text, resume_text)
    violations += check_cover_letter_ngram_overlap(cl_text, resume_text)
    violations += check_cover_letter_capability_enumeration(cl_text)
    violations += check_cover_letter_banned_opener(cl_text)
    violations += check_cover_letter_word_count(cl_text)
    violations += check_cover_letter_closing_hedges(cl_text)
    return violations
