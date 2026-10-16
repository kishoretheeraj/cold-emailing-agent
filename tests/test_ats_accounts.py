"""Tests for ats_accounts: pure fakes, tmp_path files, no browser and no network."""

import json
import logging
import os
import stat

import pytest
from cryptography.fernet import Fernet

from ats_accounts import handle_account_wall
from ats_accounts.backoff import BackoffExhausted, BackoffStore
from ats_accounts.detect import (WallKind, detect_account_wall, detect_duplicate_account_message,
                                 SELECTORS as DETECT_SELECTORS)
from ats_accounts.flows import workday
from ats_accounts.flows.base import FlowContext, FlowResult, step
from ats_accounts.passwords import SPECIALS, generate_password
from ats_accounts.vault import AttemptLimitExceeded, FernetVaultAdapter, can_attempt


# ── Fakes ──────────────────────────────────────────────────────────────────────

class FakeLocator:
    def __init__(self, present):
        self.present = present

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.present else 0

    def is_visible(self):
        return self.present


class FakePage:
    def __init__(self, visible=(), text="", boom=False):
        self.visible = set(visible)
        self.text = text
        self.boom = boom
        self.touched = 0

    def locator(self, selector):
        self.touched += 1
        if self.boom:
            raise RuntimeError("page closed")
        return FakeLocator(selector in self.visible)

    def inner_text(self, selector):
        self.touched += 1
        if self.boom:
            raise RuntimeError("page closed")
        return self.text


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.t = start

    def __call__(self):
        return self.t


@pytest.fixture
def vault(tmp_path):
    return FernetVaultAdapter(str(tmp_path / "vault.enc"), Fernet.generate_key())


@pytest.fixture
def backoff(tmp_path):
    clock = FakeClock()
    store = BackoffStore(str(tmp_path / "backoff.json"), now_fn=clock)
    store.clock = clock
    return store


# ── Passwords ──────────────────────────────────────────────────────────────────

def test_password_length_and_classes():
    for _ in range(2000):
        p = generate_password()
        assert len(p) == 24
        assert sum(c.isupper() for c in p) >= 2
        assert sum(c.islower() for c in p) >= 2
        assert sum(c.isdigit() for c in p) >= 2
        assert sum(c in SPECIALS for c in p) >= 2


def test_password_custom_length_and_too_short():
    assert len(generate_password(length=12)) == 12
    with pytest.raises(ValueError):
        generate_password(length=7)


def test_password_avoids_email_local_part():
    email = "Abc123xyz.Pass!word@example.com"
    local = email.split("@")[0].lower()
    for _ in range(500):
        p = generate_password(email=email).lower()
        for i in range(len(local) - 2):
            assert local[i:i + 3] not in p


# ── Vault ──────────────────────────────────────────────────────────────────────

def test_attempt_limit_decision_table():
    assert can_attempt({}, "signup")
    assert can_attempt({"signups": 0, "resets": 1}, "signup")
    assert not can_attempt({"signups": 1}, "signup")
    assert can_attempt({"signups": 1, "resets": 0}, "reset")
    assert not can_attempt({"resets": 1}, "reset")
    assert not can_attempt({"signups": 2}, "signup")


def test_reserve_persists_pending_before_typing(vault, tmp_path):
    reservation = vault.reserve("workday", "acme.wd5.myworkdayjobs.com", "a@b.com", "signup")
    typed = None
    with pytest.raises(RuntimeError):
        typed = reservation["password"]  # the "typing" step
        raise RuntimeError("crash before commit")
    fresh = FernetVaultAdapter(vault.path, vault.key)
    entry = fresh.get("workday:acme.wd5.myworkdayjobs.com")
    assert entry["pending_password"] == typed
    assert entry["pending_purpose"] == "signup"
    assert entry["password"] is None
    found = fresh.lookup("workday", "acme.wd5.myworkdayjobs.com", "A@B.com")
    assert found["pending_password"] == typed


