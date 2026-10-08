"""
Three-stage job-relevance scoring: job_filters (title, seniority, location, age, sponsorship)
-> local embedding similarity -> coarse LLM judge. Best-effort, never-raises per-row -- one bad
posting must never stop the batch. On the "api" backend a "strong" verdict zero-tap triggers
resume_agent's propose+build pipeline (real spend, no human pause between them for this auto-pick
path specifically); on the "subscription" backend strong rows are queued for the Beelink resume
worker instead -- see docs/superpowers/specs/2026-08-30-phase2.5-auto-apply-design.md.

The judge runs on the Claude subscription with JOB_PICK_BACKEND=subscription (the Beelink's
job-pick.service), JOB_PICK_JUDGE_BATCH postings per call. A judgment that fails to parse leaves
the row unscored for the next run instead of recording "no"; after JOB_PICK_MAX_ATTEMPTS it is
recorded as "maybe" for a human to look at (spec 2026-10-08 fifty-a-day §3.4).
"""

import json
import logging
import sys

from sentence_transformers import SentenceTransformer

import ats_platform
import candidate_profile
import claude_subscription
import config
import db
import job_filters
import job_sourcing
import resume_agent
import usage_tracking
from emailer import _call_claude

log = logging.getLogger(__name__)

_model = None


# ── Stage 1: structured filters ─────────────────────────────────────────────────

def _as_posting(job):
    snapshot = job.get("posting_snapshot") or {}
    return {"title": job.get("role"), "url": job.get("job_url"),
            "location": job.get("location") or snapshot.get("location"),
            "posted_at": job.get("posted_at") or snapshot.get("posted_at"),
            "description": snapshot.get("description") or "", "sponsorship": snapshot.get("sponsorship")}


def _filter_reason(job, prefs=None, eligibility=None):
    # Postings from job_sourcing already passed these; LinkedIn rows and rows saved before the
    # filters existed had not.
    return job_filters.reject_reason(_as_posting(job), prefs or job_filters.load_preferences({}), eligibility or {})


def _passes_structured_filters(job):
    return job_filters.title_reason(job.get("role"), job_filters.load_preferences({})) is None


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


def _embedding_similarity(job_description, profile_text, profile_vec=None):
    job_vec = _embed(job_description)
    if profile_vec is None:
        profile_vec = _embed(profile_text)
    return round(_cosine(job_vec, profile_vec), 4)


def _profile_text():
    # Delegates to the shared candidate_profile module (merge review 2026-09-28, finding 3) --
    # apply_agent.py's screening-answer prompt needs the same real candidate facts this judge
    # uses, and duplicating the master.json/metrics.json parsing in both files risked the two
    # copies drifting. Kept as a thin wrapper (rather than replacing every call site here with
    # candidate_profile.profile_text() directly) so this module's own tests and mocks
    # (job_pick._profile_text) don't need to change.
    return candidate_profile.profile_text()


# ── Stage 3: coarse LLM judge ─────────────────────────────────────────────────────

_JUDGE_PROMPT = """You are screening job postings against one candidate's real profile.
Respond with ONLY a JSON array, no other text, one object per posting, in any order:
[{{"id": <posting id>, "verdict": "strong"|"maybe"|"no", "reasoning": "<one sentence>"}}]

Use "strong" only when a posting is a clear, direct fit worth generating a tailored resume for,
AND the candidate profile below actually supports it -- do not call a job "strong" based on the
posting alone if the candidate's real experience doesn't back it up. Use "maybe" for a plausible
but uncertain fit. Use "no" for anything else. Be conservative -- a missed "maybe" costs nothing,
a wrong "strong" costs real work.

Candidate profile: {profile_text}

Postings:
{postings}
"""

_NO_PROFILE_REASONING = "no candidate profile text available to judge fit against -- needs human review"
_JUDGE_DESCRIPTION_CHARS = 2500


def _strip_json_fence(text):
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped
        if stripped.endswith("```"):
            stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def _judge_completion(prompt):
    if config.JOB_PICK_BACKEND == "subscription":
        text, usage = claude_subscription.complete(prompt, model=config.JOB_PICK_MODEL)
        usage_tracking.log_usage("job_pick", "judge", config.JOB_PICK_MODEL, usage, billing="subscription")
        return text
    return _call_claude(prompt, model=config.JOB_PICK_MODEL, module="job_pick", action="judge")


