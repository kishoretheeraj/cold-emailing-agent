"""Python validation gates for Sonnet's content-only resume JSON.

Content schema:
    {"headline": str, "summary": str,
     "experience": [{"role": str, "bullets": [str, ...]}, ...],
     "skills": [str, ...]}

candidate_profile is the ground truth: {"bullets": [{"role", "text", ...}], "skills": [str] (optional), ...}.
Every string in the profile counts as a known fact for number grounding.
"""

import re
from functools import lru_cache
from pathlib import Path

BANNED_WORDS_PATH = Path(__file__).resolve().parents[1] / "banned_words.txt"
MIN_BULLETS = 5
MAX_BULLETS = 6
MAX_BULLETS_PER_ROLE = 6
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?[%xXkKmM]?")


# ── Helpers ────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _banned_words() -> tuple[str, ...]:
    lines = BANNED_WORDS_PATH.read_text().splitlines()
    return tuple(ln.strip().lower() for ln in lines if ln.strip())


def _numbers(text: str) -> set[str]:
    return {m.group(0).replace(",", "").lower() for m in _NUM_RE.finditer(text)}


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def _schema_violations(content: dict) -> list[str]:
    out = []
    for key in ("headline", "summary"):
        if not isinstance(content.get(key), str) or not content[key].strip():
            out.append(f"schema: '{key}' must be a non-empty string")
    exp = content.get("experience")
    if not isinstance(exp, list) or not exp:
        out.append("schema: 'experience' must be a non-empty list")
    else:
        for i, entry in enumerate(exp):
            if not isinstance(entry, dict) or not isinstance(entry.get("role"), str) or not entry["role"].strip():
                out.append(f"schema: experience[{i}] needs a non-empty 'role'")
                continue
            bl = entry.get("bullets")
            if not isinstance(bl, list) or not bl or not all(isinstance(b, str) and b.strip() for b in bl):
                out.append(f"schema: experience[{i}] 'bullets' must be a non-empty list of strings")
    skills = content.get("skills")
    if not isinstance(skills, list) or not all(isinstance(s, str) and s.strip() for s in skills):
        out.append("schema: 'skills' must be a list of non-empty strings")
    return out


def _text_of(content: dict) -> str:
    parts = [content["headline"], content["summary"]]
    for entry in content["experience"]:
        parts.extend(entry["bullets"])
    parts.extend(content["skills"])
    return "\n".join(parts)


# ── Validation ─────────────────────────────────────────────────────────────────

def validate_resume(content_json: dict, candidate_profile: dict) -> tuple[bool, list[str]]:
    """Check generated content against the schema, banned words, dashes, invented numbers, unknown roles,
    ungrounded skills and bullet-count limits. Returns (is_valid, violations). Never raises on bad shapes."""
    if not isinstance(content_json, dict):
        return False, ["schema: content is not a JSON object"]
    violations = _schema_violations(content_json)
    if violations:
        return False, violations

    text = _text_of(content_json)
    for word in _banned_words():
        if re.search(r"\b" + re.escape(word) + r"\w*", text, re.I):
            violations.append(f"banned word: '{word}'")
    if "\u2014" in text or "\u2013" in text:
        violations.append("em or en dash found (use a colon, comma or period)")

    profile_bullets = candidate_profile.get("bullets") or []
    corpus = "\n".join(_strings(candidate_profile))
    for n in sorted(_numbers(text) - _numbers(corpus)):
        violations.append(f"number not in candidate profile: {n}")

    known_roles = {b.get("role") for b in profile_bullets if b.get("role")}
    for entry in content_json["experience"]:
        if known_roles and entry["role"] not in known_roles:
            violations.append(f"unknown role: '{entry['role'][:60]}'")
        if len(entry["bullets"]) > MAX_BULLETS_PER_ROLE:
            violations.append(f"role '{entry['role'][:40]}' has {len(entry['bullets'])} bullets (max {MAX_BULLETS_PER_ROLE})")

    corpus_lower = corpus.lower()
    for skill in content_json["skills"]:
        if skill.lower() not in corpus_lower:
            violations.append(f"skill not in candidate profile: '{skill[:40]}'")

    total = sum(len(e["bullets"]) for e in content_json["experience"])
    lowest = min(MIN_BULLETS, len(profile_bullets)) if profile_bullets else MIN_BULLETS
    if total < lowest or total > MAX_BULLETS:
        violations.append(f"bullet count {total} outside {lowest}-{MAX_BULLETS}")

    return not violations, violations
