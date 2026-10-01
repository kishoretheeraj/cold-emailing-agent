"""Static checks over the automation_status migration. There is no live DB in the test suite;
these pin the properties Task 7 later verifies live (grants, overload drop, trigger)."""

import re
from pathlib import Path

SQL = (Path(__file__).resolve().parent.parent / "supabase" / "migrations"
       / "20261001000000_add_automation_status_and_revision_binding.sql").read_text()
SQL_NO_COMMENTS = re.sub(r"--[^\n]*", "", SQL)

STATUSES = ["idle", "preparing", "needs_input", "ready_for_review", "approved", "submitting",
            "submitted", "needs_confirmation", "failed_retryable", "failed_terminal", "unsupported"]


def test_check_constraint_lists_every_status():
    m = re.search(r"automation_status IN \(([^)]*)\)", SQL_NO_COMMENTS)
    assert m
    listed = re.findall(r"'([a-z_]+)'", m.group(1))
    assert sorted(listed) == sorted(STATUSES)


def test_old_one_arg_approve_overload_is_dropped():
    assert re.search(r"DROP FUNCTION IF EXISTS approve_application\(BIGINT\)", SQL_NO_COMMENTS)


def test_anon_grants_cover_lifecycle_columns_but_never_hash_columns():
    grants = re.findall(r"GRANT\s+(?:UPDATE|INSERT)\s*\(([^)]*)\)\s+ON\s+job_applications\s+TO\s+anon",
                        SQL_NO_COMMENTS)
    assert grants, "explicit column grants to anon are required"
    granted = {c.strip() for g in grants for c in g.split(",")}
    assert {"automation_status", "worker_lease_id", "worker_heartbeat_at", "documents_version"} <= granted
    assert "preview_revision_hash" not in granted
    assert "approved_revision_hash" not in granted
    assert "approved_at" not in granted


def test_preview_hash_is_trigger_computed_on_every_write():
    assert re.search(r"BEFORE INSERT OR UPDATE ON job_applications", SQL_NO_COMMENTS)
    assert "NEW.preview_revision_hash :=" in SQL_NO_COMMENTS


def test_approve_binds_to_client_sent_hash():
    body = SQL_NO_COMMENTS.split("FUNCTION approve_application(p_id BIGINT, p_revision_hash TEXT)")[1]
    body = body.split("$$;")[0]
    assert "preview_revision_hash = p_revision_hash" in body
    assert "approved_revision_hash = preview_revision_hash" in body
    assert "automation_status = 'ready_for_review'" in body


def test_reset_approval_refuses_post_click_states():
    body = SQL_NO_COMMENTS.split("FUNCTION reset_approval(p_id BIGINT)")[1].split("$$;")[0]
    assert "automation_status IN ('approved', 'failed_retryable')" in body
    assert "worker_lease_id IS NULL" in body
    for forbidden in ("needs_confirmation", "submitting", "submitted"):
        assert forbidden not in body


def test_every_rpc_revokes_public_and_grants_anon():
    for sig in ("approve_application(BIGINT, TEXT)", "reset_approval(BIGINT)",
                "resolve_confirmation(BIGINT, BOOLEAN)"):
        assert f"REVOKE EXECUTE ON FUNCTION {sig} FROM PUBLIC" in SQL_NO_COMMENTS
        assert f"GRANT  EXECUTE ON FUNCTION {sig} TO anon" in SQL_NO_COMMENTS


import config


def test_config_statuses_match_migration_check_constraint():
    m = re.search(r"automation_status IN \(([^)]*)\)", SQL_NO_COMMENTS)
    assert sorted(re.findall(r"'([a-z_]+)'", m.group(1))) == sorted(config.AUTOMATION_STATUSES)


def test_lease_stale_threshold_exceeds_submit_workflow_timeout():
    # apply_agent_submit.yml has timeout-minutes: 15 -- a live submit must never look stale.
    assert config.APPLY_AGENT_LEASE_STALE_SECONDS > 15 * 60


def test_hash_includes_documents_version_and_trigger_passes_it():
    fn = SQL_NO_COMMENTS.split("FUNCTION job_application_preview_revision_hash(")[1].split("$$;")[0]
    assert "p_docs_version TEXT" in fn
    assert "coalesce(p_docs_version, '')" in fn
    assert "NEW.documents_version)" in SQL_NO_COMMENTS


def test_documents_version_column_added():
    assert "ADD COLUMN IF NOT EXISTS documents_version TEXT" in SQL_NO_COMMENTS


def test_reset_approval_does_not_require_approved_at():
    body = SQL_NO_COMMENTS.split("FUNCTION reset_approval(p_id BIGINT)")[1].split("$$;")[0]
    assert "approved_at IS NOT NULL" not in body


def test_backfill_marks_approved_post_submit_rows_submitted():
    assert "WHEN approved_at IS NOT NULL AND stage NOT IN ('saved', 'ready_to_submit') THEN 'submitted'" in SQL_NO_COMMENTS
