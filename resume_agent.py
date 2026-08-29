"""
Orchestrates the resume/cover-letter generation pipeline for one job_applications row.
Manual, interactive, two-command CLI -- see docs/superpowers/specs/2026-08-29-
phase3-resume-intelligence-design.md for the full design and why this can't run
unattended.

Usage:
  python3 resume_agent.py --job-id 42 --propose
  python3 resume_agent.py --job-id 42 --build
"""

import argparse
import datetime
import json
import logging
import os

import anthropic
from docx import Document

import config
import db
import resume_build
import resume_lint
import resume_scrub

log = logging.getLogger(__name__)

_claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=4)

_DATA_DIR = os.path.join(os.path.dirname(__file__), "resume", "data")


def _load_data(name):
    with open(os.path.join(_DATA_DIR, name)) as f:
        return json.load(f)


class DeadlinePassedError(Exception):
    """Raised when a job's posted deadline has already passed -- refuse to build."""


class LintFailedError(Exception):
    """Raised when a cover letter or resume fails lint checks after the one allowed retry."""


# ── Claude client ────────────────────────────────────────────────────────────────

def _call_claude(prompt, system=None):
    """Returns (text, usage) -- usage is {"input_tokens": int, "output_tokens": int} from the
    real API response, consumed by _track_usage to accumulate cost onto job_applications."""
    kwargs = dict(
        model=config.RESUME_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    if system:
        kwargs["system"] = system
    resp = _claude.messages.create(**kwargs)
    usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    return resp.content[0].text, usage


def _calculate_cost(usage):
    return (usage["input_tokens"] / 1_000_000 * config.RESUME_MODEL_COST_PER_MTOK_INPUT
            + usage["output_tokens"] / 1_000_000 * config.RESUME_MODEL_COST_PER_MTOK_OUTPUT)


def _track_usage(application_id, usage):
    cost = _calculate_cost(usage)
    db.record_resume_usage(application_id, usage["input_tokens"], usage["output_tokens"], cost)
    return cost


# ── Deadline gate (corpus spec Part 11: Cott/McKinsey lesson) ──────────────────

def _check_deadline(job):
    deadline_str = (job.get("posting_snapshot") or {}).get("deadline")
    if not deadline_str:
        return True
    deadline = datetime.date.fromisoformat(deadline_str[:10])
    return deadline >= datetime.date.today()


def _strip_json_fence(text):
    """Claude sometimes wraps a JSON response in a markdown code fence despite being told not
    to. Same pattern as research.py's _generate_queries -- strip it before parsing."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return text


# ── Stage 0-4: propose ──────────────────────────────────────────────────────────

_STRATEGY_PROMPT = """You are diagnosing a job posting and proposing a resume strategy.
Company: {company}
Role: {role}
Posting details: {posting_snapshot}

Follow this process:
1. Diagnose the role type and the 8-12 capabilities the posting screens for.
2. Identify the top 3 matches and 2 honest gaps against a Product Manager background.
3. Propose a strategy: section order, which projects to include (max 3), a one-line
   cover letter angle, and the honest gaps to name rather than hide.

Respond with ONLY a JSON object, no other text:
{{"section_order": [...], "projects_included": [...], "cover_letter_angle": "...", "named_gaps": [...]}}"""


def propose(application_id):
    """Run stages 0-4 (load context, diagnose, research, strategy) for one job_applications row.
    Writes the resulting strategy to job_applications.resume_strategy. Builds nothing. Raises
    ValueError if the row doesn't exist or Claude's response can't be parsed, DeadlinePassedError
    if the posting's deadline has already passed."""
    job = db.get_job_application(application_id)
    if job is None:
        raise ValueError(f"job_applications row {application_id} not found")
    if not _check_deadline(job):
        raise DeadlinePassedError(
            f"job_applications row {application_id}'s deadline has passed -- refusing to build"
        )

    prompt = _STRATEGY_PROMPT.format(
        company=job.get("company"), role=job.get("role"),
        posting_snapshot=json.dumps(job.get("posting_snapshot") or {}),
    )
    raw, usage = _call_claude(prompt)
    try:
        strategy = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"could not parse strategy from Claude's response: {exc}") from exc

    cost = _track_usage(application_id, usage)
    db.set_resume_strategy(application_id, strategy)
    log.info(
        f"[RESUME] | {application_id} | {job.get('company')} | strategy proposed | "
        f"tokens={usage['input_tokens']}in/{usage['output_tokens']}out | cost=${cost:.4f}"
    )
    return strategy


