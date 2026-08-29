"""Tests for resume_build.py's DOCX construction. Uses python-docx's own reader to verify
output, not mocks -- building a real (tiny) DOCX in a temp dir is fast and exercises the
real library integration, matching how gmail.py's tests exercise real MIME construction."""

import os

from docx import Document

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
