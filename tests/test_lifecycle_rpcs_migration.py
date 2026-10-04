"""Static checks over the lifecycle-RPC migration. No live DB in the suite; the behavioural
checks live in supabase/tests/lifecycle_rpcs_dryrun.sql, run by hand inside BEGIN/ROLLBACK."""

import re
from pathlib import Path

import pytest

SQL = (Path(__file__).resolve().parent.parent / "supabase" / "migrations"
       / "20261004000000_lifecycle_rpcs_form_signature_receipts.sql").read_text()
SQL_NO_COMMENTS = re.sub(r"--[^\n]*", "", SQL)

SIGNATURES = {
    "claim_application": "claim_application(p_id BIGINT, p_lease UUID, p_to TEXT)",
    "heartbeat_application": "heartbeat_application(p_id BIGINT, p_lease UUID)",
    "renew_submission_lease": "renew_submission_lease(p_id BIGINT, p_lease UUID, p_revision_hash TEXT)",
    "complete_preview": "complete_preview(p_id BIGINT, p_lease UUID, p_preview JSONB, p_form_signature TEXT)",
    "release_application": "release_application(p_id BIGINT, p_lease UUID, p_to TEXT, p_reason TEXT DEFAULT NULL)",
    "record_submission": "record_submission(p_id BIGINT, p_lease UUID, p_source_channel TEXT, p_applied_date DATE)",
    "mark_application_unsupported": "mark_application_unsupported(p_id BIGINT, p_reason TEXT)",
    "recover_stale_leases": "recover_stale_leases(p_stale_seconds INT)",
    "record_receipt_evidence": "record_receipt_evidence(p_id BIGINT, p_evidence JSONB)",
    "requeue_preview": "requeue_preview(p_id BIGINT)",
}


def _body(name):
    sig = SIGNATURES[name]
    after = SQL_NO_COMMENTS.split(f"CREATE OR REPLACE FUNCTION {sig}")[1]
    return after.split("$$;")[0]


def _arg_types(sig):
    inner = sig[sig.index("(") + 1:-1]
    return ", ".join(p.strip().split(" ")[1] for p in inner.split(","))


@pytest.mark.parametrize("name", list(SIGNATURES))
def test_function_is_security_definer_with_pinned_search_path(name):
    assert f"CREATE OR REPLACE FUNCTION {SIGNATURES[name]}" in SQL_NO_COMMENTS
    header = SQL_NO_COMMENTS.split(f"CREATE OR REPLACE FUNCTION {SIGNATURES[name]}")[1].split("AS $$")[0]
    assert "SECURITY DEFINER" in header
    assert "SET search_path = public, pg_temp" in header


@pytest.mark.parametrize("name", list(SIGNATURES))
def test_function_revokes_public_and_grants_anon(name):
    types = _arg_types(SIGNATURES[name])
    assert f"REVOKE EXECUTE ON FUNCTION {name}({types}) FROM PUBLIC" in SQL_NO_COMMENTS
    assert f"GRANT  EXECUTE ON FUNCTION {name}({types}) TO anon" in SQL_NO_COMMENTS


@pytest.mark.parametrize("op", ["UPDATE", "INSERT"])
@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_lifecycle_column_writes_revoked(op, role):
    pat = (rf"REVOKE {op} \(automation_status, worker_lease_id, worker_heartbeat_at\)"
           rf"\s+ON job_applications FROM {role}")
    assert re.search(pat, SQL_NO_COMMENTS)


def test_authenticated_table_level_grant_is_replaced_not_just_column_revoked():
    # a column REVOKE is a no-op against a table-level grant (see 20260925000000)
    assert "REVOKE UPDATE ON job_applications FROM authenticated" in SQL_NO_COMMENTS
    assert "REVOKE INSERT ON job_applications FROM authenticated" in SQL_NO_COMMENTS


def test_new_columns_added_idempotently():
    assert "ADD COLUMN IF NOT EXISTS form_signature TEXT" in SQL_NO_COMMENTS
    assert "ADD COLUMN IF NOT EXISTS submit_attempted_at TIMESTAMPTZ" in SQL_NO_COMMENTS
    assert "ADD COLUMN IF NOT EXISTS submission_evidence JSONB" in SQL_NO_COMMENTS
    for col in ("form_signature", "submit_attempted_at", "submission_evidence"):
        assert f"COMMENT ON COLUMN job_applications.{col}" in SQL_NO_COMMENTS


def test_stale_threshold_is_floored():
    assert "GREATEST(p_stale_seconds, 1800)" in _body("recover_stale_leases")


def test_recover_maps_attempted_submitting_to_needs_confirmation():
    body = _body("recover_stale_leases")
    assert "submit_attempted_at IS NOT NULL" in body
    assert "'needs_confirmation'" in body
    assert "'failed_retryable'" in body


def test_release_post_click_only_allows_needs_confirmation():
    body = _body("release_application")
    assert "submit_attempted_at IS NOT NULL" in body
    assert "RAISE EXCEPTION" in body


def test_submitting_claim_resets_submit_attempted_at():
    assert "submit_attempted_at = NULL" in _body("claim_application")


def test_renew_records_submission_intent():
    assert "submit_attempted_at = now()" in _body("renew_submission_lease")


def test_claim_submitting_requires_hash_binding():
    body = _body("claim_application")
    assert "approved_revision_hash = preview_revision_hash" in body
    assert "approved_at IS NOT NULL" in body


def test_no_rpc_moves_needs_confirmation_except_to_submitted():
    for name in SIGNATURES:
        body = _body(name)
        guarded = "automation_status = 'needs_confirmation'" in body
        if name == "record_receipt_evidence":
            assert guarded and "automation_status = 'submitted'" in body
        else:
            assert not guarded, name


def test_receipt_evidence_requires_message_id():
    body = _body("record_receipt_evidence")
    assert "p_evidence ? 'message_id'" in body
    assert "RAISE EXCEPTION" in body


def test_record_submission_writes_page_confirmation_evidence():
    assert "'page_confirmation'" in _body("record_submission")
