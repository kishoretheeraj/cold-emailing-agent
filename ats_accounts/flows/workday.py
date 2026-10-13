"""
Workday account flows: sign in, create an account, forgot-password recovery.

Allowed only when WORKDAY_ACCOUNT_CREATION=1 and the tenant host is on the allowlist file
(WORKDAY_TENANTS_ALLOW, default /etc/jobagent24/workday_tenants_allow.json). Rules:
- the pending password is reserved in the vault BEFORE anything is typed;
- at most one signup and one reset per tenant ever (the vault enforces it); a stored credential
  that fails sign-in goes to needs_review, never to a second account;
- at most two failed sign-ins per run, at least 60 s between auth actions, a 429 or lockout
  stops the tenant for 24 h;
- the terms checkbox is a scoped exception: ticked only while the gate flag is set, and logged;
- passwords and codes are never passed to a log call.
"""

import json
import logging
import os
import time
from dataclasses import replace
from datetime import datetime, timezone

import email_verification
from credential_vault import VaultError, read_key_file

from ..backoff import BackoffExhausted, BackoffStore
from ..detect import (WallKind, detect_account_wall, detect_duplicate_account_message,
                      detect_throttle, find_visible)
from ..vault import AttemptLimitExceeded, FernetVaultAdapter, can_attempt
from .base import FlowAbort, FlowContext, FlowResult, step

log = logging.getLogger(__name__)

# ── Selectors (provisional, unverified against live tenants) ───────────────────
# PLACEHOLDERS pending recon against real tenants. Each value is a list tried in order.

SELECTORS = {
    "email": ["[data-automation-id='email']", "input[type='email']"],
    "password": ["[data-automation-id='password']", "input[type='password']"],
    "verify_password": ["[data-automation-id='verifyPassword']"],
    "consent_checkbox": ["[data-automation-id='createAccountCheckbox']"],
    "create_submit": ["[data-automation-id='createAccountSubmitButton']"],
    "signin_submit": ["[data-automation-id='signInSubmitButton']"],
    "to_create_link": ["[data-automation-id='createAccountLink']"],
    "to_signin_link": ["[data-automation-id='signInLink']"],
    "signin_error": ["[data-automation-id='errorMessage']"],
    "verify_code": ["[data-automation-id='verificationCode']"],
    "verify_submit": ["[data-automation-id='verifyCodeSubmitButton']"],
    "forgot_link": ["[data-automation-id='forgotPasswordLink']"],
    "reset_email": ["[data-automation-id='email']"],
    "reset_request_submit": ["[data-automation-id='resetPasswordSubmitButton']"],
    "new_password": ["[data-automation-id='newPassword']"],
    "confirm_new_password": ["[data-automation-id='verifyNewPassword']"],
    "reset_submit": ["[data-automation-id='resetPasswordSetButton']"],
}

DEFAULT_ALLOW_PATH = "/etc/jobagent24/workday_tenants_allow.json"
DEFAULT_VAULT_PATH = "/var/lib/jobagent24/ats_vault.enc"
DEFAULT_KEY_FILE = "/etc/job-agent/vault.env"
WORKDAY_SENDER_DOMAINS = ("myworkday.com", "workday.com", "myworkdayjobs.com")
VERIFY_TIMEOUT_SECONDS = 120
MAX_SIGNIN_FAILURES = 2
MAX_PACE_WAIT_SECONDS = 120

_sleep = time.sleep


class _SelectorMissing(FlowAbort):
    def __init__(self, key):
        super().__init__("needs_review", f"selector_missing:{key}")


# ── Gate ───────────────────────────────────────────────────────────────────────

def gate_allows(tenant_host):
    """True only with the env flag set and the tenant on the allowlist. Never raises."""
    try:
        if os.environ.get("WORKDAY_ACCOUNT_CREATION") != "1":
            return False
        host = str(tenant_host or "").strip().lower()
        if not host:
            return False
        path = os.environ.get("WORKDAY_TENANTS_ALLOW", DEFAULT_ALLOW_PATH)
        with open(path, "r") as f:
            allowed = json.load(f)
        if isinstance(allowed, dict):
            allowed = allowed.get("tenants", [])
        return host in {str(h).strip().lower() for h in allowed}
    except Exception:
        return False


def _consent_allowed(ctx):
    # Scoped exception: the terms checkbox may only be ticked while the full gate allows it.
    return gate_allows(ctx.tenant)


