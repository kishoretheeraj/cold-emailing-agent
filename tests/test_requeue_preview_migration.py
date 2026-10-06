"""Static checks over migration 20261006000000 (requeue_preview from ready_for_review)."""

import re
from pathlib import Path

SQL = (Path(__file__).resolve().parent.parent / "supabase" / "migrations"
       / "20261006000000_requeue_preview_from_ready_for_review.sql").read_text()
SQL_NO_COMMENTS = re.sub(r"--[^\n]*", "", SQL)
BODY = SQL_NO_COMMENTS.split("CREATE OR REPLACE FUNCTION requeue_preview(p_id BIGINT)")[1].split("$$;")[0]


def test_same_signature_security_definer_with_pinned_search_path():
    assert "SECURITY DEFINER" in BODY
    assert "SET search_path = public, pg_temp" in BODY


def test_accepts_only_needs_input_and_ready_for_review_without_a_lease():
    assert "automation_status IN ('needs_input', 'ready_for_review')" in BODY
    assert "worker_lease_id IS NULL" in BODY
    for status in ("approved", "submitting", "needs_confirmation", "submitted"):
        assert f"'{status}'" not in BODY


def test_clears_approval_binding_and_stored_preview():
    for column in ("approved_at", "approved_revision_hash", "form_signature", "apply_preview",
                   "apply_blocked_reason"):
        assert f"{column} = NULL" in BODY
    assert "automation_status = 'idle'" in BODY
    assert "stage = 'saved'" in BODY


def test_raises_when_nothing_matched_and_grants_anon_only():
    assert "IF NOT FOUND THEN" in BODY and "RAISE EXCEPTION" in BODY
    assert "REVOKE EXECUTE ON FUNCTION requeue_preview(BIGINT) FROM PUBLIC;" in SQL_NO_COMMENTS
    assert "GRANT  EXECUTE ON FUNCTION requeue_preview(BIGINT) TO anon;" in SQL_NO_COMMENTS