def test_commit_verified_promotes_and_keeps_previous(vault):
    first = vault.reserve("workday", "t.example.com", "a@b.com", "signup")
    vault.commit(first, "verified")
    entry = vault.get("workday:t.example.com")
    assert entry["password"] == first["password"]
    assert entry["pending_password"] is None
    assert entry["status"] == "active"
    second = vault.reserve("workday", "t.example.com", "a@b.com", "reset")
    vault.commit(second, "verified")
    entry = vault.get("workday:t.example.com")
    assert entry["password"] == second["password"]
    assert entry["previous_password"] == first["password"]


def test_commit_failed_keeps_old_and_clears_pending(vault):
    first = vault.reserve("workday", "t.example.com", "a@b.com", "signup")
    vault.commit(first, "verified")
    second = vault.reserve("workday", "t.example.com", "a@b.com", "reset")
    vault.commit(second, "failed")
    entry = vault.get("workday:t.example.com")
    assert entry["password"] == first["password"]
    assert entry["pending_password"] is None
    assert second["password"] in entry["retired_passwords"]


@pytest.mark.parametrize("purpose", ["signup", "reset"])
def test_second_reserve_raises(vault, purpose):
    vault.reserve("workday", "t.example.com", "a@b.com", purpose)
    with pytest.raises(AttemptLimitExceeded):
        vault.reserve("workday", "t.example.com", "a@b.com", purpose)
    counts = vault.attempts("workday", "t.example.com")
    assert counts["signups" if purpose == "signup" else "resets"] == 1