def run_propose(application_id):
    strategy = propose(application_id)
    print(json.dumps(strategy, indent=2))
    print(f"\nStrategy written to job_applications.resume_strategy for row {application_id}.")
    print("Review it, then run with --build to generate the documents.")


# ── Stage 5-9: build ─────────────────────────────────────────────────────────────

_COVER_LETTER_PROMPT = """Write a cover letter for {company}, {role}.
Cover letter angle: {angle}
Named gaps to acknowledge honestly, not hide: {gaps}

Rules: do not restate resume bullets in prose. Open with a specific moment, not a
generic statement. Around 250-290 words. No em dashes. End with one concrete,
scoped 90-day item."""


def _resolve_master(master, metrics, strategy):
    """Resolve master.json's bullet_ids (referencing metrics.json entries) into literal bullet
    text, which is the shape resume_build.build_docx expects. All roles are included
    unconditionally; projects are filtered to strategy["projects_included"]."""
    metrics_by_id = {m["id"]: m["text"] for m in metrics}
    resolved = {"roles": [], "education": master.get("education"), "projects": {}}
    for role in master.get("roles", []):
        resolved["roles"].append({
            "title": role["title"], "company": role["company"], "period": role["period"],
            "bullets": [metrics_by_id[bid] for bid in role.get("bullet_ids", []) if bid in metrics_by_id],
        })
    for name in strategy.get("projects_included", []):
        project = master.get("projects", {}).get(name)
        if project is None:
            continue
        resolved["projects"][name] = {
            "bullets": [metrics_by_id[bid] for bid in project.get("bullet_ids", []) if bid in metrics_by_id],
        }
    return resolved


def _resume_content_text(resolved_master):
    bullets = [b for role in resolved_master["roles"] for b in role["bullets"]]
    bullets += [b for p in resolved_master["projects"].values() for b in p["bullets"]]
    return " ".join(bullets)


def _lint_cover_letter(cl_text, resume_text):
    violations = []
    violations += resume_lint.check_em_dashes(cl_text)
    violations += resume_lint.check_jargon(cl_text, _load_data("jargon.json"))
    violations += resume_lint.check_cover_letter(cl_text, resume_text)
    return violations


