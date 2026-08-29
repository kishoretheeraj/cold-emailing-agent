"""Tests for resume_scrub.py. PDF scrubbing is tested against pikepdf's real API on a tiny
real PDF (fast, no mocking needed for a library that's already deterministic and local)."""

import datetime

import pikepdf
import pytest

import resume_scrub


@pytest.fixture
def tiny_pdf(tmp_path):
    path = str(tmp_path / "test.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    with pdf.open_metadata() as meta:
        meta["dc:creator"] = ["LibreOffice"]
        meta["pdf:Producer"] = "LibreOffice 24.2"
    pdf.save(path)
    return path


def test_scrub_pdf_metadata_overwrites_creator_and_producer(tiny_pdf):
    resume_scrub.scrub_pdf_metadata(tiny_pdf, title="Kishore Theeraj - Resume", keywords="PM, SQL")
    with pikepdf.open(tiny_pdf) as pdf:
        with pdf.open_metadata() as meta:
            assert meta.get("dc:creator") not in (["LibreOffice"], "LibreOffice")
            assert meta.get("pdf:Producer") != "LibreOffice 24.2"
            assert meta.get("dc:title") == "Kishore Theeraj - Resume"


def test_scrub_pdf_metadata_sets_realistic_non_identical_timestamps(tiny_pdf):
    resume_scrub.scrub_pdf_metadata(tiny_pdf, title="T", keywords="k")
    with pikepdf.open(tiny_pdf) as pdf:
        docinfo = pdf.docinfo
        created = str(docinfo.get("/CreationDate", ""))
        modified = str(docinfo.get("/ModDate", ""))
        assert created and modified
        assert created != modified


# ── verify_no_fingerprints ──────────────────────────────────────────────────────

def test_verify_no_fingerprints_flags_tool_names():
    text = "Producer: LibreOffice 24.2, generated via python-docx"
    violations = resume_scrub.verify_no_fingerprints(text)
    assert any("libreoffice" in v.lower() for v in violations)
    assert any("python-docx" in v.lower() for v in violations)


def test_verify_no_fingerprints_passes_clean_text():
    assert resume_scrub.verify_no_fingerprints("Producer: Microsoft: Print To PDF") == []


def test_verify_no_fingerprints_does_not_flag_claude_in_resume_content():
    text = "Skills: Claude, Cursor, Claude Code, SQL, Python"
    assert resume_scrub.verify_no_fingerprints(text) == []


# ── read_pdf_metadata_text ──────────────────────────────────────────────────────

def test_read_pdf_metadata_text_reflects_scrubbed_values(tiny_pdf):
    resume_scrub.scrub_pdf_metadata(tiny_pdf, title="My Resume Title", keywords="PM, SQL")
    text = resume_scrub.read_pdf_metadata_text(tiny_pdf)
    assert "My Resume Title" in text
    assert "libreoffice" not in text.lower()
