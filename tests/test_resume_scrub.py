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


@pytest.fixture
def libreoffice_pdf(tmp_path):
    path = str(tmp_path / "lo.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    with pdf.open_metadata() as meta:
        meta["pdf:Producer"] = "LibreOffice 26.8"
        meta["xmp:CreatorTool"] = "Writer"
        meta["xmp:CreateDate"] = "2026-10-04T21:00:00"
    pdf.docinfo["/Producer"] = pikepdf.String("LibreOffice 26.8")
    pdf.save(path)
    return path


def test_scrub_leaves_no_pikepdf_or_libreoffice_trace(libreoffice_pdf):
    resume_scrub.scrub_pdf_metadata(libreoffice_pdf, title="Acme - Resume", keywords="PM")
    xmp = resume_scrub.read_pdf_xmp_text(libreoffice_pdf).lower()
    info = resume_scrub.read_pdf_metadata_text(libreoffice_pdf).lower()
    for trace in ("pikepdf", "libreoffice", "writer", "2026-10-04t21:00:00"):
        assert trace not in xmp, trace
        assert trace not in info, trace
    assert "microsoft: print to pdf" in xmp and "microsoft: print to pdf" in info


def test_scrub_writes_matching_dates_in_xmp_and_docinfo(libreoffice_pdf):
    resume_scrub.scrub_pdf_metadata(libreoffice_pdf, title="Acme - Resume", keywords="PM")
    with pikepdf.open(libreoffice_pdf) as pdf:
        created = str(pdf.docinfo["/CreationDate"])
        modified = str(pdf.docinfo["/ModDate"])
        assert str(pdf.docinfo["/Keywords"]) == "PM"
        with pdf.open_metadata() as meta:
            xmp_created = meta["xmp:CreateDate"]
            xmp_modified = meta["xmp:ModifyDate"]
    assert created[2:10] == xmp_created[:10].replace("-", "")
    assert modified[2:10] == xmp_modified[:10].replace("-", "")
    assert created < modified


def test_verify_no_fingerprints_catches_pikepdf():
    assert resume_scrub.verify_no_fingerprints("Producer pikepdf 10.12.0") == ["pikepdf"]


def _pdf_with_fonts(path, base_fonts):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    fonts = pikepdf.Dictionary()
    for i, name in enumerate(base_fonts):
        fonts[f"/F{i}"] = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.TrueType, BaseFont=pikepdf.Name(name)))
    pdf.pages[0].Resources = pikepdf.Dictionary(Font=fonts)
    pdf.save(path)
    return path


@pytest.mark.parametrize("names", [
    ["/AAAAAA+Calibri"], ["/BAAAAA+Calibri-Bold"], ["/CAAAAA+Calibri-Italic"],
    ["/DAAAAA+Calibri-BoldItalic"], ["/EAAAAA+Calibri,Bold"], ["/Calibri"],
])
def test_embedded_font_families_normalizes_every_face(tmp_path, names):
    assert resume_scrub.embedded_font_families(_pdf_with_fonts(str(tmp_path / "f.pdf"), names)) == {"Calibri"}


def test_embedded_font_families_finds_fonts_nested_in_form_xobjects(tmp_path):
    path = str(tmp_path / "n.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    font = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.TrueType, BaseFont=pikepdf.Name("/XYZABC+Carlito")))
    form = pdf.make_stream(b"")
    form.Type, form.Subtype = pikepdf.Name.XObject, pikepdf.Name.Form
    form.BBox = [0, 0, 10, 10]
    form.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    pdf.pages[0].Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm1=form))
    pdf.save(path)
    assert resume_scrub.embedded_font_families(path) == {"Carlito"}


def test_check_fonts_flags_libreoffice_substitutes(tmp_path):
    path = _pdf_with_fonts(str(tmp_path / "f.pdf"),
                           ["/BAAAAA+Carlito-Bold", "/CAAAAA+OpenSymbol", "/EAAAAA+Caladea-Regular",
                            "/FAAAAA+Calibri"])
    violations = resume_scrub.check_fonts(path, allowed={"Calibri"})
    assert sorted(violations) == sorted([
        "embedded font 'Caladea' is not allowed",
        "embedded font 'Carlito' is not allowed",
        "embedded font 'OpenSymbol' is not allowed",
    ])


def test_check_fonts_clean(tmp_path):
    path = _pdf_with_fonts(str(tmp_path / "f.pdf"), ["/AAAAAA+Calibri", "/BBBBBB+Calibri-Bold"])
    assert resume_scrub.check_fonts(path, allowed={"Calibri"}) == []
