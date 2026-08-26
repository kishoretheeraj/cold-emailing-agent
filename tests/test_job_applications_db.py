"""Tests for db.py's job_applications accessors."""

from unittest.mock import MagicMock

import pytest

import db


@pytest.fixture
def fake_client(mocker):
    client = MagicMock(name="supabase_client")
    mocker.patch.object(db, "_client", client)
    mocker.patch.object(db, "get_client", return_value=client)
    return client


def test_create_job_application_inserts_with_default_stage(fake_client):
    fake_client.table.return_value.insert.return_value.execute.return_value.data = [
        {"id": 1, "company": "Acme", "role": "PM", "stage": "saved"}
    ]
    result = db.create_job_application(company="Acme", role="PM")
    fake_client.table.assert_called_with("job_applications")
    inserted = fake_client.table.return_value.insert.call_args[0][0]
    assert inserted["stage"] == "saved"
    assert inserted["company"] == "Acme"
    assert inserted["role"] == "PM"
    assert result["id"] == 1


def test_create_job_application_passes_optional_fields(fake_client):
    fake_client.table.return_value.insert.return_value.execute.return_value.data = [{"id": 2}]
    db.create_job_application(
        company="Acme", role="PM", job_url="https://x", source="manual",
        contact_id=5, applied_date="2026-08-26", notes="hi",
        posting_snapshot={"salary": "150k"},
    )
    inserted = fake_client.table.return_value.insert.call_args[0][0]
    assert inserted["job_url"] == "https://x"
    assert inserted["source"] == "manual"
    assert inserted["contact_id"] == 5
    assert inserted["applied_date"] == "2026-08-26"
    assert inserted["notes"] == "hi"
    assert inserted["posting_snapshot"] == {"salary": "150k"}


def test_create_job_application_returns_none_on_empty_data(fake_client):
    fake_client.table.return_value.insert.return_value.execute.return_value.data = []
    assert db.create_job_application(company="Acme", role="PM") is None


def test_get_job_applications_returns_all_rows(fake_client):
    fake_client.table.return_value.select.return_value.order.return_value.execute.return_value.data = [
        {"id": 1, "stage": "saved"}, {"id": 2, "stage": "applied"},
    ]
    result = db.get_job_applications()
    assert len(result) == 2


def test_get_job_applications_filters_by_stage(fake_client):
    fake_client.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = [
        {"id": 2, "stage": "applied"},
    ]
    result = db.get_job_applications(stage="applied")
    fake_client.table.return_value.select.return_value.eq.assert_called_with("stage", "applied")
    assert result == [{"id": 2, "stage": "applied"}]


def test_get_job_applications_empty_on_no_data(fake_client):
    fake_client.table.return_value.select.return_value.order.return_value.execute.return_value.data = None
    assert db.get_job_applications() == []


def test_get_job_applications_propagates_on_error(mocker):
    mocker.patch.object(db, "get_client", side_effect=RuntimeError("db down"))
    with pytest.raises(RuntimeError):
        db.get_job_applications()


def test_update_job_application_stage(fake_client):
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
        {"id": 1, "stage": "onsite"}
    ]
    result = db.update_job_application_stage(1, "onsite")
    updated = fake_client.table.return_value.update.call_args[0][0]
    assert updated["stage"] == "onsite"
    assert "updated_at" in updated
    fake_client.table.return_value.update.return_value.eq.assert_called_with("id", 1)
    assert result["stage"] == "onsite"


def test_update_job_application_stage_returns_none_on_empty_data(fake_client):
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []
    assert db.update_job_application_stage(1, "onsite") is None


def test_get_job_application_by_id(fake_client):
    fake_client.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": 1, "company": "Acme"
    }
    result = db.get_job_application(1)
    fake_client.table.return_value.select.return_value.eq.assert_called_with("id", 1)
    assert result["company"] == "Acme"
