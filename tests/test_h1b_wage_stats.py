"""H-1B offered-wage stats: ingest_oflc_lca's wage folding, db accessors, and migration 20261007000000."""

import re
from pathlib import Path
from unittest.mock import MagicMock

import openpyxl
import pytest

import db
import ingest_oflc_lca as ingest


# ── Role families and wage parsing ───────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("Product Manager", "product_manager"),
    ("Senior Product Manager, Payments", "product_manager"),
    ("Associate Product Manager", "product_manager"),
    ("Technical Product Owner", "product_manager"),
    ("Director of Product Management", None),
    ("VP, Product Management", None),
    ("Head of Product Management", None),
    ("Software Engineer", None),
    ("Product Designer", None),
    (None, None),
])
def test_role_family_for(title, expected):
    assert ingest.role_family_for(title) == expected


@pytest.mark.parametrize("wage,unit,expected", [
    (150000, "Year", 150000),
    ("$150,000.00", "Year", 150000),
    (75, "Hour", 156000),
    (12500, "Month", 150000),
    (5000, "Bi-Weekly", 130000),
    (2900, "Week", 150800),
    (150000, None, 150000),
    (10, "Year", None),            # below the sanity floor
    (5000000, "Year", None),       # above the sanity ceiling
    (150000, "Fortnight", None),   # unknown unit
    ("", "Year", None),
    ("n/a", "Year", None),
])
def test_annualize_wage(wage, unit, expected):
    assert ingest.annualize_wage(wage, unit) == expected


def test_fold_wage_keys_by_normalized_employer_family_and_state():
    acc = {}
    ingest.fold_wage(acc, "Stripe, Inc.", "Product Manager", "ca", 180000, "Year")
    ingest.fold_wage(acc, "Stripe, Inc.", "Software Engineer", "CA", 200000, "Year")
    ingest.fold_wage(acc, "Stripe, Inc.", "Product Manager", None, 170000, "Year")
    assert acc == {("stripe", "product_manager", "CA"): [180000], ("stripe", "product_manager", None): [170000]}


def test_build_wage_rows_pools_employer_and_market_levels():
    acc = {("stripe", "product_manager", "CA"): [100000, 200000],
           ("acme", "product_manager", "CA"): [150000],
           ("acme", "product_manager", None): [120000]}
    rows = {(r["normalized_name"], r["worksite_state"]): r for r in ingest.build_wage_rows(acc, [2026, 2025])}

    assert set(rows) == {("stripe", "CA"), ("stripe", "*"), ("acme", "CA"), ("acme", "*"), ("*", "CA"), ("*", "*")}
    assert rows[("stripe", "CA")]["filings"] == 2
    assert rows[("acme", "*")]["filings"] == 2           # its stateless filing counts nationally only
    assert rows[("*", "CA")]["filings"] == 3
    assert rows[("*", "*")]["filings"] == 4
    assert rows[("stripe", "CA")]["wage_p25"] == 125000
    assert rows[("stripe", "CA")]["wage_median"] == 150000
    assert rows[("stripe", "CA")]["wage_p75"] == 175000
    assert rows[("acme", "CA")]["wage_p25"] == rows[("acme", "CA")]["wage_p75"] == 150000
    assert rows[("*", "*")]["fiscal_years"] == [2025, 2026]
    assert all(r["role_family"] == "product_manager" and r["updated_at"] for r in rows.values())


def test_parse_lca_file_collects_wages_for_role_family_titles(tmp_path):
    path = tmp_path / "fy2026.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["EMPLOYER_NAME", "JOB_TITLE", "WORKSITE_STATE", "CASE_STATUS", "VISA_CLASS",
               "WAGE_RATE_OF_PAY_FROM", "WAGE_UNIT_OF_PAY"])
    ws.append(["Stripe, Inc.", "Product Manager", "CA", "Certified", "H-1B", "185000", "Year"])
    ws.append(["Stripe, Inc.", "Product Manager", "CA", "Denied", "H-1B", "999000", "Year"])
    ws.append(["Stripe, Inc.", "Product Manager", "CA", "Certified", "E-3 Australian", "111000", "Year"])
    ws.append(["Stripe, Inc.", "Software Engineer", "CA", "Certified", "H-1B", "210000", "Year"])
    wb.save(path)
    acc, wages = {}, {}

    assert ingest.parse_lca_file(str(path), 2026, acc, wages) == 2
    assert wages == {("stripe", "product_manager", "CA"): [185000]}


def test_parse_lca_file_without_wage_accumulator_is_unchanged(tmp_path):
    path = tmp_path / "fy2026.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["EMPLOYER_NAME", "CASE_STATUS"])
    ws.append(["Stripe, Inc.", "Certified"])
    wb.save(path)
    acc = {}
    assert ingest.parse_lca_file(str(path), 2026, acc) == 1
    assert acc["stripe"]["lca_total"] == 1