def _require_gate(ctx):
    """create_account/forgot_password/sign_in are only reachable through handle(), but a future
    caller must not bypass the gate by calling them directly."""
    if not gate_allows(ctx.tenant):
        raise FlowAbort("needs_review", "workday_gate_closed")


def _page_alive(ctx):
    """Fail-closed liveness probe: detection helpers read a broken page as 'no wall', which
    must never verify a credential or report success."""
    try:
        ctx.page.inner_text("body")
        return True
    except Exception:
        return False


# ── Page helpers ───────────────────────────────────────────────────────────────

def _detect(ctx):
    """Strict wall detection: a page error aborts the flow, it never reads as 'no wall'."""
    try:
        return detect_account_wall(ctx.page, strict=True)
    except Exception:
        raise FlowAbort("needs_review", "detection_failed")


def _visible(ctx, key):
    try:
        return find_visible(ctx.page, SELECTORS[key], strict=True)
    except Exception:
        raise FlowAbort("needs_review", "detection_failed")


def _find(ctx, key):
    return _visible(ctx, key)


def _fill(ctx, key, value):
    loc = _find(ctx, key)
    if loc is None:
        raise _SelectorMissing(key)
    loc.fill(value)


def _click(ctx, key, required=True):
    loc = _find(ctx, key)
    if loc is None:
        if required:
            raise _SelectorMissing(key)
        return False
    loc.click()
    return True


def _settle(ctx):
    try:
        ctx.page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    try:
        ctx.page.wait_for_timeout(1500)
    except Exception:
        pass


def _watch_429(ctx):
    seen = ctx.state.setdefault("http_429", [])
    try:
        ctx.page.on("response", lambda r: seen.append(r.status) if r.status == 429 else None)
    except Exception:
        pass


def _pace(ctx):
    for _ in range(4):
        wait = ctx.backoff.check_and_mark(ctx.scope)
        if wait <= 0:
            return
        if wait > MAX_PACE_WAIT_SECONDS:
            raise FlowAbort("blocked", "backoff_active")
        _sleep(wait)
    raise FlowAbort("blocked", "backoff_active")


def _guard_throttle(ctx):
    if not _page_alive(ctx):
        raise FlowAbort("needs_review", "page_unavailable")
    kind = detect_throttle(ctx.page)
    if ctx.state.get("http_429"):
        kind = "rate_limit"
    if kind == "lockout":
        ctx.backoff.record_lockout(ctx.scope)
        raise FlowAbort("blocked", "lockout")
    if kind == "rate_limit":
        ctx.backoff.record_429(ctx.scope)
        raise FlowAbort("blocked", "rate_limited")


def _signed_in(ctx):
    # Signed in means exactly that: no wall of any kind remains and no error is shown.
    # An SSO-only or unexpected wall after a submit is not a sign-in.
    if not _page_alive(ctx):
        return False
    try:
        kind = _detect(ctx)
    except FlowAbort:
        return False
    if kind != WallKind.NONE:
        return False
    try:
        return _visible(ctx, "signin_error") is None
    except FlowAbort:
        return False


def _do_signin(ctx, secret):
    if _detect(ctx) != WallKind.WORKDAY_SIGNIN:
        _click(ctx, "to_signin_link", required=False)
    _pace(ctx)
    _fill(ctx, "email", ctx.email)
    _fill(ctx, "password", secret)
    _click(ctx, "signin_submit")
    _settle(ctx)
    _guard_throttle(ctx)
    return _signed_in(ctx)


def _verify_new_password(ctx, secret):
    if not _page_alive(ctx):
        return False
    try:
        kind = _detect(ctx)
    except FlowAbort:
        return False
    if kind == WallKind.NONE:
        return True
    if kind == WallKind.WORKDAY_SIGNIN:
        ok = _do_signin(ctx, secret)
        if not ok:
            ctx.vault.record_signin_failure(ctx.platform, ctx.tenant)
        return ok
    return False


def _apply_verification(ctx, found):
    if "code" in found:
        _fill(ctx, "verify_code", found["code"])
        _click(ctx, "verify_submit")
    elif "link" in found:
        ctx.page.goto(found["link"])
    _settle(ctx)
    _guard_throttle(ctx)


def _wait_for_mail(ctx, since, purpose):
    return email_verification.wait_for_verification(
        WORKDAY_SENDER_DOMAINS, since, timeout_seconds=VERIFY_TIMEOUT_SECONDS,
        allowed_link_hosts=(ctx.tenant,), purpose=purpose)


