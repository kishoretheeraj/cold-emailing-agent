"""
Pre-review quality gate for prepared applications (spec 2026-10-08 §6.3).

Checks the built resume and cover letter PDFs against published benchmarks before an application
can reach the operator's review queue:

  resume        one page; the name, email and core section headers survive PDF text extraction
                (what an ATS parser sees; Jobscan 2025, MIT CAPD); no placeholders or model tells.
  cover letter  about one page of text (roughly 250-400 words of body, ResumeLab / university
                career offices); names the company and the role; no placeholders, em dashes or
                model tells.
  keywords      the JD's skills from the candidate's own skills vocabulary, split into covered and
                missing (a report for the review card, never a reason to add a skill).

resume_lint.py already governs content (metrics whitelist, banned skills, jargon, cover-letter
overlap and openers) at build time; this module checks the finished documents as an employer's
system will read them.
"""

import io
import json
import logging
import math
import os
import re

import config
import db

log = logging.getLogger(__name__)

COVER_MIN_WORDS = 180   # extracted text, so the name/contact/greeting lines are included
COVER_MAX_WORDS = 450
_RESUME_HEADERS = ("EXPERIENCE", "EDUCATION")
_PLACEHOLDER = re.compile(
    r"\[[A-Z][^\]]{0,60}\]|\{[a-z_]{2,40}\}|<[A-Z][A-Za-z ]{2,40}>|\bX{3,}\b|lorem ipsum"
    r"|\b(hiring manager name|company name|job title)\b", re.IGNORECASE)
_MODEL_TELLS = re.compile(r"\bas an ai\b|\blanguage model\b|\bi (cannot|can't) (help|assist)\b"
                          r"|\bhere(?:'s| is) (a|your) (cover letter|resume)\b", re.IGNORECASE)
_COMPANY_SUFFIX = re.compile(
    r"[,.]?\s+(inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|plc|gmbh|"
    r"s\.a|ag|holdings|group|technologies|technology)\.?$", re.IGNORECASE)
_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resume", "data")
_SKILLS_PATH = os.path.join(_DATA, "skills.json")
_MASTER_PATH = os.path.join(_DATA, "master.json")


def pdf_text(content):
    """(text, page count) of a PDF, or ("", 0) when it cannot be read."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages), len(reader.pages)
    except Exception as exc:
        log.info(f"[APPLY-QUALITY] | PDF not readable: {exc}")
        return "", 0


def _squash(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _company_core(company):
    core = (company or "").strip()
    core = re.sub(r"^the\s+", "", core, flags=re.IGNORECASE)
    core = core.replace("&", " ")
    previous = None
    while previous != core:
        previous = core
        core = _COMPANY_SUFFIX.sub("", core).strip()
    return _squash(re.sub(r"[^\w\s]", " ", core))


def _common_problems(text):
    problems = []
    match = _PLACEHOLDER.search(text)
    if match:
        problems.append(f"placeholder left in the text: {match.group(0)!r}")
    if _MODEL_TELLS.search(text):
        problems.append("contains AI assistant wording")
    return problems


def check_resume(text, pages, name, email):
    """Problems with the resume as an ATS will read it (an empty list means it passes)."""
    if not text.strip():
        return ["no extractable text (an ATS would read an empty resume)"]
    problems = []
    if pages != 1:
        problems.append(f"{pages} pages; the resume must fit on one page")
    flat = _squash(text)
    if name and _squash(name) not in flat:
        problems.append("the candidate's name is not in the extracted text")
    if email and email.lower() not in flat.replace(" ", ""):
        problems.append("the email address is not in the extracted text")
    for header in _RESUME_HEADERS:
        if header.lower() not in flat:
            problems.append(f"section header {header} is not in the extracted text")
    return problems + _common_problems(text)


def check_cover_letter(text, company, role):
    """Problems with the cover letter (an empty list means it passes)."""
    if not text.strip():
        return ["no extractable text"]
    problems = []
    words = len(re.findall(r"[A-Za-z0-9'][A-Za-z0-9'-]*", text))
    if words < COVER_MIN_WORDS:
        problems.append(f"too short ({words} words; at least {COVER_MIN_WORDS})")
    if words > COVER_MAX_WORDS:
        problems.append(f"too long ({words} words; at most {COVER_MAX_WORDS}, about one page)")
    flat = _squash(re.sub(r"[^\w\s]", " ", text))
    core = _company_core(company)
    if core and core not in flat:
        problems.append(f"does not name {core.title()}")
    role_core = re.split(r"[,(|/–—-]", role or "")[0]
    role_words = [w for w in re.findall(r"[a-z]+", role_core.lower()) if len(w) > 2 and w not in ("senior", "junior", "lead")]
    if role_words and sum(w in flat.split() for w in role_words) < math.ceil(0.6 * len(role_words)):
        problems.append("does not name the role")
    if "—" in text:
        problems.append("contains an em dash")
    return problems + _common_problems(text)


def _vocabulary():
    try:
        with open(_SKILLS_PATH) as f:
            data = json.load(f)
    except Exception:
        return []
    terms = []
    for key in ("spine", "swap_pool", "banned", "flagged_unbacked"):
        terms += [t for t in data.get(key) or [] if isinstance(t, str)]
    return terms


def _term_pattern(term):
    base = re.sub(r"\s*\(.*?\)\s*", "", term).strip()
    return re.compile(r"(?<![\w])" + re.escape(base) + r"(?![\w])", re.IGNORECASE)


def keyword_coverage(jd_text, docs_text, vocabulary):
    """Skills from `vocabulary` the JD names, split by whether the documents name them too."""
    covered, missing = [], []
    for term in vocabulary:
        pattern = _term_pattern(term)
        if not pattern.search(jd_text or ""):
            continue
        (covered if pattern.search(docs_text or "") else missing).append(term)
    return {"covered": sorted(set(covered)), "missing": sorted(set(missing))}


def posting_text(job):
    """The posting's description, responsibilities and qualifications as one text."""
    snapshot = job.get("posting_snapshot") or {}
    parts = []
    for key in ("description", "responsibilities", "qualifications"):
        value = snapshot.get(key)
        parts.extend(value if isinstance(value, list) else [value] if value else [])
    return "\n".join(str(p) for p in parts)


