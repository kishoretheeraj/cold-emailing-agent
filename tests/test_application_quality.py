"""application_quality.py: the benchmark-based gate a prepared application must pass before it can
reach the review queue (spec 2026-10-08 §6.3). PDFs are built here for real so pypdf's text
extraction is exercised, not mocked."""

from unittest.mock import MagicMock

import pytest

import application_quality as aq

import json
from pathlib import Path

_MASTER = json.loads((Path(__file__).resolve().parent.parent / "resume" / "data" / "master.json").read_text())
NAME = _MASTER["name"]
EMAIL = _MASTER["contact"]["email"]  # the resume's address (forms use the receipt inbox's)


def make_pdf(pages):
    """A minimal valid PDF, one text line per string; a list of lists makes several pages."""
    if pages and isinstance(pages[0], str):
        pages = [pages]
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None,
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        ops = ["BT", "/F1 10 Tf", "12 TL", "40 780 Td"]
        for line in lines:
            safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({safe}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops)
        objects.append(f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream")
        content_num = len(objects)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                       f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_num} 0 R >>")
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = "%PDF-1.4\n", []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out.encode("latin-1")))
        out += f"{i} 0 obj\n{body}\nendobj\n"
    xref = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


RESUME_LINES = [NAME, f"Hanover, NH | {EMAIL} | linkedin.com/in/kishoretheeraj",
                "EXPERIENCE", "Associate Product Manager, Protium Finance", "Shipped SQL dashboards in Metabase",
                "EDUCATION", "Dartmouth College, MEM", "SKILLS", "Product Roadmap, PRDs, SQL, Figma, Jira"]


def cover_lines(company="Northwind", role="Product Manager", words=260):
    body = ("I want to help " + company + " grow its lending product as its next " + role +
            ". ") + " ".join(["Customers"] + ["value"] * (words - 20))
    return [NAME, EMAIL, "Dear Hiring Team,", body, "Thank you for your time.", "Sincerely,", NAME]


# ── text extraction ────────────────────────────────────────────────────────────

def test_pdf_text_reads_real_pdfs_and_counts_pages():
    text, pages = aq.pdf_text(make_pdf([["Page one line"], ["Page two line"]]))
    assert pages == 2 and "Page one line" in text and "Page two line" in text


def test_pdf_text_of_garbage_is_empty_not_an_exception():
    assert aq.pdf_text(b"not a pdf") == ("", 0)


# ── resume ─────────────────────────────────────────────────────────────────────

def test_a_clean_one_page_resume_passes():
    text, pages = aq.pdf_text(make_pdf(RESUME_LINES))
    assert aq.check_resume(text, pages, NAME, EMAIL) == []


@pytest.mark.parametrize("mutate,problem", [
    (lambda lines: [lines, lines], "2 pages"),
    (lambda lines: [l for l in lines if EMAIL not in l], "email"),
    (lambda lines: [l for l in lines if l != NAME], "name"),
    (lambda lines: [l for l in lines if l != "EDUCATION"], "EDUCATION"),
    (lambda lines: lines + ["[Insert metric here]"], "placeholder"),
    (lambda lines: lines + ["As an AI language model I cannot"], "AI"),
])
def test_resume_problems(mutate, problem):
    pages = mutate(list(RESUME_LINES))
    text, count = aq.pdf_text(make_pdf(pages))
    assert any(problem in p for p in aq.check_resume(text, count, NAME, EMAIL)), problem


def test_an_unreadable_resume_is_a_problem():
    assert any("no extractable text" in p for p in aq.check_resume("", 0, NAME, EMAIL))


# ── cover letter ───────────────────────────────────────────────────────────────

def test_a_good_cover_letter_passes():
    text, _ = aq.pdf_text(make_pdf(cover_lines()))
    assert aq.check_cover_letter(text, "Northwind, Inc.", "Product Manager") == []


@pytest.mark.parametrize("lines,company,role,problem", [
    (cover_lines(words=90), "Northwind", "Product Manager", "too short"),
    (cover_lines(words=520), "Northwind", "Product Manager", "too long"),
    (cover_lines(company="Southwind"), "Northwind", "Product Manager", "does not name Northwind"),
    (cover_lines(role="Data Analyst"), "Northwind", "Senior Product Manager, Payments", "does not name the role"),
    (cover_lines() + ["Dear [Hiring Manager Name],"], "Northwind", "Product Manager", "placeholder"),
    (cover_lines() + ["A long pause — then more"], "Northwind", "Product Manager", "em dash"),
])
def test_cover_letter_problems(lines, company, role, problem):
    text, _ = aq.pdf_text(make_pdf(lines) if "—" not in "".join(lines) else b"")
    if "—" in "".join(lines):
        text = "\n".join(lines)  # Helvetica in a hand-built PDF cannot carry an em dash
    assert any(problem in p for p in aq.check_cover_letter(text, company, role)), problem


@pytest.mark.parametrize("company,expected", [
    ("Northwind, Inc.", "northwind"), ("Acme Corporation", "acme"), ("The Home Depot", "home depot"),
    ("JPMorgan Chase & Co.", "jpmorgan chase"),
])
def test_company_core_name(company, expected):
    assert aq._company_core(company) == expected


# ── keyword coverage ───────────────────────────────────────────────────────────

def test_keyword_coverage_reports_covered_and_missing_skills():
    jd = "You will own the product roadmap, write PRDs, query data in SQL, and use Tableau and Python."
    docs = "Product Roadmap, PRDs, SQL, Figma"
    vocabulary = ["Product Roadmap", "PRDs", "SQL", "Figma", "Python", "Tableau", "Jira"]
    assert aq.keyword_coverage(jd, docs, vocabulary) == {
        "covered": ["PRDs", "Product Roadmap", "SQL"], "missing": ["Python", "Tableau"]}


