from datetime import datetime, timezone

import openpyxl
import pytest

import ingest_oflc_lca as ingest


# ── parse_lca_links (real DOL page naming conventions, verified 2026-08-10) ──────

# A trimmed excerpt reproducing the actual link shapes found on
# https://www.dol.gov/agencies/eta/foreign-labor/performance -- the FY lives
# in the filename, not surrounding link text, and other visa-program series
# (CW-1, H-2A, H-2B, PW) use overlapping "FY20XX"/"Disclosure_Data" tokens
# that must NOT be picked up as H-1B/LCA files.
_SAMPLE_DOL_HTML = """
<a href="/sites/dolgov/files/ETA/oflc/pdfs/CW-1_Disclosure_Data_FY2025_Q4.xlsx">CW-1 FY2025 Q4</a>
<a href="/sites/dolgov/files/ETA/oflc/pdfs/H-2A_Addendum_A_Disclosure_Data_FY2024_Q4.xlsx">H-2A FY2024 Q4</a>
<a href="/sites/dolgov/files/ETA/oflc/pdfs/LCA_Disclosure_Data_FY2024_Q1.xlsx">LCA FY2024 Q1</a>
<a href="/sites/dolgov/files/ETA/oflc/pdfs/LCA_Disclosure_Data_FY2024_Q4.xlsx">LCA FY2024 Q4</a>
<a href="/sites/dolgov/files/ETA/oflc/pdfs/LCA_Disclosure_Data_FY2025_Q3.xlsx">LCA FY2025 Q3</a>
<a href="/sites/dolgov/files/ETA/oflc/pdfs/LCA_Worksites_FY2025_Q4.xlsx">LCA Worksites FY2025 Q4</a>
<a href="/sites/dolgov/files/ETA/oflc/pdfs/H-1B_Disclosure_Data_FY2019.xlsx">H-1B FY2019</a>
<a href="https://www.dol.gov/media/LCA_Disclosure_Data_FY2026_Q2.xlsx">LCA FY2026 Q2</a>
"""


def test_parse_lca_links_picks_highest_quarter_per_fy():
    result = ingest.parse_lca_links(_SAMPLE_DOL_HTML)
    assert "2024_Q4" in result[2024]
    assert "2024_Q1" not in result[2024]


def test_parse_lca_links_ignores_other_visa_program_series():
    result = ingest.parse_lca_links(_SAMPLE_DOL_HTML)
    assert not any("CW-1" in url or "H-2A" in url for url in result.values())


def test_parse_lca_links_ignores_worksites_appendix_series():
    # LCA_Worksites_* is a different data cut, not the main disclosure file.
    result = ingest.parse_lca_links(_SAMPLE_DOL_HTML)
    assert "Worksites" not in result.get(2025, "")


def test_parse_lca_links_falls_back_to_pre_2020_annual_series():
    result = ingest.parse_lca_links(_SAMPLE_DOL_HTML)
    assert "H-1B_Disclosure_Data_FY2019" in result[2019]


def test_parse_lca_links_resolves_relative_and_absolute_urls():
    result = ingest.parse_lca_links(_SAMPLE_DOL_HTML)
    assert result[2024].startswith("https://www.dol.gov/")
    assert result[2026].startswith("https://www.dol.gov/")


def test_parse_lca_links_tolerates_dols_own_disclosure_typo():
    # DOL's live site has shipped "LCA_Dislclosure_Data" (confirmed 2026-08-10)
    # for at least one current-quarter link -- this was the only FY2026 link
    # on the page at all, so silently dropping it would leave the most
    # recent fiscal year permanently unmatched.
    html = '<a href="https://www.dol.gov/media/LCA_Dislclosure_Data_FY2026_Q2.xlsx">LCA FY2026 Q2</a>'
    result = ingest.parse_lca_links(html)
    assert result[2026] == "https://www.dol.gov/media/LCA_Dislclosure_Data_FY2026_Q2.xlsx"


def test_parse_lca_links_empty_or_unrecognizable_html_returns_empty_dict():
    assert ingest.parse_lca_links("") == {}
    assert ingest.parse_lca_links("<html><body>no links here</body></html>") == {}


# ── current_dol_fiscal_year ───────────────────────────────────────────────────────

def test_current_dol_fiscal_year_before_october_matches_calendar_year():
    assert ingest.current_dol_fiscal_year(datetime(2026, 8, 10, tzinfo=timezone.utc)) == 2026


def test_current_dol_fiscal_year_october_onward_is_next_calendar_year():
    assert ingest.current_dol_fiscal_year(datetime(2026, 10, 1, tzinfo=timezone.utc)) == 2027
    assert ingest.current_dol_fiscal_year(datetime(2026, 12, 31, tzinfo=timezone.utc)) == 2027