def evaluate(job, name=None, email=None):
    """{"problems": [...], "coverage": {...}} for a row with both documents built. Storage errors
    raise: an unreadable bucket is the worker's problem, not the application's."""
    client = db.get_client()
    bucket = client.storage.from_(config.RESUME_STORAGE_BUCKET)
    resume_text, resume_pages = pdf_text(bucket.download(job["resume_file_ref"]))
    cover_text, _ = pdf_text(bucket.download(job["cover_letter_file_ref"]))
    if name is None or email is None:
        # The resume is built from master.json, so its name and email must survive extraction.
        with open(_MASTER_PATH) as f:
            master = json.load(f)
        name = name or master.get("name")
        email = email or (master.get("contact") or {}).get("email")
    problems = [f"Resume: {p}" for p in check_resume(resume_text, resume_pages, name, email)]
    problems += [f"Cover letter: {p}" for p in check_cover_letter(cover_text, job.get("company"), job.get("role"))]
    coverage = keyword_coverage(posting_text(job), resume_text + "\n" + cover_text, _vocabulary())
    return {"problems": problems, "coverage": coverage}


# ── Closed postings and knock-outs ────────────────────────────────────────────

_CLOSED = re.compile(
    r"no longer accepting applications|position has been filled|job (posting )?has (expired|closed)"
    r"|this (job|posting|position) (has )?expired|(job|position|posting) (you are looking for )?is no longer available"
    r"|job may have been removed|(job|posting) (has been|was) removed|requisition (is )?closed",
    re.IGNORECASE)

# Each (reason, pattern) applies only to a candidate who needs visa sponsorship.
_SPONSORSHIP_KNOCKOUTS = (
    ("does not sponsor visas", re.compile(
        r"(unable|not able|cannot|can't|will not|won't|do not|does not|are not able) (to )?(provide |offer )?"
        r"(visa |employment )?sponsor"
        r"|(visa )?sponsorship (is )?(not |un)available|no (visa )?sponsorship"
        r"|without (the need for )?(current or future |any )?(visa )?sponsorship", re.IGNORECASE)),
    ("requires U.S. citizenship", re.compile(
        r"must be (a )?u\.?s\.? citizen|u\.?s\.? citizenship (is )?required|(only|must be) u\.?s\.? citizens", re.IGNORECASE)),
    ("requires U.S. citizenship or permanent residency", re.compile(
        r"green card holders? only|(u\.?s\.? )?(citizens|citizenship) or (lawful )?permanent residen", re.IGNORECASE)),
    ("requires a security clearance", re.compile(
        r"(secret|ts/sci|top secret|security) clearance (is )?(required|needed)|active (secret|ts/sci|top secret) clearance"
        r"|(must|able to) (obtain|hold|maintain) (a |an )?(active )?(secret |top secret )?(security )?clearance", re.IGNORECASE)),
)


def posting_closed_reason(page_text):
    """The closed-posting phrase on the page, or None while it still takes applications."""
    match = _CLOSED.search(page_text or "")
    return match.group(0) if match else None


def knockout_reasons(jd_text, eligibility):
    """Hard disqualifiers in the posting for this candidate. Only applied when the operator's
    applicant_eligibility says sponsorship is needed; an unknown answer never drops a job."""
    needs = str((eligibility or {}).get("requires_visa_sponsorship", "")).strip().lower()
    if not needs.startswith("y"):
        return []
    return [reason for reason, pattern in _SPONSORSHIP_KNOCKOUTS if pattern.search(jd_text or "")]
