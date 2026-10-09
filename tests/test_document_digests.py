"""Content-bound documents: migration 20261012000000 and the submit-side digest check."""

import hashlib
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import apply_agent

_ROOT = Path(__file__).resolve().parent.parent
SQL = (_ROOT / "supabase" / "migrations" / "20261012000000_add_document_digests.sql").read_text()
SQL_NO_COMMENTS = re.sub(r"--[^\n]*", "", SQL)
BUCKET_SQL = re.sub(r"--[^\n]*", "", (
    _ROOT / "supabase" / "migrations" / "20260829000001_create_resumes_storage_bucket.sql").read_text())


def test_migration_adds_digest_columns_and_hash_takes_them():
    assert "ADD COLUMN IF NOT EXISTS resume_sha256 TEXT" in SQL_NO_COMMENTS
    assert "ADD COLUMN IF NOT EXISTS cover_letter_sha256 TEXT" in SQL_NO_COMMENTS
    assert "p_resume_sha256 TEXT, p_cover_sha256 TEXT)" in SQL_NO_COMMENTS
    # Structured JSON serialization (not '|' concatenation) to avoid field-boundary ambiguity.
    assert "jsonb_build_object(" in SQL_NO_COMMENTS
    assert "'resume_sha256', coalesce(p_resume_sha256, '')" in SQL_NO_COMMENTS
    assert "'cover_sha256', coalesce(p_cover_sha256, '')" in SQL_NO_COMMENTS
    assert "NEW.resume_sha256, NEW.cover_letter_sha256" in SQL_NO_COMMENTS


def test_migration_drops_the_seven_arg_overload_and_the_anon_update_policy():
    assert "DROP FUNCTION IF EXISTS job_application_preview_revision_hash(JSONB, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT);" in SQL_NO_COMMENTS
    # NB: checked against raw SQL — the policy name itself contains "--", which the
    # comment stripper would mangle.
    assert 'DROP POLICY IF EXISTS "resumes bucket -- anon update" ON storage.objects;' in SQL


def test_bucket_migration_keeps_read_and_insert_but_no_update_policy():
    raw = (_ROOT / "supabase" / "migrations" / "20260829000001_create_resumes_storage_bucket.sql").read_text()
    assert '"resumes bucket -- anon read"' in raw and '"resumes bucket -- anon write"' in raw
    assert '"resumes bucket -- anon update"' not in raw


class _Page:
    def __init__(self):
        self.loc = MagicMock()

    def locator(self, sel):
        return MagicMock(first=self.loc, count=MagicMock(return_value=1))


def _client(mocker, content):
    mocker.patch("apply_agent.db.get_client", return_value=MagicMock(
        storage=MagicMock(from_=MagicMock(return_value=MagicMock(download=MagicMock(return_value=content))))))


def test_verify_document_digest_accepts_match_and_refuses_mismatch_or_missing():
    good = hashlib.sha256(b"pdf").hexdigest()
    apply_agent._verify_document_digest("resume", b"pdf", good)
    with pytest.raises(ValueError, match="do not match the approved digest"):
        apply_agent._verify_document_digest("resume", b"tampered", good)
    with pytest.raises(ValueError, match="no approved digest recorded"):
        apply_agent._verify_document_digest("resume", b"pdf", None)


@pytest.mark.parametrize("digest", ["0" * 64, None])
def test_submit_attach_refuses_replaced_or_undigested_document(mocker, digest):
    _client(mocker, b"replaced-bytes")
    mocker.patch("apply_agent._find_file_input", return_value=MagicMock())
    job = {"resume_file_ref": "resumes/1/resume-x.pdf", "resume_sha256": digest}
    with pytest.raises(ValueError, match="Refusing to submit"):
        apply_agent._attach_resume_and_cover_letter(_Page(), job, verify_digests=True)


def test_preview_attach_does_not_require_digests(mocker):
    _client(mocker, b"bytes")
    locator = MagicMock()
    mocker.patch("apply_agent._find_file_input", return_value=locator)
    report = apply_agent._attach_resume_and_cover_letter(_Page(), {"resume_file_ref": "r.pdf"})
    assert report["resume"] is True