def _judge_batch(jobs, profile_text):
    """{index: {"verdict", "reasoning"}} for the postings the model judged, keyed by position in
    `jobs`. A posting missing from the answer (or a whole answer that fails to parse) is simply
    absent: the caller retries it. Subscription failures (usage limit, auth) propagate."""
    blocks = []
    for index, job in enumerate(jobs):
        snapshot = job.get("posting_snapshot") or {}
        blocks.append(f"[id {index}] {job.get('company', 'Unknown')} -- {job.get('role', 'Unknown')}"
                      f" ({job.get('location') or snapshot.get('location') or 'location not given'})\n"
                      f"{(snapshot.get('description') or '')[:_JUDGE_DESCRIPTION_CHARS]}")
    prompt = _JUDGE_PROMPT.format(profile_text=profile_text, postings="\n\n".join(blocks))
    try:
        raw = _judge_completion(prompt)
    except claude_subscription.ClaudeSubscriptionError:
        raise
    except Exception as exc:
        log.warning(f"[JOB-PICK] | judge call failed: {exc}")
        return {}
    try:
        parsed = json.loads(_strip_json_fence(raw))
    except ValueError as exc:
        log.warning(f"[JOB-PICK] | judge response failed to parse: {exc}")
        return {}
    if isinstance(parsed, dict):
        parsed = [dict(parsed, id=0)] if len(jobs) == 1 else []
    results = {}
    for item in parsed if isinstance(parsed, list) else []:
        if not isinstance(item, dict) or item.get("verdict") not in ("strong", "maybe", "no"):
            continue
        try:
            index = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(jobs):
            results[index] = {"verdict": item["verdict"], "reasoning": str(item.get("reasoning", ""))[:500]}
    return results


def _llm_judge(job, profile_text):
    # Merge review 2026-09-28, finding 3: profile_text (the real candidate facts) is part of the
    # prompt, and a missing/empty profile degrades to "maybe" (visible, human-reviewable) rather
    # than letting the LLM fabricate unsupported fit or trigger resume work on a groundless "strong".
    # A judgment that does not parse returns verdict None: the row stays unscored and is retried.
    if not profile_text.strip():
        return {"verdict": "maybe", "reasoning": _NO_PROFILE_REASONING}
    result = _judge_batch([job], profile_text).get(0)
    return result or {"verdict": None, "reasoning": "judge response failed to parse; will retry"}


# ── Orchestration ─────────────────────────────────────────────────────────────────

def _prefilter(job, prefs, eligibility, profile_text, profile_vec):
    # Stages 1 and 2. Returns a final result, or (None, score) when the judge must decide.
    reason = _filter_reason(job, prefs, eligibility)
    if reason:
        return {"verdict": "no", "score": None, "reasoning": f"filter: {reason}"}, None
    snapshot = job.get("posting_snapshot") or {}
    description = snapshot.get("description") or job.get("role") or ""
    score = _embedding_similarity(description, profile_text, profile_vec)
    if score < config.JOB_PICK_EMBEDDING_THRESHOLD:
        return {"verdict": "no", "score": score, "reasoning": "embedding similarity below threshold"}, None
    return None, score


def score_job(job):
    """Score one job posting through three stages: filters, embedding similarity, and LLM judge."""
    prefs, eligibility = job_sourcing.load_search_settings()
    profile_text = _profile_text()
    result, score = _prefilter(job, prefs, eligibility, profile_text, None)
    if result:
        return result
    verdict = _llm_judge(job, profile_text)
    return {"verdict": verdict["verdict"], "score": score, "reasoning": verdict["reasoning"]}


def _record(job, result, counts):
    db.set_pick_verdict(job.get("id"), result["verdict"], result["score"], result["reasoning"])
    counts["scored"] += 1
    log.info(f"[JOB-PICK] | {job.get('company')} | {job.get('role')} | verdict={result['verdict']} | score={result['score']}")
    if result["verdict"] != "strong":
        return
    if config.RESUME_CLAUDE_BACKEND == "subscription":
        # GitHub Actions never holds the subscription token; the Beelink's resume-worker.service
        # picks strong rows up (resume_agent.py --drain).
        counts["queued"] += 1
        log.info(f"[JOB-PICK] | {job.get('company')} | {job.get('role')} | queued for Beelink resume worker")
        return
    try:
        resume_agent.propose(job.get("id"))
        resume_agent.build(job.get("id"))
        counts["triggered"] += 1
    except Exception as exc:
        log.warning(f"[JOB-PICK] | {job.get('company')} | resume pipeline failed: {exc}")
        counts["pipeline_errors"] += 1