# ── resolve_columns ──────────────────────────────────────────────────────────────

def test_resolve_columns_current_era_headers():
    header = ["EMPLOYER_NAME", "SOC_CODE", "WORKSITE_STATE", "PW_WAGE_LEVEL", "CASE_STATUS"]
    resolved, missing = ingest.resolve_columns(header)
    assert missing == set()
    assert resolved["employer_name"] == 0
    assert resolved["soc_code"] == 1
    assert resolved["case_status"] == 4


def test_resolve_columns_older_era_header_spellings():
    # Reproduces cross-year drift: different raw spellings for the same fields.
    header = ["Employer Name", "PW_SOC_CODE", "WORKSITE_STATE_1", "WAGE_LEVEL", "STATUS"]
    resolved, missing = ingest.resolve_columns(header)
    assert missing == set()
    assert resolved["employer_name"] == 0
    assert resolved["soc_code"] == 1
    assert resolved["wage_level"] == 3
    assert resolved["case_status"] == 4


def test_resolve_columns_missing_required_field():
    header = ["SOC_CODE", "WORKSITE_STATE"]
    resolved, missing = ingest.resolve_columns(header)
    assert missing == {"employer_name"}


def test_resolve_columns_missing_optional_field_degrades():
    header = ["EMPLOYER_NAME"]
    resolved, missing = ingest.resolve_columns(header)
    assert missing == set()
    assert "wage_level" not in resolved


# ── normalize_case_status / normalize_wage_level ─────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Certified", "certified"),
    ("CERTIFIED", "certified"),
    ("Certified - Withdrawn", "certified_withdrawn"),
    ("CERTIFIED-WITHDRAWN", "certified_withdrawn"),
    ("Denied", "denied"),
    ("Withdrawn", "withdrawn"),
    (None, "unknown"),
    ("", "unknown"),
    ("Some Unrecognized Status", "unknown"),
])
def test_normalize_case_status(raw, expected):
    assert ingest.normalize_case_status(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("I", "I"), ("Level I", "I"), ("1", "I"),
    ("IV", "IV"), ("Level 4", "IV"),
    (None, "unknown"), ("garbage", "unknown"),
])
def test_normalize_wage_level(raw, expected):
    assert ingest.normalize_wage_level(raw) == expected


# ── fold_row ─────────────────────────────────────────────────────────────────────

def test_fold_row_aggregates_across_multiple_rows():
    acc = {}
    ingest.fold_row(acc, "Acme Inc.", 2024, soc_code="15-1252", worksite_state="NY", wage_level="II")
    ingest.fold_row(acc, "ACME, INC", 2025, soc_code="15-1252", worksite_state="CA", wage_level="III")

    entry = acc["acme"]
    assert entry["lca_total"] == 2
    assert entry["lca_by_fy"] == {2024: 1, 2025: 1}
    assert entry["socs"] == {"15-1252"}
    assert entry["worksite_states"] == {"NY", "CA"}
    assert entry["wage_level_counts"]["II"] == 1
    assert entry["wage_level_counts"]["III"] == 1


def test_fold_row_skips_empty_employer_name():
    acc = {}
    ingest.fold_row(acc, "", 2024)
    assert acc == {}


# ── parse_lca_file (constructed xlsx fixtures, reproducing header drift) ────────

