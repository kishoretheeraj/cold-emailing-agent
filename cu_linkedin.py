"""
Drives the user's real, persistently-logged-in Chrome window on the Beelink's X11 display slot 0
via Anthropic's Computer Use API, reads LinkedIn job postings, and persists them into
job_applications at stage='saved', source='linkedin'.

Discovery only. This module never applies to anything, never sends a connection request, and
never messages anyone -- linkedin.com/jobs stays a permanently-excluded apply target in
config.APPLY_AGENT_AGGREGATOR_DOMAINS.

The safety property is pacing, not evasion: randomized per-action delay, capped session length,
capped postings per session. Nothing here patches, strips, or falsifies anything about the
browser. If a CAPTCHA or login challenge appears, the loop stops and flags the human.

No LinkedIn credentials exist anywhere in this module or in config.py -- the Chrome profile's
own session cookie is the credential.

Usage: python3 cu_linkedin.py
"""

import random

import config


# ── Pacing ─────────────────────────────────────────────────────────────────────

def next_action_delay(rand=random.random):
    """Seconds to wait before the next X11 action -- uniformly random inside the configured
    band, never a constant."""
    lo = config.CU_LINKEDIN_MIN_ACTION_DELAY_SECONDS
    hi = config.CU_LINKEDIN_MAX_ACTION_DELAY_SECONDS
    return lo + (hi - lo) * rand()


def keystroke_delay_ms(rand=random.random):
    """Per-keystroke delay in milliseconds for `xdotool type --delay`, jittered around the
    configured base in both directions."""
    jitter = config.CU_LINKEDIN_KEYSTROKE_JITTER_MS * (2 * rand() - 1)
    return max(1, int(round(config.CU_LINKEDIN_KEYSTROKE_DELAY_MS + jitter)))


def session_exhausted(actions_taken, elapsed_seconds):
    """True once this session has hit either its action cap or its wall-clock cap."""
    return (actions_taken >= config.CU_LINKEDIN_MAX_ACTIONS_PER_SESSION
            or elapsed_seconds >= config.CU_LINKEDIN_MAX_SESSION_SECONDS)


def daily_cap_satisfied(sessions_per_day, per_session_cap):
    """True when the declared schedule stays under the per-account daily view ceiling."""
    return sessions_per_day * per_session_cap <= config.CU_LINKEDIN_DAILY_VIEW_CAP


def worst_case_views_per_session():
    """The most postings a single session could plausibly have glanced at, derived from the
    action cap rather than from CU_LINKEDIN_MAX_POSTINGS_PER_SESSION -- the latter only caps what
    gets reported/persisted, not what got looked at. This is the quantity daily_cap_satisfied
    must be checked against to make the daily view cap a real bound rather than a bound on a
    different, looser thing."""
    return (config.CU_LINKEDIN_MAX_ACTIONS_PER_SESSION
            // config.CU_LINKEDIN_WORST_CASE_ACTIONS_PER_POSTING)
