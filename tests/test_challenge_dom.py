"""
Executable DOM fixtures for CHALLENGE_JS, run in real headless Chromium through Playwright.

Each fixture is loaded with page.set_content, CHALLENGE_JS is evaluated in the page, and the
raw tri-state string ('clear' | 'active' | 'unknown') is asserted. No network is used: every
request is aborted at the context level, and the iframe srcs point at example.com hosts that
are never loaded (CHALLENGE_JS inspects only the src attribute).

The whole module is skipped when Playwright or a launchable Chromium is not available.
Python-side behaviour (parse mapping, page+frame aggregation, wait-once flow) lives in
tests/test_challenge_detector.py.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apply_pipeline import CHALLENGE_JS, parse_challenge_result  # noqa: E402

pytest.importorskip("playwright.sync_api")


# ── Browser fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    pw = None
    try:
        pw = sync_playwright().start()
        instance = pw.chromium.launch(headless=True)
    except Exception as exc:  # missing browser binary, sandbox refusal, etc.
        if pw is not None:
            pw.stop()
        pytest.skip(f"headless Chromium could not be launched: {type(exc).__name__}: {exc}")
    yield instance
    instance.close()
    pw.stop()


@pytest.fixture(scope="module")
def context(browser):
    ctx = browser.new_context()
    ctx.route("**/*", lambda route: route.abort())  # fixtures must never touch the network
    yield ctx
    ctx.close()


def detect(context, body, title="", mutate=None):
    """Load a fixture body, optionally mutate the live DOM, then evaluate CHALLENGE_JS."""
    page = context.new_page()
    try:
        page.set_content(
            f"<!doctype html><html><head><title>{title}</title></head><body>{body}</body></html>",
            wait_until="domcontentloaded",
        )
        if mutate is not None:
            mutate(page)
        return page.evaluate(CHALLENGE_JS)
    finally:
        page.close()


# ── Fixture fragments ──────────────────────────────────────────────────────────
# vis() requires >= 20x20 px inside the viewport, so every widget is sized inline.

FORM = (
    '<form><label>Email <input name="email" style="width:200px;height:30px"></label>'
    '<button type="submit">Apply</button></form>'
)

# Real invisible-reCAPTCHA badge: logo frame plus the response textarea live INSIDE the badge.
BADGE_REAL = """
<div class="grecaptcha-badge" style="width:256px;height:60px">
  <div class="grecaptcha-logo">
    <iframe src="https://www.google.com/recaptcha/api2/anchor?ar=1&k=abc&size=invisible"
            style="width:256px;height:60px"></iframe>
  </div>
  <textarea name="g-recaptcha-response" class="g-recaptcha-response"
            style="width:250px;height:40px"></textarea>
</div>"""

# Badge with no provider iframe at all: only the badge-descendant exclusion can save a wrapper.
BADGE_NO_FRAME = """
<div class="grecaptcha-badge" style="width:256px;height:60px">
  <textarea name="g-recaptcha-response" class="g-recaptcha-response"
            style="width:250px;height:40px"></textarea>
</div>"""

UNKNOWN_BOX = """
<div class="mycaptcha-box" style="width:300px;height:80px">
  <iframe src="https://captcha.example.com/widget" style="width:300px;height:80px"></iframe>
</div>"""


def recaptcha(token, extra_attrs=""):
    """A visible reCAPTCHA v2 checkbox widget whose own response field holds `token`."""
    return f"""
<div class="g-recaptcha" data-sitekey="k" style="width:304px;height:78px" {extra_attrs}>
  <iframe src="https://www.google.com/recaptcha/api2/anchor?k=k" style="width:304px;height:78px"></iframe>
  <textarea name="g-recaptcha-response" style="display:none">{token}</textarea>