def _judge_failed(job, counts):
    attempts = (job.get("pick_attempts") or 0) + 1
    try:
        if attempts >= config.JOB_PICK_MAX_ATTEMPTS:
            _record(job, {"verdict": "maybe", "score": job.get("_score"),
                          "reasoning": f"fit judge failed {attempts} times -- needs human review"}, counts)
        else:
            db.set_pick_attempts(job.get("id"), attempts)
            counts["retry_later"] += 1
    except Exception as exc:
        log.warning(f"[JOB-PICK] | {job.get('company')} | could not record a failed judgment: {exc}")
        counts["errors"] += 1


def run():
    """Batch-score unscored job applications. Strong verdicts are queued for the Beelink resume worker (subscription backend) or built immediately (api backend)."""
    jobs = db.get_unscored_saved_applications(config.JOB_PICK_MAX_PER_RUN)
    log.info(f"[JOB-PICK] | START | jobs_to_score={len(jobs)}")
    counts = {"scored": 0, "triggered": 0, "queued": 0, "errors": 0, "pipeline_errors": 0, "retry_later": 0}
    prefs, eligibility = job_sourcing.load_search_settings()
    profile_text = _profile_text()
    profile_vec = _embed(profile_text) if profile_text.strip() else None

    to_judge = []
    for job in jobs:
        try:
            if not profile_text.strip():
                _record(job, {"verdict": "maybe", "score": None, "reasoning": _NO_PROFILE_REASONING}, counts)
                continue
            result, score = _prefilter(job, prefs, eligibility, profile_text, profile_vec)
            if result:
                _record(job, result, counts)
            else:
                to_judge.append(dict(job, _score=score))
        except Exception as exc:
            log.warning(f"[JOB-PICK] | {job.get('company')} | scoring error: {exc}")
            counts["errors"] += 1

    batch = config.JOB_PICK_JUDGE_BATCH
    for start in range(0, len(to_judge), batch):
        chunk = to_judge[start:start + batch]
        try:
            verdicts = _judge_batch(chunk, profile_text)
        except claude_subscription.ClaudeSubscriptionError as exc:
            log.warning(f"[JOB-PICK] | Claude subscription unavailable, stopping: {exc}")
            counts["errors"] += 1
            break
        for index, job in enumerate(chunk):
            verdict = verdicts.get(index)
            if not verdict:
                _judge_failed(job, counts)
                continue
            try:
                _record(job, {"verdict": verdict["verdict"], "score": job["_score"],
                              "reasoning": verdict["reasoning"]}, counts)
            except Exception as exc:
                log.warning(f"[JOB-PICK] | {job.get('company')} | scoring error: {exc}")
                counts["errors"] += 1

    log.info(f"[JOB-PICK] | DONE | scored={counts['scored']} | resume_triggered={counts['triggered']} | "
             f"resume_queued={counts['queued']} | retry_later={counts['retry_later']} | errors={counts['errors']} | "
             f"pipeline_errors={counts['pipeline_errors']}")

    if config.RESUME_CLAUDE_BACKEND != "subscription":
        return 0
    stale = 0
    try:
        stale = db.count_stale_strong_without_resume(config.RESUME_QUEUE_STALE_HOURS,
                                                     exclude_platforms=ats_platform.unpreparable_platforms())
    except Exception as exc:
        log.warning(f"[JOB-PICK] | WARNING | stale-queue check failed: {exc}")
        return 1
    if stale:
        log.warning(f"[JOB-PICK] | WARNING | strong rows waiting > {config.RESUME_QUEUE_STALE_HOURS}h for the Beelink worker: {stale}")
    return stale


if __name__ == "__main__":
    logging.basicConfig(
        filename="job_pick.log",
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M",
    )
    if run():
        sys.exit(1)
