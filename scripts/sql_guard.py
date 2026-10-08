"""
Guards a migration dry run against the real database. `db_migrate.yml --mode dryrun` sends one
script through `supabase db query --linked`: BEGIN, the migration, an optional supabase/tests
dry-run file, ROLLBACK. Anything in either file that could end that transaction early (COMMIT,
END, a ROLLBACK followed by more statements) or that cannot run inside one (CREATE INDEX
CONCURRENTLY, VACUUM) would turn the dry run into a real write, so compose() refuses it.

The scanner removes comments, string literals and dollar-quoted bodies before looking at
statements, so a plpgsql function's own BEGIN ... END never reads as transaction control.
"""

import argparse
import os
import re
import sys

# ── Scanner ────────────────────────────────────────────────────────────────────

_DOLLAR_TAG = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


def strip_sql(sql):
    """Returns sql with comments removed, string literals emptied and dollar-quoted bodies
    collapsed to an empty `$$`, so only top-level statement text is left."""
    out = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end == -1 else end
            out.append(" ")
        elif sql.startswith("/*", i):
            depth = 1
            i += 2
            while i < n and depth:
                if sql.startswith("/*", i):
                    depth += 1
                    i += 2
                elif sql.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            out.append(" ")
        elif ch == "'":
            i += 1
            while i < n:
                if sql[i] == "'" and i + 1 < n and sql[i + 1] == "'":
                    i += 2
                elif sql[i] == "'":
                    i += 1
                    break
                else:
                    i += 1
            out.append("''")
        elif ch == '"':
            end = sql.find('"', i + 1)
            end = n if end == -1 else end + 1
            out.append(sql[i:end])
            i = end
        elif ch == "$":
            match = _DOLLAR_TAG.match(sql, i)
            if match:
                closer = match.group(0)
                end = sql.find(closer, match.end())
                i = n if end == -1 else end + len(closer)
                out.append("$$")
            else:
                out.append(ch)
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def statements(sql):
    """Top-level statements (comment/string/body-free text), in order, without the semicolons."""
    return [s.strip() for s in strip_sql(sql).split(";") if s.strip()]


# ── Rules ──────────────────────────────────────────────────────────────────────

_TXN_CONTROL = re.compile(
    r"^(BEGIN|COMMIT|END|ROLLBACK|ABORT|SAVEPOINT|RELEASE|START\s+TRANSACTION"
    r"|PREPARE\s+TRANSACTION)\b",
    re.IGNORECASE,
)
_NON_TRANSACTIONAL = re.compile(
    r"^(VACUUM|CHECKPOINT|ALTER\s+SYSTEM|(CREATE|DROP)\s+(DATABASE|TABLESPACE))\b"
    r"|\bINDEX\s+CONCURRENTLY\b"
    r"|^REINDEX\b.*\bCONCURRENTLY\b",
    re.IGNORECASE | re.DOTALL,
)
_BEGIN = re.compile(r"^(BEGIN|START\s+TRANSACTION)(\s+(WORK|TRANSACTION))?$", re.IGNORECASE)
_ROLLBACK = re.compile(r"^(ROLLBACK|ABORT)(\s+(WORK|TRANSACTION))?$", re.IGNORECASE)


def _shared_problems(stmt):
    if stmt.startswith("\\"):
        return [f"psql meta-command not supported: {stmt[:60]!r}"]
    if _NON_TRANSACTIONAL.search(stmt):
        return [f"cannot run inside a rolled-back transaction: {stmt[:80]!r}"]
    return []


def check_migration(sql):
    """Problems that make a migration unsafe to dry-run (empty list = safe). A migration must
    contain no transaction control at all; the dry run supplies BEGIN/ROLLBACK itself."""
    problems = []
    for stmt in statements(sql):
        problems.extend(_shared_problems(stmt))
        if _TXN_CONTROL.match(stmt):
            problems.append(f"transaction control in migration: {stmt[:60]!r}")
    return problems


def check_test(sql):
    """Problems that make a supabase/tests dry-run file unsafe (empty list = safe). It may open
    with BEGIN and must then end with ROLLBACK as its last statement; nothing may follow the
    ROLLBACK, and COMMIT/END/savepoints are never allowed."""
    problems = []
    stmts = statements(sql)
    for index, stmt in enumerate(stmts):
        problems.extend(_shared_problems(stmt))
        if not _TXN_CONTROL.match(stmt):
            continue
        if _BEGIN.match(stmt) and index == 0:
            continue
        if _ROLLBACK.match(stmt) and index == len(stmts) - 1:
            continue
        problems.append(f"transaction control not allowed here (statement {index + 1}): {stmt[:60]!r}")
    if stmts and _BEGIN.match(stmts[0]) and not _ROLLBACK.match(stmts[-1]):
        problems.append("test opens a transaction but does not end with ROLLBACK")
    return problems


def compose(migration_sql, test_sql):
    """One script: BEGIN, the migration, the optional test, ROLLBACK. Raises ValueError listing
    every problem if either input could commit or cannot run inside a transaction."""
    problems = [f"migration: {p}" for p in check_migration(migration_sql)]
    if test_sql:
        problems += [f"test: {p}" for p in check_test(test_sql)]
    if problems:
        raise ValueError("; ".join(problems))
    parts = ["BEGIN;", migration_sql.rstrip(), ";"]
    if test_sql:
        parts.append(test_sql.rstrip())
        parts.append(";")
    parts.append("ROLLBACK;")
    return "\n".join(parts) + "\n"


# ── Paths ──────────────────────────────────────────────────────────────────────

_NAME_PATTERNS = {
    "migration": re.compile(r"^[0-9]{14}_[a-z0-9_]+\.sql$"),
    "test": re.compile(r"^[a-z0-9_]+\.sql$"),
}
_DIRS = {"migration": "migrations", "test": "tests"}


def resolve(name, kind, root=".", must_exist=True):
    """Absolute path for a bare filename under supabase/migrations or supabase/tests. Rejects
    anything that is not a plain, conventionally named .sql filename (no paths, no shell)."""
    pattern = _NAME_PATTERNS.get(kind)
    # fullmatch, not match: `$` also matches before a trailing newline.
    if pattern is None or not isinstance(name, str) or not pattern.fullmatch(name):
        raise ValueError(f"not an allowed {kind} filename: {name!r}")
    path = os.path.join(os.path.abspath(root), "supabase", _DIRS[kind], name)
    if must_exist and not os.path.isfile(path):
        raise ValueError(f"{kind} file not found: {name}")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compose a rolled-back migration dry run.")
    parser.add_argument("--migration", required=True)
    parser.add_argument("--test", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        with open(resolve(args.migration, "migration")) as f:
            migration_sql = f.read()
        test_sql = None
        if args.test:
            with open(resolve(args.test, "test")) as f:
                test_sql = f.read()
        script = compose(migration_sql, test_sql)
    except ValueError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    with open(args.out, "w") as f:
        f.write(script)
    print(f"composed dry run: {args.migration}" + (f" + {args.test}" if args.test else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
