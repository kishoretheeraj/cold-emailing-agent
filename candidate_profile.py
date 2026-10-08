"""Shared real-candidate-facts text, built from resume/data/master.json + metrics.json.

Used by job_pick.py's LLM judge (grounds the fit verdict in the candidate's real experience,
not just the job posting) and apply_agent.py's screening-answer prompt (grounds generated
answers in real experience instead of the posting's own role title -- see merge review
2026-09-28, finding 3). Deliberately dependency-free (no sentence_transformers, no db, no
emailer) so importing it never pulls job_pick.py's heavy module-level sentence_transformers
dependency into apply_agent.py's lighter requirements-apply.txt environment.
"""

import json
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
