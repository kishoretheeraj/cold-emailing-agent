"""Closeout checks for the fail-closed challenge detector (Codex finding 3 / ITEM D).

The six DOM fixtures from claude_solutions.md ITEM D are verified on the Python side
(``challenge_state`` / ``parse_challenge_result``) with a fake Playwright page, plus source
assertions on ``CHALLENGE_JS`` for the invariants that only the JS text can prove
(per-widget binding, no cached element references, unknown-provider sentinel).

Self-contained: no network, no browser, no Playwright import.
"""
import pytest

from apply_pipeline import CHALLENGE_JS, parse_challenge_result, challenge_state


class FakePage:
    """Stand-in for a Playwright Page or Frame.

    ``evaluate(js)`` returns the canned results in order; the last one repeats once the list
    is exhausted. A canned value that is an exception instance is raised instead of returned.
    Every call is recorded so tests can prove the detector re-evaluates on each use.
    """

    def __init__(self, *results):
        if not results:
            raise ValueError("FakePage needs at least one canned result")
        self._results = list(results)
        self.calls = 0
        self.scripts = []

    def evaluate(self, js):
        self.calls += 1
        self.scripts.append(js)
        result = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        if isinstance(result, BaseException):
            raise result
        return result


# ---------------------------------------------------------------------------
# Fixture 1: zero widget containers -> 'clear'
# ---------------------------------------------------------------------------

def test_fixture1_zero_widgets_evaluate_clear_gives_clear():
    page = FakePage("clear")
    assert challenge_state(page) == "clear"
    assert page.calls == 1
    assert page.scripts == [CHALLENGE_JS]


def test_fixture1_parse_clear_passes_through():
    assert parse_challenge_result("clear") == "clear"


# ---------------------------------------------------------------------------
# Fixture 2: one container, empty response field -> 'active'
# ---------------------------------------------------------------------------

def test_fixture2_empty_field_evaluate_active_gives_active():
    page = FakePage("active")
    assert challenge_state(page) == "active"
    assert page.calls == 1


def test_fixture2_parse_active_passes_through():
    assert parse_challenge_result("active") == "active"


# ---------------------------------------------------------------------------
# Fixture 3: one container, filled response field -> 'clear'
# ---------------------------------------------------------------------------

def test_fixture3_filled_field_gives_clear():
    page = FakePage("clear")
    assert challenge_state(page) == "clear"


def test_fixture3_js_clears_only_when_no_widget_is_unsolved():
    # A widget counts as solved only when its own field is populated; the document is
    # 'clear' when there are no widgets or none are unsolved.
    assert "if (!ok) unsolved++;" in CHALLENGE_JS
    assert "(total === 0 || unsolved === 0) ? 'clear' : 'active'" in CHALLENGE_JS


# ---------------------------------------------------------------------------
# Fixture 4: two containers, one filled, one empty -> 'active'
# (a token must never clear a different widget)
# ---------------------------------------------------------------------------

def test_fixture4_one_filled_one_empty_gives_active():
    page = FakePage("active")
    assert challenge_state(page) == "active"


def test_fixture4_js_binds_field_to_its_nearest_widget_ancestor():
    # Per-widget binding: each response field is attributed to the nearest enclosing widget,
    # and skipped when that widget is not the one being checked. Fails if the check is ever
    # simplified to a single global "any response field is filled" test.
    assert "fld.closest('.g-recaptcha, .h-captcha, .cf-turnstile')" in CHALLENGE_JS
    assert "if (nearest && nearest !== w) continue;" in CHALLENGE_JS


def test_fixture4_js_looks_up_response_fields_inside_the_widget_not_the_document():
    assert "w.querySelectorAll(p.resp)" in CHALLENGE_JS
    assert "document.querySelectorAll(p.resp)" not in CHALLENGE_JS
    assert "document.querySelector(p.resp)" not in CHALLENGE_JS


def test_fixture4_js_never_collapses_frames_into_a_parent_container():
    # The iframe's container is its widget ancestor or the iframe itself; a parentElement
    # fallback could merge two frames inside one form into a single "widget".
    assert "f.closest(p.widget) || f" in CHALLENGE_JS
    assert "f.parentElement" not in CHALLENGE_JS


# ---------------------------------------------------------------------------
# Fixture 5: unrecognized provider -> 'unknown' sentinel -> 'error'
# (unreadable-but-KNOWN provider fields stay 'active': wait-then-blocked, still fail-closed)
# ---------------------------------------------------------------------------

def test_fixture5_unknown_provider_sentinel_gives_error():
    page = FakePage("unknown")
    assert challenge_state(page) == "error"


def test_fixture5_parse_unknown_is_error():
    assert parse_challenge_result("unknown") == "error"


