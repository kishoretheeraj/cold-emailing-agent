"""Static checks over migration 20261008000000 (application_runs, takeover, evidence bucket,
record_submission evidence). No live DB in the suite; the behavioural checks live in
supabase/tests/application_runs_takeover_dryrun.sql, run through db_migrate.yml's dryrun mode."""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
SQL = (_ROOT / "supabase" / "migrations"
       / "20261008000000_application_runs_takeover_evidence.sql").read_text()
SQL_NO_COMMENTS = re.sub(r"--[^\n]*", "", SQL)

SIGNATURES = {
    "log_application_run": (
        "log_application_run(p_run_id UUID, p_application_id BIGINT, p_kind TEXT, p_adapter TEXT, "
        "p_host TEXT, p_started_at TIMESTAMPTZ, p_outcome TEXT, p_stop_reason TEXT DEFAULT NULL, "
        "p_fields_filled INT DEFAULT NULL, p_fields_missing INT DEFAULT NULL, "
        "p_takeovers INT DEFAULT 0, p_model_calls INT DEFAULT 0, p_error_class TEXT DEFAULT NULL, "
        "p_details JSONB DEFAULT NULL)"),
    "request_takeover": "request_takeover(p_id BIGINT, p_lease UUID, p_kind TEXT, p_reason TEXT)",
    "takeover_continue": "takeover_continue(p_id BIGINT)",
    "clear_takeover": "clear_takeover(p_id BIGINT, p_lease UUID)",
    "record_submission": (
        "record_submission(p_id BIGINT, p_lease UUID, p_source_channel TEXT, p_applied_date DATE, "
        "p_evidence JSONB DEFAULT NULL)"),
}


def _header(name):
    return SQL_NO_COMMENTS.split(f"CREATE OR REPLACE FUNCTION {SIGNATURES[name]}")[1].split("AS $$")[0]


def _body(name):
    after = SQL_NO_COMMENTS.split(f"CREATE OR REPLACE FUNCTION {SIGNATURES[name]}")[1]
    return after.split("$$;")[0]


def _arg_types(sig):
    inner = sig[sig.index("(") + 1:-1]
    return ", ".join(p.strip().split(" ")[1] for p in inner.split(","))


def _norm(text):
    return re.sub(r"\s+", " ", text)


@pytest.mark.parametrize("name", list(SIGNATURES))
def test_function_is_security_definer_with_pinned_search_path(name):
    assert f"CREATE OR REPLACE FUNCTION {SIGNATURES[name]}" in SQL_NO_COMMENTS
    header = _header(name)
    assert "SECURITY DEFINER" in header
    assert "SET search_path = public, pg_temp" in header


@pytest.mark.parametrize("name", list(SIGNATURES))
def test_function_revokes_public_and_grants_anon(name):
    types = _arg_types(SIGNATURES[name])
    assert f"REVOKE EXECUTE ON FUNCTION {name}({types}) FROM PUBLIC" in SQL_NO_COMMENTS
    assert f"GRANT  EXECUTE ON FUNCTION {name}({types}) TO anon" in SQL_NO_COMMENTS


# ── application_runs ──────────────────────────────────────────────────────────

def test_runs_table_is_read_only_for_api_roles():
    # Supabase's default privileges grant ALL on new public tables to anon/authenticated.
    assert "REVOKE ALL ON application_runs FROM anon, authenticated" in SQL_NO_COMMENTS
    assert "GRANT SELECT ON application_runs TO anon, authenticated" in SQL_NO_COMMENTS
    assert "REVOKE ALL ON SEQUENCE application_runs_id_seq FROM anon, authenticated" in SQL_NO_COMMENTS
    assert not re.search(r"GRANT\s+(INSERT|UPDATE|DELETE|ALL)[^;]*ON application_runs", SQL_NO_COMMENTS)


def test_runs_table_constrains_kind_and_outcome():
    table = _norm(SQL_NO_COMMENTS.split("CREATE TABLE IF NOT EXISTS application_runs")[1].split(");")[0])
    assert "kind TEXT NOT NULL CHECK (kind IN ('prepare', 'submit', 'bakeoff', 'dryrun'))" in table
    for outcome in ("ready", "needs_input", "takeover_timeout", "submitted", "needs_confirmation",
                    "failed_retryable", "failed_terminal", "unsupported", "skipped", "scored"):
        assert f"'{outcome}'" in table
    assert "application_id BIGINT REFERENCES job_applications(id) ON DELETE SET NULL" in table


def test_log_run_validates_its_arguments():
    body = _norm(_body("log_application_run"))
    assert "length(p_adapter) > 64" in body
    assert "length(p_stop_reason) > 500" in body
    assert "length(p_details::text) > 16384" in body
    assert "jsonb_typeof(p_details) <> 'object'" in body
    assert "p_started_at > now() + interval '5 minutes'" in body
    assert "least(coalesce(p_fields_filled, 0), coalesce(p_fields_missing, 0), p_takeovers, p_model_calls) < 0" in body


# ── takeover ──────────────────────────────────────────────────────────────────

def test_takeover_column_is_rpc_only():
    assert "ADD COLUMN IF NOT EXISTS takeover JSONB" in SQL_NO_COMMENTS
    assert "COMMENT ON COLUMN job_applications.takeover" in SQL_NO_COMMENTS
    for op in ("UPDATE", "INSERT"):
        for role in ("anon", "authenticated"):
            assert f"REVOKE {op} (takeover) ON job_applications FROM {role}" in SQL_NO_COMMENTS


