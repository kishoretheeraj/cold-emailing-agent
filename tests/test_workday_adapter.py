"""workday_adapter.py against a real browser and a local fixture tenant
(tests/fixtures/workday_tenant.html). Skipped where Playwright or Chromium is unavailable."""

import json
import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

import credential_vault
import workday_adapter
from workday_adapter import WorkdayStop

FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "workday_tenant.html").as_uri()
TENANT = "fixture.wd5.myworkdayjobs.com"
EMAIL = "me+jobs@example.com"


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    executable = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=executable)
        except Exception as exc:
            pytest.skip(f"no launchable Chromium here: {exc}")
        yield b
        b.close()


@pytest.fixture
def open_tenant(browser):
    contexts = []

    def _open(**scenario):
        context = browser.new_context()
        contexts.append(context)
        page = context.new_page()
        page.add_init_script(f"window.SCENARIO = {json.dumps(scenario)};")
        page.goto(FIXTURE)
        return page

    yield _open
    for c in contexts:
        c.close()


@pytest.fixture
def vault(tmp_path):
    return credential_vault.Vault(str(tmp_path / "vault.bin"), Fernet.generate_key().decode())


def _log(page):
    return page.evaluate("window.LOG")


def _code(value="482913"):
    return lambda: {"code": value}


# ── entry ──────────────────────────────────────────────────────────────────────

def test_enters_through_apply_manually_only(open_tenant):
    page = open_tenant()
    assert workday_adapter.enter_apply_flow(page) == "auth"
    log = _log(page)
    assert log == ["applyManually"]
    assert "autofillWithResume" not in log and "useMyLastApplication" not in log


def test_guest_tenant_goes_straight_to_the_wizard(open_tenant):
    page = open_tenant(guest=True)
    assert workday_adapter.enter_apply_flow(page) == "wizard"


def test_already_at_the_gate_clicks_nothing(open_tenant):
    page = open_tenant(start="auth")
    assert workday_adapter.enter_apply_flow(page) == "auth"
    assert _log(page) == []


def test_already_applied_stops(open_tenant):
    page = open_tenant(alreadyApplied=True)
    with pytest.raises(WorkdayStop) as stop:
        workday_adapter.enter_apply_flow(page)
    assert stop.value.kind == "already_applied"


def test_a_captcha_at_the_gate_stops_for_a_human(open_tenant):
    page = open_tenant(start="auth", captchaAtGate=True)
    with pytest.raises(WorkdayStop) as stop:
        workday_adapter.enter_apply_flow(page)
    assert stop.value.kind == "captcha"


# ── authentication ─────────────────────────────────────────────────────────────

def test_creates_an_account_with_a_password_saved_first(open_tenant, vault):
    page = open_tenant(start="auth")
    seen = {}

    def verify():
        # By the time verification runs, the password is already on disk.
        seen["entry"] = credential_vault.Vault(vault.path, vault.key).get(TENANT)
        return {"code": "482913"}

    assert workday_adapter.authenticate(page, vault, TENANT, EMAIL, "https://x", verify) == "created"
    assert seen["entry"]["status"] == "reserved"
    assert _log(page) == [f"createAccount:{EMAIL}", "verify:482913"]
    assert vault.get(TENANT)["status"] == "active"
    assert workday_adapter.page_kind(page) == "wizard"


def test_create_without_email_verification(open_tenant, vault):
    page = open_tenant(start="auth", verify="none")
    assert workday_adapter.authenticate(page, vault, TENANT, EMAIL, "https://x", _code()) == "created"


def test_existing_account_without_a_saved_password_stops_and_never_signs_in_blind(open_tenant, vault):
    page = open_tenant(start="auth", existingAccount=True)
    with pytest.raises(WorkdayStop) as stop:
        workday_adapter.authenticate(page, vault, TENANT, EMAIL, "https://x", _code())
    assert stop.value.kind == "login"
    assert "already exists" in str(stop.value)
    assert "signIn" not in _log(page)
    assert vault.get(TENANT)["status"] == "rejected"


