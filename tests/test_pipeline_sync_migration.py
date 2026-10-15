"""Static checks for migration 20261014000000 (pipeline mirror + pipeline_link_reply) and the migration
history around it. Functional checks: supabase/tests/pipeline_sync_dryrun.sql."""

import os
import re
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS = os.path.join(_ROOT, "supabase", "migrations")
_SQL = open(os.path.join(_MIGRATIONS, "20261014000000_pipeline_sync.sql")).read()


def _link_body():
    start = _SQL.index("create or replace function public.pipeline_link_reply")
    return _SQL[start:_SQL.index("$$;", start)]


def test_every_migration_version_is_unique():
    # Review finding 1: two branches each shipped a 20261013000000 migration.
    versions = Counter(name.split("_", 1)[0] for name in os.listdir(_MIGRATIONS) if name.endswith(".sql"))
    assert [v for v, n in versions.items() if n > 1] == []


def test_pipeline_sync_is_the_only_pipeline_schema():
    creators = [name for name in os.listdir(_MIGRATIONS)
                if re.search(r"create table if not exists public\.pipeline_replies",
                             open(os.path.join(_MIGRATIONS, name)).read(), re.I)]
    assert creators == ["20261014000000_pipeline_sync.sql"]


def test_link_reply_is_one_definer_function_with_a_version_bump():
    body = _link_body()
    assert "security definer" in body and "set search_path = pg_catalog, public" in body
    assert "version        = version + 1" in body
    assert "from public.job_applications where id = p_application_id" in body
    assert "on conflict (id) do nothing" in body
    assert "updated_at" not in body


def test_link_reply_is_service_role_only():
    assert ("revoke execute on function public.pipeline_link_reply(text, bigint) from public, anon, authenticated;"
            in _SQL)
    assert "grant  execute on function public.pipeline_link_reply(text, bigint) to service_role;" in _SQL


def test_link_reply_status_map_matches_reply_repo():
    import reply_repo

    pairs = dict(re.findall(r"when '(\w+)'\s+then '(\w+)'", _link_body()))
    assert pairs == reply_repo.KIND_TO_STATUS
