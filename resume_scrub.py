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
_FINGERPRINTS = ("libreoffice", "soffice", "python-docx", "docx-js", "openoffice", "pikepdf")


def scrub_pdf_metadata(pdf_path, title, keywords):
    """Overwrite XMP and docinfo metadata on pdf_path in place. Target values match a real
    Microsoft Word export, not a LibreOffice/tool default. The existing XMP packet is deleted
    first so no LibreOffice key or real timestamp survives, and pikepdf's own editor stamp is
    disabled -- left on, it rewrites pdf:Producer to "pikepdf <version>" on save."""
    created = datetime.datetime.now() - datetime.timedelta(days=5)
    modified = datetime.datetime.now()
    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        if "/Metadata" in pdf.Root:
            del pdf.Root.Metadata
        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
            meta["xmp:CreatorTool"] = "Microsoft Word"
            meta["pdf:Producer"] = "Microsoft: Print To PDF"
            meta["dc:creator"] = ["Kishore Theeraj Vasudevan Jaya"]
            meta["dc:title"] = title
            meta["xmp:CreateDate"] = created.isoformat(timespec="seconds")
            meta["xmp:ModifyDate"] = modified.isoformat(timespec="seconds")
            meta["xmp:MetadataDate"] = modified.isoformat(timespec="seconds")

        for key in list(pdf.docinfo.keys()):
            del pdf.docinfo[key]
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


def read_pdf_xmp_text(pdf_path):
    """Concatenate a PDF's XMP metadata values into one string, for verify_no_fingerprints --
    docinfo alone misses tool names LibreOffice and pikepdf write into XMP."""
    with pikepdf.open(pdf_path) as pdf:
        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
            return " ".join(f"{k} {v}" for k, v in meta.items())


def embedded_font_families(pdf_path):
    """Family names of every font object in the file -- page resources, form XObjects, anything
    nested -- with the 6-letter subset prefix and style suffix ("-Bold", ",Italic") removed:
    "/BAAAAA+Calibri-Bold" -> "Calibri"."""
    families = set()
    with pikepdf.open(pdf_path) as pdf:
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Dictionary) and obj.get("/Type") == pikepdf.Name.Font \
                    and "/BaseFont" in obj:
                base = str(obj.BaseFont).lstrip("/").split("+", 1)[-1]
                families.add(base.split("-")[0].split(",")[0])
    families.discard("")
    return families


def check_fonts(pdf_path, allowed):
    """Return a violation string per embedded font family not in `allowed` (empty = clean)."""
    return [f"embedded font '{name}' is not allowed"
            for name in sorted(embedded_font_families(pdf_path) - set(allowed))]
