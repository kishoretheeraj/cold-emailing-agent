"""Per-tenant sessions for per-job contexts: ats_sessions.load_state_for's fail-closed filtering
(cookie-domain boundaries, explicit origins, shared hosts, malformed/foreign/oversized/world-readable
files), expire/prune, IndexedDB capture, run_job's restore and save-only-after-a-clean-finish rule,
check_session's two-poll expiry, and --warm-login's guarantees. Pure tests with fakes; the real
browser round trip is in tests/test_job_context_browser.py. playwright is stubbed because
apply_pipeline imports it at module level.
"""

import json
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_sentinel = object()
_had = {name: sys.modules.get(name, _sentinel) for name in ("playwright", "playwright.sync_api")}
_pw = MagicMock()
_pw_sync = MagicMock()
_pw_sync.Error = Exception
_pw_sync.TimeoutError = Exception
sys.modules["playwright"] = _pw
sys.modules["playwright.sync_api"] = _pw_sync
try:
    import apply_pipeline as ap  # noqa: E402
finally:
    for _name, _prev in _had.items():
        if _prev is _sentinel:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _prev
del _sentinel, _had, _pw, _pw_sync, _name, _prev

import ats_sessions  # noqa: E402

HOST = "jobs.example.com"
HTTPS = ("https", HOST, 443)


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(ats_sessions.config, "APPLY_SESSIONS_DIR", str(tmp_path))
    return tmp_path


def _write(key, state, mode=0o600, raw=None):
    path = ats_sessions._path(key)
    with open(path, "w") as fh:
        fh.write(raw if raw is not None else json.dumps(state))
    os.chmod(path, mode)
    return path


def _cookie(domain, name="sid"):
    return {"name": name, "value": "v", "domain": domain, "path": "/"}


# ── load_state_for ───────────────────────────────────────────────────────────

def test_nothing_saved_reads_as_none(sessions):
    assert ats_sessions.load_state_for(HOST, HOST, {HTTPS}) is None


def test_cookies_keep_only_the_host_and_its_subdomains(sessions):
    _write(HOST, {"cookies": [_cookie(HOST, "a"), _cookie("." + HOST, "b"), _cookie("sub." + HOST, "c"),
                              _cookie("example.com", "parent"), _cookie("other.example.com", "sibling"),
                              _cookie("evil.com", "foreign"), {"name": "nodomain"}],
                  "origins": []})
    state = ats_sessions.load_state_for(HOST, HOST, {HTTPS})
    assert sorted(c["name"] for c in state["cookies"]) == ["a", "b", "c"]


def test_origins_need_an_exact_approved_scheme_host_and_port(sessions):
    def origin(o):
        return {"origin": o, "localStorage": [{"name": "k", "value": o}]}
    _write(HOST, {"cookies": [], "origins": [
        origin(f"https://{HOST}"), origin(f"http://{HOST}"), origin(f"https://{HOST}:8443"),
        origin("https://other.example.com"), origin(f"https://{HOST}/path"), origin("not a url")]})
    state = ats_sessions.load_state_for(HOST, HOST, {HTTPS})
    assert [o["origin"] for o in state["origins"]] == [f"https://{HOST}"]


def test_shared_host_tenant_restores_cookies_but_never_origin_storage(sessions):
    key = "jobs.lever.co/acme"
    _write(key, {"cookies": [_cookie("jobs.lever.co")],
                 "origins": [{"origin": "https://jobs.lever.co", "localStorage": [{"name": "k", "value": "acme"}]}]})
    state = ats_sessions.load_state_for(key, "jobs.lever.co", {("https", "jobs.lever.co", 443)})
    assert len(state["cookies"]) == 1 and state["origins"] == []


def test_local_fixture_origin_with_its_port(sessions):
    _write("127.0.0.1", {"cookies": [], "origins": [{"origin": "http://127.0.0.1:8123"},
                                                    {"origin": "http://127.0.0.1:9999"}]})
    state = ats_sessions.load_state_for("127.0.0.1", "127.0.0.1", {("http", "127.0.0.1", 8123)})
    assert [o["origin"] for o in state["origins"]] == ["http://127.0.0.1:8123"]


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
def test_a_file_others_can_read_is_refused(sessions, mode):
    _write(HOST, {"cookies": []}, mode=mode)
    with pytest.raises(ats_sessions.SessionStateUntrusted):
        ats_sessions.load_state_for(HOST, HOST, {HTTPS})