def test_attempt_limits_survive_new_instance_and_are_0600(vault):
    vault.reserve("workday", "t.example.com", "a@b.com", "signup")
    fresh = FernetVaultAdapter(vault.path, vault.key)
    with pytest.raises(AttemptLimitExceeded):
        fresh.reserve("workday", "t.example.com", "a@b.com", "signup")
    assert stat.S_IMODE(os.stat(vault.attempts_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(vault.path).st_mode) == 0o600


def test_unresolved_pending_is_retired_not_lost(vault):
    first = vault.reserve("workday", "t.example.com", "a@b.com", "signup")
    second = vault.reserve("workday", "t.example.com", "a@b.com", "reset")
    entry = vault.get("workday:t.example.com")
    assert entry["pending_password"] == second["password"]
    assert first["password"] in entry["retired_passwords"]


def test_signin_failures_counted(vault):
    assert vault.record_signin_failure("workday", "t.example.com") == 1
    assert vault.record_signin_failure("workday", "t.example.com") == 2
    assert vault.attempts("workday", "t.example.com")["signin_failures"] == 2


# ── Duplicate-account decision ─────────────────────────────────────────────────

class RecordingVault:
    def __init__(self, existing):
        self.existing = existing
        self.reserve_calls = 0

    def lookup(self, platform, tenant, email):
        return self.existing

    def reserve(self, *args):
        self.reserve_calls += 1
        raise AssertionError("must not reserve")

    def attempts(self, platform, tenant):
        return {"signups": 0, "resets": 0, "signin_failures": 0}


def test_existing_credential_goes_to_sign_in_never_second_reserve(monkeypatch, tmp_path, backoff):
    _open_gate(monkeypatch, tmp_path)
    rec = RecordingVault({"password": "x", "email": "a@b.com"})
    ctx = FlowContext(page=FakePage(), platform="workday", tenant="t.example.com",
                      email="a@b.com", vault=rec, backoff=backoff)
    monkeypatch.setattr(workday, "_click", lambda *a, **k: True)
    monkeypatch.setattr(workday, "sign_in", lambda c: FlowResult("signed_in", "stub"))
    result = workday.create_account(ctx)
    assert result.outcome == "signed_in"
    assert rec.reserve_calls == 0


def test_signup_limit_is_needs_review(monkeypatch, tmp_path, vault, backoff):
    _open_gate(monkeypatch, tmp_path)
    vault.reserve("workday", "t.example.com", "a@b.com", "signup")
    vault.commit({"platform": "workday", "tenant": "t.example.com"}, "failed")
    ctx = FlowContext(page=FakePage(), platform="workday", tenant="t.example.com",
                      email="a@b.com", vault=vault, backoff=backoff)
    result = workday.create_account(ctx)
    assert result.as_dict() == {"outcome": "needs_review", "detail": "signup_limit"}


# ── Backoff ────────────────────────────────────────────────────────────────────

def test_backoff_spacing_with_fake_clock(backoff):
    scope = "workday:t.example.com"
    assert backoff.check_and_mark(scope) == 0
    backoff.clock.t += 10
    assert backoff.check_and_mark(scope) == pytest.approx(50)
    backoff.clock.t += 50
    assert backoff.check_and_mark(scope) == 0
    assert backoff.check_and_mark("workday:other.example.com") == 0


def test_backoff_429_stops_for_24h_and_persists(backoff, tmp_path):
    scope = "workday:t.example.com"
    assert backoff.is_stopped(scope) is False
    backoff.record_429(scope)
    assert backoff.is_stopped(scope)
    backoff.clock.t += 23 * 3600
    assert backoff.is_stopped(scope)
    reopened = BackoffStore(backoff.path, now_fn=backoff.clock)
    assert reopened.check_and_mark(scope) > 0
    backoff.clock.t += 2 * 3600
    assert backoff.is_stopped(scope) is False
    assert stat.S_IMODE(os.stat(backoff.path).st_mode) == 0o600


def test_backoff_lockout_stops(backoff):
    backoff.record_lockout("workday:t.example.com")
    assert backoff.is_stopped("workday:t.example.com")


def test_backoff_for_progression(backoff):
    scope = "workday:t.example.com"
    assert [backoff.backoff_for(scope, n) for n in (1, 2, 3)] == [30, 120, 600]
    with pytest.raises(BackoffExhausted):
        backoff.backoff_for(scope, 4)


# ── Detection ──────────────────────────────────────────────────────────────────

def _sel(key, i=0):
    return DETECT_SELECTORS[key][i]


@pytest.mark.parametrize("visible,expected", [
    ((), WallKind.NONE),
    ((_sel("challenge"),), WallKind.CHALLENGE),
    ((_sel("workday_create"),), WallKind.WORKDAY_CREATE),
    ((_sel("workday_signin"),), WallKind.WORKDAY_SIGNIN),
    ((_sel("password_field"),), WallKind.UNEXPECTED),
    ((_sel("sso"),), WallKind.SSO_ONLY),
    ((_sel("sso"), _sel("email_field")), WallKind.NONE),
    ((_sel("challenge"), _sel("workday_signin")), WallKind.CHALLENGE),
])
def test_detect_kinds(visible, expected):
    assert detect_account_wall(FakePage(visible=visible)) == expected


def test_detect_never_raises():
    assert detect_account_wall(FakePage(boom=True)) == WallKind.NONE
    assert detect_duplicate_account_message(FakePage(boom=True)) is False


@pytest.mark.parametrize("text,expected", [
    ("An account already exists for this email", True),
    ("Account already registered", True),
    ("Welcome back", False),
])
def test_detect_duplicate_message(text, expected):
    assert detect_duplicate_account_message(FakePage(text=text)) is expected


# ── Dispatch and Workday gate ──────────────────────────────────────────────────

@pytest.mark.parametrize("platform", ["greenhouse", "lever", "ashby"])
def test_no_account_platforms(platform):
    assert handle_account_wall(FakePage(), platform, "h.example.com", "a@b.com") == {
        "outcome": "no_account_needed"}
    wall = FakePage(visible=(_sel("password_field"),))
    assert handle_account_wall(wall, platform, "h.example.com", "a@b.com") == {
        "outcome": "needs_review", "detail": "unexpected_account_wall:unexpected"}


def test_unknown_platform():
    result = handle_account_wall(FakePage(), "taleo", "h.example.com", "a@b.com")
    assert result["outcome"] == "needs_review"


def test_workday_gate_closed_without_env(monkeypatch, tmp_path):
    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps(["t.example.com"]))
    monkeypatch.delenv("WORKDAY_ACCOUNT_CREATION", raising=False)
    monkeypatch.setenv("WORKDAY_TENANTS_ALLOW", str(allow))
    page = FakePage()
    result = workday.handle(page, "t.example.com", "a@b.com")
    assert result == {"outcome": "needs_review", "detail": "workday_gate_closed"}
    assert page.touched == 0


