"""
Fills real job application forms unattended, stops before Submit. Routes by
platform: Greenhouse/Ashby/Lever get the hand-mapped ats_fillers.py, anything
else with a real application page gets browser-use, Workday and aggregator
links are never attempted. Best-effort per row in the preview pass -- one
job's failure never blocks the batch. See
docs/superpowers/specs/2026-08-30-phase2.5-auto-apply-design.md.
"""

import json
import logging

import ats_fillers
import ats_platform
import config
import db
from emailer import _call_claude

log = logging.getLogger(__name__)

_MODE_TAGS = {"preview": "[APPLY-PREVIEW]", "submit": "[APPLY-SUBMIT]"}


# ── Field-value sources ────────────────────────────────────────────────────────

def _standard_field_values(job):
    return {
        "name": "Kishore Theeraj Vasudevan Jaya",
        "email": "kishoretheeraj@gmail.com",
        "phone": "+1 603-322-0535",
        "location": "Hanover, NH",
        "linkedin": "linkedin.com/in/kishoretheeraj",
    }


def _eligibility_answers():
    """Sensitive fixed-answer fields (work auth, sponsorship, EEO) -- never LLM-generated per
    application. Missing key degrades to an empty dict, never a guessed answer."""
    raw = db.load_prompts().get("applicant_eligibility")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception as exc:
        log.warning(f"[APPLY-AGENT] | applicant_eligibility failed to parse: {exc}")
        return {}


_SCREENING_PROMPT = """Answer this job application screening question, grounded only in real facts
about the candidate below. Never invent experience, projects, or numbers not listed. Keep it to
2-4 sentences.

Question: {question}

Candidate facts: {profile_summary}
"""


def _answer_screening_questions(page, job):
    answers = {}
    try:
        question_elements = page.get_by_text("?").all()
    except Exception:
        return answers

    for el in question_elements:
        try:
            question_text = el.inner_text()
        except Exception:
            continue
        try:
            answer = _call_claude(
                _SCREENING_PROMPT.format(question=question_text, profile_summary=job.get("role", "")),
                module="apply_agent", action="screening_question", contact_id=None,
            )
            answers[question_text] = answer
        except Exception as exc:
            log.info(f"[APPLY-AGENT] | screening question skipped: {exc}")
    return answers


def _attach_resume_and_cover_letter(page, job):
    # set_input_files works with real bytes headlessly -- no OS dialog, no third-party dependency.
    import tempfile

    client = db.get_client()
    for field_ref_key, label, ext in (
        ("resume_file_ref", "Resume", "pdf"),
        ("cover_letter_file_ref", "Cover Letter", "pdf"),
    ):
        storage_path = job.get(field_ref_key)
        if not storage_path:
            continue
        try:
            content = client.storage.from_(config.RESUME_STORAGE_BUCKET).download(storage_path)
            with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
                f.write(content)
                temp_path = f.name
            page.get_by_label(label).set_input_files(temp_path)
        except Exception as exc:
            log.info(f"[APPLY-AGENT] | {label} attach skipped: {exc}")


# ── Browser lifecycle (real Playwright launch -- mocked in every test) ───────────

def _launch_page(job_url):
    from playwright.sync_api import sync_playwright
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(job_url)
    return page


def _browser_use_agent_run(task_description, page):
    from browser_use import Agent
    from browser_use.llm import ChatAnthropic

    agent = Agent(task=task_description, llm=ChatAnthropic(model=config.JOB_PICK_MODEL), page=page)
    return agent.run_sync()


def _fill_generic_via_browser_use(page, job, field_values):
    # Task-string build failure (missing field_values key) or a browser-use library failure both
    # degrade to a warning here -- same best-effort posture as the labeling calls.
    try:
        task = (
            f"Fill in this job application form with: name={field_values['name']}, "
            f"email={field_values['email']}, phone={field_values['phone']}, "
            f"location={field_values['location']}, linkedin={field_values['linkedin']}. "
            f"Do not click any Submit or Apply button."
        )
        _browser_use_agent_run(task, page)
    except Exception as exc:
        log.warning(f"[APPLY-AGENT] | {job.get('company')} | browser-use failed: {exc}")


# ── Preview pass ───────────────────────────────────────────────────────────────

