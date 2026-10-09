"""Static checks over migration 20261010000000 (warm paths). Behavioural checks live in
supabase/tests/warm_paths_dryrun.sql (db_migrate.yml dryrun; locally scripts/local_dryrun/run.sh),
where 12 hand-made mutations of this migration were each caught."""

import re
import subprocess
import sys
from pathlib import Path

import config

_ROOT = Path(__file__).resolve().parent.parent
SQL = (_ROOT / "supabase" / "migrations" / "20261010000000_warm_paths.sql").read_text()
NORM = re.sub(r"\s+", " ", re.sub(r"--[^\n]*", "", SQL))
TS = (_ROOT / "contact-manager" / "src" / "lib" / "warmPaths.ts").read_text()


def _function(name):
    return NORM.split(f"CREATE OR REPLACE FUNCTION {name}(")[1].split("$$;")[0]


def test_link_column_unlinks_on_delete():
    assert ("ADD COLUMN IF NOT EXISTS job_application_id BIGINT NULL REFERENCES job_applications(id) "
            "ON DELETE SET NULL") in NORM


def test_relationship_values_match_the_ui():
    check = re.search(r"relationship IN \(([^)]*)\)", NORM).group(1)
    sql_values = {v.strip(" '") for v in check.split(",")}
    ts_values = set(re.findall(r'"(\w+)"', TS.split("export const RELATIONSHIPS")[1].split("]")[0]))
    assert sql_values == ts_values == {"hiring_manager", "leader", "recruiter", "alum", "team_member", "other"}


def test_the_people_cap_is_the_same_number_everywhere():
    guard = _function("contacts_link_guard")
    assert f"IF v_linked >= {config.WARM_MAX_PEOPLE_PER_APPLICATION} THEN" in guard
    assert f"export const MAX_PEOPLE_PER_APPLICATION = {config.WARM_MAX_PEOPLE_PER_APPLICATION};" in TS


def test_guard_locks_the_application_and_runs_as_definer():
    guard = _function("contacts_link_guard")
    assert "SECURITY DEFINER SET search_path = public, pg_temp" in guard
    assert "FROM job_applications WHERE id = NEW.job_application_id FOR UPDATE" in guard
    assert ("CREATE TRIGGER contacts_link_guard BEFORE INSERT OR UPDATE OF job_application_id, deleted_at "
            "ON contacts FOR EACH ROW") in NORM


def test_the_hold_has_no_column_grant_and_a_clamped_rpc():
    assert not re.search(r"GRANT (UPDATE|INSERT)[^;]*referral_hold_until", NORM)
    hold = _function("hold_for_referral")
    assert "least(greatest(coalesce(p_days, 10), 1), 14)" in hold
    assert "automation_status = 'ready_for_review' AND approved_at IS NULL" in hold
    for fn in ("hold_for_referral(BIGINT, INTEGER)", "release_referral_hold(BIGINT)"):
        assert f"REVOKE EXECUTE ON FUNCTION {fn} FROM PUBLIC" in NORM
        assert f"GRANT EXECUTE ON FUNCTION {fn} TO anon, authenticated" in NORM


def test_sql_guard_accepts_the_dry_run():
    out = subprocess.run([sys.executable, "scripts/sql_guard.py", "--migration", "20261010000000_warm_paths.sql",
                          "--test", "warm_paths_dryrun.sql", "--out", "/dev/null"],
                         cwd=_ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_company_key_fixture_matches_python():
    import json
    import job_identity
    rows = json.loads((_ROOT / "tests" / "fixtures" / "company_keys.json").read_text())
    assert len(rows) >= 20
    for name, key in rows:
        assert job_identity.company_key(name) == key, name