def test_request_takeover_needs_the_live_lease_and_an_active_status():
    body = _norm(_body("request_takeover"))
    assert "worker_lease_id = p_lease" in body
    assert "automation_status IN ('preparing', 'submitting')" in body
    # The lease is stamped into the request, so a request from an earlier lease never
    # shows as open after a new claim.
    assert "'lease', p_lease" in body
    assert "'continue_at', NULL" in body
    for kind in ("captcha", "sms_code", "email_verification", "login", "unrecognized_page", "other"):
        assert f"'{kind}'" in body
    assert "length(p_reason) > 500" in body


def test_takeover_continue_only_answers_an_open_request_of_the_live_lease():
    body = _norm(_body("takeover_continue"))
    assert "takeover->>'lease' = worker_lease_id::text" in body
    assert "takeover->>'continue_at' IS NULL" in body
    assert "worker_lease_id IS NOT NULL" in body
    # It changes nothing but the takeover object.
    assert "automation_status =" not in body
    assert "approved" not in body


def test_clear_takeover_needs_the_lease():
    body = _norm(_body("clear_takeover"))
    assert "SET takeover = NULL" in body
    assert "worker_lease_id = p_lease" in body


# ── record_submission with evidence ───────────────────────────────────────────

def test_old_record_submission_is_dropped_not_overloaded():
    # CREATE OR REPLACE with a new argument list makes an overload; PostgREST could keep routing
    # to the old one.
    assert "DROP FUNCTION IF EXISTS record_submission(BIGINT, UUID, TEXT, DATE)" in SQL_NO_COMMENTS
    drop = SQL_NO_COMMENTS.index("DROP FUNCTION IF EXISTS record_submission(BIGINT, UUID, TEXT, DATE)")
    create = SQL_NO_COMMENTS.index(f"CREATE OR REPLACE FUNCTION {SIGNATURES['record_submission']}")
    assert drop < create


def test_record_submission_keeps_its_transition_guards():
    body = _norm(_body("record_submission"))
    assert "AND worker_lease_id = p_lease" in body
    assert "AND automation_status = 'submitting'" in body
    assert "automation_status = 'submitted'" in body
    assert "stage = 'applied'" in body
    assert "worker_lease_id = NULL" in body
    assert "takeover = NULL" in body


def test_record_submission_evidence_cannot_spoof_server_fields():
    body = _norm(_body("record_submission"))
    assert "jsonb_typeof(p_evidence) <> 'object'" in body
    assert "length(p_evidence::text) > 16384" in body
    # Server-owned keys come last-wins from the server side, and a client can never claim a
    # Gmail receipt (message_id is the reconciler's proof).
    assert ("coalesce(p_evidence, '{}'::jsonb) - 'source' - 'at' - 'message_id'"
            " || jsonb_build_object('source', 'page_confirmation', 'at', now())") in body


# ── evidence bucket ───────────────────────────────────────────────────────────

def test_evidence_bucket_is_private_size_and_type_limited():
    text = _norm(SQL_NO_COMMENTS)
    assert ("INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types) "
            "VALUES ('application-evidence', 'application-evidence', false, 5242880, "
            "ARRAY['image/png', 'image/jpeg', 'text/plain', 'application/json'])") in text
    assert "ON CONFLICT (id) DO NOTHING" in text


def test_evidence_bucket_is_insert_only_for_anon():
    policies = re.findall(r"CREATE POLICY[^;]*application-evidence[^;]*;", SQL_NO_COMMENTS, flags=re.S)
    assert len(policies) == 1
    policy = _norm(policies[0])
    assert "FOR INSERT TO anon" in policy
    assert "WITH CHECK (bucket_id = 'application-evidence')" in policy
    # No read, update or delete policy: evidence is shown later only through signed URLs from
    # an authenticated server route (spec §9.1).
    assert not re.search(r"FOR (SELECT|UPDATE|DELETE|ALL)[^;]*application-evidence", SQL_NO_COMMENTS)


def test_policy_creation_is_rerunnable():
    assert 'DROP POLICY IF EXISTS "application-evidence anon insert" ON storage.objects' in SQL_NO_COMMENTS


def test_functional_dryrun_exists_and_rolls_back():
    test = (_ROOT / "supabase" / "tests" / "application_runs_takeover_dryrun.sql").read_text()
    assert test.lstrip().splitlines()[0].startswith("--")
    assert "ROLLBACK;" in test.rstrip().splitlines()[-1]
    assert "SELECT 'DRYRUN OK';" in test


def test_config_vocabularies_match_the_migration():
    import config
    table = _norm(SQL_NO_COMMENTS.split("CREATE TABLE IF NOT EXISTS application_runs")[1].split(");")[0])
    kinds = re.search(r"kind IN \(([^)]*)\)", table).group(1)
    outcomes = re.search(r"outcome IN \(([^)]*)\)", table).group(1)
    takeover = re.search(r"p_kind NOT IN \(([^)]*)\)", _norm(_body("request_takeover"))).group(1)

    def items(s):
        return tuple(x.strip().strip("'") for x in s.split(","))

    assert items(kinds) == config.APPLICATION_RUN_KINDS
    assert items(outcomes) == config.APPLICATION_RUN_OUTCOMES
    assert items(takeover) == config.TAKEOVER_KINDS