@pytest.mark.parametrize("raw", ["{not json", "[]", '{"cookies": "x"}', '{"origins": {}}'])
def test_malformed_state_is_refused(sessions, raw):
    _write(HOST, None, raw=raw)
    with pytest.raises(ats_sessions.SessionStateUntrusted):
        ats_sessions.load_state_for(HOST, HOST, {HTTPS})


def test_oversized_state_is_refused(sessions):
    _write(HOST, None, raw='{"cookies": [], "pad": "' + "x" * (1 << 20) + '"}')
    with pytest.raises(ats_sessions.SessionStateUntrusted):
        ats_sessions.load_state_for(HOST, HOST, {HTTPS})


# ── expire / prune / IndexedDB ───────────────────────────────────────────────

def test_expire_sets_the_session_aside_and_prunes_only_old_ones(sessions):
    path = _write(HOST, {"cookies": []})
    os.utime(path, (time.time() - 30 * 86400,) * 2)  # saved a month ago
    old = os.path.join(str(sessions), "deadbeef.json.expired")
    open(old, "w").close()
    os.utime(old, (time.time() - 8 * 86400,) * 2)

    target = ats_sessions.expire(HOST)

    assert target == path + ".expired" and os.path.exists(target) and not os.path.exists(path)
    assert not os.path.exists(old)  # older than seven days: pruned
    assert ats_sessions.state_path(HOST) is None
    assert ats_sessions.expire(HOST) is None  # nothing left to expire


def test_save_state_captures_indexed_db_when_asked(sessions):
    calls = []

    class Ctx:
        def storage_state(self, **kwargs):
            calls.append(kwargs)
            return {"cookies": [_cookie(HOST)], "origins": [{"origin": f"https://{HOST}", "indexedDB": []}]}

    ats_sessions.save_state(Ctx(), HOST, domain=HOST, indexed_db=True)
    assert calls == [{"indexed_db": True}]
    assert oct(os.stat(ats_sessions._path(HOST)).st_mode & 0o777) == "0o600"


def test_save_state_falls_back_without_indexed_db_support(sessions):
    class OldCtx:
        def storage_state(self, **kwargs):
            if kwargs:
                raise TypeError("unexpected keyword")
            return {"cookies": [], "origins": []}

    ats_sessions.save_state(OldCtx(), HOST, domain=HOST, indexed_db=True)
    assert ats_sessions.state_path(HOST)


# ── run_job: restore, and save only after a clean finish ─────────────────────

