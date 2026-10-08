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


@pytest.fixture
def no_duplicates(mocker):
    return mocker.patch.object(db, "_dedup_rows", return_value=[])


def test_create_job_application_inserts_with_default_stage(fake_client, no_duplicates):
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


def test_create_job_application_passes_optional_fields(fake_client, no_duplicates):
    fake_client.table.return_value.insert.return_value.execute.return_value.data = [{"id": 2}]
    db.create_job_application(
        company="Acme", role="PM", job_url="https://x.example/jobs/1", source="manual",
        contact_id=5, applied_date="2026-08-26", notes="hi",
        posting_snapshot={"salary": "150k"}, location="Boston, MA", posted_at="2026-10-01T00:00:00+00:00",
    )
    inserted = fake_client.table.return_value.insert.call_args[0][0]
    assert inserted["job_url"] == "https://x.example/jobs/1"
    assert inserted["source"] == "manual"
    assert inserted["contact_id"] == 5
    assert inserted["applied_date"] == "2026-08-26"
    assert inserted["notes"] == "hi"
    assert inserted["posting_snapshot"] == {"salary": "150k"}
    assert inserted["location"] == "Boston, MA"
    assert inserted["posted_at"] == "2026-10-01T00:00:00+00:00"


def test_create_job_application_writes_the_identity_fields(fake_client, no_duplicates):
    fake_client.table.return_value.insert.return_value.execute.return_value.data = [{"id": 3}]
    description = " ".join(f"word{i}" for i in range(80))
    db.create_job_application(company="Figma, Inc.", role="Associate Product Manager",
                              job_url="https://boards.greenhouse.io/figma/jobs/6180116004?gh_src=x",
                              posting_snapshot={"description": description})
    inserted = fake_client.table.return_value.insert.call_args[0][0]
    assert inserted["job_key"] == "greenhouse:6180116004"
    assert inserted["platform"] == "greenhouse"
    assert inserted["company_key"] == "figma"
    assert inserted["title_key"] == "associate manager product"
    assert len(inserted["jd_fingerprint"]) == 16


def test_create_job_application_returns_none_on_empty_data(fake_client, no_duplicates):
    fake_client.table.return_value.insert.return_value.execute.return_value.data = []
    assert db.create_job_application(company="Acme", role="PM") is None
    assert db.save_job_application(company="Acme", role="PM") == (None, "no_row")


def test_create_job_application_skips_a_duplicate_without_inserting(fake_client, mocker):
    mocker.patch.object(db, "_dedup_rows", return_value=[{"id": 9, "job_key": "lever:" + "a" * 8}])
    mocker.patch.object(db, "duplicate_reason", return_value="same_job")
    assert db.save_job_application(company="Acme", role="PM", job_url="https://x") == (None, "same_job")
    fake_client.table.return_value.insert.assert_not_called()


def test_a_lost_insert_race_returns_none_at_once_instead_of_retrying(fake_client, no_duplicates, mocker):
    sleep = mocker.patch("db.time.sleep")
    fake_client.table.return_value.insert.return_value.execute.side_effect = Exception(
        "{'code': '23505', 'message': 'duplicate key value violates unique constraint'}")
    assert db.save_job_application(company="Acme", role="PM", job_url="https://x") == (None, "conflict")
    assert fake_client.table.return_value.insert.return_value.execute.call_count == 1
    sleep.assert_not_called()


def test_other_insert_errors_are_still_retried_and_raised(fake_client, no_duplicates, mocker):
    mocker.patch("db.time.sleep")
    fake_client.table.return_value.insert.return_value.execute.side_effect = Exception("503 upstream")
    with pytest.raises(Exception, match="503"):
        db.create_job_application(company="Acme", role="PM")
    assert fake_client.table.return_value.insert.return_value.execute.call_count == 3


def test_dedup_rows_queries_key_url_and_title_identity(fake_client):
    table = fake_client.table.return_value
    table.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{"id": 1}]
    (table.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value
     .execute.return_value.data) = [{"id": 2}]
    fields = {"job_key": "lever:abc", "company_key": "acme", "title_key": "manager product"}
    rows = db._dedup_rows(fields, "https://jobs.lever.co/acme/abc")
    assert [r["id"] for r in rows] == [1, 1, 2]
    eq_calls = [c.args for c in table.select.return_value.eq.call_args_list]
    assert ("job_key", "lever:abc") in eq_calls and ("job_url", "https://jobs.lever.co/acme/abc") in eq_calls
    assert ("company_key", "acme") in eq_calls


def test_dedup_rows_skips_queries_it_has_no_value_for(fake_client):
    assert db._dedup_rows({"job_key": None, "company_key": None, "title_key": None}, None) == []
    fake_client.table.assert_not_called()


# ── duplicate_reason (pure) ────────────────────────────────────────────────────

_NOW = db.datetime(2026, 10, 9, tzinfo=db.timezone.utc)


