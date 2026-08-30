"""
Three-stage job-relevance scoring: structured filters -> local embedding similarity
-> coarse LLM judge. Replaces job_discovery.py's old target_roles word-overlap
check with something that actually reads the posting. Best-effort, never-raises
per-row -- one bad posting must never stop the batch. A "strong" verdict
zero-tap triggers resume_agent's propose+build pipeline (real spend, no human
pause between them for this auto-pick path specifically -- see
docs/superpowers/specs/2026-08-30-phase2.5-auto-apply-design.md).
"""

import json
import logging
from pathlib import Path

from sentence_transformers import SentenceTransformer

import config
import db
import resume_agent
from emailer import _call_claude

log = logging.getLogger(__name__)

_model = None
_profile_text_cache = None
_MASTER_DATA_PATH = Path(__file__).resolve().parent / "resume" / "data" / "master.json"


# ── Stage 1: structured filters ─────────────────────────────────────────────────

def _passes_structured_filters(job):
    role = (job.get("role") or "").lower()
    target_words = {"product", "pm", "manager", "analyst", "strategy", "operations"}
    return any(word in role for word in target_words)


# ── Stage 2: local embedding similarity ──────────────────────────────────────────

def _embed(text):
    # Wraps SentenceTransformer so tests can mock this function directly instead of loading real weights.
    global _model
    if _model is None:
        _model = SentenceTransformer(config.JOB_PICK_EMBEDDING_MODEL)
    return _model.encode(text).tolist()


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _embedding_similarity(job_description, profile_text):
    job_vec = _embed(job_description)
    profile_vec = _embed(profile_text)
    return round(_cosine(job_vec, profile_vec), 4)


def _profile_text():
    # Real profile text from resume/data/master.json and metrics.json, cached at module level.
    global _profile_text_cache
    if _profile_text_cache is None:
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
        _profile_text_cache = " ".join(parts)
    return _profile_text_cache


# ── Stage 3: coarse LLM judge ─────────────────────────────────────────────────────

_JUDGE_PROMPT = """You are screening one job posting against a candidate's real profile.
Respond with ONLY a JSON object, no other text: {{"verdict": "strong"|"maybe"|"no", "reasoning": "<one sentence>"}}

Use "strong" only when this is a clear, direct fit worth spending money to generate a tailored
resume for. Use "maybe" for a plausible but uncertain fit. Use "no" for anything else. Be
conservative -- a missed "maybe" costs nothing, a wrong "strong" costs real money.

Job: {company} -- {role}
Description: {description}
"""


def _strip_json_fence(text):
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped
        if stripped.endswith("```"):
            stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def _llm_judge(job):
    prompt = _JUDGE_PROMPT.format(
        company=job.get("company", "Unknown"),
        role=job.get("role", "Unknown"),
        description=(job.get("posting_snapshot") or {}).get("description", ""),
    )
    try:
        raw = _call_claude(prompt, model=config.JOB_PICK_MODEL, module="job_pick", action="judge")
        parsed = json.loads(_strip_json_fence(raw))
        if parsed.get("verdict") not in ("strong", "maybe", "no"):
            raise ValueError("unexpected verdict value")
        return {"verdict": parsed["verdict"], "reasoning": parsed.get("reasoning", "")}
    except Exception as exc:
        return {"verdict": "no", "reasoning": f"judge response failed to parse: {exc}"}


# ── Orchestration ─────────────────────────────────────────────────────────────────

def score_job(job):
    """Score one job posting through three stages: structured filters, embedding similarity, and LLM judge."""
    if not _passes_structured_filters(job):
        return {"verdict": "no", "score": None, "reasoning": "structured filter: title/keyword mismatch"}

    description = (job.get("posting_snapshot") or {}).get("description", job.get("role", ""))
    score = _embedding_similarity(description, _profile_text())
    if score < config.JOB_PICK_EMBEDDING_THRESHOLD:
        return {"verdict": "no", "score": score, "reasoning": "embedding similarity below threshold"}

    verdict = _llm_judge(job)
    return {"verdict": verdict["verdict"], "score": score, "reasoning": verdict["reasoning"]}


def run():
    """Batch-score unscored job applications; trigger resume_agent on strong verdicts."""
    jobs = db.get_unscored_saved_applications()
    log.info(f"[JOB-PICK] | START | jobs_to_score={len(jobs)}")
    scored = 0
    triggered = 0
    errors = 0
    pipeline_errors = 0

    for job in jobs:
        job_id = job.get("id")
        try:
            result = score_job(job)
            db.set_pick_verdict(job_id, result["verdict"], result["score"], result["reasoning"])
            scored += 1
            log.info(f"[JOB-PICK] | {job.get('company')} | {job.get('role')} | verdict={result['verdict']} | score={result['score']}")

            if result["verdict"] == "strong":
                try:
                    resume_agent.propose(job_id)
                    resume_agent.build(job_id)
                    triggered += 1
                except Exception as exc:
                    log.warning(f"[JOB-PICK] | {job.get('company')} | resume pipeline failed: {exc}")
                    pipeline_errors += 1
        except Exception as exc:
            log.warning(f"[JOB-PICK] | {job.get('company')} | scoring error: {exc}")
            errors += 1

    log.info(f"[JOB-PICK] | DONE | scored={scored} | resume_triggered={triggered} | errors={errors} | pipeline_errors={pipeline_errors}")