def test_signs_in_with_the_saved_account(open_tenant, vault):
    entry = vault.reserve(TENANT, EMAIL, "https://x")
    vault.mark(TENANT, "active")
    page = open_tenant(start="auth", password=entry["password"])
    assert workday_adapter.authenticate(page, vault, TENANT, EMAIL, "https://x", _code()) == "signed_in"
    assert _log(page) == ["signIn"]


def test_a_refused_sign_in_stops_and_never_creates_another_account(open_tenant, vault):
    vault.reserve(TENANT, EMAIL, "https://x")
    page = open_tenant(start="auth", password="something-else")
    with pytest.raises(WorkdayStop) as stop:
        workday_adapter.authenticate(page, vault, TENANT, EMAIL, "https://x", _code())
    assert stop.value.kind == "login"
    assert not any(e.startswith("createAccount") for e in _log(page))
    assert vault.get(TENANT)["status"] == "rejected"


def test_a_previously_refused_account_is_not_retried(open_tenant, vault):
    vault.reserve(TENANT, EMAIL, "https://x")
    vault.mark(TENANT, "rejected")
    page = open_tenant(start="auth")
    with pytest.raises(WorkdayStop) as stop:
        workday_adapter.authenticate(page, vault, TENANT, EMAIL, "https://x", _code())
    assert stop.value.kind == "login"
    assert _log(page) == []


@pytest.mark.parametrize("verify", [lambda: None, lambda: {"code": "000000"}])
def test_verification_that_does_not_complete_stops(open_tenant, vault, verify):
    page = open_tenant(start="auth")
    with pytest.raises(WorkdayStop) as stop:
        workday_adapter.authenticate(page, vault, TENANT, EMAIL, "https://x", verify)
    assert stop.value.kind == "email_verification"


def test_password_never_reaches_the_logs(open_tenant, vault, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    page = open_tenant(start="auth")
    workday_adapter.authenticate(page, vault, TENANT, EMAIL, "https://x", _code())
    assert vault.get(TENANT)["password"] not in caplog.text


# ── wizard ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("generation", ["new", "old"])
def test_walks_every_step_and_stops_at_review_without_submitting(open_tenant, generation):
    page = open_tenant(start="wizard", generation=generation, fields=[])
    moves = 0
    while workday_adapter.advance(page):
        moves += 1
        assert moves < 10
    assert moves == 4
    assert workday_adapter.progress(page) == (4, 5)
    assert "SUBMIT" not in _log(page)


def test_an_error_banner_after_next_stops(open_tenant):
    page = open_tenant(start="wizard", errorOnNext=0)
    with pytest.raises(WorkdayStop) as stop:
        workday_adapter.advance(page)
    assert "Legal First Name is required" in str(stop.value)


def test_an_unreadable_step_bar_clicks_nothing(open_tenant):
    page = open_tenant(start="wizard")
    page.evaluate("document.getElementById('bar').remove()")
    with pytest.raises(WorkdayStop):
        workday_adapter.advance(page)
    assert _log(page) == []


def test_a_next_button_that_reads_like_submit_is_refused_even_mid_flow(open_tenant):
    page = open_tenant(start="wizard")
    page.evaluate("document.getElementById('next').textContent = 'Review and Submit'")
    with pytest.raises(WorkdayStop):
        workday_adapter.advance(page)
    assert _log(page) == []


@pytest.mark.parametrize("scenario,kind", [
    ({}, "job"), ({"start": "auth"}, "auth"), ({"start": "wizard"}, "wizard"),
    ({"start": "wizard", "generation": "old"}, "wizard"), ({"alreadyApplied": True}, "already_applied"),
])
def test_page_kind(open_tenant, scenario, kind):
    assert workday_adapter.page_kind(open_tenant(**scenario)) == kind


def test_next_with_a_required_field_empty_shows_the_banner_and_stops(open_tenant):
    page = open_tenant(start="wizard")
    with pytest.raises(WorkdayStop) as stop:
        workday_adapter.advance(page)
    assert "errors on this step" in str(stop.value)
    assert workday_adapter.progress(page) == (0, 5)
