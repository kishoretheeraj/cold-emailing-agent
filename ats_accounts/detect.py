"""
Account-wall detection from the current page. Read-only; never raises (any page error reads as
no wall).

NOTE: every selector below is a PLACEHOLDER pending recon against live tenants.
"""

import re

# ── Selectors (provisional, unverified against live tenants) ───────────────────

SELECTORS = {
    "challenge": [
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "iframe[src*='challenges.cloudflare.com']",
        "[data-automation-id='captchaContainer']",
    ],
    "workday_create": [
        "[data-automation-id='createAccountSubmitButton']",
        "[data-automation-id='verifyPassword']",
    ],
    "workday_signin": [
        "[data-automation-id='signInSubmitButton']",
        "[data-automation-id='signInContent']",
    ],
    "password_field": ["input[type='password']"],
    "email_field": ["input[type='email']", "input[autocomplete='username']"],
    "sso": [
        "[data-automation-id='signInWithGoogle']",
        "button:has-text('Sign in with Google')",
        "button:has-text('Continue with Google')",
        "button:has-text('Sign in with Microsoft')",
        "button:has-text('Single Sign-On')",
    ],
}

_DUPLICATE_RE = re.compile(
    r"already exists|account already registered|already (?:have|has) an account|"
    r"email (?:address )?is already (?:in use|registered)|account with this email",
    re.IGNORECASE)
_LOCKOUT_RE = re.compile(
    r"account (?:has been )?locked|temporarily locked|too many (?:failed )?(?:sign|log)[- ]?in attempts",
    re.IGNORECASE)
_RATE_RE = re.compile(r"too many requests|rate limit|try again later|\b429\b", re.IGNORECASE)


class WallKind:
    NONE = "none"
    WORKDAY_SIGNIN = "workday_signin"
    WORKDAY_CREATE = "workday_create"
    UNEXPECTED = "unexpected"
    SSO_ONLY = "sso_only"
    CHALLENGE = "challenge"


def find_visible(page, selectors, strict=False):
    """First visible locator among `selectors`, or None. With strict=True a page error
    propagates instead of reading as 'not visible'."""
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.is_visible():
                return loc
        except Exception:
            if strict:
                raise
            continue
    return None


def any_visible(page, key, strict=False):
    return find_visible(page, SELECTORS[key], strict=strict) is not None


def detect_account_wall(page, strict=False):
    """One of the WallKind strings for what `page` is showing. With strict=True a page error
    propagates (fail closed); otherwise any page error reads as no wall."""
    try:
        if any_visible(page, "challenge", strict=strict):
            return WallKind.CHALLENGE
        if any_visible(page, "workday_create", strict=strict):
            return WallKind.WORKDAY_CREATE
        if any_visible(page, "workday_signin", strict=strict):
            return WallKind.WORKDAY_SIGNIN
        if any_visible(page, "password_field", strict=strict):
            return WallKind.UNEXPECTED
        if any_visible(page, "sso", strict=strict) and not any_visible(page, "email_field", strict=strict):
            return WallKind.SSO_ONLY
        return WallKind.NONE
    except Exception:
        if strict:
            raise
        return WallKind.NONE


def _body_text(page):
    return page.inner_text("body") or ""


def detect_duplicate_account_message(page):
    """True when the page says an account already exists for the email."""
    try:
        return bool(_DUPLICATE_RE.search(_body_text(page)))
    except Exception:
        return False


def detect_throttle(page):
    """'lockout', 'rate_limit' or None, from the visible page text."""
    try:
        text = _body_text(page)
        if _LOCKOUT_RE.search(text):
            return "lockout"
        if _RATE_RE.search(text):
            return "rate_limit"
    except Exception:
        pass
    return None