def _reservation_from(ctx, cred, secret=None):
    # The password is included so commit()'s stale-reservation check stays armed: without it,
    # a concurrent reset could swap the pending password and recovery would promote one it
    # never verified. The dict is never passed to step()/log.
    return {"platform": ctx.platform, "tenant": ctx.tenant, "scope": ctx.scope, "email": ctx.email,
            "purpose": cred.get("pending_purpose") or "signup",
            "password": secret if secret is not None else cred.get("pending_password")}


# ── Flows ──────────────────────────────────────────────────────────────────────

def sign_in(ctx):
    _require_gate(ctx)
    cred = ctx.vault.lookup(ctx.platform, ctx.tenant, ctx.email)
    if not cred:
        return FlowResult("needs_review", "no_credential")
    if cred.get("status") == "rejected":
        return FlowResult("needs_review", "credential_rejected")
    candidates = []
    for key in ("password", "pending_password"):
        value = cred.get(key)
        if value and value not in candidates:
            candidates.append(value)
    if not candidates:
        return FlowResult("needs_review", "no_credential")
    failures = 0
    for secret in candidates:
        step(ctx, "signin_attempt", n=failures + 1)
        if _do_signin(ctx, secret):
            if cred.get("pending_password") and secret == cred["pending_password"]:
                ctx.vault.commit(_reservation_from(ctx, cred, secret), "verified")
            else:
                ctx.vault.mark_status(ctx.platform, ctx.tenant, "active")
            step(ctx, "signed_in")
            return FlowResult("signed_in", "ok")
        failures += 1
        ctx.vault.record_signin_failure(ctx.platform, ctx.tenant)
        step(ctx, "signin_failed", n=failures)
        if failures >= MAX_SIGNIN_FAILURES:
            break
    ctx.vault.mark_status(ctx.platform, ctx.tenant, "rejected")
    return FlowResult("needs_review", "signin_failed")


def create_account(ctx):
    _require_gate(ctx)
    if ctx.vault.lookup(ctx.platform, ctx.tenant, ctx.email):
        step(ctx, "create_skipped", reason="existing_credential")
        _click(ctx, "to_signin_link", required=False)
        return sign_in(ctx)
    try:
        reservation = ctx.vault.reserve(ctx.platform, ctx.tenant, ctx.email, "signup")
    except AttemptLimitExceeded:
        return FlowResult("needs_review", "signup_limit")
    password = reservation["password"]
    step(ctx, "signup_reserved")
    _fill(ctx, "email", ctx.email)
    _fill(ctx, "password", password)
    _fill(ctx, "verify_password", password)
    if _consent_allowed(ctx):
        box = _find(ctx, "consent_checkbox")
        if box is not None:
            box.check()
            step(ctx, "consent_checked", scoped_exception="account_terms")
    since = datetime.now(timezone.utc)
    _pace(ctx)
    _click(ctx, "create_submit")
    _settle(ctx)
    _guard_throttle(ctx)
    if detect_duplicate_account_message(ctx.page):
        ctx.vault.commit(reservation, "failed")
        step(ctx, "duplicate_account")
        if can_attempt(ctx.vault.attempts(ctx.platform, ctx.tenant), "reset"):
            return forgot_password(ctx)
        return FlowResult("needs_review", "duplicate_account_no_reset")
    found = _wait_for_mail(ctx, since, "verify")
    if not found:
        # The account may exist: the pending password stays in the vault for the next sign-in.
        return FlowResult("needs_review", "verification_timeout")
    _apply_verification(ctx, found)
    if not _verify_new_password(ctx, password):
        return FlowResult("needs_review", "post_signup_signin_failed")
    ctx.vault.commit(reservation, "verified")
    step(ctx, "account_created")
    return FlowResult("account_created", "ok")


