"""
Workday prelude for the Beelink apply worker (spec 2026-10-08 §6.1).

Gets a Workday tenant from the job page into the application wizard, and moves through wizard
steps without ever pressing Submit:

  enter_apply_flow  Apply -> Apply Manually (never Autofill with Resume / Use My Last Application:
                    they import data nobody reviewed)
  authenticate      sign in with the vault's account for this tenant, or create one. The password
                    is generated and saved to the vault BEFORE anything is typed, so a crash
                    mid-signup never loses it. A refused login or an "already in use" email stops
                    for a human; nothing here resets a password or opens a second account.
  advance           presses the footer's Next only while the step bar shows a later step and the
                    button does not read like Submit. On Review it returns False: submitting is
                    apply_agent.submit()'s job, after the operator's signed approval.

Selectors are Workday's stable data-automation-id values, gathered from MIT-licensed
djwmobley/claude-interview-coach (entry and auth ids verified read-only on live tenants,
2026-10-05) plus selector facts from ubangura/Workday-Application-Automator and
amgenene/workday_auto. Both UI generations are accepted. Anything unrecognised raises WorkdayStop,
which the caller turns into a takeover or a needs_input reason. Passwords are never logged.
"""

import logging
import re
import time

import takeover

log = logging.getLogger(__name__)

ADVENTURE = "[data-automation-id='adventureButton']"
APPLY_MANUALLY = "[data-automation-id='applyManually']"
ALREADY_APPLIED = "[data-automation-id='alreadyApplied']"
AUTH_GATE = ("[data-automation-id='signInContent'], form[data-automation-id='signInFormo'], "
             "[data-automation-id='signInFormContainer'], [data-automation-id='createAccountForm']")
SIGN_IN_LINK = "[data-automation-id='signInLink']"
CREATE_ACCOUNT_LINK = "[data-automation-id='createAccountLink']"
EMAIL = "[data-automation-id='email']:visible"
PASSWORD = "[data-automation-id='password']:visible"
VERIFY_PASSWORD = "[data-automation-id='verifyPassword']:visible"
CREATE_CHECKBOX = "[data-automation-id='createAccountCheckbox']:visible"
SIGN_IN_SUBMIT = "[data-automation-id='signInSubmitButton']"
CREATE_SUBMIT = "[data-automation-id='createAccountSubmitButton']"
OVERLAY = "[data-automation-id='noCaptchaWrapper']:visible [data-automation-id='click_filter']"
AUTH_ERROR = "[data-automation-id='errorMessage']:visible"
VERIFY_CODE = "[data-automation-id='verificationCode'], input[name='verificationCode']"
VERIFY_BUTTON = "[data-automation-id='verifyButton']"
WIZARD = ("[data-automation-id='applyFlowMyInfoPage'], [data-automation-id='pageFooterNextButton'], "
          "[data-automation-id='bottom-navigation-next-button'], [data-automation-id='progressBar'], "
          "[data-automation-id='contactInformationPage'], [data-automation-id='myExperiencePage']")
NEXT_BUTTON = ("[data-automation-id='pageFooterNextButton']:visible, "
               "[data-automation-id='bottom-navigation-next-button']:visible")
STEPS = ("[data-automation-id='progressBar'] [data-automation-id='progressBarActiveStep'], "
         "[data-automation-id='progressBar'] [data-automation-id='progressBarCompletedStep'], "
         "[data-automation-id='progressBar'] [data-automation-id='progressBarInactiveStep']")
ACTIVE_STEP = "progressBarActiveStep"
ERROR_BANNER = "[data-automation-id='errorBanner']:visible"

_NEXT_WORDS = re.compile(r"^\s*(save and continue|next|continue)\s*$", re.IGNORECASE)
# Deny wins over allow: anything that reads like a final action is never clicked here.
_SUBMIT_WORDS = re.compile(r"submit|send|finish|\bapply\b|review and", re.IGNORECASE)
_ACCOUNT_EXISTS = re.compile(r"already (in use|exists|registered)|sign in instead", re.IGNORECASE)


