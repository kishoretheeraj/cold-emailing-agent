"""Static checks over migration 20261013000000 (atomic dashboard decisions, document leases)."""

import re
from pathlib import Path

SQL = (Path(__file__).resolve().parent.parent / "supabase" / "migrations"
       / "20261013000000_dashboard_decisions_and_document_leases.sql").read_text()
CODE = re.sub(r"--[^\n]*", "", SQL)


def _body(name):
    return CODE.split(f"CREATE OR REPLACE FUNCTION {name}(")[1].split("$$;")[0]


def test_decision_rpc_is_one_transaction_with_a_locked_row_and_a_cas():
    body = _body("record_dashboard_decision")
    assert "SECURITY DEFINER" in body and "SET search_path = public, pg_temp" in body
    assert "FOR UPDATE" in body
    assert body.index("FOR UPDATE") < body.index("INSERT INTO application_runs") < body.index("SET stage = 'withdrawn'")
    assert "dashboard_decision_conflict" in body
    assert "IF NOT FOUND THEN" in body.split("SET stage = 'withdrawn'")[1]


def test_decision_rpc_never_touches_the_approval_columns():
    body = _body("record_dashboard_decision")
    for column in ("approved_at =", "approved_revision_hash", "approval_signature", "automation_status ="):
        assert column not in body
    assert "v_approved IS NOT NULL" in body  # an already-approved row is refused


def test_decisions_page_is_latest_per_application_and_capped():
    body = _body("get_dashboard_decisions_page")
    assert "DISTINCT ON (r.application_id)" in body
    assert "r.ended_at DESC" in body and "r.application_id > p_after" in body
    assert "least(greatest(p_limit, 1), 500)" in body


def test_document_lease_release_is_bound_to_the_lease_and_preparing():
    body = _body("finish_document_generation")
    assert "worker_lease_id = p_lease" in body and "automation_status = 'preparing'" in body
    assert "'idle'" in body and "'failed_retryable'" in body
    assert "NOT p_ok OR resume_file_ref IS NOT NULL" in body


def test_grants_are_anon_only_and_revoked_from_public():
    for sig in ("record_dashboard_decision(UUID, BIGINT, TEXT, TEXT, TEXT)",
                "get_dashboard_decisions_page(BIGINT, INT)",
                "finish_document_generation(BIGINT, UUID, BOOLEAN, TEXT)"):
        assert f"REVOKE EXECUTE ON FUNCTION {sig} FROM PUBLIC;" in CODE
        assert f"GRANT  EXECUTE ON FUNCTION {sig} TO anon;" in CODE