def forgot_password(ctx):
    _require_gate(ctx)
    try:
        reservation = ctx.vault.reserve(ctx.platform, ctx.tenant, ctx.email, "reset")
    except AttemptLimitExceeded:
        return FlowResult("needs_review", "reset_limit")
    new_password = reservation["password"]
    step(ctx, "reset_reserved")
    _click(ctx, "forgot_link")
    _fill(ctx, "reset_email", ctx.email)
    since = datetime.now(timezone.utc)
    _pace(ctx)
    _click(ctx, "reset_request_submit")
    _settle(ctx)
    _guard_throttle(ctx)
    found = _wait_for_mail(ctx, since, "reset")
    if not found:
        ctx.vault.commit(reservation, "failed")
        return FlowResult("needs_review", "reset_timeout")
    _apply_verification(ctx, found)
    _fill(ctx, "new_password", new_password)
    _fill(ctx, "confirm_new_password", new_password)
    _pace(ctx)
    _click(ctx, "reset_submit")
    _settle(ctx)
    _guard_throttle(ctx)
    if _verify_new_password(ctx, new_password):
        ctx.vault.commit(reservation, "verified")
        step(ctx, "password_reset")
        return FlowResult("password_reset", "ok")
    ctx.vault.commit(reservation, "failed")
    return FlowResult("needs_review", "reset_signin_failed")


# ── Entry point ────────────────────────────────────────────────────────────────

def _default_vault():
    path = os.environ.get("ATS_VAULT_PATH", DEFAULT_VAULT_PATH)
    key = os.environ.get("VAULT_KEY") or read_key_file(os.environ.get("VAULT_KEY_FILE", DEFAULT_KEY_FILE))
    return FernetVaultAdapter(path, key)


def _build_context(page, tenant_host, email, ctx):
    # The gated tenant_host always wins: a supplied FlowContext must not smuggle in a
    # different tenant, platform, or email (which would also skew the vault/backoff scopes).
    tenant = str(tenant_host or "").strip().lower()
    if isinstance(ctx, FlowContext):
        return replace(ctx, page=page, platform="workday", tenant=tenant, email=email or ctx.email)
    opts = ctx or {}
    return FlowContext(
        page=page, platform="workday", tenant=tenant, email=email,
        vault=opts.get("vault") or _default_vault(),
        backoff=opts.get("backoff") or BackoffStore(),
        dry_run=bool(opts.get("dry_run")))


def _run(ctx):
    stopped = ctx.backoff.is_stopped(ctx.scope)
    if stopped:
        return FlowResult("blocked", f"backoff_until:{stopped}")
    if not _page_alive(ctx):
        return FlowResult("needs_review", "page_unavailable")
    _watch_429(ctx)
    kind = _detect(ctx)
    step(ctx, "detect", kind=kind)
    # A page that already shows lockout/rate-limit text gets no further auth attempt, and the
    # 24h stop is recorded even when there is no account wall to act on.
    _guard_throttle(ctx)
    if kind == WallKind.NONE:
        return FlowResult("signed_in", "no_wall")
    if kind == WallKind.SSO_ONLY:
        return FlowResult("needs_review", "sso_only")
    if kind == WallKind.CHALLENGE:
        return FlowResult("blocked", "challenge")
    if kind == WallKind.UNEXPECTED:
        return FlowResult("needs_review", "unexpected_account_wall")
    if ctx.dry_run:
        return FlowResult("needs_review", f"dry_run:{kind}")
    if kind == WallKind.WORKDAY_SIGNIN:
        if ctx.vault.lookup(ctx.platform, ctx.tenant, ctx.email):
            return sign_in(ctx)
        if not can_attempt(ctx.vault.attempts(ctx.platform, ctx.tenant), "signup"):
            return FlowResult("needs_review", "no_credential")
        _click(ctx, "to_create_link")
        return create_account(ctx)
    return create_account(ctx)


def handle(page, tenant_host, email, ctx=None):
    """{'outcome', 'detail'} for a Workday account wall. Never raises."""
    if not gate_allows(tenant_host):
        return {"outcome": "needs_review", "detail": "workday_gate_closed"}
    try:
        fctx = _build_context(page, tenant_host, email, ctx)
    except Exception as exc:
        log.warning(f"[ATS] | workday | {tenant_host} | context unavailable | {type(exc).__name__}")
        return {"outcome": "needs_review", "detail": "workday_context_unavailable"}
    try:
        return _run(fctx).as_dict()
    except FlowAbort as abort:
        step(fctx, "aborted", outcome=abort.outcome, detail=abort.detail)
        return abort.result().as_dict()
    except BackoffExhausted:
        return FlowResult("blocked", "backoff_exhausted").as_dict()
    except (VaultError, AttemptLimitExceeded) as exc:
        step(fctx, "vault_error", error=type(exc).__name__)
        return FlowResult("needs_review", "vault_error").as_dict()
    except Exception as exc:
        step(fctx, "failed", error=type(exc).__name__)
        return FlowResult("failed", "exception:" + type(exc).__name__).as_dict()