def test_fixture5_js_returns_unknown_sentinel_for_unrecognized_captcha_containers():
    assert "return 'unknown'" in CHALLENGE_JS
    # Name heuristic covers captcha-like vendor names across id/class/name/title/aria-label/iframe-src.
    assert "unknownName" in CHALLENGE_JS
    assert "captcha|turnstile|arkose|geetest|datadome|perimeterx|awswaf" in CHALLENGE_JS
    assert "nameOf(el)" in CHALLENGE_JS


def test_fixture5_js_unknown_check_excludes_known_providers_and_counted_widgets():
    # Known provider widgets/frames and anything already counted must not be
    # reported as unknown, otherwise every reCAPTCHA page would read 'error'.
    assert "el.closest(knownSel)" in CHALLENGE_JS
    assert "knownFrame" in CHALLENGE_JS
    assert "counted.some(" in CHALLENGE_JS
    # The invisible-badge wrapper false positive: a captcha-named ancestor that
    # merely wraps the badge must be skipped.
    assert "el.querySelector('.grecaptcha-badge')" in CHALLENGE_JS
    # The help-text false positive: a candidate needs widget-like structure
    # (an iframe or a frame/form control inside it) to count.
    assert "iframe, input, textarea, select, button" in CHALLENGE_JS


def test_fixture5_known_provider_with_unreadable_field_stays_active_not_error():
    # Scope note: a recognized widget whose response field cannot be read is an unsolved
    # widget -> 'active' (one 30s wait, then blocked). Only unrecognized-provider containers
    # map to the sentinel.
    page = FakePage("active")
    assert challenge_state(page) == "active"
    assert parse_challenge_result("active") != "error"


# ---------------------------------------------------------------------------
# Fixture 6: SPA remount -> detector reflects the current DOM on every call
# ---------------------------------------------------------------------------

def test_fixture6_spa_remount_reflects_latest_evaluate_result_each_call():
    # Widget solved, then remounted empty, then solved again, then remounted again.
    page = FakePage("clear", "active", "clear", "active")
    assert challenge_state(page) == "clear"
    assert challenge_state(page) == "active"
    assert challenge_state(page) == "clear"
    assert challenge_state(page) == "active"
    assert page.calls == 4


def test_fixture6_python_side_never_memoizes_evaluate_results():
    page = FakePage("clear")
    for _ in range(3):
        assert challenge_state(page) == "clear"
    assert page.calls == 3
    assert page.scripts == [CHALLENGE_JS] * 3


def test_fixture6_js_has_no_module_level_widget_cache():
    # The whole detector is one arrow function with no closure state outside it.
    prefix, sep, _body = CHALLENGE_JS.partition("() => {")
    assert sep, "CHALLENGE_JS must be a single arrow function"
    assert prefix.strip() == "", "no declarations may precede the arrow function"
    for forbidden in ("WeakMap", "globalThis", "window.", "__challenge", "sessionStorage", "localStorage"):
        assert forbidden not in CHALLENGE_JS, forbidden


def test_fixture6_js_requeries_the_dom_on_every_invocation():
    assert "document.querySelectorAll(p.widget)" in CHALLENGE_JS
    assert "document.querySelectorAll('iframe')" in CHALLENGE_JS
    # Widget sets are rebuilt per provider per call, not persisted.
    assert "const widgets = new Set();" in CHALLENGE_JS
    assert "const seen = new Set();" in CHALLENGE_JS


# ---------------------------------------------------------------------------
# Error handling and multi-target aggregation
# ---------------------------------------------------------------------------

def test_evaluate_raising_gives_error():
    page = FakePage(RuntimeError("Execution context was destroyed"))
    assert challenge_state(page) == "error"


def test_parse_garbage_gives_error():
    for garbage in (None, "", "CLEAR", "Active", " active", "clear ", 0, 1, True, False,
                    ["active"], {"state": "active"}, b"clear", object()):
        assert parse_challenge_result(garbage) == "error", repr(garbage)


def test_multi_target_page_clear_frame_active_gives_active():
    page = FakePage("clear")
    frame = FakePage("active")
    assert challenge_state(page, frame) == "active"
    assert page.calls == 1
    assert frame.calls == 1


def test_multi_target_any_error_wins_over_clear_and_active():
    assert challenge_state(FakePage("active"), FakePage(RuntimeError("boom"))) == "error"
    assert challenge_state(FakePage(RuntimeError("boom")), FakePage("clear")) == "error"
    assert challenge_state(FakePage("unknown"), FakePage("active")) == "error"
    assert challenge_state(FakePage("clear"), FakePage("active"), FakePage("garbage")) == "error"


def test_multi_target_all_clear_gives_clear_and_evaluates_each_target_once():
    targets = [FakePage("clear") for _ in range(3)]
    assert challenge_state(*targets) == "clear"
    assert [t.calls for t in targets] == [1, 1, 1]