def test_workday_gate_closed_for_unlisted_tenant(monkeypatch, tmp_path):
    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps(["other.example.com"]))
    monkeypatch.setenv("WORKDAY_ACCOUNT_CREATION", "1")
    monkeypatch.setenv("WORKDAY_TENANTS_ALLOW", str(allow))
    page = FakePage()
    assert workday.handle(page, "t.example.com", "a@b.com")["detail"] == "workday_gate_closed"
    assert page.touched == 0


def test_workday_gate_missing_file_denies(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKDAY_ACCOUNT_CREATION", "1")
    monkeypatch.setenv("WORKDAY_TENANTS_ALLOW", str(tmp_path / "nope.json"))
    assert workday.gate_allows("t.example.com") is False


def test_workday_gate_open(monkeypatch, tmp_path):
    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps(["T.example.com"]))
    monkeypatch.setenv("WORKDAY_ACCOUNT_CREATION", "1")
    monkeypatch.setenv("WORKDAY_TENANTS_ALLOW", str(allow))
    assert workday.gate_allows("t.example.com") is True


def test_workday_open_gate_no_wall_and_challenge(monkeypatch, tmp_path, vault, backoff):
    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps(["t.example.com"]))
    monkeypatch.setenv("WORKDAY_ACCOUNT_CREATION", "1")
    monkeypatch.setenv("WORKDAY_TENANTS_ALLOW", str(allow))
    opts = {"vault": vault, "backoff": backoff}
    assert workday.handle(FakePage(), "t.example.com", "a@b.com", opts)["outcome"] == "signed_in"
    challenge = FakePage(visible=(_sel("challenge"),))
    assert workday.handle(challenge, "t.example.com", "a@b.com", opts) == {
        "outcome": "blocked", "detail": "challenge"}
    sso = FakePage(visible=(_sel("sso"),))
    assert workday.handle(sso, "t.example.com", "a@b.com", opts)["detail"] == "sso_only"


def test_workday_stopped_scope_blocks(monkeypatch, tmp_path, vault, backoff):
    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps(["t.example.com"]))
    monkeypatch.setenv("WORKDAY_ACCOUNT_CREATION", "1")
    monkeypatch.setenv("WORKDAY_TENANTS_ALLOW", str(allow))
    backoff.record_429("workday:t.example.com")
    page = FakePage(visible=(_sel("workday_signin"),))
    result = workday.handle(page, "t.example.com", "a@b.com", {"vault": vault, "backoff": backoff})
    assert result["outcome"] == "blocked"


# ── Logging ────────────────────────────────────────────────────────────────────

def test_step_never_logs_passwords(caplog, vault, backoff):
    ctx = FlowContext(page=None, platform="workday", tenant="t.example.com",
                      email="a@b.com", vault=vault, backoff=backoff)
    with caplog.at_level(logging.INFO):
        step(ctx, "typing", field="email", password="Sup3r!Secret", new_password="N3w!Secret",
             client_secret="shh", n=2)
    text = caplog.text
    assert "Sup3r!Secret" not in text
    assert "N3w!Secret" not in text
    assert "shh" not in text
    assert "field=email" in text and "n=2" in text