def test_run_upserts_wage_rows_after_employer_rows(mocker, tmp_path):
    mocker.patch.object(ingest, "current_dol_fiscal_year", return_value=2026)
    mocker.patch.object(ingest, "discover_lca_file_urls", return_value={2026: "https://example.test/fy26.xlsx"})
    mocker.patch.object(ingest, "download_file")

    def fake_parse(path, fy, acc, wages):
        ingest.fold_row(acc, "Stripe, Inc.", fy)
        wages[("stripe", "product_manager", "CA")] = [180000]
        return 1

    mocker.patch.object(ingest, "parse_lca_file", side_effect=fake_parse)
    calls = []
    mocker.patch.object(ingest.db, "upsert_employer_h1b_stats", side_effect=lambda rows: calls.append("employers"))
    upsert_wages = mocker.patch.object(ingest.db, "upsert_h1b_wage_stats",
                                       side_effect=lambda rows: calls.append("wages") or True)
    record = mocker.patch.object(ingest.db, "record_run")

    ingest.run(fiscal_years_back=1)

    assert calls == ["employers", "wages"]
    keys = {(r["normalized_name"], r["worksite_state"]) for r in upsert_wages.call_args[0][0]}
    assert keys == {("stripe", "CA"), ("stripe", "*"), ("*", "CA"), ("*", "*")}
    assert record.call_args[0][3] == 0  # errors


# ── db accessors ─────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_client(mocker):
    client = MagicMock(name="supabase_client")
    mocker.patch.object(db, "_client", client)
    mocker.patch.object(db, "get_client", return_value=client)
    return client


def test_upsert_h1b_wage_stats_uses_the_composite_key(fake_client):
    assert db.upsert_h1b_wage_stats([{"normalized_name": "stripe"}]) is True
    fake_client.table.assert_called_with("h1b_wage_stats")
    fake_client.table.return_value.upsert.assert_called_once_with(
        [{"normalized_name": "stripe"}], on_conflict="normalized_name,role_family,worksite_state")


def test_upsert_h1b_wage_stats_is_best_effort(fake_client, mocker):
    mocker.patch.object(db, "_retry", side_effect=RuntimeError("db down"))
    assert db.upsert_h1b_wage_stats([{"normalized_name": "stripe"}]) is False
    assert db.upsert_h1b_wage_stats([]) is True


def test_get_h1b_wage_stats_filters_names_and_family(fake_client):
    chain = fake_client.table.return_value.select.return_value.in_.return_value.eq.return_value
    chain.execute.return_value.data = [{"normalized_name": "*"}]
    assert db.get_h1b_wage_stats(["*", "stripe"], "product_manager") == [{"normalized_name": "*"}]
    fake_client.table.return_value.select.return_value.in_.assert_called_once_with("normalized_name", ["*", "stripe"])
    fake_client.table.return_value.select.return_value.in_.return_value.eq.assert_called_once_with(
        "role_family", "product_manager")
    assert db.get_h1b_wage_stats([], "product_manager") == []


def test_get_employer_h1b_normalized_name(fake_client):
    chain = fake_client.table.return_value.select.return_value.eq.return_value.limit.return_value
    chain.execute.return_value.data = [{"normalized_name": "axs group"}]
    assert db.get_employer_h1b_normalized_name(7) == "axs group"
    chain.execute.return_value.data = []
    assert db.get_employer_h1b_normalized_name(7) is None
    assert db.get_employer_h1b_normalized_name(None) is None


# ── Migration 20261007000000 ─────────────────────────────────────────────────────

_SQL = re.sub(r"--[^\n]*", "", (Path(__file__).resolve().parent.parent / "supabase" / "migrations"
                                / "20261007000000_create_h1b_wage_stats.sql").read_text())


def test_migration_creates_the_table_idempotently_with_the_upsert_key():
    assert "CREATE TABLE IF NOT EXISTS h1b_wage_stats" in _SQL
    assert re.search(r"CREATE UNIQUE INDEX IF NOT EXISTS \w+\s+ON h1b_wage_stats "
                     r"\(normalized_name, role_family, worksite_state\)", _SQL)
    for column in ("filings", "wage_p25", "wage_median", "wage_p75", "fiscal_years", "updated_at"):
        assert column in _SQL


def test_migration_grants_the_anon_writer_and_never_delete():
    assert "GRANT SELECT, INSERT, UPDATE ON h1b_wage_stats TO anon, authenticated;" in _SQL
    assert "DELETE" not in _SQL.upper().replace("ON DELETE", "")