@pytest.fixture
def runnable(monkeypatch, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF")
    monkeypatch.setattr(ap, "RESUME_PATH", str(resume))
    monkeypatch.setattr(ap, "live_gate", lambda: None)
    monkeypatch.setattr(ap, "validate_job_url", lambda url: None)
    monkeypatch.setattr(ap, "resolve_job_url", lambda url, base: url)
    monkeypatch.setattr(ap, "approved_for_job", lambda platform: set())
    monkeypatch.setattr(ap, "ev", lambda *a, **k: None)
    detached = []
    monkeypatch.setattr(ap.JobScope, "detach",
                        lambda self, persist_key=None, persist_domain=None: detached.append((persist_key, persist_domain)))
    return detached


JOB = {"id": "j1", "url": f"https://{HOST}/apply/1", "platform": "generic"}


def test_clean_finish_saves_the_tenant_session(runnable, monkeypatch):
    monkeypatch.setattr(ap, "_load_session", lambda jid, key, host: {"cookies": [], "origins": []})
    seen = {}

    def fake_run(scope, *a, **k):
        seen["state"] = scope.storage_state
        scope.persist = True
        return "dry_run_ok", "ok"

    monkeypatch.setattr(ap, "_run_job", fake_run)
    assert ap.run_job(object(), JOB, "http://t") == ("dry_run_ok", "ok")
    assert seen["state"] == {"cookies": [], "origins": []}
    assert runnable == [(HOST, HOST)]


@pytest.mark.parametrize("outcome", ["jobend", "no_persist_flag", "crash"])
def test_anything_but_a_clean_finish_saves_nothing(runnable, monkeypatch, outcome):
    monkeypatch.setattr(ap, "_load_session", lambda jid, key, host: None)

    def fake_run(scope, *a, **k):
        if outcome == "jobend":
            scope.persist = True  # even if it got far, an explicit end is not a clean finish
            raise ap.JobEnd("blocked", "host_not_allowed")
        if outcome == "crash":
            raise RuntimeError("boom")
        return "dry_run_incomplete", "x"

    monkeypatch.setattr(ap, "_run_job", fake_run)
    with pytest.raises(ap.JobEnd) if outcome != "no_persist_flag" else _noop():
        ap.run_job(object(), JOB, "http://t")
    assert runnable == [(None, HOST)]


class _noop:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_untrusted_saved_state_blocks_the_job(runnable, monkeypatch, sessions):
    _write(HOST, {"cookies": []}, mode=0o644)
    monkeypatch.setattr(ap, "_run_job", MagicMock())
    with pytest.raises(ap.JobEnd) as exc:
        ap.run_job(object(), JOB, "http://t")
    assert exc.value.detail == "session_state_untrusted"
    ap._run_job.assert_not_called()


def test_local_fixture_jobs_never_use_sessions(runnable, monkeypatch):
    load = MagicMock()
    monkeypatch.setattr(ap, "_load_session", load)
    monkeypatch.setattr(ap, "_run_job", lambda *a, **k: ("dry_run_ok", ""))
    ap.run_job(object(), dict(JOB, platform="test_local"), "http://t")
    load.assert_not_called()


def test_detach_saves_before_closing_and_never_after_a_violation(monkeypatch, sessions):
    saved, order = [], []
    monkeypatch.setattr(ap.ats_sessions, "save_state",
                        lambda ctx, key, domain=None, indexed_db=False: (saved.append((key, domain, indexed_db)),
                                                                        order.append("save")))
    monkeypatch.setattr(ap, "ev", lambda *a, **k: None)

    def scope_with_context():
        scope = ap.JobScope(MagicMock(), "j")
        scope.context = MagicMock()
        scope.context.close.side_effect = lambda: order.append("close")
        return scope

    scope = scope_with_context()
    scope.detach(persist_key=HOST, persist_domain=HOST)
    assert saved == [(HOST, HOST, True)] and order == ["save", "close"]

    saved.clear()
    scope = scope_with_context()
    scope.note_violation("popup_host_not_allowed")
    scope.detach(persist_key=HOST, persist_domain=HOST)
    assert saved == []


def test_a_failed_save_still_closes_and_never_raises(monkeypatch):
    monkeypatch.setattr(ap.ats_sessions, "save_state", MagicMock(side_effect=OSError("disk full")))
    monkeypatch.setattr(ap, "ev", lambda *a, **k: None)
    scope = ap.JobScope(MagicMock(), "j")
    ctx = scope.context = MagicMock()
    scope.detach(persist_key=HOST, persist_domain=HOST)
    ctx.close.assert_called_once()


# ── check_session: expiry needs two polls ────────────────────────────────────

def _walls(monkeypatch, sequence):
    from ats_accounts import detect

    seq = iter(sequence)
    monkeypatch.setattr(detect, "detect_account_wall", lambda page: next(seq))


def test_restored_session_on_a_sign_in_wall_twice_is_expired(monkeypatch):
    _walls(monkeypatch, ["workday_signin", "workday_signin"])
    expired = []
    monkeypatch.setattr(ap.ats_sessions, "expire", lambda key: expired.append(key))
    monkeypatch.setattr(ap, "ev", lambda *a, **k: None)
    scope = ap.JobScope(MagicMock(), "j", storage_state={"cookies": []})
    with pytest.raises(ap.JobEnd) as exc:
        ap.check_session(MagicMock(), scope, f"https://{HOST}/apply", "j")
    assert exc.value.status == "needs_review" and exc.value.detail == "session_expired"
    assert expired == [HOST]


def test_one_wall_reading_is_not_enough(monkeypatch):
    _walls(monkeypatch, ["unexpected", "none"])
    expire = MagicMock()
    monkeypatch.setattr(ap.ats_sessions, "expire", expire)
    scope = ap.JobScope(MagicMock(), "j", storage_state={"cookies": []})
    ap.check_session(MagicMock(), scope, f"https://{HOST}/apply", "j")
    expire.assert_not_called()


def test_no_restored_session_means_no_expiry_check(monkeypatch):
    _walls(monkeypatch, [])  # would raise StopIteration if consulted
    ap.check_session(MagicMock(), ap.JobScope(MagicMock(), "j"), f"https://{HOST}/apply", "j")


# ── --warm-login ─────────────────────────────────────────────────────────────

class _FakePW:
    def __enter__(self):
        return object()

    def __exit__(self, *a):
        return False


@pytest.fixture
def warm(monkeypatch, tmp_path, sessions):
    state = {"saved": [], "page_url": f"https://{HOST}/dashboard", "touch": True}
    monkeypatch.setattr(ap, "validate_job_url", lambda url: None)
    monkeypatch.setattr(ap, "ev", lambda *a, **k: None)
    monkeypatch.setattr(ap, "sync_playwright", lambda: _FakePW())
    monkeypatch.setattr(ap, "launch_browser", lambda pw: MagicMock())
    monkeypatch.setattr(ap, "close_browser", lambda b: True)
    monkeypatch.setattr(ap, "DISPLAY_LOCK_PATH", None)
    monkeypatch.setattr(ap.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    sentinel = tmp_path / "warm_login_done.fixed"
    page = MagicMock()
    page.url = state["page_url"]

    def wait(ms):
        if state["touch"]:
            sentinel.touch()

    page.wait_for_timeout.side_effect = wait
    monkeypatch.setattr(ap.JobScope, "attach", lambda self: setattr(self, "context", MagicMock()))
    monkeypatch.setattr(ap.JobScope, "open_page", lambda self: page)
    monkeypatch.setattr(ap.JobScope, "check", lambda self: None)
    monkeypatch.setattr(ap.ats_sessions, "save_state",
                        lambda ctx, key, domain=None, indexed_db=False: state["saved"].append((key, domain, indexed_db)))
    state["page"], state["sentinel"], state["dir"] = page, sentinel, str(tmp_path)
    return state


def test_warm_login_saves_only_after_the_sentinel_and_a_signed_in_page(warm, monkeypatch):
    monkeypatch.setattr(ap, "_signed_out", lambda page: False)
    rc = ap.warm_login(f"https://{HOST}/apply/1", sentinel_dir=warm["dir"], timeout_s=30)
    assert rc == 0 and warm["saved"] == [(HOST, HOST, True)]
    assert not warm["sentinel"].exists()  # cleaned up


def test_warm_login_still_signed_out_saves_nothing(warm, monkeypatch):
    monkeypatch.setattr(ap, "_signed_out", lambda page: True)
    assert ap.warm_login(f"https://{HOST}/apply/1", sentinel_dir=warm["dir"], timeout_s=30) == 1
    assert warm["saved"] == []


def test_warm_login_on_another_host_saves_nothing(warm, monkeypatch):
    monkeypatch.setattr(ap, "_signed_out", lambda page: False)
    warm["page"].url = "https://accounts.google.com/signin"
    assert ap.warm_login(f"https://{HOST}/apply/1", sentinel_dir=warm["dir"], timeout_s=30) == 1
    assert warm["saved"] == []


def test_warm_login_times_out_without_the_sentinel(warm, monkeypatch):
    warm["touch"] = False
    monkeypatch.setattr(ap, "_signed_out", lambda page: False)
    stale = os.path.join(warm["dir"], "warm_login_done.old")  # a leftover from an earlier run
    open(stale, "w").close()
    assert ap.warm_login(f"https://{HOST}/apply/1", sentinel_dir=warm["dir"], timeout_s=0) == 1
    assert warm["saved"] == []


def test_warm_login_refuses_a_bad_url_and_a_busy_display(monkeypatch, tmp_path):
    monkeypatch.setattr(ap, "ev", lambda *a, **k: None)
    monkeypatch.setattr(ap, "validate_job_url", MagicMock(side_effect=ap.JobEnd("blocked", "host_not_allowed")))
    assert ap.warm_login("https://evil.example/x") == 2
    monkeypatch.setattr(ap, "validate_job_url", lambda url: None)
    lock = str(tmp_path / "display2.lock")
    monkeypatch.setattr(ap, "DISPLAY_LOCK_PATH", lock)
    held = ap.take_display_lock(lock)
    try:
        assert ap.warm_login(f"https://{HOST}/apply/1") == 3
    finally:
        ap.release_display_lock(held)


def test_warm_login_scope_uses_live_rules_without_the_submit_guard(monkeypatch):
    created = []
    real_init = ap.JobScope.__init__

    def spy(self, *a, **k):
        real_init(self, *a, **k)
        created.append(self)

    monkeypatch.setattr(ap.JobScope, "__init__", spy)
    monkeypatch.setattr(ap, "validate_job_url", lambda url: None)
    monkeypatch.setattr(ap, "ev", lambda *a, **k: None)
    monkeypatch.setattr(ap, "sync_playwright", lambda: _FakePW())
    monkeypatch.setattr(ap, "launch_browser", lambda pw: MagicMock())
    monkeypatch.setattr(ap, "close_browser", lambda b: True)
    monkeypatch.setattr(ap, "DISPLAY_LOCK_PATH", None)
    monkeypatch.setattr(ap.JobScope, "attach", MagicMock(side_effect=ap.JobEnd("blocked", "x")))
    ap.warm_login(f"https://{HOST}/apply/1", timeout_s=0)
    scope = created[0]
    assert scope.dry_run is False and scope.submit_guard is False and scope.page_guards is True
