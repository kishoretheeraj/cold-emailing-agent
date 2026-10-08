"""Static checks over migration 20261008000001 (signed approvals). Behavioural checks live in
supabase/tests/signed_approval_dryrun.sql, run through db_migrate.yml's dryrun mode."""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
SQL = (_ROOT / "supabase" / "migrations" / "20261008000001_signed_approval.sql").read_text()
SQL_NO_COMMENTS = re.sub(r"--[^\n]*", "", SQL)
SIG = "approve_application(p_id BIGINT, p_revision_hash TEXT, p_signature TEXT, p_signed_at_ms BIGINT)"


def _norm(text):
    return re.sub(r"\s+", " ", text)


def _body():
    return _norm(SQL_NO_COMMENTS.split(f"CREATE OR REPLACE FUNCTION {SIG}")[1].split("$$;")[0])


def test_unsigned_overload_is_dropped_before_the_new_one_is_created():
    drop = SQL_NO_COMMENTS.index("DROP FUNCTION IF EXISTS approve_application(BIGINT, TEXT);")
    assert drop < SQL_NO_COMMENTS.index(f"CREATE OR REPLACE FUNCTION {SIG}")


def test_security_definer_pinned_path_and_grants():
    header = SQL_NO_COMMENTS.split(f"CREATE OR REPLACE FUNCTION {SIG}")[1].split("AS $$")[0]
    assert "SECURITY DEFINER" in header and "SET search_path = public, pg_temp" in header
    assert "REVOKE EXECUTE ON FUNCTION approve_application(BIGINT, TEXT, TEXT, BIGINT) FROM PUBLIC" in SQL_NO_COMMENTS
    assert "GRANT  EXECUTE ON FUNCTION approve_application(BIGINT, TEXT, TEXT, BIGINT) TO anon" in SQL_NO_COMMENTS


def test_signature_columns_are_rpc_only():
    for op in ("UPDATE", "INSERT"):
        for role in ("anon", "authenticated"):
            assert (f"REVOKE {op} (approval_signature, approval_signed_at_ms) ON job_applications FROM {role}"
                    in SQL_NO_COMMENTS)


def test_signature_shape_and_freshness_are_required():
    body = _body()
    assert "p_signature !~ '^[0-9a-f]{64}$'" in body
    assert "abs(extract(epoch FROM now()) * 1000 - p_signed_at_ms) > 120000" in body


def test_existing_approval_guards_are_kept_verbatim():
    body = _body()
    for guard in ("AND stage = 'ready_to_submit'", "AND apply_preview IS NOT NULL", "AND approved_at IS NULL",
                  "AND automation_status = 'ready_for_review'", "AND preview_revision_hash = p_revision_hash"):
        assert guard in body
    assert "approved_revision_hash = preview_revision_hash" in body
    assert "approval_signature = p_signature" in body
    assert "approval_signed_at_ms = p_signed_at_ms" in body


def test_worker_skew_window_covers_the_rpc_freshness_window():
    # The RPC accepts a signature up to 120 s old; approved_at is now() at that moment, so the
    # worker's tolerance must be wider or a valid approval would be rejected.
    import approval_signature
    assert approval_signature.MAX_SKEW_SECONDS * 1000 > 120000


def test_functional_dryrun_exists():
    test = (_ROOT / "supabase" / "tests" / "signed_approval_dryrun.sql").read_text()
    assert "SELECT 'DRYRUN OK';" in test and test.rstrip().endswith("ROLLBACK;")
