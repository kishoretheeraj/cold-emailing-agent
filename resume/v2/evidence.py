"""Shared evidence package for the stage-3 parallel builds (cover letter today).

Pure function, no I/O. Everything a grounded writer may use is in the package: the requirements, the
ranked source bullets, the candidate's bank facts, the JD, the banned words and the topics never to invent.
"""

from resume.v2.validator import _banned_words

JD_CHARS = 12000
_BANK_KEYS = ("full_name", "email", "phone", "linkedin", "location")


def build_evidence(job, jd_text, requirements, ranked_bullets, bank_facts):
    """Assemble the package. job is accepted for symmetry with the other builders and is not read.
    bank_facts is a plain dict; missing keys become "". Extra keys are dropped except
    "unknown_do_not_invent", which feeds unknown_topics."""
    bank = bank_facts or {}
    return {
        "requirements": requirements,
        "bullets": [
            {"id": b.get("id"), "role": b.get("role", ""), "text": b.get("text", ""),
             "tags": list(b.get("tags") or [])}
            for b in (ranked_bullets or [])],
        "bank_facts": {k: bank.get(k) or "" for k in _BANK_KEYS},
        "jd_text": str(jd_text or "")[:JD_CHARS],
        "banned_words": list(_banned_words()),
        "unknown_topics": list(bank.get("unknown_do_not_invent") or []),
    }