def _row(days_ago, **over):
    created = (_NOW - db.timedelta(days=days_ago)).isoformat()
    return {"id": 1, "job_url": "https://old", "job_key": "k:old", "stage": "saved",
            "automation_status": "idle", "created_at": created, "jd_fingerprint": None, **over}


_FIELDS = {"job_key": "k:new", "company_key": "acme", "title_key": "manager product", "jd_fingerprint": None}


@pytest.mark.parametrize("rows,url,expected", [
    ([], "https://new", None),
    ([_row(400, job_key="k:new")], "https://new", "same_job"),
    ([_row(400, job_url="https://new")], "https://new", "same_job"),
    ([_row(10)], "https://new", "same_role"),
    ([_row(10, stage="withdrawn")], "https://new", "same_role"),
    ([_row(60)], "https://new", None),
    ([_row(60, stage="applied")], "https://new", "repost_of_applied"),
    ([_row(60, automation_status="submitted")], "https://new", "repost_of_applied"),
    ([_row(200, stage="applied")], "https://new", None),
])
def test_duplicate_reason(rows, url, expected):
    assert db.duplicate_reason(_FIELDS, url, rows, now=_NOW) == expected


def test_a_different_posting_for_the_same_title_is_not_a_repost():
    jd = "We need a product manager for payments onboarding activation and retention " * 6
    other = "Warehouse forklift operator loading trucks scanning inventory on weekend shifts " * 6
    fields = dict(_FIELDS, jd_fingerprint=db.job_identity.fingerprint(jd))
    applied = _row(60, stage="applied", jd_fingerprint=db.job_identity.fingerprint(other))
    same = _row(60, stage="applied", jd_fingerprint=db.job_identity.fingerprint(jd))
    assert db.duplicate_reason(fields, "https://new", [applied], now=_NOW) is None
    assert db.duplicate_reason(fields, "https://new", [same], now=_NOW) == "repost_of_applied"


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


def test_set_resume_files_writes_fresh_documents_version_each_call(fake_client):
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"id": 1}]
    import uuid
    versions = []
    for _ in range(2):
        db.set_resume_files(1, resume_file_ref="r.pdf")
        versions.append(fake_client.table.return_value.update.call_args[0][0]["documents_version"])
    assert versions[0] != versions[1]
    assert str(uuid.UUID(versions[0])) == versions[0]


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


def test_record_resume_usage_accumulates_onto_existing_totals(fake_client):
    fake_client.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": 1, "resume_tokens_input": 100, "resume_tokens_output": 50, "resume_cost_usd": 0.001050,
    }
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"id": 1}]
    db.record_resume_usage(1, tokens_input=200, tokens_output=100, cost_usd=0.002100)
    updated = fake_client.table.return_value.update.call_args[0][0]
    assert updated["resume_tokens_input"] == 300
    assert updated["resume_tokens_output"] == 150
    assert updated["resume_cost_usd"] == pytest.approx(0.003150)


def test_record_resume_usage_starts_from_zero_when_no_prior_usage(fake_client):
    fake_client.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": 1, "resume_tokens_input": None, "resume_tokens_output": None, "resume_cost_usd": None,
    }
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"id": 1}]
    db.record_resume_usage(1, tokens_input=200, tokens_output=100, cost_usd=0.002100)
    updated = fake_client.table.return_value.update.call_args[0][0]
    assert updated["resume_tokens_input"] == 200
    assert updated["resume_tokens_output"] == 100
    assert updated["resume_cost_usd"] == pytest.approx(0.002100)


def test_get_unscored_saved_applications_filters_correctly(fake_client):
    fake_client.table.return_value.select.return_value.eq.return_value.is_.return_value.order.return_value.execute.return_value.data = [
        {"id": 1, "stage": "saved", "pick_verdict": None}
    ]
    result = db.get_unscored_saved_applications()
    fake_client.table.assert_called_with("job_applications")
    assert result == [{"id": 1, "stage": "saved", "pick_verdict": None}]


def test_set_pick_verdict_writes_all_three_fields(fake_client):
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"id": 1}]
    db.set_pick_verdict(1, "strong", 0.62, "Title and skills overlap heavily.")
    updated = fake_client.table.return_value.update.call_args[0][0]
    assert updated["pick_verdict"] == "strong"
    assert updated["pick_score"] == 0.62
    assert updated["pick_reasoning"] == "Title and skills overlap heavily."


def test_set_apply_preview_sets_preview_and_stage(fake_client):
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"id": 1}]
    db.set_apply_preview(1, {"name": "Kishore"})
    updated = fake_client.table.return_value.update.call_args[0][0]
    assert updated["apply_preview"] == {"name": "Kishore"}
    assert updated["stage"] == "ready_to_submit"


def test_set_apply_blocked_sets_reason_without_changing_stage(fake_client):
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"id": 1}]
    db.set_apply_blocked(1, "Workday -- permanently excluded")
    updated = fake_client.table.return_value.update.call_args[0][0]
    assert updated["apply_blocked_reason"] == "Workday -- permanently excluded"
    assert "stage" not in updated
