"""takeover.py: challenge detection and the wait-for-a-human protocol. db and the clock are mocked."""

from unittest.mock import MagicMock

import pytest

import takeover


@pytest.fixture(autouse=True)
def _db(mocker):
    mocker.patch.object(takeover.db, "request_takeover", return_value=True)
    mocker.patch.object(takeover.db, "heartbeat_application")
    mocker.patch.object(takeover.db, "clear_takeover", return_value=True)
    mocker.patch.object(takeover.db, "takeover_state", return_value="waiting")
    mocker.patch.object(takeover.time, "sleep")


@pytest.fixture
def clock(mocker):
    now = {"t": 0.0}

    def tick(seconds):
        now["t"] += seconds

    takeover.time.sleep.side_effect = tick
    mocker.patch.object(takeover.time, "monotonic", side_effect=lambda: now["t"])
    return now


def test_resumes_when_the_human_presses_done(clock):
    takeover.db.takeover_state.side_effect = ["waiting", "waiting", "continued"]
    assert takeover.await_human(7, "L", "captcha", "hCaptcha", timeout_seconds=60, poll_seconds=5) is True
    takeover.db.request_takeover.assert_called_once_with(7, "L", "captcha", "hCaptcha")
    assert takeover.db.heartbeat_application.call_count == 3
    takeover.db.clear_takeover.assert_called_once_with(7, "L")


def test_times_out_closes_the_request_and_returns_false(clock):
    assert takeover.await_human(7, "L", "captcha", "x", timeout_seconds=30, poll_seconds=10) is False
    takeover.db.clear_takeover.assert_called_once_with(7, "L")
    # heartbeats kept the lease alive the whole time
    assert takeover.db.heartbeat_application.call_count == 3


def test_timeout_survives_a_failed_close(clock):
    takeover.db.clear_takeover.side_effect = RuntimeError("db down")
    assert takeover.await_human(7, "L", "captcha", "x", timeout_seconds=10, poll_seconds=10) is False


def test_a_lost_lease_stops_the_wait(clock):
    takeover.db.takeover_state.side_effect = ["waiting", "lost"]
    with pytest.raises(takeover.TakeoverLost):
        takeover.await_human(7, "L", "captcha", "x", timeout_seconds=60, poll_seconds=5)
    takeover.db.clear_takeover.assert_not_called()


def test_a_refused_request_is_a_lost_lease(clock):
    takeover.db.request_takeover.return_value = False
    with pytest.raises(takeover.TakeoverLost):
        takeover.await_human(7, "L", "captcha", "x", timeout_seconds=60, poll_seconds=5)
    takeover.db.takeover_state.assert_not_called()


def _page(visible_selectors):
    page = MagicMock()

    def locator(selector):
        loc = MagicMock()
        hit = selector in visible_selectors
        loc.count.return_value = 1 if hit else 0
        loc.nth.return_value.is_visible.return_value = hit
        return loc

    page.locator.side_effect = locator
    return page


@pytest.mark.parametrize("selector", [
    "iframe[src*='recaptcha']", "iframe[src*='hcaptcha']", "iframe[src*='challenges.cloudflare.com']",
    ".h-captcha",
])
def test_detects_common_challenges(selector):
    assert takeover.challenge_present(_page({selector})) is True


def test_no_challenge_on_a_plain_form():
    assert takeover.challenge_present(_page(set())) is False


def test_a_hidden_challenge_frame_does_not_count():
    page = MagicMock()
    loc = MagicMock()
    loc.count.return_value = 1
    loc.nth.return_value.is_visible.return_value = False
    page.locator.return_value = loc
    assert takeover.challenge_present(page) is False


def test_detection_never_raises():
    page = MagicMock()
    page.locator.side_effect = RuntimeError("page closed")
    assert takeover.challenge_present(page) is False


# ── Real DOM (skipped where no browser is available) ──────────────────────────

_CHALLENGE_PAGES = {
    "hcaptcha": "<form><input name=email></form><iframe src='https://newassets.hcaptcha.com/captcha/v1/x'></iframe>",
    "recaptcha": "<div class='g-recaptcha' data-sitekey='abc' style='width:300px;height:80px'></div>",
    "turnstile": "<iframe src='https://challenges.cloudflare.com/cdn-cgi/challenge-platform/x' width=300 height=65></iframe>",
}
_CLEAN_PAGES = {
    "plain form": "<form><label>Email<input name=email></label><button>Submit Application</button></form>",
    "hidden recaptcha": "<div class='g-recaptcha' data-sitekey='abc' style='display:none'></div>",
    "captcha word in copy": "<p>We never use a captcha on this page.</p>",
}


@pytest.fixture(scope="module")
def browser_page():
    sync_api = pytest.importorskip("playwright.sync_api")
    import os
    executable = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=executable)
        except Exception as exc:
            pytest.skip(f"no launchable Chromium here: {exc}")
        yield browser.new_page()
        browser.close()


@pytest.mark.parametrize("name", list(_CHALLENGE_PAGES))
def test_real_dom_detects_challenge(browser_page, name):
    browser_page.set_content(_CHALLENGE_PAGES[name])
    assert takeover.challenge_present(browser_page) is True


@pytest.mark.parametrize("name", list(_CLEAN_PAGES))
def test_real_dom_ignores_clean_or_hidden(browser_page, name):
    browser_page.set_content(_CLEAN_PAGES[name])
    assert takeover.challenge_present(browser_page) is False
