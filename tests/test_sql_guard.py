"""Tests for scripts/sql_guard.py -- the guard that makes a migration dry run against the real
database unable to commit anything. Every case here is a way a rolled-back dry run could
silently become a real write."""

import os
import sys

import pytest

sys.path.insert(0, "scripts")
import sql_guard  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── strip_sql ───────────────────────────────────────────────────────────────────

def test_strip_removes_dollar_quoted_function_bodies():
    sql = "CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $$ BEGIN COMMIT; END; $$;"
    stripped = sql_guard.strip_sql(sql)
    assert "COMMIT" not in stripped.upper()
    assert "CREATE FUNCTION" in stripped


def test_strip_removes_tagged_dollar_quotes():
    sql = "DO $body$ BEGIN ROLLBACK; END $body$; SELECT 1;"
    stripped = sql_guard.strip_sql(sql)
    assert "ROLLBACK" not in stripped.upper()
    assert "SELECT 1" in stripped


def test_strip_removes_line_and_block_comments_and_strings():
    sql = "-- COMMIT;\n/* ROLLBACK; */ SELECT 'COMMIT;' AS x;"
    stripped = sql_guard.strip_sql(sql)
    assert "COMMIT" not in stripped.upper()
    assert "ROLLBACK" not in stripped.upper()
    assert "SELECT" in stripped


def test_strip_handles_escaped_quote_in_string():
    stripped = sql_guard.strip_sql("SELECT 'it''s; COMMIT;' ; SELECT 2;")
    assert "COMMIT" not in stripped.upper()
    assert "SELECT 2" in stripped


# ── statements ─────────────────────────────────────────────────────────────────

def test_statements_split_on_top_level_semicolons_only():
    sql = "CREATE TABLE t (x int); DO $$ BEGIN PERFORM 1; END $$; SELECT 1"
    assert [s.split()[0].upper() for s in sql_guard.statements(sql)] == ["CREATE", "DO", "SELECT"]


# ── migration checks ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "COMMIT;",
    "commit work;",
    "END;",
    "ROLLBACK;",
    "BEGIN;",
    "START TRANSACTION;",
    "SAVEPOINT a;",
    "RELEASE SAVEPOINT a;",
    "PREPARE TRANSACTION 'x';",
    "COMMIT PREPARED 'x';",
    "CREATE INDEX CONCURRENTLY i ON t (x);",
    "DROP INDEX CONCURRENTLY i;",
    "VACUUM t;",
    "ALTER SYSTEM SET work_mem = '1MB';",
    "CREATE DATABASE x;",
    "REINDEX TABLE CONCURRENTLY t;",
])
def test_migration_rejects_transaction_control_and_non_transactional_statements(bad):
    problems = sql_guard.check_migration("CREATE TABLE t (x int);\n" + bad)
    assert problems, bad


def test_migration_allows_plpgsql_begin_end_and_ordinary_ddl():
    sql = """
    ALTER TABLE job_applications ADD COLUMN IF NOT EXISTS foo TEXT;
    CREATE OR REPLACE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $$
    BEGIN
      UPDATE job_applications SET foo = 'x';
    END;
    $$;
    GRANT EXECUTE ON FUNCTION f() TO anon;
    """
    assert sql_guard.check_migration(sql) == []


def test_every_existing_migration_passes_the_guard():
    # The guard must not block the repo's real migrations, or nobody can dry-run them.
    mig_dir = os.path.join(_ROOT, "supabase", "migrations")
    for name in sorted(os.listdir(mig_dir)):
        if name.startswith("2026") and name.endswith(".sql"):
            with open(os.path.join(mig_dir, name)) as f:
                problems = sql_guard.check_migration(f.read())
            # Pre-existing migrations that use non-transactional statements would show here;
            # none of the 2026 ones do today.
            assert problems == [], (name, problems)


# ── test-file checks ───────────────────────────────────────────────────────────

def test_dryrun_test_may_begin_first_and_rollback_last():
    assert sql_guard.check_test("BEGIN; SELECT 1; SELECT 'DRYRUN OK'; ROLLBACK;") == []


@pytest.mark.parametrize("bad", [
    "BEGIN; SELECT 1; COMMIT;",
    "BEGIN; SELECT 1; END;",
    "BEGIN; SELECT 1; ROLLBACK; SELECT 2;",   # statement after the rollback would autocommit
    "SELECT 1; BEGIN; ROLLBACK;",             # BEGIN not first
    "BEGIN; SAVEPOINT a; ROLLBACK;",
    "BEGIN; VACUUM t; ROLLBACK;",
])
def test_dryrun_test_rejects_anything_that_could_commit(bad):
    assert sql_guard.check_test(bad), bad


def test_existing_dryrun_tests_pass_the_guard():
    tests_dir = os.path.join(_ROOT, "supabase", "tests")
    for name in sorted(os.listdir(tests_dir)):
        if name.endswith(".sql"):
            with open(os.path.join(tests_dir, name)) as f:
                assert sql_guard.check_test(f.read()) == [], name


# ── compose ────────────────────────────────────────────────────────────────────

def test_compose_wraps_everything_in_one_transaction_that_rolls_back():
    script = sql_guard.compose("ALTER TABLE t ADD COLUMN y int;", "BEGIN; SELECT 1; ROLLBACK;")
    stmts = [s.strip().upper() for s in sql_guard.statements(script)]
    assert stmts[0] == "BEGIN"
    assert stmts[-1] == "ROLLBACK"
    assert "COMMIT" not in sql_guard.strip_sql(script).upper()
    # The migration runs inside the transaction, before the test body.
    assert stmts.index("ALTER TABLE T ADD COLUMN Y INT") > 0


def test_compose_without_test_still_rolls_back():
    script = sql_guard.compose("ALTER TABLE t ADD COLUMN y int;", None)
    stmts = [s.strip().upper() for s in sql_guard.statements(script)]
    assert stmts[0] == "BEGIN" and stmts[-1] == "ROLLBACK"


def test_compose_refuses_unsafe_input():
    with pytest.raises(ValueError):
        sql_guard.compose("CREATE TABLE t (x int); COMMIT;", None)
    with pytest.raises(ValueError):
        sql_guard.compose("CREATE TABLE t (x int);", "BEGIN; COMMIT;")


# ── path validation ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,kind,ok", [
    ("20261007000000_h1b_wage_stats.sql", "migration", True),
    ("lifecycle_rpcs_dryrun.sql", "test", True),
    ("../config.toml", "migration", False),
    ("20261007000000_x.sql; rm -rf /", "migration", False),
    ("/etc/passwd", "test", False),
    ("x.sql\n", "test", False),
    ("", "migration", False),
])
def test_resolve_only_accepts_plain_filenames_in_the_right_directory(name, kind, ok):
    if ok:
        path = sql_guard.resolve(name, kind, root=_ROOT, must_exist=False)
        expected_dir = "migrations" if kind == "migration" else "tests"
        assert path == os.path.join(_ROOT, "supabase", expected_dir, name)
    else:
        with pytest.raises(ValueError):
            sql_guard.resolve(name, kind, root=_ROOT, must_exist=False)


def test_resolve_requires_the_file_to_exist_by_default():
    with pytest.raises(ValueError):
        sql_guard.resolve("29990101000000_missing.sql", "migration", root=_ROOT)
