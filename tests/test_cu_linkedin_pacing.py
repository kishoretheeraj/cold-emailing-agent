"""Pure pacing/rate-limit tests for cu_linkedin.py. No mocking needed -- every function here is
deterministic given an injected rand."""

import os
import re

import config
import cu_linkedin


# ── next_action_delay ──────────────────────────────────────────────────────────

def test_next_action_delay_is_within_configured_bounds():
    for r in (0.0, 0.25, 0.5, 0.99, 1.0):
        delay = cu_linkedin.next_action_delay(rand=lambda: r)
        assert config.CU_LINKEDIN_MIN_ACTION_DELAY_SECONDS <= delay
        assert delay <= config.CU_LINKEDIN_MAX_ACTION_DELAY_SECONDS


def test_next_action_delay_is_not_a_constant():
    # Distinct rand() draws through a strictly monotonic function give distinct outputs by
    # construction -- this proves next_action_delay varies with its input, not that any
    # particular sequence of real calls is non-linear (that property comes from rand() itself
    # being uniform, which this function doesn't and can't control).
    draws = [cu_linkedin.next_action_delay(rand=lambda r=r: r)
             for r in (0.01, 0.2, 0.4, 0.6, 0.8, 0.99)]
    assert len(set(draws)) == len(draws)


def test_next_action_delay_uses_the_real_random_by_default():
    draws = {cu_linkedin.next_action_delay() for _ in range(50)}
    assert len(draws) > 1


# ── keystroke_delay_ms ─────────────────────────────────────────────────────────

def test_keystroke_delay_ms_centers_on_the_configured_base():
    assert cu_linkedin.keystroke_delay_ms(rand=lambda: 0.5) == config.CU_LINKEDIN_KEYSTROKE_DELAY_MS


def test_keystroke_delay_ms_jitters_both_directions_and_stays_positive():
    low = cu_linkedin.keystroke_delay_ms(rand=lambda: 0.0)
    high = cu_linkedin.keystroke_delay_ms(rand=lambda: 1.0)
    assert low < config.CU_LINKEDIN_KEYSTROKE_DELAY_MS < high
    assert low >= 1


# ── session_exhausted ──────────────────────────────────────────────────────────

def test_session_exhausted_false_while_under_both_caps():
    assert cu_linkedin.session_exhausted(0, 0) is False
    assert cu_linkedin.session_exhausted(
        config.CU_LINKEDIN_MAX_ACTIONS_PER_SESSION - 1,
        config.CU_LINKEDIN_MAX_SESSION_SECONDS - 1,
    ) is False


def test_session_exhausted_true_on_action_cap():
    assert cu_linkedin.session_exhausted(config.CU_LINKEDIN_MAX_ACTIONS_PER_SESSION, 0) is True


def test_session_exhausted_true_on_wallclock_cap():
    assert cu_linkedin.session_exhausted(0, config.CU_LINKEDIN_MAX_SESSION_SECONDS) is True


# ── worst_case_views_per_session ────────────────────────────────────────────────

def test_worst_case_views_per_session_divides_actions_by_the_per_posting_floor():
    assert cu_linkedin.worst_case_views_per_session() == (
        config.CU_LINKEDIN_MAX_ACTIONS_PER_SESSION
        // config.CU_LINKEDIN_WORST_CASE_ACTIONS_PER_POSTING
    )


# ── daily_cap_satisfied ────────────────────────────────────────────────────────

def test_daily_cap_satisfied_rejects_a_schedule_over_the_cap():
    assert cu_linkedin.daily_cap_satisfied(10, 25) is False


def test_daily_cap_arithmetic_holds_for_shipped_config():
    # This is NOT a test of postings persisted -- CU_LINKEDIN_MAX_POSTINGS_PER_SESSION only caps
    # what the model reports in its final answer, which is unrelated to how many postings it
    # actually looked at while browsing. The quantity that maps to real exposure is actions taken
    # (each screenshot-then-action round is roughly one "look"), so the worst-case bound has to be
    # derived from CU_LINKEDIN_MAX_ACTIONS_PER_SESSION divided by the fewest actions a single
    # posting glance could plausibly take -- see the WORST_CASE_ACTIONS_PER_POSTING comment in
    # config.py. Loosening MAX_ACTIONS_PER_SESSION, SESSIONS_PER_DAY, or
    # WORST_CASE_ACTIONS_PER_POSTING without the others must fail here.
    assert cu_linkedin.daily_cap_satisfied(
        config.CU_LINKEDIN_SESSIONS_PER_DAY,
        cu_linkedin.worst_case_views_per_session(),
    ) is True


# ── requirements.txt floor ──────────────────────────────────────────────────────

def test_anthropic_floor_supports_the_computer_use_toolset():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "requirements.txt")) as f:
        requirements = f.read()
    match = re.search(r"^anthropic>=([\d.]+)$", requirements, re.MULTILINE)
    assert match, "anthropic pin not found or not in >=X.Y.Z form"
    assert tuple(int(p) for p in match.group(1).split(".")) >= (0, 125, 0)
