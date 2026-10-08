"""The last steps of submit() against a real browser DOM (spec 2026-10-08 fifty-a-day F12/F13):
which button is pressed, how long confirmation is waited for, and the emailed security code a site
can hold a submission for. Skipped where Playwright or Chromium is unavailable."""

import os
from datetime import datetime, timezone

import pytest

import apply_agent
import config


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


@pytest.fixture(autouse=True)
def _short_waits(mocker):
    mocker.patch.object(config, "APPLY_CONFIRMATION_POLLS", 3)


@pytest.fixture
def page(browser):
    context = browser.new_context()
    yield context.new_page()
    context.close()


# ── Which button ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("html", [
    "<button>Submit application</button>",
    "<button>Submit Application</button><button style='display:none'>Submit Application</button>",
    "<form><input type='submit' value='Submit Application'></form>",
    "<button>Submit your application</button><button>Save draft</button>",
])
def test_one_visible_enabled_submit_button_is_found(page, html):
    page.set_content(html)
    button = apply_agent._resolve_submit_control(page)
    assert button.is_visible()


@pytest.mark.parametrize("html,found", [
    ("<button>Next</button>", 0),
    ("<button>Submit application</button><button>Submit application</button>", 2),
    ("<button disabled>Submit application</button>", 0),
    ("<button>Submit</button>", 0),                       # not the application's submit
    ("<button>Submit application and subscribe</button>", 0),
])
def test_an_ambiguous_or_missing_button_refuses_before_any_click(page, html, found):
    page.set_content(html + "<script>window.clicks = 0; document.querySelectorAll('button').forEach("
                            "b => b.addEventListener('click', () => window.clicks++))</script>")
    with pytest.raises(ValueError, match=f"found {found}"):
        apply_agent._resolve_submit_control(page)
    assert page.evaluate("window.clicks") == 0


# ── How long confirmation is waited for ────────────────────────────────────────

def test_a_slow_confirmation_is_still_seen(page):
    page.set_content("<div id=x>Uploading...</div><script>setTimeout(() => "
                     "document.getElementById('x').textContent = 'Thank you for applying!', 4000)</script>")
    assert apply_agent._submission_confirmed(page, polls=8) is True


def test_rejection_copy_ends_the_wait_at_once(page):
    page.set_content("<p>Please complete all required fields.</p>"
                     "<script>setTimeout(() => document.body.append('Thank you for applying'), 3000)</script>")
    assert apply_agent._submission_confirmed(page, polls=8) is False


def test_a_confirmation_url_counts(page):
    page.route("https://boards.example/**", lambda route: route.fulfill(body="<p>Done</p>", content_type="text/html"))
    page.goto("https://boards.example/acme/jobs/123/confirmation")
    assert apply_agent._submission_confirmed(page, polls=2) is True


def test_no_confirmation_is_not_a_confirmation(page):
    page.set_content("<form><input name=name></form>")
    assert apply_agent._submission_confirmed(page, polls=2) is False


# ── The emailed security code ──────────────────────────────────────────────────

_CODE_STEP = """
<p>We sent a security code to your email. Enter the code to submit your application.</p>
{inputs}
<button onclick="window.submits = (window.submits || 0) + 1;
  const code = [...document.querySelectorAll('input')].map(i => i.value).join('');
  if (code === '{expect}') document.body.innerHTML = '<h1>Thank you for applying</h1>';">Submit application</button>
"""


def test_a_code_step_is_recognised_only_with_a_code_field(page):
    page.set_content(_CODE_STEP.format(inputs="<input id=security_code>", expect="X"))
    assert apply_agent._email_code_requested(page)
    page.set_content("<p>Your verification code was sent last week.</p>")
    assert not apply_agent._email_code_requested(page)


@pytest.mark.parametrize("inputs", [
    "<input id=security_code autocomplete=one-time-code>",
    "".join(f"<input name=code_{i} maxlength=1>" for i in range(8)),
])
def test_the_emailed_code_completes_the_same_submission(page, mocker, inputs):
    page.set_content(_CODE_STEP.format(inputs=inputs, expect="AB12CD34"))
    wait = mocker.patch.object(apply_agent.email_verification, "wait_for_verification",
                               return_value={"code": "AB12CD34"})
    clicked_at = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
    assert apply_agent._complete_email_code(page, 7, "lease", clicked_at) is True
    assert page.evaluate("window.submits") == 1
    senders, since = wait.call_args.args
    assert senders == config.APPLY_EMAIL_CODE_SENDERS and since < clicked_at


def test_without_a_code_a_human_finishes_it(page, mocker):
    page.set_content(_CODE_STEP.format(inputs="<input id=security_code>", expect="X"))
    mocker.patch.object(apply_agent.email_verification, "wait_for_verification", return_value=None)
    mocker.patch.object(config, "APPLY_TAKEOVER_ENABLED", True)
    human = mocker.patch.object(apply_agent.takeover, "await_human", return_value=True)
    assert apply_agent._complete_email_code(page, 7, "lease", datetime.now(timezone.utc)) is False
    assert human.call_args.args[2] == "email_verification"
    assert page.evaluate("window.submits") is None      # the worker itself never pressed Submit


def test_without_takeover_a_missing_code_leaves_it_unconfirmed(page, mocker):
    page.set_content(_CODE_STEP.format(inputs="<input id=security_code>", expect="X"))
    mocker.patch.object(apply_agent.email_verification, "wait_for_verification", return_value=None)
    mocker.patch.object(config, "APPLY_TAKEOVER_ENABLED", False)
    assert apply_agent._complete_email_code(page, 7, "lease", datetime.now(timezone.utc)) is False
