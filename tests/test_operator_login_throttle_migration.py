"""Static checks for migration 20261017000000 (operator login attempt budget). Functional checks:
supabase/tests/operator_login_throttle_dryrun.sql."""

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SQL = open(os.path.join(_ROOT, "supabase", "migrations", "20261017000000_operator_login_throttle.sql")).read()
_ROUTE = open(os.path.join(_ROOT, "contact-manager", "src", "app", "api", "login", "route.ts")).read()


def test_attempt_function_is_definer_serialized_and_fixed_limits():
    assert re.search(r"CREATE OR REPLACE FUNCTION operator_login_attempt\(\)", _SQL)  # no caller-supplied limits
    assert "SECURITY DEFINER" in _SQL and "SET search_path = public, pg_temp" in _SQL
    assert "pg_advisory_xact_lock" in _SQL
    assert "v_recent >= 5 OR v_day >= 20" in _SQL


def test_table_is_closed_to_every_role():
    assert "ENABLE ROW LEVEL SECURITY" in _SQL
    assert "REVOKE ALL ON operator_login_attempts FROM PUBLIC, anon, authenticated;" in _SQL
    assert not re.search(r"GRANT\s+\w+.*ON operator_login_attempts", _SQL)


def test_login_route_spends_an_attempt_before_comparing_the_pin():
    assert _ROUTE.index('rpc("operator_login_attempt")') < _ROUTE.index("passwordMatches(body")
