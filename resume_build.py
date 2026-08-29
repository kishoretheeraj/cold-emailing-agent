"""
Builds a resume DOCX from a strategy (resume_agent.py's stage-4 output) and the
master resume data (resume/data/master.json). Pure/deterministic -- no LLM calls,
no I/O beyond writing the output file. Raises on failure; does not swallow
exceptions, since this is a manual, interactive tool. See
docs/superpowers/specs/2026-08-29-phase3-resume-intelligence-design.md.
"""

import os
import subprocess

from docx import Document
from docx.shared import Pt, Twips
from docx.enum.text import WD_LINE_SPACING
from pypdf import PdfReader

import config

# ── Typography and spacing presets (corpus spec Part 12) ───────────────────────

_MARGIN_PRESETS = {
    "comfortable": {"top": 1080, "bottom": 1080, "left": 1152, "right": 1152},
    "standard": {"top": 1080, "bottom": 900, "left": 1080, "right": 1080},
    "tight": {"top": 780, "bottom": 720, "left": 1080, "right": 1080},
    "aggressive": {"top": 720, "bottom": 720, "left": 900, "right": 900},
    "floor": {"top": 720, "bottom": 720, "left": 720, "right": 720},
}
_MARGIN_LADDER = ["comfortable", "standard", "tight", "aggressive", "floor"]

_NAME_SIZE_PT = 22
_SECTION_HEADER_SIZE_PT = 10.5
_BODY_SIZE_PT = 10
_FONT_NAME = "Calibri"


# ── Section builders ───────────────────────────────────────────────────────────

def _apply_margins(doc, preset_name):
    preset = _MARGIN_PRESETS[preset_name]
    section = doc.sections[0]
    section.top_margin = Twips(preset["top"])
    section.bottom_margin = Twips(preset["bottom"])
    section.left_margin = Twips(preset["left"])
    section.right_margin = Twips(preset["right"])


def _add_name_heading(doc, name):
    p = doc.add_paragraph()
    run = p.add_run(name)
    run.font.name = _FONT_NAME
    run.font.size = Pt(_NAME_SIZE_PT)
    run.bold = True


def _add_section_header(doc, title):
    p = doc.add_paragraph()
    run = p.add_run(title.upper())
    run.font.name = _FONT_NAME
    run.font.size = Pt(_SECTION_HEADER_SIZE_PT)
    run.bold = True


def _add_body_line(doc, text, bullet=False):
    p = doc.add_paragraph(style="List Bullet" if bullet else None)
    run = p.add_run(text)
    run.font.name = _FONT_NAME
    run.font.size = Pt(_BODY_SIZE_PT)
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    p.paragraph_format.space_after = Pt(4)


def _add_experience_section(doc, master):
    _add_section_header(doc, "Experience")
    for role in master.get("roles", []):
        _add_body_line(doc, f"{role['title']}, {role['company']} ({role['period']})")
        for bullet in role.get("bullets", []):
            _add_body_line(doc, bullet, bullet=True)


def _add_projects_section(doc, master, projects_included):
    _add_section_header(doc, "Projects")
    for project_name in projects_included:
        project = master.get("projects", {}).get(project_name)
        if not project:
            continue
        _add_body_line(doc, project_name)
        for bullet in project.get("bullets", []):
            _add_body_line(doc, bullet, bullet=True)


def _add_education_section(doc, master):
    education = master.get("education")
    if not education:
        return
    _add_section_header(doc, "Education")
    _add_body_line(doc, f"{education['institution']} -- {education['program']} ({education['graduation']})")


_SECTION_BUILDERS = {
    "Experience": _add_experience_section,
    "Education": _add_education_section,
}


def build_docx(strategy, master, output_path, margin_preset="standard"):
    """Build a resume DOCX from `strategy` (section order, projects included) and `master`
    (resume/data/master.json content). Returns output_path."""
    doc = Document()
    _apply_margins(doc, margin_preset)
    _add_name_heading(doc, "Kishore Theeraj Vasudevan Jaya")

    for section_name in strategy.get("section_order", []):
        if section_name == "Projects":
            _add_projects_section(doc, master, strategy.get("projects_included", []))
        elif section_name in _SECTION_BUILDERS:
            _SECTION_BUILDERS[section_name](doc, master)

    doc.save(output_path)
    return output_path


# ── PDF conversion ──────────────────────────────────────────────────────────────

class StillOverflowError(Exception):
    """Raised when the deterministic fitting-ladder rungs can't get a resume to one page.
    Caller (resume_agent.py) catches this and triggers a content-editing regeneration."""


def convert_to_pdf(docx_path, output_dir):
    """Convert docx_path to PDF via LibreOffice headless. Returns the output PDF path. Raises on
    any failure (missing soffice binary, conversion error, timeout) -- never swallowed."""
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf", "--outdir", output_dir, docx_path],
        check=True, capture_output=True, timeout=config.RESUME_SOFFICE_TIMEOUT_SECONDS,
    )
    base = os.path.splitext(os.path.basename(docx_path))[0]
    return os.path.join(output_dir, base + ".pdf")


def page_count(pdf_path):
    return len(PdfReader(pdf_path).pages)


# ── Fitting ladder (corpus spec Part 13, formatting rungs only -- see Task 9's docstring note) ──

_FORMATTING_RUNGS = ["line_spacing", "bullet_spacing", "header_spacing", "margins", "font_floor"]


def fit_to_one_page(strategy, master, output_path, output_dir):
    """
    Build, convert, and check page count, walking the deterministic formatting rungs of the
    corpus spec's Part 13 ladder in order (line spacing -> bullet spacing -> header spacing ->
    margins -> font floor) until the PDF is one page. Returns (pdf_path, margin_preset_used).
    Raises StillOverflowError if still >1 page after every rung -- the caller should treat that
    as a signal to shorten content, not retry formatting again.
    """
    # Start at "standard" (build_docx's own default), not "comfortable" (ladder index 0) --
    # the ladder only ever tightens from the normal baseline, never loosens past it.
    preset_index = _MARGIN_LADDER.index("standard")
    for rung_index, rung in enumerate(_FORMATTING_RUNGS):
        preset_name = _MARGIN_LADDER[preset_index]
        docx_path = build_docx(strategy, master, output_path, margin_preset=preset_name)
        pdf_path = convert_to_pdf(docx_path, output_dir)
        if page_count(pdf_path) <= 1:
            return pdf_path, preset_name
        if rung == "margins" and preset_index < len(_MARGIN_LADDER) - 1:
            preset_index += 1
    raise StillOverflowError(
        f"still overflows one page after every formatting rung (final preset: "
        f"{_MARGIN_LADDER[preset_index]})"
    )
