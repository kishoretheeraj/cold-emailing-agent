"""Resume v2 delivery contract: upload the tailored PDF, write a cover letter, summarize a run.

Thin wrappers over db.py, resume_agent and resume_build where those exist. emit_run_digest has no
delivery channel yet and raises NotImplementedError rather than silently doing nothing.
"""

import logging
import os

import db

log = logging.getLogger("resume_v2")

_KIND_FILENAMES = {"resume": "resume.pdf", "cover_letter": "cover_letter.pdf"}
MAX_COVER_LETTER_ATTEMPTS = 2


# ── Upload ─────────────────────────────────────────────────────────────────────

def upload_pdf(application_id, pdf_path, kind="resume", title=None, keywords=""):
    """Upload a built PDF to the resumes bucket under a content-addressed, never-overwritten path.
    kind is "resume" or "cover_letter". With `title`, document metadata is scrubbed first
    (resume_scrub). Returns (storage_ref, sha256_hex); raises on failure, because a failed upload
    must not look like success."""
    if kind not in _KIND_FILENAMES:
        raise ValueError(f"unknown document kind: {kind}")
    if title:
        import resume_scrub
        resume_scrub.scrub_pdf_metadata(pdf_path, title=title, keywords=keywords)
    with open(pdf_path, "rb") as handle:
        data = handle.read()
    return db.upload_resume_file(f"resumes/{application_id}/{_KIND_FILENAMES[kind]}", data, "application/pdf")


# ── Cover letter ───────────────────────────────────────────────────────────────

def _content_text(content):
    parts = [content.get("headline", ""), content.get("summary", "")]
    for entry in content.get("experience") or []:
        parts.extend(entry.get("bullets") or [])
    return "\n".join(p for p in parts if p)


def generate_cover_letter(job, content, gaps=None):
    """Cover letter text for a job_applications row, grounded in the tailored resume `content`
    (tailor_v2's content_json). Reuses resume_agent's prompt, model call and lint; retries once with
    the violations. Every call is logged to api_usage_log. Raises ValueError if it still fails lint.
    Subscription errors propagate."""
    import config
    import resume_agent
    import usage_tracking
    billing = "subscription" if config.RESUME_CLAUDE_BACKEND == "subscription" else "api"
    prompt = resume_agent._COVER_LETTER_PROMPT.format(
        company=job.get("company"), role=job.get("role"),
        angle=content.get("summary") or content.get("headline") or "", gaps=gaps or [])
    resume_text = _content_text(content)
    violations = []
    for _ in range(MAX_COVER_LETTER_ATTEMPTS):
        text, usage = resume_agent._call_claude(prompt)
        usage_tracking.log_usage("resume_v2", "cover_letter", config.RESUME_MODEL, usage,
                                 job_application_id=job.get("id"), billing=billing)
        violations = resume_agent._lint_cover_letter(text, resume_text)
        if not violations:
            return text
        prompt = prompt + f"\n\nFix these violations from the previous draft: {violations}"
    raise ValueError(f"cover letter still fails lint: {violations}")


def render_cover_letter_pdf(text, workdir):
    """Render cover letter text to a PDF in workdir via resume_build (LibreOffice). Returns the path."""
    import resume_build
    docx_path = os.path.join(workdir, "cover_letter.docx")
    doc = resume_build.new_document()
    doc.add_paragraph(text)
    doc.save(docx_path)
    return resume_build.convert_to_pdf(docx_path, workdir)


# ── Run digest ─────────────────────────────────────────────────────────────────

def summarize_runs(runs):
    """Pure. application_runs rows -> {"total", "by_kind", "by_outcome", "failures": [(id, error_class)]}."""
    summary = {"total": 0, "by_kind": {}, "by_outcome": {}, "failures": []}
    for row in runs or []:
        summary["total"] += 1
        for field, bucket in (("kind", "by_kind"), ("outcome", "by_outcome")):
            key = row.get(field) or "unknown"
            summary[bucket][key] = summary[bucket].get(key, 0) + 1
        if str(row.get("outcome", "")).startswith("failed"):
            summary["failures"].append((row.get("application_id"), row.get("error_class")))
    return summary


def emit_run_digest(since_hours=24):
    """Deliver the digest of the last `since_hours` of runs (build it with summarize_runs over
    db.get_recent_application_runs)."""
    # TODO: pick a channel (Gmail draft to self vs notify_failure's mailer) before wiring a timer.
    raise NotImplementedError("run digest delivery is not implemented")
