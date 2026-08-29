"""Tests for db.py's job_applications accessors."""

from unittest.mock import MagicMock

import pytest

import config
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
    fake_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
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


def test_create_job_application_skips_when_job_url_already_exists(fake_client):
    fake_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": 9}
    ]
    result = db.create_job_application(company="Acme", role="PM", job_url="https://x")
    assert result is None
    fake_client.table.return_value.insert.assert_not_called()


def test_create_job_application_skips_dedup_check_when_no_job_url(fake_client):
    fake_client.table.return_value.insert.return_value.execute.return_value.data = [{"id": 3}]
    result = db.create_job_application(company="Acme", role="PM")
    fake_client.table.return_value.select.assert_not_called()
    assert result["id"] == 3


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


def test_set_resume_strategy_updates_the_row(fake_client):
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
        {"id": 1, "resume_strategy": {"angle": "projects-first"}}
    ]
    result = db.set_resume_strategy(1, {"angle": "projects-first"})
    fake_client.table.assert_called_with("job_applications")
    updated = fake_client.table.return_value.update.call_args[0][0]
    assert updated["resume_strategy"] == {"angle": "projects-first"}
    assert result["id"] == 1


def test_set_resume_files_only_sets_provided_fields(fake_client):
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"id": 1}]
    db.set_resume_files(1, resume_file_ref="resumes/1/resume.pdf")
    updated = fake_client.table.return_value.update.call_args[0][0]
    assert updated["resume_file_ref"] == "resumes/1/resume.pdf"
    assert "cover_letter_file_ref" not in updated
    assert "resume_variant" not in updated


def test_set_resume_files_sets_all_fields_when_provided(fake_client):
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"id": 1}]
    db.set_resume_files(1, resume_file_ref="r.pdf", cover_letter_file_ref="cl.pdf", resume_variant="v1")
    updated = fake_client.table.return_value.update.call_args[0][0]
    assert updated["resume_file_ref"] == "r.pdf"
    assert updated["cover_letter_file_ref"] == "cl.pdf"
    assert updated["resume_variant"] == "v1"


def test_upload_resume_file_calls_storage_and_returns_path(fake_client):
    result = db.upload_resume_file("resumes/1/resume.pdf", b"filebytes", "application/pdf")
    fake_client.storage.from_.assert_called_with(config.RESUME_STORAGE_BUCKET)
    fake_client.storage.from_.return_value.upload.assert_called_once()
    args, kwargs = fake_client.storage.from_.return_value.upload.call_args
    assert args[0] == "resumes/1/resume.pdf"
    assert args[1] == b"filebytes"
    assert result == "resumes/1/resume.pdf"


def test_upload_resume_file_raises_on_failure(fake_client):
    fake_client.storage.from_.return_value.upload.side_effect = RuntimeError("storage down")
    with pytest.raises(RuntimeError):
        db.upload_resume_file("resumes/1/resume.pdf", b"x", "application/pdf")
