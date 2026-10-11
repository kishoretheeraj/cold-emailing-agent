"""Deterministic ReportLab PDF rendering from content JSON.

Same JSON -> byte-identical PDF: reportlab's `invariant` mode pins the creation date and document ID, and
nothing here reads the clock. Single column, no tables, standard Helvetica (ATS-safe text extraction).
Layout values mirror resume_tailor.render_pdf.
"""

import html
from pathlib import Path

CONTACT_ORDER = ("location", "phone", "email", "linkedin")


# ── Rendering ──────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def render_pdf(content_json: dict, output_path: str) -> int:
    """Write the resume PDF to output_path and return its page count (the caller enforces one page).
    Optional keys beyond the content schema: "name", "contact" (dict), "education" (list of strings)."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate

    base = dict(fontName="Helvetica", fontSize=9, leading=10.8, spaceAfter=1)
    styles = {
        "name": ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=15.5, leading=18,
                               alignment=TA_CENTER, spaceAfter=2),
        "contact": ParagraphStyle("contact", fontName="Helvetica", fontSize=8.5, leading=10,
                                  alignment=TA_CENTER, spaceAfter=3),
        "headline": ParagraphStyle("headline", fontName="Helvetica-Bold", fontSize=10, leading=12,
                                   alignment=TA_CENTER, spaceAfter=4),
        "heading": ParagraphStyle("heading", fontName="Helvetica-Bold", fontSize=10.5, leading=12,
                                  spaceBefore=6, spaceAfter=1),
        "role": ParagraphStyle("role", spaceBefore=2, **{**base, "fontName": "Helvetica-Bold"}),
        "bullet": ParagraphStyle("bullet", leftIndent=12, firstLineIndent=0, bulletIndent=2, **base),
        "body": ParagraphStyle("body", **base),
    }

    def heading(title):
        return [Paragraph(title, styles["heading"]),
                HRFlowable(width="100%", thickness=0.6, color=colors.black, spaceBefore=0, spaceAfter=2)]

    name = str(content_json.get("name") or "")
    contact = content_json.get("contact") or {}
    contact_line = " | ".join(str(contact[k]) for k in CONTACT_ORDER if contact.get(k))

    flow = []
    if name:
        flow.append(Paragraph(_esc(name), styles["name"]))
    if contact_line:
        flow.append(Paragraph(_esc(contact_line), styles["contact"]))
    flow.append(Paragraph(_esc(content_json["headline"]), styles["headline"]))
    flow += heading("SUMMARY")
    flow.append(Paragraph(_esc(content_json["summary"]), styles["body"]))
    flow += heading("EXPERIENCE")
    for entry in content_json["experience"]:
        flow.append(Paragraph(_esc(entry["role"]), styles["role"]))
        for bullet in entry["bullets"]:
            flow.append(Paragraph(_esc(bullet), styles["bullet"], bulletText="\u2022"))
    if content_json.get("skills"):
        flow += heading("SKILLS")
        flow.append(Paragraph(_esc(", ".join(content_json["skills"])), styles["body"]))
    if content_json.get("education"):
        flow += heading("EDUCATION")
        for line in content_json["education"]:
            flow.append(Paragraph(_esc(line), styles["body"]))

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out), pagesize=letter, leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.45 * inch, bottomMargin=0.45 * inch, title=f"{name} Resume".strip(), author=name,
        invariant=1)
    doc.build(flow)
    return doc.page