def _process_one_preview(job):
    job_id = job["id"]
    platform = ats_platform.classify(job.get("job_url"))

    if platform == "workday":
        db.set_apply_blocked(job_id, "workday -- permanently excluded, see spec's Rejected section")
        return "blocked"
    if platform == "aggregator":
        db.set_apply_blocked(job_id, "aggregator/listing link, not a real application page")
        return "blocked"

    page = _launch_page(job.get("job_url"))
    field_values = _standard_field_values(job)

    if platform in config.APPLY_AGENT_HAND_MAPPED_PLATFORMS:
        {"greenhouse": ats_fillers.fill_greenhouse,
         "ashby": ats_fillers.fill_ashby,
         "lever": ats_fillers.fill_lever}[platform](page, field_values)
    else:
        _fill_generic_via_browser_use(page, job, field_values)

    _attach_resume_and_cover_letter(page, job)
    screening_answers = _answer_screening_questions(page, job)

    preview = {
        "platform": platform,
        "field_values": field_values,
        "eligibility_answers": _eligibility_answers(),
        "screening_answers": screening_answers,
    }
    db.set_apply_preview(job_id, preview)
    return "filled"


def run_preview():
    jobs = [j for j in db.get_job_applications(stage="saved")
            if j.get("resume_file_ref") and j.get("cover_letter_file_ref")]
    log.info(f"[APPLY-PREVIEW] | START | eligible_jobs={len(jobs)}")
    filled = 0
    blocked = 0
    errors = 0

    for job in jobs:
        try:
            status = _process_one_preview(job)
            if status == "blocked":
                blocked += 1
            else:
                filled += 1
        except Exception as exc:
            log.warning(f"[APPLY-PREVIEW] | {job.get('company')} | error: {exc}")
            try:
                db.set_apply_blocked(job["id"], f"preview pass error: {exc}")
            except Exception:
                pass
            errors += 1

    log.info(f"[APPLY-PREVIEW] | DONE | filled={filled} | blocked={blocked} | errors={errors}")


# ── Submit pass ────────────────────────────────────────────────────────────────

_SUBMIT_BUTTON_NAME = "Submit Application"


def submit(job_id):
    """Re-fills a job_applications row's application form fresh and submits it -- but only when
    APPLY_AGENT_ARMED is exactly '1'. This env var must never be set anywhere except
    apply_agent_submit.yml's own job definition -- never a repo secret, never set in
    build-continue.yml, never set by a test, and never placed in .env -- config.load_dotenv()
    would arm a local run. See the CI-safety note in
    docs/superpowers/specs/2026-08-30-phase2.5-auto-apply-design.md."""
    import os

    job = db.get_job_application(job_id)
    platform = ats_platform.classify(job.get("job_url"))
    if platform in ("workday", "aggregator"):
        raise ValueError(f"submit() called on a permanently-excluded platform: {platform}")
    page = _launch_page(job.get("job_url"))
    field_values = _standard_field_values(job)

    if platform in config.APPLY_AGENT_HAND_MAPPED_PLATFORMS:
        {"greenhouse": ats_fillers.fill_greenhouse,
         "ashby": ats_fillers.fill_ashby,
         "lever": ats_fillers.fill_lever}[platform](page, field_values)
    else:
        # generic-platform fill runs an LLM browser agent against the real page before the ARMED
        # gate below -- restrained only by the task-string instruction not to click Submit, not a
        # hard guarantee. See the Phase 2.5 review notes.
        _fill_generic_via_browser_use(page, job, field_values)

    _attach_resume_and_cover_letter(page, job)
    _answer_screening_questions(page, job)

    if os.environ.get("APPLY_AGENT_ARMED") != "1":
        log.info(f"[APPLY-SUBMIT] | {job.get('company')} | not armed -- filled but did not submit")
        return

    page.get_by_role("button", name=_SUBMIT_BUTTON_NAME).click()
    db.update_job_application_stage(job_id, "applied")
    log.info(f"[APPLY-SUBMIT] | {job.get('company')} | submitted")


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        filename="apply_agent.log",
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--submit", type=int, default=None)
    args = parser.parse_args()

    if args.preview:
        run_preview()
    elif args.submit is not None:
        submit(args.submit)
    else:
        parser.error("pass --preview or --submit <id>")