def _write_workbook(path, header, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    wb.save(path)


def test_parse_lca_file_current_era(tmp_path):
    path = tmp_path / "fy2025.xlsx"
    _write_workbook(
        path,
        ["EMPLOYER_NAME", "SOC_CODE", "WORKSITE_STATE", "PW_WAGE_LEVEL", "CASE_STATUS"],
        [
            ["Acme Inc.", "15-1252", "NY", "II", "Certified"],
            ["Acme Inc.", "15-1252", "CA", "III", "Certified"],
            ["Widgets LLC", "13-1111", "TX", "I", "Denied"],
        ],
    )
    acc = {}
    rows_folded = ingest.parse_lca_file(str(path), 2025, acc)
    assert rows_folded == 2  # denied row excluded
    assert acc["acme"]["lca_total"] == 2
    assert "widgets" not in acc


def test_parse_lca_file_filters_out_h1b1_and_e3_visa_classes(tmp_path):
    # The consolidated LCA file (FY2020+) covers H-1B, H-1B1, and E-3
    # together via a VISA_CLASS column -- this module is H-1B specifically.
    path = tmp_path / "fy2026.xlsx"
    _write_workbook(
        path,
        ["EMPLOYER_NAME", "CASE_STATUS", "VISA_CLASS"],
        [
            ["Acme Inc.", "Certified", "H-1B"],
            ["Acme Inc.", "Certified", "H-1B1 Chile"],
            ["Acme Inc.", "Certified", "H-1B1 Singapore"],
            ["Acme Inc.", "Certified", "E-3 Australian"],
        ],
    )
    acc = {}
    rows_folded = ingest.parse_lca_file(str(path), 2026, acc)
    assert rows_folded == 1
    assert acc["acme"]["lca_total"] == 1


def test_parse_lca_file_blank_visa_class_cell_degrades_to_no_filter(tmp_path):
    # Regression: a present-but-blank VISA_CLASS cell (whitespace-only, which
    # is how openpyxl round-trips it -- a truly empty cell reads back as
    # None, already handled) must degrade to "don't filter" like an absent
    # column does -- not be treated as "confirmed not H-1B" and silently
    # dropped, which would undercount a real H-1B filer's lca_recent_2fy.
    path = tmp_path / "fy2026.xlsx"
    _write_workbook(
        path,
        ["EMPLOYER_NAME", "CASE_STATUS", "VISA_CLASS"],
        [["Acme Inc.", "Certified", " "]],
    )
    acc = {}
    rows_folded = ingest.parse_lca_file(str(path), 2026, acc)
    assert rows_folded == 1
    assert acc["acme"]["lca_total"] == 1


def test_parse_lca_file_missing_visa_class_column_degrades_to_no_filter(tmp_path):
    # Pre-2020 vintages have no VISA_CLASS column at all (H-1B only then).
    path = tmp_path / "fy2019.xlsx"
    _write_workbook(
        path,
        ["EMPLOYER_NAME", "CASE_STATUS"],
        [["Acme Inc.", "Certified"]],
    )
    acc = {}
    rows_folded = ingest.parse_lca_file(str(path), 2019, acc)
    assert rows_folded == 1


def test_parse_lca_file_older_era_headers_still_resolve(tmp_path):
    path = tmp_path / "fy2019.xlsx"
    _write_workbook(
        path,
        ["Employer Name", "PW_SOC_CODE", "WORKSITE_STATE_1", "WAGE_LEVEL", "STATUS"],
        [["Beta Corp", "11-2021", "WA", "Level II", "CERTIFIED"]],
    )
    acc = {}
    rows_folded = ingest.parse_lca_file(str(path), 2019, acc)
    assert rows_folded == 1
    assert acc["beta"]["lca_total"] == 1
    assert acc["beta"]["wage_level_counts"]["II"] == 1


def test_parse_lca_file_missing_required_column_raises(tmp_path):
    path = tmp_path / "broken.xlsx"
    _write_workbook(path, ["SOC_CODE", "WORKSITE_STATE"], [["15-1252", "NY"]])
    with pytest.raises(ingest.MissingRequiredColumnError):
        ingest.parse_lca_file(str(path), 2024, {})


def test_parse_lca_file_missing_optional_column_degrades_without_abort(tmp_path):
    path = tmp_path / "no_wage_level.xlsx"
    _write_workbook(
        path,
        ["EMPLOYER_NAME", "CASE_STATUS"],
        [["Gamma LLC", "Certified"]],
    )
    acc = {}
    rows_folded = ingest.parse_lca_file(str(path), 2024, acc)
    assert rows_folded == 1
    assert acc["gamma"]["wage_level_counts"]["unknown"] == 1


# ── build_rows_for_upsert / materiality filter ───────────────────────────────────

def test_build_rows_for_upsert_applies_materiality_filter():
    acc = {}
    ingest.fold_row(acc, "Active Widgets", 2024)
    ingest.fold_row(acc, "Active Widgets", 2025)
    ingest.fold_row(acc, "Stale Widgets", 2019)  # not in the ingested/recent window

    rows = ingest.build_rows_for_upsert(acc, ingested_fiscal_years=[2019, 2024, 2025])
    names = {r["normalized_name"] for r in rows}
    assert "active widgets" in names
    # Stale Widgets has zero LCAs in the two most recent ingested FYs (2024, 2025)
    assert "stale widgets" not in names


def test_build_rows_for_upsert_respects_row_cap():
    acc = {}
    for i in range(10):
        ingest.fold_row(acc, f"Company {i}", 2025)
    rows = ingest.build_rows_for_upsert(acc, ingested_fiscal_years=[2025], max_rows=3)
    assert len(rows) == 3


def test_build_rows_for_upsert_row_shape():
    acc = {}
    ingest.fold_row(acc, "Acme Inc.", 2025, soc_code="15-1252", worksite_state="NY", wage_level="II")
    rows = ingest.build_rows_for_upsert(acc, ingested_fiscal_years=[2025])
    row = rows[0]
    assert row["normalized_name"] == "acme"
    assert row["display_name"] == "Acme Inc."
    assert row["lca_recent_2fy"] == 1
    assert row["distinct_socs"] == 1
    assert row["latest_filing_fy"] == 2025
    assert "oflc_lca" in row["source_vintages"]
