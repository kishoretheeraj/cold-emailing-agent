"""
Fills real job application forms unattended, stops before Submit. Routes by
platform: Greenhouse/Ashby/Lever get the hand-mapped ats_fillers.py, anything
else with a real application page gets browser-use, Workday and aggregator
links are never attempted. Best-effort per row in the preview pass -- one
job's failure never blocks the batch. See
docs/superpowers/specs/2026-08-30-phase2.5-auto-apply-design.md.
"""

import hashlib
import json
import logging
import re
from datetime import date

import ats_fillers
import ats_platform
import candidate_profile
import config
import db
from emailer import _call_claude

log = logging.getLogger(__name__)

_MODE_TAGS = {"preview": "[APPLY-PREVIEW]", "submit": "[APPLY-SUBMIT]"}


# ── Field-value sources ────────────────────────────────────────────────────────

def _standard_field_values(job):
    return {
        "name": "Kishore Theeraj Vasudevan Jaya",
        "email": "kishoretheerajvj@gmail.com",
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

# Merge review 2026-09-28, finding 3: a missing/empty candidate profile used to still get an
# LLM-generated answer, grounded in nothing but the job's own role title -- exactly what the
# prompt above claims never to do. Flag it for the human reviewing apply_preview instead of
# calling Claude with no real facts to ground an answer in.
_NO_PROFILE_ANSWER = "NEEDS HUMAN REVIEW -- no candidate profile text available to ground an answer in."


def _generate_screening_answers(page, job):
    """Finds on-page screening questions and generates grounded answers via Claude --
    generation only, this never fills the page. Called exactly once, in the preview pass;
    submit() must reuse the stored result via _fill_screening_questions instead of calling
    this again, so a submitted application always matches what the human reviewed in the
    preview rather than a freshly (and differently) generated answer."""
    answers = {}
    try:
        question_elements = page.get_by_text("?").all()
    except Exception:
        return answers

    # Merge review 2026-09-28, finding 3: this used to pass job.get("role", "") as
    # profile_summary -- a posting titled "Senior Product Manager" was presented back to the LLM
    # as the candidate's own facts. candidate_profile.profile_text() is the same real
    # experience/projects text job_pick.py's fit judge is grounded in.
    profile_summary = candidate_profile.profile_text()

    for el in question_elements:
        try:
            question_text = el.inner_text()
        except Exception:
            continue
        if not profile_summary.strip():
            answers[question_text] = _NO_PROFILE_ANSWER
            continue
        try:
            answer = _call_claude(
                _SCREENING_PROMPT.format(question=question_text, profile_summary=profile_summary),
                module="apply_agent", action="screening_question", contact_id=None,
            )
            answers[question_text] = answer
        except Exception as exc:
            log.info(f"[APPLY-AGENT] | screening question skipped: {exc}")
    return answers


def _set_field_by_label(page, label_pattern, value):
    """Best-effort generic field setter supporting the three control shapes a screening or
    eligibility question can actually be: a <select> dropdown, a radio-button group, or a
    plain text/textarea field. A generic filler can't know a question's control type in
    advance, so this tries each in turn and stops at the first that succeeds -- Playwright
    raises when a locator method doesn't apply to the element it resolved to (e.g.
    select_option() on a text input, fill() on a radio), which is exactly the signal used to
    fall through to the next strategy. Radios are scoped to the question's own
    role="group"/fieldset first, since page.get_by_role("radio", name=value) alone would grab
    the first same-labeled radio anywhere on the page if more than one Yes/No question is
    present. Returns True on success, False if no strategy worked; never raises."""
    # Each strategy re-resolves the locator independently (rather than sharing one `locator`
    # variable across all three) so a failure IN page.get_by_label() itself -- e.g. a strict-
    # mode violation from more than one match -- degrades to "try the next strategy" like any
    # other failure, instead of raising past this function entirely.
    try:
        page.get_by_label(label_pattern).select_option(label=str(value))
        return True
    except Exception:
        pass
    try:
        page.get_by_role("group", name=label_pattern).get_by_role("radio", name=str(value)).click()
        return True
    except Exception:
        pass
    try:
        page.get_by_label(label_pattern).fill(str(value))
        return True
    except Exception:
        pass
    return False


def _fill_screening_questions(page, answers):
    """Writes pre-computed screening-question answers into the page, by label -- best-effort
    per question via _set_field_by_label (a screening question can be a dropdown or a
    yes/no radio, not only free text). Used right after generation in the preview pass, and
    again during submit to replay a stored preview's answers verbatim."""
    for question_text, answer in (answers or {}).items():
        if not answer:
            continue
        if not _set_field_by_label(page, question_text, answer):
            log.info(f"[APPLY-AGENT] | screening field not fillable: {question_text[:60]!r}")


# Known applicant_eligibility keys -> the real on-page question wording those keys mean,
# matched as a case-insensitive substring/regex against the form's actual label text. The
# `applicant_eligibility` prompts key itself stays keyed by these stable internal names (the
# user edits it live via the contact-manager's Prompts page, and nothing here should force a
# migration of that live data) -- but a real ATS form never has a field literally labeled
# "work_authorized_us", so filling must go through this translation, not the raw key. An
# eligibility key with no entry here falls back to trying the raw key as the label (today's
# behavior) and is logged as unmapped, so a custom key the user adds still gets attempted
# rather than silently dropped.
_ELIGIBILITY_QUESTION_PATTERNS = {
    "work_authorized_us": re.compile(r"(legally )?authorized to work", re.IGNORECASE),
    "requires_visa_sponsorship": re.compile(r"require.{0,25}sponsorship", re.IGNORECASE),
    "gender": re.compile(r"\bgender\b", re.IGNORECASE),
    "race_ethnicity": re.compile(r"race|ethnicity", re.IGNORECASE),
    "veteran_status": re.compile(r"veteran", re.IGNORECASE),
    "disability_status": re.compile(r"disability", re.IGNORECASE),
}


def _fill_eligibility_answers(page, answers):
    """Writes the fixed EEO/work-authorization answers into the page -- translates each
    internal applicant_eligibility key to the real question wording it means
    (_ELIGIBILITY_QUESTION_PATTERNS) before locating the field, and fills via
    _set_field_by_label so a dropdown or radio-button EEO question (the overwhelmingly common
    shape for these specific questions) is handled, not only a text input. Same best-effort
    posture as _fill_screening_questions -- a field this generic filler can't locate is logged
    and skipped, never a blocking error."""
    for key, value in (answers or {}).items():
        if not value:
            continue
        pattern = _ELIGIBILITY_QUESTION_PATTERNS.get(key, key)
        if not _set_field_by_label(page, pattern, value):
            log.info(f"[APPLY-AGENT] | eligibility field not fillable: {key!r}")


# A rejection/validation-error message takes precedence over any confirmation match below --
# checked first, and short-circuits to "not confirmed" regardless of what else is on the page.
# Without this, "Your application is incomplete" / "is invalid" (real Greenhouse/Lever
# client-side validation copy) would otherwise need to NOT match the confirmation pattern by
# omission alone, which is exactly how the previous version's unanchored `...(complete|in)`
# alternative broke: "in" matched as a bare prefix of "incomplete"/"invalid" with no word
# boundary, so both rejection messages read as confirmed. An explicit, checked-first rejection
# list is a second, independent line of defense against that whole class of bug, not just a
# fix for this one regex.
_REJECTION_TEXT_PATTERN = re.compile(
    r"application is (incomplete|invalid)"
    r"|please (correct|complete|review|fix)"
    r"|this field is required"
    r"|could not (be )?(submitted|processed)"
    r"|something went wrong"
    r"|an error occurred",
    re.IGNORECASE,
)

_CONFIRMATION_TEXT_PATTERN = re.compile(
    r"\bapplication (has been |was )?(successfully )?(submitted|received)\b"
    r"|\bthank you for (applying|your application)\b"
    r"|\byour application (has been|was) (successfully )?(submitted|received)\b",
    re.IGNORECASE,
)


def _submission_confirmed(page):
    """Best-effort post-click confirmation check -- clicking Submit is not proof the
    application landed; a client-side validation error can leave the button's click handler
    a no-op with the form still on screen. Looks for common ATS post-submit copy after a
    short settle/navigation wait, checking the rejection pattern FIRST so validation-error
    copy can never read as a confirmation (see _REJECTION_TEXT_PATTERN). Never raises: a
    check failure degrades to "not confirmed", the safe direction, since submit() treats an
    unconfirmed click as a failed submission and does not flip the row to 'applied'."""
    try:
        page.wait_for_timeout(2000)
    except Exception:
        pass
    try:
        if page.get_by_text(_REJECTION_TEXT_PATTERN).count() > 0:
            return False
        return page.get_by_text(_CONFIRMATION_TEXT_PATTERN).count() > 0
    except Exception:
        return False


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

# Maps a launched page to the browser + driver behind it, so _close_page can tear the
# whole stack down. run_preview launches one per eligible row in a loop; without this,
# every Chromium and every driver process stays alive for the entire run.
_OPEN_SESSIONS = {}


# _launch_page opens Chromium with a local CDP debugging port so browser-use's own async
# Browser (see _browser_use_agent_run) can attach to the exact same running browser/tab that
# this synchronous Playwright page drives -- browser-use 0.1.x has no way to accept an existing
# playwright.sync_api.Page object directly, only a CDP URL (see merge review 2026-09-28, finding
# 2). Keyed the same way as _OPEN_SESSIONS.
_CDP_PORTS = {}


def _free_local_port():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _launch_page(job_url):
    from playwright.sync_api import sync_playwright
    playwright = sync_playwright().start()
    debug_port = _free_local_port()
    browser = playwright.chromium.launch(
        headless=True, args=[f"--remote-debugging-port={debug_port}"]
    )
    page = browser.new_page()
    page.goto(job_url)
    _OPEN_SESSIONS[id(page)] = (browser, playwright)
    _CDP_PORTS[id(page)] = debug_port
    return page


def _close_page(page):
    _CDP_PORTS.pop(id(page), None)
    browser, playwright = _OPEN_SESSIONS.pop(id(page), (None, None))
    for label, target in (("page", page), ("browser", browser), ("playwright", playwright)):
        closer = getattr(target, "stop" if label == "playwright" else "close", None)
        if closer is None:
            continue
        try:
            closer()
        except Exception as exc:
            log.info(f"[APPLY-AGENT] | {label} cleanup skipped: {exc}")


def _browser_use_agent_run(task_description, page):
    # Verified against the real installed browser-use==0.1.40 API (2026-09-28, merge review
    # finding 2) -- the version pinned in requirements-apply.txt. That release has no
    # browser_use.llm module (llm= takes a langchain BaseChatModel), no page= constructor arg
    # (only browser=/browser_context=), and Agent.run() is async-only (no run_sync()). All three
    # were previously wrong here and silently broke every generic-ATS fill.
    import asyncio

    from browser_use import Agent, Browser, BrowserConfig
    from langchain_anthropic import ChatAnthropic

    debug_port = _CDP_PORTS.get(id(page))
    if debug_port is None:
        raise RuntimeError(
            "no CDP debugging port recorded for this page -- it wasn't opened via _launch_page"
        )

    # _force_keep_browser_alive=True: this Browser wraps a CDP *client connection* to the same
    # Chromium process _launch_page already launched and _close_page already owns the lifecycle
    # of. Without this, agent_browser.close() below would tear down the real browser out from
    # under the rest of _process_one_preview()/submit(), which keep using `page` afterward
    # (resume attach, screening/eligibility fill).
    agent_browser = Browser(
        config=BrowserConfig(cdp_url=f"http://localhost:{debug_port}", _force_keep_browser_alive=True)
    )
    try:
        agent = Agent(
            task=task_description,
            llm=ChatAnthropic(model=config.JOB_PICK_MODEL),
            browser=agent_browser,
        )
        history = asyncio.run(agent.run())
    finally:
        asyncio.run(agent_browser.close())

    # Merge review 2026-09-28, finding 2: browser-use agents don't raise on a failed task -- they
    # just stop and report failure via the returned history. Silently treating that the same as
    # success is exactly how a row could reach ready_to_submit with an unfilled form. Raise so
    # the caller's existing failure-handling (release_application to failed_retryable in the
    # preview pass, failed_retryable/needs_confirmation in submit()) does its job instead of this being swallowed one level down.
    if not history.is_successful() or history.has_errors():
        raise RuntimeError(f"browser-use did not complete the task: {history.errors()}")
    return history


def _fill_generic_via_browser_use(page, job, field_values):
    # Merge review 2026-09-28, finding 2: this used to catch and swallow every failure here
    # (a missing field_values key, or any browser-use failure), so _process_one_preview() always
    # proceeded to mark the row ready_to_submit even when the generic fill produced an empty
    # form. Building the task string can still fail on a genuinely missing key -- that's a
    # programming error in _standard_field_values, not a best-effort external-service failure --
    # so it's allowed to raise too. The preview pass's and submit()'s own
    # exception handlers already release the lease (failed_retryable / needs_confirmation) on
    # any exception from this function; letting the failure propagate is what makes that existing safety net actually reachable.
    task = (
        f"Fill in this job application form with: name={field_values['name']}, "
        f"email={field_values['email']}, phone={field_values['phone']}, "
        f"location={field_values['location']}, linkedin={field_values['linkedin']}. "
        f"Do not click any Submit or Apply button."
    )
    _browser_use_agent_run(task, page)


# ── Form signature ─────────────────────────────────────────────────────────────

class FormChangedError(Exception):
    pass


# Never reads el.id: SPA frameworks generate ids like ":r3:" that differ on every load.
_FORM_FIELDS_JS = """() => {
  const skip = ['hidden', 'submit', 'button', 'reset', 'image'];
  const out = [];
  for (const el of document.querySelectorAll('input, select, textarea')) {
    if (skip.includes((el.type || '').toLowerCase()) || el.disabled) continue;
    const name = el.name || '';
    if (/captcha/i.test(name)) continue;
    const ident = name
      || el.getAttribute('aria-label')
      || (el.labels && el.labels[0] && el.labels[0].innerText)
      || el.placeholder
      || '';
    if (!ident.trim()) continue;
    out.push((el.type || el.tagName.toLowerCase()) + ':' + ident);
  }
  return out;
}"""


def _form_signature(page):
    try:
        page.wait_for_selector("input, select, textarea", timeout=15000)
        raw = page.evaluate(_FORM_FIELDS_JS)
        idents = sorted({re.sub(r"\s+", " ", str(x)).strip().lower() for x in (raw or [])})
        if not idents:
            return None
        return hashlib.sha256(json.dumps(idents).encode()).hexdigest()
    except Exception as exc:
        log.warning(f"[APPLY-AGENT] | form signature failed: {exc}")
        return None


# ── Preview pass ───────────────────────────────────────────────────────────────

def _process_one_preview(job):
    job_id = job["id"]
    platform = ats_platform.classify(job.get("job_url"))

    if platform == "workday":
        db.mark_unsupported(job_id, "workday -- permanently excluded, see spec's Rejected section")
        return "blocked"
    if platform == "aggregator":
        db.mark_unsupported(job_id, "aggregator/listing link, not a real application page")
        return "blocked"

    lease = db.claim_application(job_id, "preparing")
    if lease is None:
        log.info(f"[APPLY-PREVIEW] | {job.get('company')} | skipped: row not claimable (another worker holds it)")
        return "skipped"

    try:
        page = _launch_page(job.get("job_url"))
        try:
            signature = _form_signature(page)
            if not signature:
                raise ValueError("Could not fingerprint the application form; preview must be prepared again")
            db.heartbeat_application(job_id, lease)
            field_values = _standard_field_values(job)

            if platform in config.APPLY_AGENT_HAND_MAPPED_PLATFORMS:
                {"greenhouse": ats_fillers.fill_greenhouse,
                 "ashby": ats_fillers.fill_ashby,
                 "lever": ats_fillers.fill_lever}[platform](page, field_values)
            else:
                _fill_generic_via_browser_use(page, job, field_values)
            db.heartbeat_application(job_id, lease)

            _attach_resume_and_cover_letter(page, job)
            db.heartbeat_application(job_id, lease)
            screening_answers = _generate_screening_answers(page, job)
            _fill_screening_questions(page, screening_answers)
            db.heartbeat_application(job_id, lease)
            eligibility_answers = _eligibility_answers()
            _fill_eligibility_answers(page, eligibility_answers)

            preview = {
                "platform": platform,
                "field_values": field_values,
                "eligibility_answers": eligibility_answers,
                "screening_answers": screening_answers,
            }
            if not db.complete_preview(job_id, lease, preview, signature):
                log.warning(f"[APPLY-PREVIEW] | {job.get('company')} | lease lost, preview discarded "
                            f"(row was recovered by lease recovery)")
                return "lost"
            return "filled"
        finally:
            _close_page(page)
    except Exception as exc:
        try:
            db.release_application(job_id, lease, "failed_retryable", f"preview pass error: {exc}")
        except Exception:
            pass
        raise


def run_preview():
    db.recover_stale_leases(config.APPLY_AGENT_LEASE_STALE_SECONDS)
    jobs = [j for j in db.get_job_applications(stage="saved")
            if j.get("resume_file_ref") and j.get("cover_letter_file_ref")
            and j.get("automation_status", "idle") in config.APPLY_AGENT_PREVIEW_ELIGIBLE_STATUSES]
    log.info(f"[APPLY-PREVIEW] | START | eligible_jobs={len(jobs)}")
    filled = 0
    blocked = 0
    skipped = 0
    errors = 0

    for job in jobs:
        try:
            status = _process_one_preview(job)
            if status == "blocked":
                blocked += 1
            elif status == "skipped":
                skipped += 1
            elif status == "lost":
                errors += 1
            else:
                filled += 1
        except Exception as exc:
            log.warning(f"[APPLY-PREVIEW] | {job.get('company')} | error: {exc}")
            errors += 1

    log.info(f"[APPLY-PREVIEW] | DONE | filled={filled} | blocked={blocked} | skipped={skipped} | errors={errors}")


# ── Submit pass ────────────────────────────────────────────────────────────────

_SUBMIT_BUTTON_NAME = "Submit Application"


def submit(job_id):
    """Re-fills a job_applications row's application form fresh and submits it -- but only when
    APPLY_AGENT_ARMED is exactly '1'. This env var must never be set anywhere except
    apply_agent_submit.yml's own job definition -- never a repo secret, never set in
    build-continue.yml, and never placed in .env -- config.load_dotenv() would arm a local run.
    The one exception is transiently inside a test, via mocker.patch.dict, where _launch_page
    and db are mocked so no real page can be reached -- that coverage is what proves the armed
    path actually clicks Submit and flips the stage, so deleting it to satisfy the letter of
    this rule would make the gate strictly less safe, not more. See the CI-safety note in
    docs/superpowers/specs/2026-08-30-phase2.5-auto-apply-design.md."""
    import os

    db.recover_stale_leases(config.APPLY_AGENT_LEASE_STALE_SECONDS)

    job = db.get_job_application(job_id)
    if not job:
        raise ValueError(f"submit() called on a nonexistent job_applications row: id={job_id}")

    platform = ats_platform.classify(job.get("job_url"))
    if platform in ("workday", "aggregator"):
        reason = f"submit() called on a permanently-excluded platform: {platform}"
        log.warning(f"[APPLY-SUBMIT] | {job.get('company')} | error: {reason}")
        try:
            db.mark_unsupported(job_id, reason)
        except Exception:
            pass
        raise ValueError(reason)

    lease = db.claim_application(job_id, "submitting")
    if lease is None:
        raise ValueError(
            f"submit() could not claim job_id={job_id}: row is not in automation_status 'approved' "
            f"or another worker holds it"
        )

    # True from the line before the Submit click onward: the site may have the application, so any
    # failure after that is needs_confirmation (never auto-retryable), not failed_retryable.
    clicked = False
    # C1: everything below runs only once this worker holds the lease. Any exception past this
    # point releases the row with a status and reason (so ApplicationsPage.tsx can show it) and
    # re-raises -- submit() is a single-row armed action, not a batch, so it must still fail loudly.
    try:
        # Re-read after the claim: the gate must judge the row we now hold, not the pre-claim copy.
        job = db.get_job_application(job_id)
        if not job:
            raise ValueError(f"submit() row vanished after claim: id={job_id}")

        # The ARMED gate proves a human tapped *something*; this proves they approved *this row*.
        # Without it, any id reaching the workflow gets submitted -- a stale id, a mistyped manual
        # workflow_dispatch, or a row that was never previewed (no eligibility answers, no screening
        # answers, possibly no resume) would go to a real employer. approved_at (Task 1) closes the
        # last gap: it can only ever be set via the approve_application RPC, which a human actually
        # tapping "Approve & Submit" in the UI triggers -- so this is the one condition here that
        # can't be satisfied by a stale id or a mistyped manual workflow_dispatch alone.
        if (
            job.get("stage") != "ready_to_submit"
            or not job.get("apply_preview")
            or not job.get("approved_at")
        ):
            raise ValueError(
                f"submit() called on an unapproved row: id={job_id} | stage={job.get('stage')} | "
                f"has_preview={bool(job.get('apply_preview'))} | "
                f"approved={bool(job.get('approved_at'))}"
            )
        # Added conditions: we really hold the lease, and the human approved the exact revision
        # that is about to be submitted (the DB trigger owns preview_revision_hash).
        if (
            job.get("automation_status") != "submitting"
            or not job.get("approved_revision_hash")
            or job.get("approved_revision_hash") != job.get("preview_revision_hash")
        ):
            raise ValueError(
                f"submit() approval does not match the current preview: id={job_id} | "
                f"automation_status={job.get('automation_status')} | "
                f"hash_match={job.get('approved_revision_hash') == job.get('preview_revision_hash')}"
            )

        page = _launch_page(job.get("job_url"))
        try:
            expected_signature = job.get("form_signature")
            if not expected_signature:
                log.warning(f"[APPLY-SUBMIT] | {job.get('company')} | no preview form_signature, drift check skipped")
            elif _form_signature(page) != expected_signature:
                raise FormChangedError("Form changed after approval")
            field_values = _standard_field_values(job)

            if platform in config.APPLY_AGENT_HAND_MAPPED_PLATFORMS:
                {"greenhouse": ats_fillers.fill_greenhouse,
                 "ashby": ats_fillers.fill_ashby,
                 "lever": ats_fillers.fill_lever}[platform](page, field_values)
            else:
                # generic-platform fill runs an LLM browser agent against the real page before
                # the ARMED gate below -- restrained only by the task-string instruction not to
                # click Submit, not a hard guarantee. See the Phase 2.5 review notes.
                _fill_generic_via_browser_use(page, job, field_values)
            db.heartbeat_application(job_id, lease)

            _attach_resume_and_cover_letter(page, job)
            # Reuse the stored preview's answers verbatim -- never regenerate here. The human
            # approved what's in apply_preview when they tapped "Approve & Submit"; a fresh
            # Claude call at submit time could produce a different answer than the one they saw,
            # and any screening/eligibility question the preview pass couldn't fill would
            # otherwise go out blank instead of being retried from the same known values.
            preview = job.get("apply_preview") or {}
            _fill_screening_questions(page, preview.get("screening_answers"))
            _fill_eligibility_answers(page, preview.get("eligibility_answers"))
            db.heartbeat_application(job_id, lease)

            if os.environ.get("APPLY_AGENT_ARMED") != "1":
                log.info(f"[APPLY-SUBMIT] | {job.get('company')} | not armed -- filled but did not submit")
                db.release_application(job_id, lease, "approved")
                return

            # Filling may take minutes. A lease recovered during that time, or a document
            # rebuild invalidating the revision, must stop this worker before the external
            # action. Best-effort progress heartbeats cannot establish that permission.
            if not db.renew_submission_lease(job_id, lease, job["approved_revision_hash"]):
                raise RuntimeError("Submit stopped: worker lease or approved revision changed during preparation")

            # from here on the site may have the application -- any failure is needs_confirmation, never retryable.
            clicked = True
            page.get_by_role("button", name=_SUBMIT_BUTTON_NAME).click()
            # The click succeeding is not proof the application landed -- a client-side validation
            # error commonly leaves the button's own click handler a no-op with the form still on
            # screen. Do not advance the stage until the site itself confirms it.
            if not _submission_confirmed(page):
                raise RuntimeError(
                    f"submit() clicked Submit for job_id={job_id} ({job.get('company')}) but found "
                    f"no confirmation on the page afterward -- treating this as a failed submission "
                    f"and leaving the stage unchanged so a human can investigate before any retry."
                )
            recorded = db.record_submission(job_id, lease, platform, date.today().isoformat())
            if not recorded:
                log.warning(
                    f"[APPLY-SUBMIT] | {job.get('company')} | submission confirmed but our lease was "
                    f"already recovered as stale -- row was already moved to needs_confirmation by lease recovery"
                )
            else:
                log.info(f"[APPLY-SUBMIT] | {job.get('company')} | submitted")
        finally:
            _close_page(page)
    except Exception as exc:
        log.warning(f"[APPLY-SUBMIT] | {job.get('company') if job else job_id} | error: {exc}")
        to_status = ("needs_confirmation" if clicked
                     else "needs_input" if isinstance(exc, FormChangedError) else "failed_retryable")
        try:
            db.release_application(job_id, lease, to_status, str(exc))
        except Exception:
            pass
        raise


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