def test_keyword_matching_is_whole_word():
    assert aq.keyword_coverage("We use SQLAlchemy", "", ["SQL"]) == {"covered": [], "missing": []}


# ── evaluate ───────────────────────────────────────────────────────────────────

def _storage(resume_pdf, cover_pdf):
    client = MagicMock()
    files = {"r.pdf": resume_pdf, "c.pdf": cover_pdf}
    client.storage.from_.return_value.download.side_effect = lambda path: files[path]
    return client


def test_evaluate_downloads_both_documents_and_reports(mocker):
    mocker.patch.object(aq.db, "get_client", return_value=_storage(make_pdf(RESUME_LINES), make_pdf(cover_lines())))
    report = aq.evaluate({"company": "Northwind", "role": "Product Manager", "resume_file_ref": "r.pdf",
                          "cover_letter_file_ref": "c.pdf",
                          "posting_snapshot": {"qualifications": ["SQL and Python required"]}})
    assert report["problems"] == []
    assert report["coverage"]["covered"] == ["SQL"]
    assert report["coverage"]["missing"] == ["Python"]


def test_evaluate_reports_problems_from_both_documents(mocker):
    mocker.patch.object(aq.db, "get_client", return_value=_storage(
        make_pdf([RESUME_LINES, RESUME_LINES]), make_pdf(cover_lines(words=60))))
    report = aq.evaluate({"company": "Northwind", "role": "Product Manager", "resume_file_ref": "r.pdf",
                          "cover_letter_file_ref": "c.pdf"})
    assert any(p.startswith("Resume:") and "2 pages" in p for p in report["problems"])
    assert any(p.startswith("Cover letter:") and "too short" in p for p in report["problems"])


def test_evaluate_lets_a_download_failure_raise(mocker):
    client = MagicMock()
    client.storage.from_.return_value.download.side_effect = RuntimeError("storage down")
    mocker.patch.object(aq.db, "get_client", return_value=client)
    with pytest.raises(RuntimeError):
        aq.evaluate({"company": "N", "role": "PM", "resume_file_ref": "r.pdf", "cover_letter_file_ref": "c.pdf"})


def test_evaluate_checks_identity_from_master_json(mocker):
    # The resume's own name and email come from resume/data/master.json; a resume missing them
    # must fail even when the caller passes no identity.
    stripped = [l for l in RESUME_LINES if l != NAME and EMAIL not in l]
    mocker.patch.object(aq.db, "get_client", return_value=_storage(make_pdf(stripped), make_pdf(cover_lines())))
    report = aq.evaluate({"company": "Northwind", "role": "Product Manager", "resume_file_ref": "r.pdf",
                          "cover_letter_file_ref": "c.pdf"})
    assert any("name" in p for p in report["problems"])
    assert any("email" in p for p in report["problems"])


# ── closed postings and knock-outs ─────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Sorry, this job is no longer accepting applications.",
    "The position has been filled.",
    "This job posting has expired.",
    "The job you are looking for is no longer available.",
    "Page not found. The job may have been removed.",
])
def test_closed_posting_is_recognized(text):
    assert aq.posting_closed_reason(f"Acme Careers\n{text}\nSearch jobs")


@pytest.mark.parametrize("text", [
    "Apply for this job. We are accepting applications on a rolling basis.",
    "Applications are no longer accepted by mail; apply online below.",
    "",
])
def test_open_posting_is_not_closed(text):
    assert aq.posting_closed_reason(text) is None


NEEDS_SPONSOR = {"requires_visa_sponsorship": "Yes", "work_authorized_us": "Yes"}
NO_SPONSOR = {"requires_visa_sponsorship": "No", "work_authorized_us": "Yes"}


@pytest.mark.parametrize("jd,expected", [
    ("We are unable to sponsor employment visas for this role.", "does not sponsor visas"),
    ("Visa sponsorship is not available.", "does not sponsor visas"),
    ("No sponsorship available.", "does not sponsor visas"),
    ("Candidates must be authorized to work in the US without current or future sponsorship.", "does not sponsor visas"),
    ("Must be a U.S. citizen.", "requires U.S. citizenship"),
    ("US citizenship is required for this position.", "requires U.S. citizenship"),
    ("Active Secret security clearance required.", "requires a security clearance"),
    ("Green card holders only.", "requires U.S. citizenship or permanent residency"),
])
def test_knockouts_for_a_candidate_who_needs_sponsorship(jd, expected):
    assert expected in aq.knockout_reasons(jd, NEEDS_SPONSOR)


@pytest.mark.parametrize("jd", [
    "We sponsor visas and welcome international candidates.",
    "Visa sponsorship is available for this role.",
    "We are happy to provide sponsorship.",
    "Experience with security tooling and clearance of tickets.",
])
def test_no_knockout_when_the_posting_allows_it(jd):
    assert aq.knockout_reasons(jd, NEEDS_SPONSOR) == []


def test_sponsorship_refusal_is_no_knockout_for_a_candidate_who_needs_none():
    assert aq.knockout_reasons("We are unable to sponsor employment visas.", NO_SPONSOR) == []


def test_unknown_sponsorship_need_never_knocks_out():
    # No answer on file is not a "yes": nothing is dropped on a guess.
    assert aq.knockout_reasons("We are unable to sponsor visas. Must be a US citizen.", {}) == []
