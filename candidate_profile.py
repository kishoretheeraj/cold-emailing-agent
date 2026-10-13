"""Shared real-candidate-facts text, built from resume/data/master.json + metrics.json.

Used by job_pick.py's LLM judge (grounds the fit verdict in the candidate's real experience,
not just the job posting) and apply_agent.py's screening-answer prompt (grounds generated
answers in real experience instead of the posting's own role title -- see merge review
2026-09-28, finding 3). Deliberately dependency-free (no sentence_transformers, no db, no
emailer) so importing it never pulls job_pick.py's heavy module-level sentence_transformers
dependency into apply_agent.py's lighter requirements-apply.txt environment.
"""

import json
import re
from pathlib import Path

_MASTER_DATA_PATH = Path(__file__).resolve().parent / "resume" / "data" / "master.json"
_cache = None


def profile_text():
    # Real profile text from resume/data/master.json and metrics.json, cached at module level.
    global _cache
    if _cache is None:
        with open(_MASTER_DATA_PATH) as f:
            master = json.load(f)
        with open(_MASTER_DATA_PATH.parent / "metrics.json") as f:
            metrics_by_id = {m["id"]: m["text"] for m in json.load(f)}

        parts = []
        for role in master.get("roles", []):
            parts.append(f"{role.get('title', '')} at {role.get('company', '')}")
            parts += [metrics_by_id[bid] for bid in role.get("bullet_ids", []) if bid in metrics_by_id]
        for project_name, project in master.get("projects", {}).items():
            parts.append(project_name)
            parts += [metrics_by_id[bid] for bid in project.get("bullet_ids", []) if bid in metrics_by_id]
        _cache = " ".join(parts)
    return _cache


def candidate_name():
    """The candidate's name as master.json spells it ('' when unreadable)."""
    try:
        with open(_MASTER_DATA_PATH) as f:
            return str(json.load(f).get("name") or "")
    except (OSError, ValueError):
        return ""


# ── Standard application-form facts ───────────────────────────────────────────

def _city_candidates(location):
    # "Hanover, NH" -> also "Hanover, New Hampshire" / "..., United States": location pickers list
    # the state spelled out, and the value has to equal or begin one of their options.
    import job_filters

    city, _, state = (part.strip() for part in str(location or "").partition(","))
    if not city:
        return []
    out = [f"{city}, {state}" if state else city]
    name = next((n for n, code in job_filters._US_STATES if code == state.lower()), None)
    if name:
        spelled = f"{city}, {name.title()}"
        out += [spelled, f"{spelled}, United States", f"{spelled}, USA", f"{city}, {state}, USA"]
    return out


def _year(text):
    match = re.search(r"\b(19|20)\d{2}\b", str(text or ""))
    return match.group(0) if match else None


def _degree(program):
    # "Master of Engineering Management (MEM) | Joint program ..." -> ("master",
    # "Master of Engineering Management", "Engineering Management").
    # "Bachelor of Engineering, Computer Science and Engineering" -> ("bachelor",
    # "Bachelor of Engineering", "Computer Science and Engineering").
    head = re.sub(r"\s*\([^)]*\)", "", str(program or "").split("|")[0]).strip()
    title, _, field = (part.strip() for part in head.partition(","))
    level = title.split(" ")[0].lower() if title else ""
    if not field and " of " in title:
        field = title.split(" of ", 1)[1]
    return level, title, field


_DEGREE_NAMES = {
    "master": ["Master's Degree", "Master's", "Masters"],
    "bachelor": ["Bachelor's Degree", "Bachelor's", "Bachelors"],
}


def _education_answers(entry):
    level, title, field = _degree(entry.get("program"))
    answers = {"school": [entry["institution"]] if entry.get("institution") else []}
    answers["degree"] = ([title] if title else []) + _DEGREE_NAMES.get(level, [])
    disciplines = [field] if field else []
    if " and " in field:
        disciplines.append(field.split(" and ")[0])
    elif len(field.split()) == 2:
        disciplines.append(field.split()[0])  # "Engineering Management" -> "Engineering"
    answers["discipline"] = disciplines
    year = _year(entry.get("graduation"))
    answers["end_year"] = [year] if year else []
    return answers


def form_answers():
    """{key: [acceptable answers, best first]} for the standard questions application forms ask
    (country, city, school, degree, discipline, graduation year), all derived from master.json.
    A form option must equal one of these (or begin with a multi-word one) to be chosen; a free-text
    box gets the first. A fact master.json does not hold (start year, GPA) is simply absent."""
    try:
        with open(_MASTER_DATA_PATH) as f:
            master = json.load(f)
    except (OSError, ValueError):
        return {}
    answers = {}
    location = (master.get("contact") or {}).get("location")
    cities = _city_candidates(location)
    if cities:
        answers["city"] = cities
        if len(cities) > 1:  # a US state was recognized
            answers["country"] = ["United States", "United States of America", "USA"]
    education = [e for e in master.get("education") or [] if isinstance(e, dict)]
    if education:
        # A form with one School/Degree block asks about the most recent degree (listed first).
        answers.update(_education_answers(education[0]))
        for entry in education:
            level = _degree(entry.get("program"))[0]
            key = {"master": "school_graduate", "bachelor": "school_undergraduate"}.get(level)
            if key and key not in answers and entry.get("institution"):
                answers[key] = [entry["institution"]]
    return {key: values for key, values in answers.items() if values}