class WorkdayStop(Exception):
    """The prelude cannot continue on its own. `kind` is a takeover kind (captcha, login,
    email_verification, unrecognized_page, other) or a terminal one (already_applied)."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def _visible(page, selector):
    try:
        return page.locator(selector).first.is_visible()
    except Exception:
        return False


def page_kind(page):
    """Which Workday screen this is: already_applied, verify, auth, wizard, job, or unknown."""
    if _visible(page, ALREADY_APPLIED):
        return "already_applied"
    if _visible(page, VERIFY_CODE):
        return "verify"
    if _visible(page, AUTH_GATE):
        return "auth"
    if _visible(page, WIZARD):
        return "wizard"
    if _visible(page, ADVENTURE):
        return "job"
    return "unknown"


def _wait_for(page, kinds, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    while True:
        kind = page_kind(page)
        if kind in kinds:
            return kind
        if time.monotonic() >= deadline:
            return None
        page.wait_for_timeout(250)


def _stop_if_challenge(page):
    if takeover.challenge_present(page):
        raise WorkdayStop("captcha", "CAPTCHA on the Workday page")


def enter_apply_flow(page, timeout_seconds=15):
    """From the job page to the auth gate ("auth") or a guest tenant's wizard ("wizard")."""
    kind = _wait_for(page, {"job", "auth", "wizard", "already_applied"}, timeout_seconds)
    if kind == "already_applied":
        raise WorkdayStop("already_applied", "Workday says this job was already applied to")
    _stop_if_challenge(page)
    if kind in ("auth", "wizard"):
        return kind
    if kind is None:
        raise WorkdayStop("unrecognized_page", "No Workday Apply button, sign-in form or wizard on this page")
    page.locator(ADVENTURE).first.click()
    manual = page.locator(APPLY_MANUALLY).first
    try:
        manual.wait_for(state="visible", timeout=10_000)
    except Exception:
        raise WorkdayStop("unrecognized_page", "Apply Manually was not offered after Apply") from None
    manual.click()
    kind = _wait_for(page, {"auth", "wizard", "already_applied"}, timeout_seconds)
    if kind == "already_applied":
        raise WorkdayStop("already_applied", "Workday says this job was already applied to")
    if kind is None:
        raise WorkdayStop("unrecognized_page", "Neither a sign-in form nor the wizard appeared after Apply Manually")
    _stop_if_challenge(page)
    return kind


def _show_panel(page, panel):
    submit = SIGN_IN_SUBMIT if panel == "signin" else CREATE_SUBMIT
    toggle = SIGN_IN_LINK if panel == "signin" else CREATE_ACCOUNT_LINK
    if _visible(page, submit):
        return
    if _visible(page, toggle):
        page.locator(toggle).first.click()
        try:
            page.locator(submit).first.wait_for(state="visible", timeout=5_000)
            return
        except Exception:
            pass
    raise WorkdayStop("unrecognized_page", f"Workday {panel} form not found")


def _press_auth_submit(page, button):
    # The real button is aria-hidden under a click_filter overlay that takes the pointer.
    if _visible(page, OVERLAY):
        page.locator(OVERLAY).first.click()
    else:
        page.locator(button).first.click()


def _after_auth(page, timeout_seconds=15):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _visible(page, AUTH_ERROR):
            return "error", page.locator(AUTH_ERROR).first.inner_text().strip()
        kind = page_kind(page)
        if kind in ("verify", "wizard", "already_applied"):
            return kind, ""
        page.wait_for_timeout(250)
    return None, ""


def _verify_email(page, verify):
    found = verify() or {}
    if found.get("code"):
        page.locator(VERIFY_CODE).first.fill(found["code"])
        page.locator(VERIFY_BUTTON).first.click()
    elif found.get("link"):
        page.goto(found["link"])
    else:
        raise WorkdayStop("email_verification", "No Workday verification email arrived; verify by hand")
    outcome, text = _after_auth(page)
    if outcome != "wizard":
        raise WorkdayStop("email_verification", f"Email verification did not complete: {text or outcome}")


def authenticate(page, vault, tenant, email, login_url, verify):
    """Sign in or sign up on the auth gate. Returns "signed_in" or "created"; raises WorkdayStop.
    `verify()` returns {"code": ...}, {"link": ...} or None (email_verification.wait_for_verification)."""
    _stop_if_challenge(page)
    entry = vault.get(tenant)
    if entry and entry.get("status") == "rejected":
        raise WorkdayStop("login", "This site's saved account was refused before; sign in by hand, then press I'm done")

    if entry:
        _show_panel(page, "signin")
        page.locator(EMAIL).first.fill(entry["email"])
        page.locator(PASSWORD).first.fill(entry["password"])
        _press_auth_submit(page, SIGN_IN_SUBMIT)
        outcome, text = _after_auth(page)
        if outcome == "error":
            vault.mark(tenant, "rejected")
            raise WorkdayStop("login", f"Workday refused the saved account ({text}); sign in by hand")
        if outcome == "verify":
            _verify_email(page, verify)
        elif outcome != "wizard":
            raise WorkdayStop("unrecognized_page", "Sign-in did not reach the application")
        vault.mark(tenant, "active")
        log.info(f"[APPLY-WORKDAY] | {tenant} | signed in")
        return "signed_in"

    entry = vault.reserve(tenant, email, login_url)
    _show_panel(page, "create")
    page.locator(EMAIL).first.fill(entry["email"])
    page.locator(PASSWORD).first.fill(entry["password"])
    if _visible(page, VERIFY_PASSWORD):
        page.locator(VERIFY_PASSWORD).first.fill(entry["password"])
    if _visible(page, CREATE_CHECKBOX) and not page.locator(CREATE_CHECKBOX).first.is_checked():
        page.locator(CREATE_CHECKBOX).first.check()
    _press_auth_submit(page, CREATE_SUBMIT)
    outcome, text = _after_auth(page)
    if outcome == "error":
        vault.mark(tenant, "rejected")
        if _ACCOUNT_EXISTS.search(text):
            raise WorkdayStop("login", "An account already exists for this email on this site; "
                                       "sign in or reset the password by hand")
        raise WorkdayStop("login", f"Workday refused the new account ({text})")
    if outcome == "verify":
        _verify_email(page, verify)
    elif outcome != "wizard":
        raise WorkdayStop("unrecognized_page", "Account creation did not reach the application")
    vault.mark(tenant, "active")
    log.info(f"[APPLY-WORKDAY] | {tenant} | account created")
    return "created"


