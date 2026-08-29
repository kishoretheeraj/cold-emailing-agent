"""Tests for resume_build.py's DOCX construction. Uses python-docx's own reader to verify
output, not mocks -- building a real (tiny) DOCX in a temp dir is fast and exercises the
real library integration, matching how gmail.py's tests exercise real MIME construction."""

import os
from unittest.mock import MagicMock

import pytest
from docx import Document

import config
import resume_build

_STRATEGY = {
    "section_order": ["Experience", "Projects", "Skills"],
    "projects_included": ["Viant", "Hiya"],
}
_MASTER = {
    "roles": [
        {"title": "Associate Product Manager", "company": "Protium Finance",
         "period": "Apr 2025 - Aug 2025", "bullets": ["Eliminated vendor cost via a build-vs-buy model."]},
    ],
    "education": {"institution": "Dartmouth College", "program": "Master's, Engineering Management",
                   "graduation": "Nov 2026"},
    "projects": {
        "Viant": {"bullets": ["Narrowed 7 vendors to 2 finalists."]},
        "Hiya": {"bullets": ["Protected 500M+ users."]},
    },
}


def test_build_docx_creates_file_at_output_path(tmp_path):
    output_path = str(tmp_path / "resume.docx")
    result = resume_build.build_docx(_STRATEGY, _MASTER, output_path)
    assert result == output_path
    assert os.path.exists(output_path)


def test_build_docx_includes_role_and_project_content():
    doc_path = "test_build_docx_includes_role_and_project_content.docx"
    try:
        resume_build.build_docx(_STRATEGY, _MASTER, doc_path)
        doc = Document(doc_path)
        full_text = "\n".join(p.text for p in doc.paragraphs)
        assert "Associate Product Manager" in full_text
        assert "Protium Finance" in full_text
        assert "Viant" in full_text
        assert "Narrowed 7 vendors to 2 finalists." in full_text
    finally:
        if os.path.exists(doc_path):
            os.remove(doc_path)


def test_build_docx_applies_named_margin_preset():
    from docx.shared import Twips
    doc_path = "test_build_docx_applies_named_margin_preset.docx"
    try:
        resume_build.build_docx(_STRATEGY, _MASTER, doc_path, margin_preset="tight")
        doc = Document(doc_path)
        section = doc.sections[0]
        preset = resume_build._MARGIN_PRESETS["tight"]
        assert section.top_margin == Twips(preset["top"])
        assert section.left_margin == Twips(preset["left"])
    finally:
        if os.path.exists(doc_path):
            os.remove(doc_path)


def test_margin_ladder_covers_every_preset():
    assert set(resume_build._MARGIN_LADDER) == set(resume_build._MARGIN_PRESETS.keys())


# ── convert_to_pdf ─────────────────────────────────────────────────────────────

def test_convert_to_pdf_calls_soffice_and_returns_pdf_path(mocker, tmp_path):
    run = mocker.patch("resume_build.subprocess.run")
    docx_path = str(tmp_path / "resume.docx")
    result = resume_build.convert_to_pdf(docx_path, str(tmp_path))
    run.assert_called_once()
    args = run.call_args[0][0]
    assert args[0] == "soffice"
    assert "--headless" in args
    assert docx_path in args
    assert result == str(tmp_path / "resume.pdf")


def test_convert_to_pdf_uses_configured_timeout(mocker, tmp_path):
    run = mocker.patch("resume_build.subprocess.run")
    resume_build.convert_to_pdf(str(tmp_path / "r.docx"), str(tmp_path))
    assert run.call_args.kwargs["timeout"] == config.RESUME_SOFFICE_TIMEOUT_SECONDS


# ── page_count ─────────────────────────────────────────────────────────────────

def test_page_count_reads_pdf_page_count(mocker):
    fake_reader = MagicMock()
    fake_reader.pages = [MagicMock(), MagicMock()]
    mocker.patch("resume_build.PdfReader", return_value=fake_reader)
    assert resume_build.page_count("fake.pdf") == 2


# ── fit_to_one_page ────────────────────────────────────────────────────────────

def test_fit_to_one_page_returns_immediately_when_already_one_page(mocker, tmp_path):
    mocker.patch.object(resume_build, "build_docx", return_value="r.docx")
    mocker.patch.object(resume_build, "convert_to_pdf", return_value="r.pdf")
    mocker.patch.object(resume_build, "page_count", return_value=1)
    pdf_path, preset = resume_build.fit_to_one_page(_STRATEGY, _MASTER, "r.docx", str(tmp_path))
    assert pdf_path == "r.pdf"
    assert preset == "standard"


def test_fit_to_one_page_walks_margin_ladder_until_it_fits(mocker, tmp_path):
    mocker.patch.object(resume_build, "build_docx", return_value="r.docx")
    mocker.patch.object(resume_build, "convert_to_pdf", return_value="r.pdf")
    # 5 rungs total (line/bullet/header spacing don't change page count in this fake sequence,
    # margins rung on attempt 4 finally fits)
    mocker.patch.object(resume_build, "page_count", side_effect=[2, 2, 2, 1])
    pdf_path, preset = resume_build.fit_to_one_page(_STRATEGY, _MASTER, "r.docx", str(tmp_path))
    assert pdf_path == "r.pdf"
    assert preset in resume_build._MARGIN_LADDER


def test_fit_to_one_page_raises_still_overflow_error_after_exhausting_ladder(mocker, tmp_path):
    mocker.patch.object(resume_build, "build_docx", return_value="r.docx")
    mocker.patch.object(resume_build, "convert_to_pdf", return_value="r.pdf")
    mocker.patch.object(resume_build, "page_count", return_value=2)
    with pytest.raises(resume_build.StillOverflowError):
        resume_build.fit_to_one_page(_STRATEGY, _MASTER, "r.docx", str(tmp_path))
