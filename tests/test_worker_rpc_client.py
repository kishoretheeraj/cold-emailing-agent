"""Worker lifecycle RPCs go through the service-role client (migration 20261015000000 makes them
service_role-only, so the public anon key can no longer forge a preview, a submission or a receipt).
Without the key the call falls back to the anon client, so the code can ship before the migration."""

import logging
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import db

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = (ROOT / "supabase" / "migrations" / "20261015000000_worker_rpcs_service_role_only.sql").read_text()
MIGRATION_SQL = re.sub(r"--[^\n]*", "", MIGRATION)


@pytest.fixture
def clients(mocker):
    anon, admin = MagicMock(name="anon"), MagicMock(name="admin")
    for c in (anon, admin):
        c.rpc.return_value.execute.return_value.data = True
    mocker.patch.object(db, "get_client", return_value=anon)
    mocker.patch.object(db, "get_admin_client", return_value=admin)
    mocker.patch.object(db.time, "sleep")
    mocker.patch.object(db, "_warned_no_service_key", False)
    return anon, admin


WORKER_CALLS = [
    ("claim_application", lambda: db.claim_application(7, "preparing")),
    ("heartbeat_application", lambda: db.heartbeat_application(7, "lease")),
    ("renew_submission_lease", lambda: db.renew_submission_lease(7, "lease", "hash")),
    ("complete_preview", lambda: db.complete_preview(7, "lease", {"x": 1}, "sig")),
    ("release_application", lambda: db.release_application(7, "lease", "failed_retryable", "r")),
    ("record_submission", lambda: db.record_submission(7, "lease", "greenhouse", "2026-10-15")),
    ("mark_application_unsupported", lambda: db.mark_unsupported(7, "r")),
    ("record_receipt_evidence", lambda: db.record_receipt_evidence(7, {"message_id": "<m@x>"})),
    ("recover_stale_leases", lambda: db.recover_stale_leases(1800)),
    ("request_takeover", lambda: db.request_takeover(7, "lease", "captcha", "r")),
    ("clear_takeover", lambda: db.clear_takeover(7, "lease")),
]


@pytest.mark.parametrize("name,call", WORKER_CALLS, ids=[n for n, _ in WORKER_CALLS])
def test_worker_rpc_uses_service_role_when_key_is_set(clients, mocker, name, call):
    anon, admin = clients
    mocker.patch.object(db, "SUPABASE_SERVICE_ROLE_KEY", "service-key")
    call()
    assert name in [c.args[0] for c in admin.rpc.call_args_list]
    assert anon.rpc.call_count == 0


@pytest.mark.parametrize("name,call", WORKER_CALLS, ids=[n for n, _ in WORKER_CALLS])
def test_worker_rpc_falls_back_to_anon_without_key(clients, mocker, name, call):
    anon, admin = clients
    mocker.patch.object(db, "SUPABASE_SERVICE_ROLE_KEY", None)
    call()
    assert name in [c.args[0] for c in anon.rpc.call_args_list]
    assert admin.rpc.call_count == 0


def test_missing_key_warns_once(clients, mocker, caplog):
    mocker.patch.object(db, "SUPABASE_SERVICE_ROLE_KEY", None)
    with caplog.at_level(logging.WARNING, logger="db"):
        db.heartbeat_application(7, "lease")
        db.heartbeat_application(7, "lease")
    warnings = [r for r in caplog.records if "SUPABASE_SERVICE_ROLE_KEY" in r.getMessage()]
    assert len(warnings) == 1


def test_non_worker_rpc_stays_on_anon_client(clients, mocker):
    anon, admin = clients
    mocker.patch.object(db, "SUPABASE_SERVICE_ROLE_KEY", "service-key")
    db._rpc("requeue_preview", {"p_id": 7})
    anon.rpc.assert_called_once_with("requeue_preview", {"p_id": 7})
    assert admin.rpc.call_count == 0


def _granted_names(sql, grantee):
    return set(re.findall(rf"GRANT\s+EXECUTE ON FUNCTION (\w+)\([^)]*\) TO {grantee};", sql))


def _revoked_names(sql):
    return set(re.findall(r"REVOKE EXECUTE ON FUNCTION (\w+)\([^)]*\) FROM PUBLIC, anon, authenticated;", sql))


def test_migration_and_db_agree_on_the_worker_set():
    assert _revoked_names(MIGRATION_SQL) == set(db._WORKER_RPCS)
    assert _granted_names(MIGRATION_SQL, "service_role") == set(db._WORKER_RPCS)


def test_every_db_rpc_is_classified():
    # A new RPC wrapper must decide which client it uses; the UI RPCs are called from the
    # contact-manager, never from db.py.
    called = set(re.findall(r'_rpc\("(\w+)"', (ROOT / "db.py").read_text()))
    assert called <= set(db._WORKER_RPCS)


def test_migration_never_touches_ui_rpcs():
    for name in ("approve_application", "reset_approval", "requeue_preview", "resolve_confirmation",
                 "hold_for_referral", "release_referral_hold", "takeover_continue"):
        assert name not in MIGRATION_SQL


def _latest_signature(name):
    # The newest CREATE OR REPLACE across the earlier migrations, as an argument-type list.
    sigs = []
    for path in sorted((ROOT / "supabase" / "migrations").glob("*.sql")):
        if path.name >= "20261015000000":
            continue
        text = re.sub(r"--[^\n]*", "", path.read_text())
        for m in re.finditer(rf"CREATE OR REPLACE FUNCTION {name}\(([^)]*)\)", text):
            types = [p.strip().split()[1].upper() for p in m.group(1).split(",") if p.strip()]
            sigs.append(", ".join(types))
    return sigs[-1]


@pytest.mark.parametrize("name", sorted(db._WORKER_RPCS))
def test_migration_targets_the_live_signature(name):
    # REVOKE on a signature that no longer exists fails the whole migration (the 4-arg
    # record_submission was dropped by 20261008000000).
    types = _latest_signature(name).replace("INTEGER", "INT")
    assert f"REVOKE EXECUTE ON FUNCTION {name}({types}) FROM PUBLIC, anon, authenticated;" in MIGRATION_SQL