</div>"""


def wrapped(inner, cls="captcha-wrapper"):
    return f'<div class="{cls}" style="width:320px;height:100px">{inner}</div>'


# ── Badge and captcha-named non-widgets (false-positive regressions) ───────────

def test_badge_alone_is_clear(context):
    assert detect(context, FORM + BADGE_REAL) == "clear"


def test_badge_inside_visible_captcha_wrapper_is_clear(context):
    # Regression: a visible captcha-named ancestor of the (ignored) badge used to read 'unknown'.
    body = FORM + (
        '<div id="captcha-wrapper" class="captcha-area" style="width:300px;height:80px">'
        + BADGE_REAL + "</div>"
    )
    assert detect(context, body) == "clear"


def test_badge_without_frame_inside_visible_captcha_wrapper_is_clear(context):
    # Same regression with no provider iframe present, so only the badge-descendant rule applies.
    body = FORM + (
        '<div id="captcha-wrapper" class="captcha-area" style="width:300px;height:80px">'
        + BADGE_NO_FRAME + "</div>"
    )
    assert detect(context, body) == "clear"


def test_captcha_help_text_is_clear(context):
    # Regression: an ordinary visible element with a captcha-ish id but no widget structure.
    body = FORM + (
        '<div id="captcha-help" style="width:300px;height:80px">'
        "<p>Having trouble with the captcha? Contact support.</p></div>"
    )
    assert detect(context, body) == "clear"


# ── Unknown providers ──────────────────────────────────────────────────────────

def test_unknown_provider_box_is_unknown_and_parses_to_error(context):
    result = detect(context, FORM + UNKNOWN_BOX)
    assert result == "unknown"
    assert parse_challenge_result(result) == "error"


def test_unknown_provider_iframe_alone_is_unknown(context):
    body = FORM + (
        '<iframe src="https://captcha.example.com/widget" title="captcha"'
        ' style="width:300px;height:80px"></iframe>'
    )
    assert detect(context, body) == "unknown"


def test_hidden_unknown_provider_is_clear(context):
    assert detect(context, FORM + f'<div style="display:none">{UNKNOWN_BOX}</div>') == "clear"


# ── Known providers ────────────────────────────────────────────────────────────

def test_known_widget_with_empty_response_is_active(context):
    assert detect(context, FORM + recaptcha("")) == "active"


def test_known_widget_with_filled_response_is_clear(context):
    assert detect(context, FORM + recaptcha("03AGdBq24token")) == "clear"


def test_two_widgets_one_filled_one_empty_is_active(context):
    # A token from one widget must never clear a different widget.
    assert detect(context, FORM + recaptcha("03AGdBq24token") + recaptcha("")) == "active"


def test_two_widgets_both_filled_is_clear(context):
    assert detect(context, FORM + recaptcha("tokenA") + recaptcha("tokenB")) == "clear"


def test_zero_widgets_is_clear(context):
    assert detect(context, FORM) == "clear"


def test_captcha_wrapper_around_solved_known_widget_is_clear(context):
    assert detect(context, FORM + wrapped(recaptcha("03AGdBq24token"))) == "clear"


def test_captcha_wrapper_around_unsolved_known_widget_is_active(context):
    assert detect(context, FORM + wrapped(recaptcha(""))) == "active"


def test_invisible_known_widget_with_badge_is_clear(context):
    # Invisible reCAPTCHA: the .g-recaptcha div is empty and the frame lives in the badge.
    body = FORM + '<div class="g-recaptcha" data-size="invisible" data-sitekey="k"></div>' + BADGE_REAL
    assert detect(context, body) == "clear"


def test_interstitial_phrase_is_active(context):
    assert detect(context, FORM, title="Just a moment...") == "active"


def test_spa_remount_is_requeried_on_every_call(context):
    # The detector must never cache element references: a remounted widget with an empty
    # response field must read 'active' even though the previous DOM read 'clear'.
    def remount(page):
        assert page.evaluate(CHALLENGE_JS) == "clear"
        page.evaluate(
            """() => {
                 const old = document.querySelector('.g-recaptcha');
                 const fresh = old.cloneNode(true);
                 fresh.querySelector('textarea').value = '';
                 old.replaceWith(fresh);
               }"""
        )

    assert detect(context, FORM + recaptcha("03AGdBq24token"), mutate=remount) == "active"


# ── Python-side mapping (no browser needed) ────────────────────────────────────

def test_parse_maps_unknown_to_error():
    assert parse_challenge_result("unknown") == "error"
    assert parse_challenge_result("clear") == "clear"
    assert parse_challenge_result("active") == "active"

