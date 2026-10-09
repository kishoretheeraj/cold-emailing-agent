"""Static checks over migration 20261009000000 (job identity, preview attempts, job_boards).
Behavioural checks live in supabase/tests/job_identity_queue_dryrun.sql, run through
db_migrate.yml's dryrun mode (and locally with scripts/local_dryrun/run.sh)."""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
SQL = (_ROOT / "supabase" / "migrations" / "20261009000000_job_identity_queue_and_boards.sql").read_text()
SQL_NO_COMMENTS = re.sub(r"--[^\n]*", "", SQL)
NORM = re.sub(r"\s+", " ", SQL_NO_COMMENTS)


def _function(name):
    return NORM.split(f"CREATE OR REPLACE FUNCTION {name}(")[1].split("$$;")[0]


def test_job_key_is_unique_when_present():
    assert ("CREATE UNIQUE INDEX IF NOT EXISTS idx_job_applications_job_key_unique "
            "ON job_applications (job_key) WHERE job_key IS NOT NULL") in NORM


def test_new_columns_are_granted_but_the_attempt_counter_is_not():
    for op in ("INSERT", "UPDATE"):
        grant = re.search(rf"GRANT {op} \(([^)]*)\) ON job_applications TO anon, authenticated", NORM)
        assert grant, op
        columns = {c.strip() for c in grant.group(1).split(",")}
        assert {"job_key", "platform", "company_key", "title_key", "location", "posted_at",
                "jd_fingerprint", "pick_attempts"} <= columns
        assert "prepare_attempts" not in columns


def test_preview_claim_counts_attempts_and_requeue_resets_them():
    claim = _function("claim_application")
    preparing, submitting = claim.split("ELSIF p_to = 'submitting'")
    assert "prepare_attempts = prepare_attempts + 1" in preparing
    assert "prepare_attempts" not in submitting
    requeue = _function("requeue_preview")
    assert "prepare_attempts = 0" in requeue
    assert "automation_status IN ('needs_input', 'ready_for_review', 'failed_retryable')" in requeue
    for name in ("claim_application", "requeue_preview"):
        header = NORM.split(f"CREATE OR REPLACE FUNCTION {name}(")[1].split("AS $$")[0]
        assert "SECURITY DEFINER" in header and "SET search_path = public, pg_temp" in header


def test_claim_keeps_every_existing_guard():
    claim = _function("claim_application")
    for guard in ("automation_status IN ('idle', 'failed_retryable')", "stage = 'saved'",
                  "automation_status = 'approved'", "approved_revision_hash = preview_revision_hash",
                  "submit_attempted_at = NULL", "worker_lease_id IS NULL"):
        assert guard in claim, guard


def test_api_roles_cannot_delete_applications_or_boards():
    assert "REVOKE DELETE, TRUNCATE, TRIGGER ON job_applications FROM anon, authenticated" in NORM
    assert "REVOKE ALL ON job_boards FROM anon, authenticated" in NORM
    assert "GRANT SELECT, INSERT, UPDATE ON job_boards TO anon, authenticated" in NORM
    assert "DELETE ON job_boards" not in NORM.split("REVOKE ALL ON job_boards")[1]
