"""Deterministic single-page cover letter PDF (ReportLab, Helvetica, invariant mode).

Name, contact block, date and salutation come from Python-side inputs (bank_facts and the render-time
clock), never from model JSON. Same content + bank_facts + date -> byte-identical PDF.
"""

import html
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CONTACT_ORDER = ("location", "phone", "email", "linkedin")
SALUTATION = "Dear Hiring Manager,"
TIMEZONE = "America/New_York"


# ── Rendering ──────────────────────────────────────────────────────────────────

def _esc(text):
    return html.escape(str(text), quote=False)


def format_date(day):
    return f"{day:%B} {day.day}, {day.year}"


def render_letter_pdf(content, bank_facts, output_path, today=None):
    """Write the letter PDF to output_path and return its page count (the caller enforces one page).
    `today` (a date) exists for tests; by default the date is the current America/New_York day."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    day = today or datetime.now(ZoneInfo(TIMEZONE)).date()
    name = str(bank_facts.get("full_name") or "")
    contact_line = " | ".join(str(bank_facts[k]) for k in CONTACT_ORDER if bank_facts.get(k))

    base = dict(fontName="Helvetica", fontSize=10.5, leading=14.5, spaceAfter=10)
    styles = {
        "name": ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=14, leading=17, spaceAfter=2),
        "contact": ParagraphStyle("contact", fontName="Helvetica", fontSize=9, leading=11, spaceAfter=18),
        "body": ParagraphStyle("body", **base),
    }

    flow = []
    if name:
        flow.append(Paragraph(_esc(name), styles["name"]))
    if contact_line:
        flow.append(Paragraph(_esc(contact_line), styles["contact"]))
    flow.append(Paragraph(_esc(format_date(day)), styles["body"]))
    flow.append(Paragraph(_esc(SALUTATION), styles["body"]))
    flow.append(Paragraph(_esc(content["opening_hook"]), styles["body"]))
    for para in content["body"]:
        flow.append(Paragraph(_esc(para["text"]), styles["body"]))
    flow.append(Paragraph(_esc(content["closing"]), styles["body"]))
    flow.append(Spacer(1, 4))
    flow.append(Paragraph("Sincerely,", styles["body"]))
    if name:
        flow.append(Paragraph(_esc(name), styles["body"]))

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out), pagesize=letter, leftMargin=1 * inch, rightMargin=1 * inch,
        topMargin=0.9 * inch, bottomMargin=0.9 * inch, title=f"{name} Cover Letter".strip(), author=name,
        invariant=1)
    doc.build(flow)
    return doc.page
