"""Static checks over the resume-subscription migration (no live DB in the suite; Task 10
verifies the grants live)."""

import re
from pathlib import Path

SQL = (Path(__file__).resolve().parent.parent / "supabase" / "migrations"
       / "20261005000000_resume_subscription_billing_and_error.sql").read_text()
CODE = re.sub(r"--[^\n]*", "", SQL)


def test_billing_column_defaults_to_api_and_is_constrained():
    assert re.search(r"ADD COLUMN IF NOT EXISTS billing TEXT NOT NULL DEFAULT 'api'", CODE)
    assert re.search(r"CHECK \(billing IN \('api', 'subscription'\)\)", CODE)


def test_resume_error_is_nullable_text():
    assert re.search(r"ADD COLUMN IF NOT EXISTS resume_error TEXT;", CODE)


def test_resume_error_granted_to_anon_and_authenticated():
    for role in ("anon", "authenticated"):
        assert re.search(rf"GRANT UPDATE \(resume_error\) ON job_applications TO {role};", CODE), role


def test_migration_never_touches_approval_columns():
    for col in ("approved_at", "approved_revision_hash", "preview_revision_hash", "automation_status"):
        assert col not in CODE