def progress(page):
    """(active step index, step count) from the step bar, or None when it cannot be read."""
    try:
        steps = page.locator(STEPS)
        count = steps.count()
        ids = [steps.nth(i).get_attribute("data-automation-id") for i in range(count)]
    except Exception:
        return None
    if count < 2 or ids.count(ACTIVE_STEP) != 1:
        return None
    return ids.index(ACTIVE_STEP), count


def advance(page, timeout_seconds=10):
    """Press Next once. False on the last (Review) step, where nothing is clicked."""
    position = progress(page)
    if position is None:
        raise WorkdayStop("unrecognized_page", "The Workday step bar could not be read; not clicking anything")
    active, count = position
    if active >= count - 1:
        return False
    button = page.locator(NEXT_BUTTON).first
    try:
        label = button.inner_text(timeout=2_000).strip()
    except Exception:
        raise WorkdayStop("unrecognized_page", "No Workday Next button on this step") from None
    if _SUBMIT_WORDS.search(label) or not _NEXT_WORDS.match(label):
        raise WorkdayStop("unrecognized_page", f"Refusing to press {label!r}: it is not a Next button")
    button.click()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _visible(page, ERROR_BANNER):
            text = page.locator(ERROR_BANNER).first.inner_text().strip()
            raise WorkdayStop("unrecognized_page", f"Workday reported errors on this step: {text}")
        moved = progress(page)
        if moved and moved[0] > active:
            return True
        page.wait_for_timeout(250)
    raise WorkdayStop("unrecognized_page", "The next Workday step did not load")


# ── Wizard steps ───────────────────────────────────────────────────────────────

# Contact fields Workday names by data-automation-id in both UI generations. Filled only when
# empty: a value the tenant prefilled (from the account or a resume parse) is left as it is.
_KNOWN_FIELDS = (
    ("first_name", ("legalNameSection_firstName", "legalName--firstName")),
    ("last_name", ("legalNameSection_lastName", "legalName--lastName")),
    ("phone_local", ("phone-number", "phoneNumber--phoneNumber")),
    ("city", ("addressSection_city", "address--city")),
)


def fill_known_fields(page, values):
    """Fill the empty contact fields this step shows. Returns the keys it filled."""
    filled = []
    for key, ids in _KNOWN_FIELDS:
        value = values.get(key)
        if not value:
            continue
        for aid in ids:
            field = page.locator(f"input[data-automation-id='{aid}']:visible, "
                                 f"[data-automation-id='formField-{aid}'] input:visible").first
            try:
                if field.count() and not field.input_value().strip():
                    field.fill(str(value))
                    filled.append(key)
                    break
            except Exception as exc:
                log.info(f"[APPLY-WORKDAY] | {key} not fillable: {exc}")
    return filled


def step_label(page):
    """The active step's name from the step bar, or None."""
    try:
        active = page.locator(f"[data-automation-id='progressBar'] [data-automation-id='{ACTIVE_STEP}']").first
        return active.inner_text(timeout=2_000).strip() or None
    except Exception:
        return None


def submit_button(page):
    """The Review step's Submit button. Raises WorkdayStop anywhere else, or when the button on
    Review does not read exactly Submit."""
    position = progress(page)
    if position is None or position[0] != position[1] - 1:
        raise WorkdayStop("unrecognized_page", "Not on the Workday Review step")
    button = page.locator(NEXT_BUTTON).first
    try:
        label = button.inner_text(timeout=2_000).strip()
    except Exception:
        raise WorkdayStop("unrecognized_page", "No button on the Workday Review step") from None
    if not re.fullmatch(r"submit( application)?", label, re.IGNORECASE):
        raise WorkdayStop("unrecognized_page", f"The Review step's button reads {label!r}, not Submit")
    return button
