"""Tests for db.py's company_intel / employer_h1b_stats accessors."""

from unittest.mock import MagicMock

import pytest

import db


@pytest.fixture
def fake_client(mocker):
    client = MagicMock(name="supabase_client")
    mocker.patch.object(db, "_client", client)
    mocker.patch.object(db, "get_client", return_value=client)
    return client


# ── get_employer_h1b_stats_corpus ────────────────────────────────────────────────

def test_get_employer_h1b_stats_corpus_returns_rows(fake_client):
    fake_client.table.return_value.select.return_value.execute.return_value.data = [
        {"id": 1, "normalized_name": "acme", "lca_recent_2fy": 5, "latest_filing_fy": 2025, "approval_rate": 0.9},
    ]
    result = db.get_employer_h1b_stats_corpus()
    fake_client.table.assert_called_with("employer_h1b_stats")
    assert result[0]["normalized_name"] == "acme"
    assert result[0]["lca_recent_2fy"] == 5


def test_get_employer_h1b_stats_corpus_empty_on_no_data(fake_client):
    fake_client.table.return_value.select.return_value.execute.return_value.data = None
    assert db.get_employer_h1b_stats_corpus() == []


def test_get_employer_h1b_stats_corpus_propagates_on_error(mocker):
    mocker.patch.object(db, "get_client", side_effect=RuntimeError("db down"))
    with pytest.raises(RuntimeError):
        db.get_employer_h1b_stats_corpus()


# ── upsert_employer_h1b_stats ────────────────────────────────────────────────────

def test_upsert_employer_h1b_stats_calls_upsert_with_payload(fake_client):
    rows = [{"normalized_name": "acme", "lca_total": 5}]
    assert db.upsert_employer_h1b_stats(rows) is True
    fake_client.table.assert_called_with("employer_h1b_stats")
    fake_client.table.return_value.upsert.assert_called_with(rows, on_conflict="normalized_name")


def test_upsert_employer_h1b_stats_empty_rows_is_noop(fake_client):
    assert db.upsert_employer_h1b_stats([]) is True
    fake_client.table.assert_not_called()


def test_upsert_employer_h1b_stats_never_raises_on_error(mocker):
    mocker.patch.object(db, "get_client", side_effect=RuntimeError("db down"))
    assert db.upsert_employer_h1b_stats([{"normalized_name": "acme"}]) is False


def test_upsert_employer_h1b_stats_logs_warning_on_error(mocker, caplog):
    import logging
    mocker.patch.object(db, "get_client", side_effect=RuntimeError("db down"))
    with caplog.at_level(logging.WARNING):
        db.upsert_employer_h1b_stats([{"normalized_name": "acme"}])
    assert any("employer_h1b_stats" in r.message for r in caplog.records)


# ── get_company_intel_by_normalized_names ────────────────────────────────────────

def test_get_company_intel_by_normalized_names_returns_rows(fake_client):
    fake_client.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
        {"normalized_name": "acme", "match_status": "confirmed"},
    ]
    result = db.get_company_intel_by_normalized_names(["acme"])
    fake_client.table.assert_called_with("company_intel")
    assert result == [{"normalized_name": "acme", "match_status": "confirmed"}]


def test_get_company_intel_by_normalized_names_empty_input_is_noop(fake_client):
    assert db.get_company_intel_by_normalized_names([]) == []
    fake_client.table.assert_not_called()


def test_get_company_intel_by_normalized_names_propagates_on_error(mocker):
    mocker.patch.object(db, "get_client", side_effect=RuntimeError("db down"))
    with pytest.raises(RuntimeError):
        db.get_company_intel_by_normalized_names(["acme"])


# ── upsert_company_intel ─────────────────────────────────────────────────────────

def test_upsert_company_intel_calls_upsert_with_payload(fake_client):
    rows = [{"normalized_name": "acme", "match_status": "auto"}]
    assert db.upsert_company_intel(rows) is True
    fake_client.table.assert_called_with("company_intel")
    fake_client.table.return_value.upsert.assert_called_with(rows, on_conflict="normalized_name")


def test_upsert_company_intel_never_raises_on_error(mocker):
    mocker.patch.object(db, "get_client", side_effect=RuntimeError("db down"))
    assert db.upsert_company_intel([{"normalized_name": "acme"}]) is False


# ── update_contact_company_intel_id ──────────────────────────────────────────────

def test_update_contact_company_intel_id_calls_update(fake_client):
    assert db.update_contact_company_intel_id(7, 42) is True
    fake_client.table.assert_called_with("contacts")
    fake_client.table.return_value.update.assert_called_with({"company_intel_id": 42})
    fake_client.table.return_value.update.return_value.eq.assert_called_with("id", 7)


def test_update_contact_company_intel_id_never_raises_on_error(mocker):
    mocker.patch.object(db, "get_client", side_effect=RuntimeError("db down"))
    assert db.update_contact_company_intel_id(7, 42) is False
