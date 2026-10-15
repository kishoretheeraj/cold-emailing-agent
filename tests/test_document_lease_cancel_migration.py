"""Static checks over migration 20261013120000 (cancel document lease, atomic unsupported)."""

import re
from pathlib import Path

SQL = (Path(__file__).resolve().parent.parent / "supabase" / "migrations"
       / "20261013120000_document_lease_cancel_and_atomic_unsupported.sql").read_text()
CODE = re.sub(r"--[^\n]*", "", SQL)


def _body(name):
    return CODE.split(f"CREATE OR REPLACE FUNCTION {name}(")[1].split("$$;")[0]


def test_cancel_refunds_the_attempt_and_is_bound_to_the_lease():
    body = _body("cancel_document_generation")
    assert "SECURITY DEFINER" in body and "SET search_path = public, pg_temp" in body
    assert "prepare_attempts = greatest(prepare_attempts - 1, 0)" in body
    assert "automation_status = 'idle'" in body
    assert "worker_lease_id = p_lease" in body and "automation_status = 'preparing'" in body
    assert "stage = 'saved'" in body
    assert "left(p_reason, 900)" in body and "RETURN FOUND" in body


def test_unsupported_under_lease_is_one_update_that_clears_lease_and_approval():
    body = _body("mark_unsupported_under_lease")
    assert body.count("UPDATE job_applications") == 1
    assert "automation_status = 'unsupported'" in body
    for column in ("approved_at = NULL", "approved_revision_hash = NULL",
                   "worker_lease_id = NULL", "worker_heartbeat_at = NULL"):
        assert column in body
    assert "worker_lease_id = p_lease" in body and "automation_status = 'preparing'" in body


def test_grants_are_anon_only_and_revoked_from_public():
    for sig in ("cancel_document_generation(BIGINT, UUID, TEXT)",
                "mark_unsupported_under_lease(BIGINT, UUID, TEXT)"):
        assert f"REVOKE EXECUTE ON FUNCTION {sig} FROM PUBLIC;" in CODE
        assert f"GRANT  EXECUTE ON FUNCTION {sig} TO anon;" in CODE
