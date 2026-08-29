"""
Overwrites PDF metadata (XMP + docinfo) that LibreOffice's PDF conversion leaves
behind, and verifies no tool fingerprint survives in the built file. Corpus spec
Part 14. No I/O beyond mutating the given file path; raises on failure. See
docs/superpowers/specs/2026-08-29-phase3-resume-intelligence-design.md.
"""

import datetime
import re

import pikepdf

# Fingerprints that must never survive in a built file's metadata (case-insensitive).
# NOTE: "claude" is deliberately excluded from this list -- it legitimately appears in
# resume *content* (Skills, project descriptions), and this function is also used to scan
# metadata-only strings where that distinction doesn't apply the same way. Callers that scan
# whole-file content (not just metadata fields) are responsible for only passing metadata text.
_FINGERPRINTS = ("libreoffice", "soffice", "python-docx", "docx-js", "openoffice")


def scrub_pdf_metadata(pdf_path, title, keywords):
    """Overwrite XMP and docinfo metadata on pdf_path in place. Target values match a real
    Microsoft Word export, not a LibreOffice/tool default."""
    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        created = datetime.datetime.now() - datetime.timedelta(days=5)
        modified = datetime.datetime.now()

        with pdf.open_metadata() as meta:
            meta["xmp:CreatorTool"] = "Microsoft Word"
            meta["pdf:Producer"] = "Microsoft: Print To PDF"
            meta["dc:creator"] = ["Kishore Theeraj Vasudevan Jaya"]
            meta["dc:title"] = title

        pdf.docinfo["/Creator"] = pikepdf.String("Microsoft Word")
        pdf.docinfo["/Producer"] = pikepdf.String("Microsoft: Print To PDF")
        pdf.docinfo["/Author"] = pikepdf.String("Kishore Theeraj Vasudevan Jaya")
        pdf.docinfo["/Title"] = pikepdf.String(title)
        pdf.docinfo["/Keywords"] = pikepdf.String(keywords)
        pdf.docinfo["/CreationDate"] = pikepdf.String(created.strftime("D:%Y%m%d%H%M%S"))
        pdf.docinfo["/ModDate"] = pikepdf.String(modified.strftime("D:%Y%m%d%H%M%S"))

        pdf.save(pdf_path)


def verify_no_fingerprints(text):
    """Return a list of matched tool-fingerprint strings found in `text` (case-insensitive).
    Empty list means clean. Callers must pass metadata/property text, not resume body content --
    'Claude' legitimately appears in Skills/Projects and is not a fingerprint by itself."""
    lowered = text.lower()
    return [fp for fp in _FINGERPRINTS if fp in lowered]


def read_pdf_metadata_text(pdf_path):
    """Concatenate a PDF's docinfo metadata field values into one string, for
    verify_no_fingerprints to scan after scrub_pdf_metadata has run."""
    with pikepdf.open(pdf_path) as pdf:
        return " ".join(str(v) for v in pdf.docinfo.values())