def build(application_id):
    """Run stages 5-9 (build, humanize/lint, scrub, upload) for a job_applications row that
    already has a proposed resume_strategy. Returns {"resume_file_ref": ..., "cover_letter_file_ref": ...}.
    Raises ValueError if no strategy has been proposed yet, LintFailedError if the resume content
    references an unresolved metric conflict (no retry -- the data is static, retrying changes
    nothing until the user edits resume/data/metrics.json) or if the cover letter still fails lint
    after one retry, resume_build.StillOverflowError if the resume can't be formatted to one page
    after one content-editing retry."""
    job = db.get_job_application(application_id)
    strategy = job.get("resume_strategy")
    if not strategy:
        raise ValueError(f"job_applications row {application_id} has no resume_strategy -- run --propose first")

    master_raw = _load_data("master.json")
    metrics = _load_data("metrics.json")
    jargon = _load_data("jargon.json")
    master = _resolve_master(master_raw, metrics, strategy)

    resume_text = _resume_content_text(master)
    resume_violations = resume_lint.check_metrics_whitelist(resume_text, metrics)
    resume_violations += resume_lint.check_jargon(resume_text, jargon)
    if resume_violations:
        raise LintFailedError(f"resume content fails lint: {resume_violations}")

    pdf_path = None
    for attempt in range(config.RESUME_MAX_BUILD_RETRIES + 1):
        try:
            pdf_path, preset_used = resume_build.fit_to_one_page(
                strategy, master, f"/tmp/resume_{application_id}.docx", "/tmp",
            )
            break
        except resume_build.StillOverflowError:
            if attempt >= config.RESUME_MAX_BUILD_RETRIES:
                raise
            log.warning(f"[RESUME] | {application_id} | still overflows one page, retrying once")

    resume_scrub.scrub_pdf_metadata(
        pdf_path, title=f"{job.get('company')} - Resume", keywords=job.get("role", ""),
    )
    resume_fingerprints = resume_scrub.verify_no_fingerprints(resume_scrub.read_pdf_metadata_text(pdf_path))
    if resume_fingerprints:
        raise LintFailedError(f"resume PDF metadata still contains fingerprints: {resume_fingerprints}")

    cl_prompt = _COVER_LETTER_PROMPT.format(
        company=job.get("company"), role=job.get("role"),
        angle=strategy.get("cover_letter_angle", ""), gaps=strategy.get("named_gaps", []),
    )

    cl_text = None
    for attempt in range(config.RESUME_MAX_BUILD_RETRIES + 1):
        cl_text, usage = _call_claude(cl_prompt)
        _track_usage(application_id, usage)
        violations = _lint_cover_letter(cl_text, resume_text)
        if not violations:
            break
        if attempt >= config.RESUME_MAX_BUILD_RETRIES:
            raise LintFailedError(f"cover letter still fails lint after retry: {violations}")
        log.warning(f"[RESUME] | {application_id} | cover letter lint failed, retrying once: {violations}")
        cl_prompt = cl_prompt + f"\n\nFix these violations from the previous draft: {violations}"

    cl_docx_path = f"/tmp/cover_letter_{application_id}.docx"
    cl_doc = Document()
    cl_doc.add_paragraph(cl_text)
    cl_doc.save(cl_docx_path)
    cl_pdf_path = resume_build.convert_to_pdf(cl_docx_path, "/tmp")
    resume_scrub.scrub_pdf_metadata(
        cl_pdf_path, title=f"{job.get('company')} - Cover Letter", keywords=job.get("role", ""),
    )
    cl_fingerprints = resume_scrub.verify_no_fingerprints(resume_scrub.read_pdf_metadata_text(cl_pdf_path))
    if cl_fingerprints:
        raise LintFailedError(f"cover letter PDF metadata still contains fingerprints: {cl_fingerprints}")

    with open(pdf_path, "rb") as f:
        resume_ref = db.upload_resume_file(f"resumes/{application_id}/resume.pdf", f.read(), "application/pdf")
    with open(cl_pdf_path, "rb") as f:
        cl_ref = db.upload_resume_file(f"resumes/{application_id}/cover_letter.pdf", f.read(), "application/pdf")

    db.set_resume_files(application_id, resume_file_ref=resume_ref, cover_letter_file_ref=cl_ref)
    log.info(f"[RESUME] | {application_id} | {job.get('company')} | build complete")
    return {"resume_file_ref": resume_ref, "cover_letter_file_ref": cl_ref}


def run_build(application_id):
    result = build(application_id)
    print(f"Resume: {result['resume_file_ref']}")
    print(f"Cover letter: {result['cover_letter_file_ref']}")


if __name__ == "__main__":
    logging.basicConfig(
        filename="resume_agent.log",
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", type=int, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--propose", action="store_true")
    mode.add_argument("--build", action="store_true")
    args = parser.parse_args()

    if args.propose:
        run_propose(args.job_id)
    elif args.build:
        run_build(args.job_id)
