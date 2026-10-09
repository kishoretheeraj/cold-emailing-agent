"""Static checks over migration 20261011000000 (application outcomes). The functional dry run is
supabase/tests/application_outcomes_dryrun.sql (removing the anon grant makes it fail)."""

import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
SQL = re.sub(r"\s+", " ", re.sub(r"--[^\n]*", "", (_ROOT / "supabase" / "migrations" /
                                                   "20261011000000_application_outcomes.sql").read_text()))


def test_column_and_grant():
    assert "ADD COLUMN IF NOT EXISTS outcome_evidence JSONB NULL" in SQL
    assert "GRANT UPDATE (outcome_evidence) ON job_applications TO anon, authenticated" in SQL


def test_nothing_else_is_granted_or_revoked():
    assert len(re.findall(r"\bGRANT\b", SQL)) == 1
    assert "REVOKE" not in SQL and "DROP" not in SQL


def test_sql_guard_accepts_the_dry_run():
    out = subprocess.run([sys.executable, "scripts/sql_guard.py", "--migration", "20261011000000_application_outcomes.sql",
                          "--test", "application_outcomes_dryrun.sql", "--out", "/dev/null"],
                         cwd=_ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