def test_step_scrubs_nested_reservation_dicts(caplog, vault, backoff):
    ctx = FlowContext(page=None, platform="workday", tenant="t.example.com",
                      email="a@b.com", vault=vault, backoff=backoff)
    with caplog.at_level(logging.INFO):
        step(ctx, "recovery", reservation={"password": "Sup3r!Secret", "purpose": "reset"})
    assert "Sup3r!Secret" not in caplog.text
    assert "purpose" in caplog.text


# ── Gate hardening ─────────────────────────────────────────────────────────────

def _open_gate(monkeypatch, tmp_path, tenants=("t.example.com",)):
    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps(list(tenants)))
    monkeypatch.setenv("WORKDAY_ACCOUNT_CREATION", "1")
    monkeypatch.setenv("WORKDAY_TENANTS_ALLOW", str(allow))


def test_direct_create_account_respects_gate(monkeypatch, tmp_path, vault, backoff):
    monkeypatch.delenv("WORKDAY_ACCOUNT_CREATION", raising=False)
    ctx = FlowContext(page=FakePage(), platform="workday", tenant="t.example.com",
                      email="a@b.com", vault=vault, backoff=backoff)
    with pytest.raises(Exception) as ei:
        workday.create_account(ctx)
    assert "workday_gate_closed" in str(ei.value)


def test_supplied_context_cannot_smuggle_tenant(monkeypatch, tmp_path, vault, backoff):
    _open_gate(monkeypatch, tmp_path)
    ctx = FlowContext(page=None, platform="workday", tenant="evil.example.com",
                      email="a@b.com", vault=vault, backoff=backoff)
    built = workday._build_context(FakePage(), "t.example.com", "a@b.com", ctx)
    assert built.tenant == "t.example.com"
    assert built.platform == "workday"


def test_reservation_from_carries_password_for_stale_check(vault):
    reservation = vault.reserve("workday", "t.example.com", "a@b.com", "signup")
    cred = vault.lookup("workday", "t.example.com", "a@b.com")
    ctx = FlowContext(page=None, platform="workday", tenant="t.example.com",
                      email="a@b.com", vault=vault, backoff=None)
    rebuilt = workday._reservation_from(ctx, cred, reservation["password"])
    assert rebuilt["password"] == cred["pending_password"]


def test_dead_page_never_verifies(monkeypatch, tmp_path, vault, backoff):
    _open_gate(monkeypatch, tmp_path)
    reservation = vault.reserve("workday", "t.example.com", "a@b.com", "signup")
    ctx = FlowContext(page=FakePage(boom=True), platform="workday", tenant="t.example.com",
                      email="a@b.com", vault=vault, backoff=backoff)
    assert workday._verify_new_password(ctx, reservation["password"]) is False
    assert workday._signed_in(ctx) is False
    assert workday._run(ctx).outcome == "needs_review"


class FlakyPage(FakePage):
    """inner_text works (the liveness probe passes) but every locator raises."""

    def locator(self, selector):
        raise RuntimeError("locator broken")


def test_page_failing_after_probe_is_never_success(monkeypatch, tmp_path, vault, backoff):
    _open_gate(monkeypatch, tmp_path)
    opts = {"vault": vault, "backoff": backoff}
    result = workday.handle(FlakyPage(), "t.example.com", "a@b.com", opts)
    assert result == {"outcome": "needs_review", "detail": "detection_failed"}
    ctx = FlowContext(page=FlakyPage(), platform="workday", tenant="t.example.com",
                      email="a@b.com", vault=vault, backoff=backoff)
    assert workday._signed_in(ctx) is False
    assert workday._verify_new_password(ctx, "x") is False


def test_throttle_text_blocks_before_any_auth(monkeypatch, tmp_path, vault, backoff):
    _open_gate(monkeypatch, tmp_path)
    page = FakePage(text="Your account has been locked due to too many failed sign in attempts")
    result = workday.handle(page, "t.example.com", "a@b.com", {"vault": vault, "backoff": backoff})
    assert result["outcome"] == "blocked"
    assert backoff.is_stopped("workday:t.example.com")
