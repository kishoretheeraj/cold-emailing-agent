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
from datetime import date, datetime, timezone
from urllib.parse import urlparse

import approval_signature
import ats_fillers
import ats_sessions
import ats_platform
import candidate_profile
import claude_subscription
import config
import credential_vault
import db
import email_verification
import takeover
import usage_tracking
import workday_adapter
from emailer import _call_claude

log = logging.getLogger(__name__)

_MODE_TAGS = {"preview": "[APPLY-PREVIEW]", "submit": "[APPLY-SUBMIT]"}


# ── Field-value sources ────────────────────────────────────────────────────────

def _standard_field_values(job):
    return {
        "name": "Kishore Theeraj Vasudevan Jaya",
        "first_name": "Kishore Theeraj",
        "last_name": "Vasudevan Jaya",
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


_SCREENING_PROMPT = """Answer this job application question, grounded only in real facts about the
candidate below. Never invent experience, projects, or numbers not listed.
- If the question asks for a short fact (a name, employer, title, city, date, yes/no), reply with
  just that fact.
- Otherwise keep it to 2-4 sentences.
- If the facts below don't contain the answer (for example a desired salary or a start date),
  reply exactly: NEEDS HUMAN REVIEW
{options_block}
Question: {question}

Candidate facts: {profile_summary}
"""

_OPTIONS_BLOCK = """- This question has fixed choices. Reply with exactly one of them, verbatim:
{options}
"""

# Merge review 2026-09-28, finding 3: a missing/empty candidate profile used to still get an
# LLM-generated answer, grounded in nothing but the job's own role title -- exactly what the
# prompt above claims never to do. Flag it for the human reviewing apply_preview instead of
# calling Claude with no real facts to ground an answer in.
_NO_PROFILE_ANSWER = "NEEDS HUMAN REVIEW -- no candidate profile text available to ground an answer in."
_NEEDS_REVIEW_PREFIX = "NEEDS HUMAN REVIEW"


# ── Form inventory ─────────────────────────────────────────────────────────────

# Field-driven, not text-driven: the first live preview (2026-10-06) found questions with
# page.get_by_text("?"), which picked up page headings ("What You'll Do?"), missed questions
# without a "?" ("Desired salary*"), and then could not locate any control by that text. This
# reads every real control instead: its question label, kind, required flag, options, whether it
# currently holds a value, and a selector to reach it again. Values are never returned.
_FORM_INVENTORY_JS = r"""() => {
  const clean = (t) => (t || '').replace(/\s+/g, ' ').replace(/\s*\*\s*$/, '').trim().slice(0, 300);
  const starred = (t) => /\*\s*$/.test((t || '').trim());
  const textOf = (ids) => (ids || '').split(/\s+/).map((i) => {
    const n = document.getElementById(i); return n ? n.innerText : '';
  }).join(' ');
  const visible = (el) => !!(el.offsetParent || el.getClientRects().length);
  const sel = (el) => el.id ? '#' + CSS.escape(el.id)
    : (el.name ? el.tagName.toLowerCase() + '[name="' + el.name.replace(/"/g, '\\"') + '"]' : null);
  const rawLabel = (el) => {
    if (el.getAttribute('aria-labelledby')) return textOf(el.getAttribute('aria-labelledby'));
    if (el.labels && el.labels[0]) return el.labels[0].innerText;
    return el.getAttribute('aria-label') || el.placeholder || el.name || el.id || '';
  };
  const groupLabel = (el) => {
    const g = el.closest('fieldset, [role=radiogroup], [role=group]');
    if (!g) {
      // A lone checkbox ("I agree to ...") is its own question; a radio's own label is an option.
      if (el.type === 'checkbox' && el.labels && el.labels[0]) return el.labels[0].innerText;
      return el.name || '';
    }
    if (g.getAttribute('aria-labelledby')) return textOf(g.getAttribute('aria-labelledby'));
    if (g.getAttribute('aria-label')) return g.getAttribute('aria-label');
    const legend = g.querySelector('legend');
    return legend ? legend.innerText : (el.name || '');
  };
  const skip = ['hidden', 'submit', 'button', 'reset', 'image'];
  const out = [];
  const groups = {};
  for (const el of document.querySelectorAll('input, select, textarea')) {
    const type = (el.type || '').toLowerCase();
    if (skip.includes(type) || el.disabled) continue;
    if (/captcha/i.test((el.name || '') + ' ' + (el.id || ''))) continue;
    if (type !== 'file' && !visible(el)) continue;
    if (type === 'radio' || type === 'checkbox') {
      const key = 'group:' + (el.name || el.id);
      const optLabel = clean(el.labels && el.labels[0] ? el.labels[0].innerText : el.value);
      if (!groups[key]) {
        const raw = groupLabel(el);
        groups[key] = {key, kind: type, label: clean(raw), required: false, filled: false,
                       options: [], option_selectors: [], selector: sel(el)};
        groups[key]._starred = starred(raw);
        out.push(groups[key]);
      }
      const g = groups[key];
      g.options.push(optLabel);
      // A name-only radio's sel() is the whole group's; the value tells its options apart.
      g.option_selectors.push(el.id ? sel(el) : (el.name
        ? 'input[name="' + el.name.replace(/"/g, '\\"') + '"][value="' + (el.value || '').replace(/"/g, '\\"') + '"]'
        : null));
      g.required = g.required || el.required || el.getAttribute('aria-required') === 'true' || g._starred;
      g.filled = g.filled || el.checked;
      continue;
    }
    const raw = rawLabel(el);
    const f = {key: sel(el) || ('field:' + out.length), selector: sel(el), label: clean(raw),
               required: el.required || el.getAttribute('aria-required') === 'true' || starred(raw),
               options: [], kind: el.tagName.toLowerCase(), filled: false};
    if (type === 'file') { f.kind = 'file'; f.filled = el.files && el.files.length > 0; }
    else if (el.tagName === 'SELECT') {
      f.options = Array.from(el.options).filter((o) => o.value !== '').map((o) => clean(o.text));
      f.filled = el.value !== '';
    } else if (el.getAttribute('role') === 'combobox') {
      f.kind = 'combobox';
      const box = el.closest('[class*="container"]') || (el.parentElement && el.parentElement.parentElement);
      f.filled = el.value.trim() !== '' || !!(box && box.querySelector(
        '[class*="single-value"], [class*="singleValue"], [class*="multi-value"], [class*="multiValue"]'));
    } else { f.filled = (el.value || '').trim() !== ''; }
    out.push(f);
  }
  // Workday-style listboxes: a button that opens a role=listbox popup. Options are read when it
  // is opened (_fill_field), so only the question, the required flag and the current text count.
  for (const el of document.querySelectorAll('button[aria-haspopup="listbox"]')) {
    if (el.disabled || !visible(el)) continue;
    const raw = rawLabel(el);
    const text = (el.innerText || '').trim();
    out.push({key: sel(el) || ('listbox:' + out.length), selector: sel(el), label: clean(raw),
              kind: 'listbox', options: [],
              required: el.getAttribute('aria-required') === 'true' || starred(raw),
              filled: text !== '' && !/^select one$/i.test(text)});
  }
  for (const g of Object.values(groups)) delete g._starred;
  return out;
}"""


def _form_inventory(page):
    try:
        fields = page.evaluate(_FORM_INVENTORY_JS)
    except Exception as exc:
        log.warning(f"[APPLY-AGENT] | form inventory failed: {exc}")
        return None
    if not isinstance(fields, list):
        return None
    return [f for f in fields if isinstance(f, dict) and f.get("label")]


def _norm_label(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().rstrip("*").strip().lower()


def _required_unfilled(inventory):
    if inventory is None:
        return ["form questions (the form could not be read)"]
    return [f["label"] for f in inventory if f.get("required") and not f.get("filled")]


# Forms word "decline" many ways ("Decline To Self Identify", "I don't wish to answer").
_DECLINE_OPTION = re.compile(r"decline|don.?t wish|do not wish|prefer not|not to (say|answer|disclose)"
                             r"|choose not|rather not", re.IGNORECASE)


def _norm_option(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _pick_option(options, value):
    want = _norm_option(value)
    if not want:
        return None
    normed = [(o, _norm_option(o)) for o in (options or []) if _norm_option(o)]
    for checks in (lambda o: o == want, lambda o: o.startswith(want), lambda o: want.startswith(o),
                   lambda o: want in o):
        for original, o in normed:
            if checks(o):
                return original
    if _DECLINE_OPTION.search(str(value)):
        for original, o in normed:
            if _DECLINE_OPTION.search(original):
                return original
    return None


def _listbox_options(page, field):
    # Opens the popup only to read its options, then closes it again.
    timeout = config.APPLY_AGENT_FIELD_TIMEOUT_MS
    try:
        page.locator(field["selector"]).first.click(timeout=timeout)
        options = page.locator("[role='listbox']:visible [role='option']")
        options.first.wait_for(state="visible", timeout=timeout)
        labels = [t.strip() for t in options.all_inner_texts() if t.strip()]
    except Exception:
        labels = []
    page.keyboard.press("Escape")
    if page.locator("[role='listbox']:visible").count():
        page.mouse.click(1, 1)
    return labels


def _pick_listbox_option(page, field, value, timeout):
    # Open the popup, choose the option that matches exactly as _pick_option rules allow, and
    # confirm the button now shows it. No match closes the popup and reports False: a listbox
    # is never answered with a guess.
    button = page.locator(field["selector"]).first
    button.click(timeout=timeout)
    options = page.locator("[role='listbox']:visible [role='option']")
    try:
        options.first.wait_for(state="visible", timeout=timeout)
    except Exception:
        return False
    labels = [t.strip() for t in options.all_inner_texts()]
    choice = _pick_option(labels, value)
    if choice is None:
        page.keyboard.press("Escape")
        if page.locator("[role='listbox']:visible").count():
            page.mouse.click(1, 1)
        return False
    options.nth(labels.index(choice)).click(timeout=timeout)
    return _norm_option(button.inner_text(timeout=timeout)) == _norm_option(choice)


def _fill_field(page, field, value):
    kind = field.get("kind")
    timeout = config.APPLY_AGENT_FIELD_TIMEOUT_MS
    try:
        if kind in ("input", "textarea"):
            page.locator(field["selector"]).first.fill(str(value), timeout=timeout)
        elif kind == "select":
            option = _pick_option(field.get("options"), value)
            if option is None:
                return False
            page.locator(field["selector"]).first.select_option(label=option, timeout=timeout)
        elif kind in ("radio", "checkbox"):
            options = field.get("options") or []
            if kind == "checkbox" and len(options) == 1:
                if _norm_label(value) not in ("yes", "true", "i agree", "agree", _norm_label(options[0])):
                    return False
                index = 0
            else:
                option = _pick_option(options, value)
                if option is None:
                    return False
                index = options.index(option)
            target = (field.get("option_selectors") or [None] * len(options))[index]
            if not target:
                return False
            page.locator(target).first.check(timeout=timeout, force=True)
        elif kind == "combobox":
            box = page.locator(field["selector"]).first
            box.click(timeout=timeout)
            box.fill(str(value), timeout=timeout)
            page.keyboard.press("Enter")
        elif kind == "listbox":
            return _pick_listbox_option(page, field, value, timeout)
        else:
            return False
        return True
    except Exception as exc:
        log.info(f"[APPLY-AGENT] | field not fillable: {field.get('label', '')[:60]!r} | {exc}")
        return False


def _eligibility_value_for(label, answers):
    for key, value in (answers or {}).items():
        if not value:
            continue
        pattern = _ELIGIBILITY_QUESTION_PATTERNS.get(key)
        if pattern is not None:
            if pattern.search(label):
                return value
        elif _norm_label(key) and _norm_label(key) in _norm_label(label):
            # A key the user added to applicant_eligibility themselves (e.g. "desired salary")
            # matches any question label containing it.
            return value
    return None


# ── Screening questions ────────────────────────────────────────────────────────

def _screening_completion(prompt, job_id):
    if config.APPLY_CLAUDE_BACKEND == "subscription":
        text, usage = claude_subscription.complete(prompt, model=config.APPLY_MODEL)
        usage_tracking.log_usage("apply_agent", "screening_question", config.APPLY_MODEL, usage,
                                 job_application_id=job_id, billing="subscription")
        return text
    return _call_claude(prompt, module="apply_agent", action="screening_question", contact_id=None)


def _generate_screening_answers(page, job):
    """Generates grounded answers for the form's required, still-empty questions via Claude --
    generation only, this never fills the page. Called exactly once, in the preview pass;
    submit() must reuse the stored result via _fill_screening_questions instead of calling
    this again, so a submitted application always matches what the human reviewed in the
    preview rather than a freshly (and differently) generated answer. Questions the
    applicant_eligibility answers cover are left to _fill_eligibility_answers, and optional
    questions are left blank. Keyed by the question's label."""
    if config.APPLY_CLAUDE_BACKEND not in ("api", "subscription"):
        raise ValueError(f"unknown APPLY_CLAUDE_BACKEND {config.APPLY_CLAUDE_BACKEND!r} -- use 'subscription' or 'api'")
    answers = {}
    inventory = _form_inventory(page) or []

    # Merge review 2026-09-28, finding 3: this used to pass job.get("role", "") as
    # profile_summary -- a posting titled "Senior Product Manager" was presented back to the LLM
    # as the candidate's own facts. candidate_profile.profile_text() is the same real
    # experience/projects text job_pick.py's fit judge is grounded in.
    profile_summary = candidate_profile.profile_text()
    eligibility = _eligibility_answers()

    for field in inventory:
        label = field.get("label") or ""
        if (field.get("filled") or not field.get("required") or field.get("kind") == "file"
                or _eligibility_value_for(label, eligibility) is not None):
            continue
        if not profile_summary.strip():
            answers[label] = _NO_PROFILE_ANSWER
            continue
        options = field.get("options") or []
        if field.get("kind") == "listbox" and not options:
            options = _listbox_options(page, field)
        options_block = _OPTIONS_BLOCK.format(options="\n".join(options)) if options else ""
        prompt = _SCREENING_PROMPT.format(question=label, profile_summary=profile_summary,
                                          options_block=options_block)
        try:
            answer = _screening_completion(prompt, job.get("id")).strip()
        except claude_subscription.ClaudeSubscriptionError:
            # Usage limit, missing token or a broken CLI affects every question; skipping one would
            # only resurface later as a misleading "required question blank".
            raise
        except Exception as exc:
            log.info(f"[APPLY-AGENT] | screening question skipped: {exc}")
            continue
        if options and not answer.startswith(_NEEDS_REVIEW_PREFIX):
            answer = _pick_option(options, answer) or answer
        answers[label] = answer
    return answers


def _set_field_by_label(page, label_pattern, value):
    """Best-effort generic field setter supporting the three control shapes a screening or
    eligibility question can actually be: a <select> dropdown, a radio-button group, or a
    plain text/textarea field. Used only for a stored answer whose label is no longer in the
    form inventory (e.g. a preview stored before the inventory existed). Tries each shape in
    turn and stops at the first that succeeds; returns True on success, False if no strategy
    worked; never raises."""
    try:
        page.get_by_label(label_pattern).select_option(label=str(value), timeout=config.APPLY_AGENT_FIELD_TIMEOUT_MS)
        return True
    except Exception:
        pass
    try:
        page.get_by_role("group", name=label_pattern).get_by_role("radio", name=str(value)).click(
            timeout=config.APPLY_AGENT_FIELD_TIMEOUT_MS)
        return True
    except Exception:
        pass
    try:
        page.get_by_label(label_pattern).fill(str(value), timeout=config.APPLY_AGENT_FIELD_TIMEOUT_MS)
        return True
    except Exception:
        pass
    return False


def _fill_screening_questions(page, answers, only_present=False):
    """Writes pre-computed screening-question answers into the page. Each stored label is
    matched to a control in the form inventory and filled by its selector; a label with no
    match falls back to _set_field_by_label. An answer still flagged NEEDS HUMAN REVIEW is
    never typed into the form. Used right after generation in the preview pass, and again
    during submit to replay a stored (possibly human-edited) preview's answers verbatim.
    With only_present (multi-step forms), answers whose question is not on this page are left for
    a later page instead of searched for by label. Returns {label: filled}."""
    report = {}
    by_label = {_norm_label(f["label"]): f for f in (_form_inventory(page) or [])}
    for question_text, answer in (answers or {}).items():
        if not answer or str(answer).startswith(_NEEDS_REVIEW_PREFIX):
            report[question_text] = False
            continue
        field = by_label.get(_norm_label(question_text))
        if field is None and only_present:
            continue
        ok = _fill_field(page, field, answer) if field else _set_field_by_label(page, question_text, answer)
        if not ok:
            log.info(f"[APPLY-AGENT] | screening field not fillable: {question_text[:60]!r}")
        report[question_text] = ok
    return report


# Known applicant_eligibility keys -> the real on-page question wording those keys mean,
# matched as a case-insensitive regex against the form's actual label text. The
# `applicant_eligibility` prompts key itself stays keyed by these stable internal names (the
# user edits it live via the contact-manager's Prompts page, and nothing here should force a
# migration of that live data) -- but a real ATS form never has a field literally labeled
# "work_authorized_us", so filling must go through this translation. A key with no entry here
# (one the user added, e.g. "desired salary") matches any label containing the key's text.
_ELIGIBILITY_QUESTION_PATTERNS = {
    "work_authorized_us": re.compile(r"(legally )?authorized to work", re.IGNORECASE),
    "requires_visa_sponsorship": re.compile(r"require.{0,40}sponsor", re.IGNORECASE),
    "gender": re.compile(r"\bgender\b", re.IGNORECASE),
    "race_ethnicity": re.compile(r"\brace\b|ethnicity", re.IGNORECASE),
    "veteran_status": re.compile(r"veteran", re.IGNORECASE),
    "disability_status": re.compile(r"disability", re.IGNORECASE),
    "lgbtq_identity": re.compile(r"lgbtq|sexual orientation", re.IGNORECASE),
}


def _fill_eligibility_answers(page, answers):
    """Writes the fixed EEO/work-authorization answers into the page: every still-empty
    control in the form inventory whose label matches an applicant_eligibility key
    (_ELIGIBILITY_QUESTION_PATTERNS, or the key's own text for a user-added key) is filled
    with that key's value, picking the matching option for a dropdown, radio or combobox.
    Best-effort per field; returns {label: filled}."""
    report = {}
    for field in _form_inventory(page) or []:
        if field.get("filled") or field.get("kind") == "file":
            continue
        value = _eligibility_value_for(field.get("label") or "", answers)
        if value is None:
            continue
        ok = _fill_field(page, field, value)
        if not ok:
            log.info(f"[APPLY-AGENT] | eligibility field not fillable: {field['label'][:60]!r}")
        report[field["label"]] = ok
    return report


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


_RESUME_INPUTS = ["#resume", "input[type='file'][name='resume']", "#_systemfield_resume",
                  "input[type='file'][id*='resume' i]", "input[type='file'][name*='resume' i]",
                  "input[type='file'][data-automation-id='file-upload-input-ref']"]
_COVER_INPUTS = ["#cover_letter", "input[type='file'][id*='cover' i]", "input[type='file'][name*='cover' i]"]


def _find_file_input(page, selectors):
    for sel in selectors:
        try:
            locator = page.locator(sel).first
            if locator.count() > 0:
                return locator
        except Exception as exc:
            log.info(f"[APPLY-AGENT] | file input {sel} lookup failed: {exc}")
    return None


def _attach_resume_and_cover_letter(page, job):
    # set_input_files works with real bytes headlessly -- no OS dialog, no third-party dependency.
    import tempfile

    client = db.get_client()
    report = {}
    for report_key, field_ref_key, selectors, fallback in (
        ("resume", "resume_file_ref", _RESUME_INPUTS,
         ["input[type='file']:not([id*='cover' i]):not([name*='cover' i])"]),
        ("cover_letter", "cover_letter_file_ref", _COVER_INPUTS, []),
    ):
        report[report_key] = None
        storage_path = job.get(field_ref_key)
        if not storage_path:
            continue
        try:
            locator = _find_file_input(page, selectors) or _find_file_input(page, fallback)
            if locator is None:
                log.info(f"[APPLY-AGENT] | {report_key} attach skipped: no file input on the form")
                continue
            content = client.storage.from_(config.RESUME_STORAGE_BUCKET).download(storage_path)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(content)
                temp_path = f.name
            locator.set_input_files(temp_path, timeout=config.APPLY_AGENT_FIELD_TIMEOUT_MS)
            report[report_key] = True
        except Exception as exc:
            report[report_key] = False
            log.info(f"[APPLY-AGENT] | {report_key} attach skipped: {exc}")
    return report


def _missing_required(fill_report):
    fields = fill_report.get("fields") or {}
    attachments = fill_report.get("attachments") or {}
    missing = []
    if not (fields.get("name") is True
            or (fields.get("first_name") is True and fields.get("last_name") is True)):
        missing.append("name")
    if fields.get("email") is not True:
        missing.append("email")
    if attachments.get("resume") is not True:
        missing.append("resume")
    missing.extend(fill_report.get("required_unfilled") or [])
    return missing


_ASHBY_POSTING = re.compile(r"^(https://jobs\.ashbyhq\.com/[^/?#]+/[0-9a-f-]{36})/?(?=$|[?#])", re.I)
_LEVER_POSTING = re.compile(r"^(https://jobs\.lever\.co/[^/?#]+/[0-9a-f-]{36})/?(?=$|[?#])", re.I)


def _application_url(job_url):
    # Ashby and Lever posting URLs show the job description; the form lives one path deeper.
    # Opening the posting page timed out the form fingerprint on the first live preview.
    url = job_url or ""
    for pattern, suffix in ((_ASHBY_POSTING, "/application"), (_LEVER_POSTING, "/apply")):
        match = pattern.match(url)
        if match:
            return match.group(1) + suffix + url[match.end():]
    return url


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


def _launch_page(job_url, storage_state=None):
    from playwright.sync_api import sync_playwright
    playwright = sync_playwright().start()
    # Only browser-use's CDP bridge needs a debugging port; without it, nothing else on the host
    # can attach to this (possibly armed) browser.
    debug_port = _free_local_port() if config.APPLY_GENERIC_ADAPTER == "browser_use" else None
    browser = playwright.chromium.launch(
        headless=config.APPLY_BROWSER_HEADLESS, channel=config.APPLY_BROWSER_CHANNEL,
        args=[f"--remote-debugging-port={debug_port}"] if debug_port else []
    )
    if storage_state:
        page = browser.new_context(storage_state=storage_state).new_page()
    else:
        page = browser.new_page()
    page.goto(job_url)
    _OPEN_SESSIONS[id(page)] = (browser, playwright)
    if debug_port:
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


class ApprovalSignatureError(Exception):
    pass


class TakeoverTimeout(Exception):
    pass


def _handle_challenge(page, job_id, lease, reason):
    # Beelink only: a human solves it over noVNC while this worker holds the lease. Never solved
    # or bypassed here. Raises TakeoverTimeout when nobody does, TakeoverLost if the lease is gone.
    if not config.APPLY_TAKEOVER_ENABLED or not takeover.challenge_present(page):
        return False
    if not takeover.await_human(job_id, lease, "captcha", reason):
        raise TakeoverTimeout(f"{reason}: nobody solved it within "
                              f"{config.APPLY_TAKEOVER_TIMEOUT_SECONDS // 60} minutes")
    return True


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
        # :visible -- a hidden field (an auth panel left in the DOM) must not satisfy the wait.
        page.wait_for_selector("input:visible, select:visible, textarea:visible", timeout=15000)
        raw = page.evaluate(_FORM_FIELDS_JS)
        idents = sorted({re.sub(r"\s+", " ", str(x)).strip().lower() for x in (raw or [])})
        if not idents:
            return None
        return hashlib.sha256(json.dumps(idents).encode()).hexdigest()
    except Exception as exc:
        log.warning(f"[APPLY-AGENT] | form signature failed: {exc}")
        return None


def _log_inventory(job, inventory):
    # Labels, kinds and filled flags only -- never field values. The run log is the only way to
    # see how a live form is built without opening it.
    try:
        slim = [{k: f.get(k) for k in ("label", "kind", "required", "filled", "options")}
                for f in (inventory or [])]
        log.info(f"[APPLY-FORM] | {job.get('company')} | {json.dumps(slim)[:8000]}")
    except Exception:
        pass


# ── Preview pass ───────────────────────────────────────────────────────────────

# ── Workday (Beelink only: APPLY_WORKDAY_ENABLED) ──────────────────────────────

def _workday_values(field_values):
    digits = re.sub(r"\D", "", field_values.get("phone", ""))
    # Workday asks for the local number; the country code is its own dropdown.
    local = digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
    city = (field_values.get("location") or "").split(",")[0].strip()
    return {"first_name": field_values.get("first_name"), "last_name": field_values.get("last_name"),
            "phone_local": local, "city": city}


def _workday_session(job):
    tenant = ats_sessions.tenant_key(job.get("job_url"))
    if not tenant:
        raise workday_adapter.WorkdayStop("unrecognized_page", "Not a Workday tenant URL")
    return tenant, credential_vault.Vault(config.APPLY_VAULT_PATH, config.VAULT_KEY)


def _workday_reach_wizard(page, job_id, lease, tenant, vault, email, login_url):
    # A small state machine over Workday's screens. A WorkdayStop becomes a takeover where a human
    # can help (the Beelink); after "I'm done" the screen is re-read, since the human may have
    # signed in, verified or moved on themselves.
    for _ in range(6):
        try:
            kind = workday_adapter.page_kind(page)
            if kind == "wizard":
                return
            if kind == "already_applied":
                raise workday_adapter.WorkdayStop("already_applied", "Workday says this job was already applied to")
            if kind == "verify":
                raise workday_adapter.WorkdayStop("email_verification", "Workday is waiting for an email verification code")
            if kind == "auth":
                since = datetime.now(timezone.utc)
                host = urlparse(login_url).hostname or ""

                def verify():
                    return email_verification.wait_for_verification(
                        config.WORKDAY_VERIFICATION_SENDERS, since, timeout_seconds=120,
                        allowed_link_hosts=(host,))

                workday_adapter.authenticate(page, vault, tenant, email, login_url, verify)
                continue
            workday_adapter.enter_apply_flow(page)
        except workday_adapter.WorkdayStop as stop:
            if stop.kind == "already_applied" or not config.APPLY_TAKEOVER_ENABLED:
                raise
            if not takeover.await_human(job_id, lease, stop.kind, str(stop)[:480]):
                raise TakeoverTimeout(f"{stop}: nobody took over within "
                                      f"{config.APPLY_TAKEOVER_TIMEOUT_SECONDS // 60} minutes") from None
    raise workday_adapter.WorkdayStop("unrecognized_page", "Could not reach the Workday application")


def _walk_workday(page, job, job_id, lease, field_values, eligibility, replay=None):
    """Fill each wizard step and press Next until Review. Returns (answers, steps, missing,
    attachments); `missing` is non-empty when a step's required questions are still blank, and the
    walk stops on that step. `replay` (submit) refills reviewed answers instead of generating."""
    answers, steps, attach = {}, [], {"resume": None, "cover_letter": None}
    values = _workday_values(field_values)
    for _ in range(20):
        position = workday_adapter.progress(page)
        if position and position[0] == position[1] - 1:
            return answers, steps, [], attach
        db.heartbeat_application(job_id, lease)
        label = workday_adapter.step_label(page) or f"Step {len(steps) + 1}"
        workday_adapter.fill_known_fields(page, values)
        for key, ok in _attach_resume_and_cover_letter(page, job).items():
            if ok is not None:
                attach[key] = attach[key] or ok
        _fill_eligibility_answers(page, eligibility)
        if replay is None:
            step_answers = _generate_screening_answers(page, job)
            _fill_screening_questions(page, step_answers, only_present=True)
            answers.update(step_answers)
        else:
            _fill_screening_questions(page, replay, only_present=True)
        missing = _required_unfilled(_form_inventory(page))
        if missing:
            return answers, steps, [f"{label}: {m}" for m in missing], attach
        steps.append(label)
        if not workday_adapter.advance(page):
            return answers, steps, [], attach
    raise workday_adapter.WorkdayStop("unrecognized_page", "Too many Workday steps")


def _prepare_workday(job, job_id, lease, page):
    tenant, vault = _workday_session(job)
    field_values = _standard_field_values(job)
    _workday_reach_wizard(page, job_id, lease, tenant, vault, field_values["email"], job.get("job_url"))
    ats_sessions.save_state(page.context, tenant)
    signature = _form_signature(page)
    eligibility = _eligibility_answers()
    answers, steps, missing, attach = _walk_workday(page, job, job_id, lease, field_values, eligibility)
    _log_inventory(job, _form_inventory(page))
    if missing:
        reason = ("Preview couldn't fill required questions: " + "; ".join(missing))[:900]
        reason += (". To answer one every time, add it to applicant_eligibility on the Prompts "
                   "page, keyed by a word from the question, then Re-prepare.")
        log.warning(f"[APPLY-PREVIEW] | {job.get('company')} | {reason}")
        if not db.release_application(job_id, lease, "needs_input", reason):
            return "lost"
        return "blocked"
    _handle_challenge(page, job_id, lease, "CAPTCHA after filling the form")
    preview = {
        "platform": "workday",
        "field_values": field_values,
        "eligibility_answers": eligibility,
        "screening_answers": answers,
        "workday_steps": steps,
        "fill_report": {"attachments": attach, "required_unfilled": []},
    }
    if not db.complete_preview(job_id, lease, preview, signature):
        log.warning(f"[APPLY-PREVIEW] | {job.get('company')} | lease lost, preview discarded")
        return "lost"
    return "filled"


def _launch_for(job, platform):
    if platform == "workday":
        state = ats_sessions.state_path(ats_sessions.tenant_key(job.get("job_url")) or "")
        return _launch_page(job.get("job_url"), storage_state=state)
    return _launch_page(_application_url(job.get("job_url")))


def _process_one_preview(job):
    job_id = job["id"]
    platform = ats_platform.classify(job.get("job_url"))

    if platform == "workday" and not config.APPLY_WORKDAY_ENABLED:
        db.mark_unsupported(job_id, "workday -- needs the Beelink worker (a human may have to take over)")
        return "blocked"
    if platform == "workday" and not config.VAULT_KEY:
        log.info(f"[APPLY-PREVIEW] | {job.get('company')} | skipped: no VAULT_KEY for Workday accounts")
        return "skipped"
    if platform == "aggregator":
        db.mark_unsupported(job_id, "aggregator/listing link, not a real application page")
        return "blocked"
    if (platform not in config.APPLY_AGENT_HAND_MAPPED_PLATFORMS and platform != "workday"
            and config.APPLY_GENERIC_ADAPTER == "none"):
        # Left idle, not unsupported: a generic adapter for this host is still to come (Phase F).
        log.info(f"[APPLY-PREVIEW] | {job.get('company')} | skipped: no generic-platform adapter on this host")
        return "skipped"

    lease = db.claim_application(job_id, "preparing")
    if lease is None:
        log.info(f"[APPLY-PREVIEW] | {job.get('company')} | skipped: row not claimable (another worker holds it)")
        return "skipped"

    try:
        page = _launch_for(job, platform)
        try:
            _handle_challenge(page, job_id, lease, "CAPTCHA on the application page")
            if platform == "workday":
                return _prepare_workday(job, job_id, lease, page)
            signature = _form_signature(page)
            if not signature:
                raise ValueError("Could not fingerprint the application form; preview must be prepared again")
            db.heartbeat_application(job_id, lease)
            field_values = _standard_field_values(job)

            fields_report = None
            if platform in config.APPLY_AGENT_HAND_MAPPED_PLATFORMS:
                fields_report = {"greenhouse": ats_fillers.fill_greenhouse,
                                 "ashby": ats_fillers.fill_ashby,
                                 "lever": ats_fillers.fill_lever}[platform](page, field_values)
            else:
                _fill_generic_via_browser_use(page, job, field_values)
            db.heartbeat_application(job_id, lease)

            attach_report = _attach_resume_and_cover_letter(page, job)
            fill_report = None
            if fields_report is not None:
                fill_report = {"fields": fields_report, "attachments": attach_report}
                missing = _missing_required(fill_report)
                if missing:
                    reason = "Preview couldn't fill required fields: " + ", ".join(missing)
                    log.warning(f"[APPLY-PREVIEW] | {job.get('company')} | {reason}")
                    if not db.release_application(job_id, lease, "needs_input", reason):
                        return "lost"
                    return "blocked"
            db.heartbeat_application(job_id, lease)
            eligibility_answers = _eligibility_answers()
            _fill_eligibility_answers(page, eligibility_answers)
            screening_answers = _generate_screening_answers(page, job)
            question_report = _fill_screening_questions(page, screening_answers)
            db.heartbeat_application(job_id, lease)

            inventory = _form_inventory(page)
            _log_inventory(job, inventory)
            if fill_report is not None:
                fill_report["questions"] = question_report
                fill_report["required_unfilled"] = _required_unfilled(inventory)
                if fill_report["required_unfilled"]:
                    reason = ("Preview couldn't fill required questions: "
                              + "; ".join(fill_report["required_unfilled"]))[:900]
                    reason += (". To answer one every time, add it to applicant_eligibility on the Prompts "
                               "page, keyed by a word from the question (e.g. \"salary\"), then Re-prepare.")
                    log.warning(f"[APPLY-PREVIEW] | {job.get('company')} | {reason}")
                    if not db.release_application(job_id, lease, "needs_input", reason):
                        return "lost"
                    return "blocked"

            _handle_challenge(page, job_id, lease, "CAPTCHA after filling the form")
            preview = {
                "platform": platform,
                "field_values": field_values,
                "eligibility_answers": eligibility_answers,
                "screening_answers": screening_answers,
            }
            if fill_report is not None:
                preview["fill_report"] = fill_report
            if not db.complete_preview(job_id, lease, preview, signature):
                log.warning(f"[APPLY-PREVIEW] | {job.get('company')} | lease lost, preview discarded "
                            f"(row was recovered by lease recovery)")
                return "lost"
            return "filled"
        finally:
            _close_page(page)
    except Exception as exc:
        if isinstance(exc, takeover.TakeoverLost):
            raise
        to_status = "failed_retryable"
        if isinstance(exc, claude_subscription.ClaudeUsageLimitError):
            reason = f"Claude usage limit reached; will retry on a later run: {exc}"
        elif isinstance(exc, (TakeoverTimeout, workday_adapter.WorkdayStop)):
            to_status, reason = "needs_input", str(exc)
        else:
            reason = f"preview pass error: {exc}"
        try:
            db.release_application(job_id, lease, to_status, reason[:900])
        except Exception:
            pass
        raise


def preview_candidates():
    """Rows the preview pass may claim: saved, both documents built, idle or retryable."""
    return [j for j in db.get_job_applications(stage="saved")
            if j.get("resume_file_ref") and j.get("cover_letter_file_ref")
            and (j.get("automation_status") or "idle") in config.APPLY_AGENT_PREVIEW_ELIGIBLE_STATUSES]


def run_preview():
    db.recover_stale_leases(config.APPLY_AGENT_LEASE_STALE_SECONDS)
    jobs = preview_candidates()
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
        except claude_subscription.ClaudeSubscriptionError as exc:
            # Same posture as resume_agent.drain(): every later row would fail the same way.
            log.warning(f"[APPLY-PREVIEW] | {job.get('company')} | Claude subscription unavailable, stopping: {exc}")
            errors += 1
            break
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
    if platform == "aggregator" or (platform == "workday" and not config.APPLY_WORKDAY_ENABLED):
        reason = f"submit() called on a permanently-excluded platform: {platform}"
        log.warning(f"[APPLY-SUBMIT] | {job.get('company')} | error: {reason}")
        try:
            db.mark_unsupported(job_id, reason)
        except Exception:
            pass
        raise ValueError(reason)

    if (platform not in config.APPLY_AGENT_HAND_MAPPED_PLATFORMS and platform != "workday"
            and config.APPLY_GENERIC_ADAPTER == "none"):
        raise ValueError(f"submit() has no adapter for platform {platform!r} on this host; approval left intact")
    if platform == "workday" and not config.VAULT_KEY:
        raise RuntimeError("VAULT_KEY is not configured; Workday accounts are unreadable, refusing to submit")

    # A worker without the key cannot tell a real approval from a forged one. Refuse before the
    # claim, so the operator's approval survives until the worker is configured.
    if not approval_signature.key_configured():
        raise RuntimeError("APPROVAL_SIGNING_KEY is not configured; refusing to submit")

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

        # The hash proves which revision was approved; the signature proves the operator's
        # logged-in contact-manager approved it, not someone calling the RPC with the anon key.
        problem = approval_signature.verify(job)
        if problem:
            raise ApprovalSignatureError(f"Refusing to submit: {problem}; approve it again")

        page = _launch_for(job, platform)
        try:
            _handle_challenge(page, job_id, lease, "CAPTCHA on the application page")
            if platform == "workday":
                tenant, vault = _workday_session(job)
                _workday_reach_wizard(page, job_id, lease, tenant, vault,
                                      _standard_field_values(job)["email"], job.get("job_url"))
                ats_sessions.save_state(page.context, tenant)
            expected_signature = job.get("form_signature")
            if not expected_signature:
                log.warning(f"[APPLY-SUBMIT] | {job.get('company')} | no preview form_signature, drift check skipped")
            elif _form_signature(page) != expected_signature:
                raise FormChangedError("Form changed after approval")
            field_values = _standard_field_values(job)
            if platform == "workday":
                # Walk the wizard again from the reviewed answers; Submit only on Review.
                preview = job.get("apply_preview") or {}
                _, _, missing, _ = _walk_workday(page, job, job_id, lease, field_values,
                                                 preview.get("eligibility_answers") or {},
                                                 replay=preview.get("screening_answers") or {})
                if missing:
                    raise ValueError("Refusing to submit: required fields not filled: " + "; ".join(missing))
                submit_control = workday_adapter.submit_button(page)
            else:
                fields_report = None
                if platform in config.APPLY_AGENT_HAND_MAPPED_PLATFORMS:
                    fields_report = {"greenhouse": ats_fillers.fill_greenhouse,
                                     "ashby": ats_fillers.fill_ashby,
                                     "lever": ats_fillers.fill_lever}[platform](page, field_values)
                else:
                    # generic-platform fill runs an LLM browser agent against the real page before
                    # the ARMED gate below -- restrained only by the task-string instruction not to
                    # click Submit, not a hard guarantee. See the Phase 2.5 review notes.
                    _fill_generic_via_browser_use(page, job, field_values)
                db.heartbeat_application(job_id, lease)

                attach_report = _attach_resume_and_cover_letter(page, job)
                # Reuse the stored preview's answers verbatim -- never regenerate here. The human
                # approved what's in apply_preview when they tapped "Approve & Submit"; a fresh
                # Claude call at submit time could produce a different answer than the one they saw,
                # and any screening/eligibility question the preview pass couldn't fill would
                # otherwise go out blank instead of being retried from the same known values.
                preview = job.get("apply_preview") or {}
                _fill_eligibility_answers(page, preview.get("eligibility_answers"))
                _fill_screening_questions(page, preview.get("screening_answers"))
                db.heartbeat_application(job_id, lease)

                if fields_report is not None:
                    missing = _missing_required({"fields": fields_report, "attachments": attach_report,
                                                 "required_unfilled": _required_unfilled(_form_inventory(page))})
                    if missing:
                        raise ValueError("Refusing to submit: required fields not filled: " + ", ".join(missing))
                submit_control = page.get_by_role("button", name=_SUBMIT_BUTTON_NAME)

            if os.environ.get("APPLY_AGENT_ARMED") != "1":
                log.info(f"[APPLY-SUBMIT] | {job.get('company')} | not armed -- filled but did not submit")
                db.release_application(job_id, lease, "approved")
                return

            # Filling may take minutes. A lease recovered during that time, or a document
            # rebuild invalidating the revision, must stop this worker before the external
            # action. Best-effort progress heartbeats cannot establish that permission.
            _handle_challenge(page, job_id, lease, "CAPTCHA before Submit")
            if not db.renew_submission_lease(job_id, lease, job["approved_revision_hash"]):
                raise RuntimeError("Submit stopped: worker lease or approved revision changed during preparation")

            # from here on the site may have the application -- any failure is needs_confirmation, never retryable.
            clicked = True
            submit_control.click()
            # The click succeeding is not proof the application landed -- a client-side validation
            # error commonly leaves the button's own click handler a no-op with the form still on
            # screen. Do not advance the stage until the site itself confirms it.
            confirmed = _submission_confirmed(page)
            # A challenge after the click: a human finishes it. This worker never clicks Submit
            # again; an unsolved one leaves the row in needs_confirmation (clicked is True).
            if not confirmed and _handle_challenge(
                    page, job_id, lease,
                    "CAPTCHA after Submit (solve it, press Submit yourself only if the form asks again)"):
                confirmed = _submission_confirmed(page)
            if not confirmed:
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
                     else "needs_input" if isinstance(exc, (FormChangedError, ApprovalSignatureError))
                     else "failed_retryable")
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
