import openpyxl
import pytest

import ingest_oflc_lca as ingest


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
