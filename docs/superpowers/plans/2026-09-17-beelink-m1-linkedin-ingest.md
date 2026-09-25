# Beelink M1 — Base Provisioning + LinkedIn Computer-Use Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **For the hourly auto-continue workflow:** Tasks 1-6 and 8 are ordinary TDD/doc tasks and can be
> executed unattended. **Task 7 is a manual host runbook** — every one of its steps requires a
> physical Beelink mini PC that does not exist as a reachable machine in this environment, plus a
> human logging into LinkedIn by hand over VNC. Those steps are marked
> `<!-- blocked: ... -->`; leave them unchecked and stop. **Never set `APPLY_AGENT_ARMED` in any
> file this plan touches** — M1 ships no armed unit at all, and the whole point of the M1 units
> setting `Environment=APPLY_AGENT_ARMED=` is that the absence is testable.

**Goal:** Ship `cu_linkedin.py` — a paced Anthropic Computer Use agent that reads LinkedIn job
postings from the user's own real, logged-in Chrome session and persists them into
`job_applications` at `stage='saved'`, `source='linkedin'` — plus the Beelink host artifacts
(systemd display-slot units, timer, env template, runbook) that will eventually run it.

**Architecture:** `cu_linkedin.py` owns the sampling loop itself (screenshot → Claude → `tool_use`
action → execute via `xdotool`/`scrot` against a real X11 display → `tool_result` → repeat until
`stop_reason != "tool_use"`). Four separable layers, built in this order: pure pacing functions,
the X11 subprocess action-execution layer, the Computer Use sampling loop, and posting extraction →
`db.create_job_application`. The systemd units are real repo artifacts under `deploy/beelink/`,
guarded by a static test, but are never started or verified from this environment.

**Tech Stack:** Python 3.11 / pytest / pytest-mock. `anthropic` SDK (already in
`requirements.txt`), `computer_toolset_20260801` toolset, defaulting to `claude-sonnet-5`
(`claude-opus-5` is a one-line upshift if needed — both are priced in `MODEL_PRICING`).
`xdotool` and `scrot`
are **system binaries invoked via `subprocess`**, not Python packages. **No new package is
pip-installed anywhere in this plan** — `requirements-jobs.txt`, `requirements-apply.txt` and
`requirements-dev.txt` are all untouched, and `requirements.txt` gets exactly one version-floor
bump on the `anthropic` pin already there (Task 1, Step 6) so it actually covers the Computer Use
toolset this plan depends on.
All outbound calls (Anthropic, Supabase, every `subprocess.run`) are mocked in tests — nothing here
touches a real Claude API, real Supabase project, real X display, or real linkedin.com.

**Spec:** `docs/superpowers/specs/2026-09-17-beelink-24-7-automation-design.md` — read it in full
before starting, especially "Governance amendments", "The 'real browser' property, precisely",
"LinkedIn pacing parameters", and "Milestones". This plan implements **M1 only**.

## Global Constraints

- **Never pass `--headless` or `--remote-debugging-port`** on the LinkedIn Chrome profile. That's
  what sets Chrome's automation-detectable state; a CDP *attachment* alone doesn't, but the launch
  configuration does. This means the LinkedIn profile cannot use Playwright, browser-use, or any
  CDP-based tool — input must come from real X11 events and screenshots from real X11 capture.
  `cu_linkedin.py` never launches Chrome, so the only enforcement site for this rule is
  `deploy/beelink/systemd/chrome-profile@.service` and the static test in Task 6.
- **Pacing, not evasion, is the safety property.** No stealth patching, no CDP-flag stripping, no
  patched `navigator.webdriver`, no fingerprint spoofing. If a CAPTCHA or login challenge appears,
  the loop stops and flags the human — it never attempts to solve or bypass it.
- **Rate caps:** job-posting views target **≤100/day** (`worst_case_views_per_session()`, derived
  from the action cap, not from postings persisted — see the config.py comments for
  `CU_LINKEDIN_MAX_ACTIONS_PER_SESSION`/`CU_LINKEDIN_WORST_CASE_ACTIONS_PER_POSTING`; this is a
  self-imposed ceiling, not a documented LinkedIn limit — their published 500/day figure governs
  *profile* views, an unrelated resource), randomized, **never linear timing**
  (perfectly-regular intervals are the single most commonly cited detection trigger). Session
  shape: a few short sessions per day, each capped to a modest number of postings viewed, with
  per-action jitter inside the loop (starting values: ~25ms keystroke delay, multi-step cursor
  interpolation rather than instant jumps). Easy-Apply-equivalent actions: **none are planned or
  built in this phase** — LinkedIn is discovery-only.
- **LinkedIn-discovered jobs enter the existing auto-pick pipeline from day one, deliberately.**
  `job_pick.run()` scores every `stage='saved'` row with no `pick_verdict`, with no `source`
  filter, and a `strong` verdict zero-taps `resume_agent.propose()`/`build()` — real Anthropic
  spend and a real Storage upload. This applies to `cu_linkedin.py`'s rows exactly as it already
  does to `ats_scan` and `jobright` rows; M1 adds no `source`-aware gate to `job_pick.py`. This is
  an explicit decision, not an overlooked side effect: it's what the spec's vision ("99%
  automation, one approve tap") calls for, and `job_pick`'s design already treats every source
  equally. If a future milestone wants LinkedIn rows held back from auto-pick, that's a deliberate
  change to `job_pick.py`, not a gap in this plan.
- **LinkedIn credentials are never written to any env file.** The persistent Chrome profile's
  session cookie is the credential; the user logs in once by hand over VNC. Concretely: there is
  **no `CU_LINKEDIN_EMAIL` / `CU_LINKEDIN_PASSWORD` constant, ever**, and every `CU_LINKEDIN_*`
  constant in `config.py` is a plain literal with **no `os.environ.get`** call at all.
- **`cu_linkedin.py` must never import `sentence_transformers` or `torch`**, directly or
  transitively. That's `job_pick.py`'s job and it stays a short-lived `oneshot` systemd unit
  precisely so torch is never co-resident with a long-lived browser-agent process (spec:
  "Resource budget on 16GB"). Enforced by a static AST test in Task 5, not by `sys.modules`
  inspection — the test suite already imports `job_pick`, so a `sys.modules` check would pass
  vacuously.
- **LinkedIn stays a permanently-excluded *apply* target.** This phase adds LinkedIn only as a
  discovery source feeding `job_applications`. `config.APPLY_AGENT_AGGREGATOR_DOMAINS` already
  contains `linkedin.com/jobs` and is not touched by this plan.
- **Governance posture, same as the visa gate and Form D:** absence is not-observed, never a
  negative. A posting that can't be parsed is skipped, never inserted with `None` fields.
- Python house style: no type annotations, no docstrings on `_prefixed` helpers, public functions
  get a one-line docstring, `f""` strings for pipe-separated log lines
  (`prefix | name | company | event | extra`), `# ── Section ──...` banners for file sections.
- `cu_linkedin.py` is **best-effort, never-raises past its own boundary** — same posture as
  `jobright.py`/`ats.py`. A per-posting persist failure never stops the batch.
- `logging.basicConfig` must be called before any project-module import, inside the `__main__`
  block, writing to its own `cu_linkedin.log`.
- Definition of done (this repo's own rule): tests green, CLAUDE.md updated, memory updated. Those
  obligations are discharged by Tasks 5, 6 and 8 — do not consider the branch finished before
  Task 8 lands.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `config.py` (modify) | `CU_LINKEDIN_*` constants; `MODEL_PRICING` entries for the Computer Use models | 1, 3 |
| `cu_linkedin.py` (create) | The whole agent: pacing → X11 execution → sampling loop → extraction → `run()` | 1-5 |
| `tests/test_cu_linkedin_pacing.py` | Pure pacing functions + shipped-config cap arithmetic | 1 |
| `tests/test_cu_linkedin_actions.py` | X11 action layer, all `subprocess` mocked | 2 |
| `tests/test_cu_linkedin_loop.py` | Sampling loop + batch/failure semantics + usage logging | 3 |
| `tests/test_cu_linkedin_persist.py` | Posting extraction + `job_applications` write | 4 |
| `tests/test_cu_linkedin_run.py` | `run()` never-raises, record_run, static heavy-import guard | 5 |
| `deploy/beelink/systemd/*.service`, `*.timer` | Display slots + ingest unit, as repo artifacts | 6 |
| `deploy/beelink/env/base.env.example` | Env template (no credentials, no arm flag) | 6 |
| `tests/test_beelink_units.py` | Static guards on the unit files and env template | 6 |
| `deploy/beelink/RUNBOOK.md` | Exact host-provisioning commands the user runs by hand | 7 |
| `CLAUDE.md` (modify) | Module layout, log marker, MODEL_PRICING note, new section | 5, 6 |
| `~/.claude/projects/.../memory/project-beelink-m1.md` + `MEMORY.md` | Memory entry | 8 |

---

### Task 1: Pacing primitives and `CU_LINKEDIN_*` config

Pure, dependency-free functions first — they need no mocking and they encode the safety property
the rest of the module depends on.

**Files:**
- Create: `cu_linkedin.py`
- Modify: `config.py` (append a new section at end of file)
- Test: `tests/test_cu_linkedin_pacing.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `cu_linkedin.next_action_delay(rand=random.random) -> float` (seconds)
  - `cu_linkedin.keystroke_delay_ms(rand=random.random) -> int` (milliseconds, >= 1)
  - `cu_linkedin.session_exhausted(actions_taken, elapsed_seconds) -> bool`
  - `cu_linkedin.daily_cap_satisfied(sessions_per_day, per_session_cap) -> bool`
  - `config.CU_LINKEDIN_*` constants (full list in Step 3)

- [ ] **Step 1: Write the failing test**

Create `tests/test_cu_linkedin_pacing.py`:

```python
"""Pure pacing/rate-limit tests for cu_linkedin.py. No mocking needed -- every function here is
deterministic given an injected rand."""

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_cu_linkedin_pacing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cu_linkedin'`

- [ ] **Step 3: Add the config constants**

Append to the end of `config.py`:

```python
# ── LinkedIn computer-use ingestion (Beelink M1) ───────────────────────────────

# Every constant below is a plain literal on purpose. There is NO CU_LINKEDIN_EMAIL and NO
# CU_LINKEDIN_PASSWORD, and there never will be: the persistent Chrome profile's own session
# cookie is the credential, and the user logs in once by hand over VNC. See
# docs/superpowers/specs/2026-09-17-beelink-24-7-automation-design.md, "The ARMED gate on a
# persistent host".
CU_LINKEDIN_ENABLED = True

# computer_toolset_20260801 is supported on claude-opus-5 / claude-sonnet-5 (and the Opus 4.8+
# family). Defaults to claude-sonnet-5, not claude-opus-5: this is fundamentally a screen-reading
# task (a session runs 15-40 turns at 1,000-1,800 tokens per screenshot), and at Opus pricing
# across a few sessions/day that's a real, avoidable monthly cost. Upshifting to claude-opus-5 is
# a one-line change here if sonnet-5 proves unreliable at reading LinkedIn's UI -- both are priced
# in MODEL_PRICING.
CU_LINKEDIN_MODEL = "claude-sonnet-5"
CU_LINKEDIN_MAX_TOKENS = 4096

# X11 display slot 0 -- the LinkedIn slot is exactly 1, always. Concurrency buys nothing (the
# pacing cap is per-account, not per-process) and two simultaneous LinkedIn sessions is itself a
# detection signal.
CU_LINKEDIN_DISPLAY = ":0"
CU_LINKEDIN_SUBPROCESS_TIMEOUT_SECONDS = 30

# Session shape. A screenshot costs 1,000-1,800 tokens and a session runs 15-40 turns, so these
# are cost ceilings as much as pacing ceilings.
#
# MAX_ACTIONS_PER_SESSION is tightened to 60, not the 120 an earlier draft used. Reasoning:
# CU_LINKEDIN_MAX_POSTINGS_PER_SESSION (below) only caps what the model *reports* in its final
# JSON answer -- it says nothing about how many postings it actually looked at while browsing,
# since a single screenshot of a search-results list can surface many postings' summaries at
# once, or a single detailed posting page can take several actions to read. The quantity that
# maps to real LinkedIn-side exposure is actions taken, not postings reported. See
# WORST_CASE_ACTIONS_PER_POSTING below for the derivation this cap is checked against.
CU_LINKEDIN_MAX_TURNS = 40
CU_LINKEDIN_MAX_ACTIONS_PER_SESSION = 60
CU_LINKEDIN_MAX_SESSION_SECONDS = 900
CU_LINKEDIN_MAX_POSTINGS_PER_SESSION = 25

# The fewest actions a single posting glance could plausibly take (e.g. one scroll + one read),
# used as the conservative (most-views-permissive) end of the range for sizing the daily cap.
# This is a judgment call, not a measured LinkedIn number -- there is no documented LinkedIn
# limit for job-posting views specifically (their published 500/day figure governs *profile*
# views, a different, unrelated resource, and citing it here would be comparing the wrong
# metric).
CU_LINKEDIN_WORST_CASE_ACTIONS_PER_POSTING = 2

# Declared schedule (must match job-linkedin-ingest.timer's OnCalendar= firing count).
# SESSIONS_PER_DAY * (MAX_ACTIONS_PER_SESSION // WORST_CASE_ACTIONS_PER_POSTING) must stay
# <= DAILY_VIEW_CAP -- test_daily_cap_arithmetic_holds_for_shipped_config fails if it doesn't.
# This bounds the worst-case number of postings the model could plausibly have glanced at, not
# just the number it chooses to report -- see cu_linkedin.worst_case_views_per_session().
CU_LINKEDIN_SESSIONS_PER_DAY = 3
CU_LINKEDIN_DAILY_VIEW_CAP = 100

# Per-action jitter. Never a constant: perfectly-regular intervals are the single most commonly
# cited bot-detection trigger.
CU_LINKEDIN_MIN_ACTION_DELAY_SECONDS = 1.5
CU_LINKEDIN_MAX_ACTION_DELAY_SECONDS = 6.0
CU_LINKEDIN_KEYSTROKE_DELAY_MS = 25
CU_LINKEDIN_KEYSTROKE_JITTER_MS = 15

# Prune all but the last N screenshots out of the message history each turn -- the single
# biggest cost lever in the loop.
CU_LINKEDIN_SCREENSHOT_HISTORY = 3
```

- [ ] **Step 4: Write the minimal implementation**

Create `cu_linkedin.py`:

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_cu_linkedin_pacing.py -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Bump the `anthropic` floor -- the current one predates the Computer Use toolset**

`requirements.txt` pins `anthropic>=0.40.0`. `computer_toolset_20260801` (Task 3) does not exist
in that version's request shapes -- it needs the version actually installed and verified against
in this repo's `.venv` (0.125.0). A fresh `pip install -r requirements.txt` today would resolve to
latest and happen to work, but the floor itself would keep quietly describing a version too old
for the code that depends on it, which is exactly the kind of drift that produces an
import-time-clean module that 400s on its first real API call. Add this test to
`tests/test_cu_linkedin_pacing.py`:

```python
# ── requirements.txt floor ──────────────────────────────────────────────────────

def test_anthropic_floor_supports_the_computer_use_toolset():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "requirements.txt")) as f:
        requirements = f.read()
    match = re.search(r"^anthropic>=([\d.]+)$", requirements, re.MULTILINE)
    assert match, "anthropic pin not found or not in >=X.Y.Z form"
    assert tuple(int(p) for p in match.group(1).split(".")) >= (0, 125, 0)
```

Add `import os` and `import re` to the test file's imports (Step 1) if not already present. Then
edit `requirements.txt`, changing only:

```diff
-anthropic>=0.40.0
+anthropic>=0.125.0
```

Run: `python3 -m pytest tests/test_cu_linkedin_pacing.py -v`
Expected: PASS (12 tests)

- [ ] **Step 7: Commit**

```bash
git add cu_linkedin.py config.py requirements.txt tests/test_cu_linkedin_pacing.py
git commit -m "feat(cu_linkedin): pacing primitives and CU_LINKEDIN_* config"
```

---

### Task 2: X11 action-execution layer

Executes one `computer_toolset_20260801` member action against a real X display using `xdotool`
and `scrot`. Every outbound call is `subprocess.run`, mocked in tests.

**Files:**
- Modify: `cu_linkedin.py` (append a new `# ── X11 action execution ──` section)
- Test: `tests/test_cu_linkedin_actions.py`

**Interfaces:**
- Consumes: `cu_linkedin.keystroke_delay_ms` (Task 1), `config.CU_LINKEDIN_DISPLAY`,
  `config.CU_LINKEDIN_SUBPROCESS_TIMEOUT_SECONDS`.
- Produces: `cu_linkedin.execute_action(name, params, rand=random.random) -> (content, is_error)`.
  `content` is either the string `"OK"` / a short text reply, or a list of Anthropic image content
  blocks (for `screenshot`). `is_error` is a bool. **Never raises.** Task 3 wraps the return value
  in a `tool_result` block.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cu_linkedin_actions.py`:

```python
"""Tests for cu_linkedin.py's X11 action-execution layer. Every subprocess call is mocked --
no real X display, no real xdotool, no real scrot."""

import base64
import subprocess
from unittest.mock import MagicMock

import pytest

import config
import cu_linkedin


@pytest.fixture
def run(mocker):
    proc = MagicMock(name="completed_process")
    proc.stdout = b""
    return mocker.patch.object(cu_linkedin.subprocess, "run", return_value=proc)


def _commands(run):
    return [call.args[0] for call in run.call_args_list]


# ── screenshot ─────────────────────────────────────────────────────────────────

def test_screenshot_returns_a_base64_image_content_block(mocker, run):
    mocker.patch.object(cu_linkedin, "_read_png", return_value=b"\x89PNGfake")
    content, is_error = cu_linkedin.execute_action("screenshot", {})
    assert is_error is False
    assert content == [{
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(b"\x89PNGfake").decode("ascii"),
        },
    }]
    assert _commands(run)[0][0] == "scrot"


def test_every_subprocess_call_targets_the_configured_display(mocker, run):
    mocker.patch.object(cu_linkedin, "_read_png", return_value=b"png")
    cu_linkedin.execute_action("screenshot", {})
    assert run.call_args.kwargs["env"]["DISPLAY"] == config.CU_LINKEDIN_DISPLAY
    assert run.call_args.kwargs["timeout"] == config.CU_LINKEDIN_SUBPROCESS_TIMEOUT_SECONDS


# ── clicks and movement ────────────────────────────────────────────────────────

def test_left_click_moves_then_clicks_button_one(run):
    content, is_error = cu_linkedin.execute_action("left_click", {"coordinate": [640, 480]})
    assert (content, is_error) == ("OK", False)
    assert _commands(run) == [
        ["xdotool", "mousemove", "--sync", "640", "480"],
        ["xdotool", "click", "1"],
    ]


def test_left_click_without_coordinate_clicks_in_place(run):
    cu_linkedin.execute_action("left_click", {})
    assert _commands(run) == [["xdotool", "click", "1"]]


def test_left_click_with_modifier_text_holds_then_releases_it(run):
    cu_linkedin.execute_action("left_click", {"coordinate": [1, 2], "text": "ctrl+shift"})
    assert _commands(run) == [
        ["xdotool", "mousemove", "--sync", "1", "2"],
        ["xdotool", "keydown", "ctrl+shift"],
        ["xdotool", "click", "1"],
        ["xdotool", "keyup", "ctrl+shift"],
    ]


@pytest.mark.parametrize("name,button", [
    ("right_click", "3"),
    ("middle_click", "2"),
])
def test_other_buttons_map_to_their_xdotool_button_numbers(run, name, button):
    cu_linkedin.execute_action(name, {})
    assert _commands(run) == [["xdotool", "click", button]]


@pytest.mark.parametrize("name,repeat", [("double_click", "2"), ("triple_click", "3")])
def test_multi_clicks_use_repeat(run, name, repeat):
    cu_linkedin.execute_action(name, {})
    assert _commands(run) == [["xdotool", "click", "--repeat", repeat, "1"]]


def test_mouse_move_interpolates_instead_of_jumping(run):
    # `run`'s mocked stdout is always b"", so _current_position() falls back to (0, 0) here --
    # this test only proves the step count and the final target, not the from-current-position
    # behavior (see test_mouse_move_interpolates_from_the_current_position_not_the_origin below
    # for that). The leading command is the getmouselocation query _glide_to issues before moving.
    cu_linkedin.execute_action("mouse_move", {"coordinate": [100, 200]})
    commands = _commands(run)
    assert commands[0] == ["xdotool", "getmouselocation", "--shell"]
    assert len(commands) == config.CU_LINKEDIN_MOUSE_STEPS + 1
    assert commands[-1] == ["xdotool", "mousemove", "--sync", "100", "200"]


def test_mouse_move_interpolates_from_the_current_position_not_the_origin(mocker):
    # The bug this guards against: an earlier draft interpolated from (0, 0) unconditionally,
    # which teleports the cursor to a fraction of the way from the screen's top-left corner
    # before walking to the target -- a stronger automation tell than a single jump, not a
    # weaker one. Mock a real-looking non-origin current position and assert every intermediate
    # step lies on the line from THAT point to the target, never on the line from (0, 0).
    start_x, start_y = 500, 300
    target = (520, 260)
    located = MagicMock(name="getmouselocation")
    located.stdout = f"X={start_x}\nY={start_y}\nSCREEN=0\nWINDOW=1\n".encode()
    moved = MagicMock(name="mousemove")
    moved.stdout = b""
    run = mocker.patch.object(
        cu_linkedin.subprocess, "run",
        side_effect=[located] + [moved] * config.CU_LINKEDIN_MOUSE_STEPS,
    )
    cu_linkedin.execute_action("mouse_move", {"coordinate": list(target)})
    commands = _commands(run)
    assert commands[0] == ["xdotool", "getmouselocation", "--shell"]
    move_commands = commands[1:]
    assert len(move_commands) == config.CU_LINKEDIN_MOUSE_STEPS

    steps = config.CU_LINKEDIN_MOUSE_STEPS
    expected = [
        ["xdotool", "mousemove", "--sync",
         str(start_x + int((target[0] - start_x) * step / steps)),
         str(start_y + int((target[1] - start_y) * step / steps))]
        for step in range(1, steps + 1)
    ]
    assert move_commands == expected
    assert move_commands[-1] == ["xdotool", "mousemove", "--sync", "520", "260"]  # the target
    # An origin-based glide's first step would land near (130, 65) (1/4 of the way from (0,0) to
    # the target) -- nothing here does, because the glide starts from (500, 300), not (0, 0).
    assert move_commands[0] != ["xdotool", "mousemove", "--sync", "130", "65"]


def test_left_click_drag_presses_moves_and_releases(run):
    # As with test_mouse_move_interpolates_instead_of_jumping, `run`'s mocked stdout is b"", so
    # the glide's internal getmouselocation call falls back to (0, 0) -- this test proves the
    # press/glide/release sequencing, not from-current-position behavior specifically.
    cu_linkedin.execute_action("left_click_drag",
                               {"start_coordinate": [1, 2], "coordinate": [3, 4]})
    commands = _commands(run)
    assert commands[0] == ["xdotool", "mousemove", "--sync", "1", "2"]
    assert commands[1] == ["xdotool", "mousedown", "1"]
    assert commands[2] == ["xdotool", "getmouselocation", "--shell"]
    assert commands[-2] == ["xdotool", "mousemove", "--sync", "3", "4"]
    assert commands[-1] == ["xdotool", "mouseup", "1"]


@pytest.mark.parametrize("name,verb", [
    ("left_mouse_down", "mousedown"),
    ("left_mouse_up", "mouseup"),
])
def test_explicit_mouse_button_state(run, name, verb):
    cu_linkedin.execute_action(name, {})
    assert _commands(run) == [["xdotool", verb, "1"]]


def test_cursor_position_parses_the_shell_output(mocker):
    proc = MagicMock()
    proc.stdout = b"X=511\nY=744\nSCREEN=0\nWINDOW=1234\n"
    mocker.patch.object(cu_linkedin.subprocess, "run", return_value=proc)
    content, is_error = cu_linkedin.execute_action("cursor_position", {})
    assert (content, is_error) == ("X=511, Y=744", False)


# ── keyboard ───────────────────────────────────────────────────────────────────

def test_type_uses_a_jittered_keystroke_delay(run):
    cu_linkedin.execute_action("type", {"text": "product manager"}, rand=lambda: 0.5)
    assert _commands(run) == [[
        "xdotool", "type", "--delay", str(config.CU_LINKEDIN_KEYSTROKE_DELAY_MS),
        "--clearmodifiers", "product manager",
    ]]


def test_key_passes_the_chord_and_repeat_count(run):
    cu_linkedin.execute_action("key", {"text": "ctrl+l", "repeat": 2})
    assert _commands(run) == [["xdotool", "key", "--repeat", "2", "--clearmodifiers", "ctrl+l"]]


def test_key_defaults_repeat_to_one(run):
    cu_linkedin.execute_action("key", {"text": "Return"})
    assert _commands(run) == [["xdotool", "key", "--repeat", "1", "--clearmodifiers", "Return"]]


def test_hold_key_presses_sleeps_then_releases(mocker, run):
    sleep = mocker.patch.object(cu_linkedin.time, "sleep")
    cu_linkedin.execute_action("hold_key", {"text": "shift", "duration": 2})
    assert _commands(run) == [
        ["xdotool", "keydown", "--clearmodifiers", "shift"],
        ["xdotool", "keyup", "--clearmodifiers", "shift"],
    ]
    sleep.assert_called_once_with(2)


# ── scroll and wait ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("direction,button", [
    ("up", "4"), ("down", "5"), ("left", "6"), ("right", "7"),
])
def test_scroll_maps_direction_to_a_wheel_button(run, direction, button):
    cu_linkedin.execute_action("scroll", {
        "coordinate": [10, 20], "scroll_direction": direction, "scroll_amount": 3,
    })
    assert _commands(run) == [
        ["xdotool", "mousemove", "--sync", "10", "20"],
        ["xdotool", "click", "--repeat", "3", button],
    ]


def test_wait_sleeps_but_is_clamped(mocker, run):
    sleep = mocker.patch.object(cu_linkedin.time, "sleep")
    content, is_error = cu_linkedin.execute_action("wait", {"duration": 9999})
    assert (content, is_error) == ("OK", False)
    sleep.assert_called_once_with(config.CU_LINKEDIN_MAX_WAIT_SECONDS)


# ── failure posture ────────────────────────────────────────────────────────────

def test_unknown_action_is_an_error_not_a_raise(run):
    content, is_error = cu_linkedin.execute_action("teleport", {})
    assert is_error is True
    assert "teleport" in content
    run.assert_not_called()


def test_zoom_is_rejected_because_the_toolset_config_disables_it(run):
    content, is_error = cu_linkedin.execute_action("zoom", {"region": [0, 0, 10, 10]})
    assert is_error is True
    run.assert_not_called()


def test_subprocess_failure_degrades_to_an_error_result(mocker):
    mocker.patch.object(cu_linkedin.subprocess, "run",
                        side_effect=subprocess.CalledProcessError(1, "xdotool"))
    content, is_error = cu_linkedin.execute_action("left_click", {"coordinate": [1, 1]})
    assert is_error is True
    assert "left_click" in content


def test_subprocess_timeout_degrades_to_an_error_result(mocker):
    mocker.patch.object(cu_linkedin.subprocess, "run",
                        side_effect=subprocess.TimeoutExpired("xdotool", 30))
    content, is_error = cu_linkedin.execute_action("type", {"text": "hi"})
    assert is_error is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_cu_linkedin_actions.py -v`
Expected: FAIL — `AttributeError: module 'cu_linkedin' has no attribute 'subprocess'`

- [ ] **Step 3: Write the implementation**

Add two constants to the `CU_LINKEDIN_*` block in `config.py`:

```python
# Multi-step cursor interpolation rather than instant jumps -- mirrors values used in
# comparable open-source computer-use implementations.
CU_LINKEDIN_MOUSE_STEPS = 4
CU_LINKEDIN_MAX_WAIT_SECONDS = 300
```

Extend `cu_linkedin.py` — add to the imports at the top:

```python
import base64
import logging
import os
import random
import subprocess
import tempfile
import time

import config

log = logging.getLogger(__name__)
```

and append this section:

```python
# ── X11 action execution ───────────────────────────────────────────────────────

# The display is 1280x800 (see deploy/beelink/systemd/xvfb@.service). Long edge 1280 is under
# the toolset's 2576px limit, so screenshots are sent unscaled and Claude's coordinates apply
# directly to the screen. Do NOT add a scale factor here -- there is no inverse transform to get
# wrong.
_BUTTONS = {"left_click": "1", "middle_click": "2", "right_click": "3"}
_MULTI_CLICKS = {"double_click": "2", "triple_click": "3"}
_SCROLL_BUTTONS = {"up": "4", "down": "5", "left": "6", "right": "7"}


def _x11_env():
    return dict(os.environ, DISPLAY=config.CU_LINKEDIN_DISPLAY)


def _run(command):
    proc = subprocess.run(
        command, check=True, capture_output=True,
        timeout=config.CU_LINKEDIN_SUBPROCESS_TIMEOUT_SECONDS, env=_x11_env(),
    )
    return proc.stdout or b""


def _xdotool(args):
    return _run(["xdotool"] + args)


def _read_png(path):
    with open(path, "rb") as f:
        return f.read()


def _screenshot_content():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "screen.png")
        _run(["scrot", "--overwrite", path])
        data = _read_png(path)
    return [{
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(data).decode("ascii"),
        },
    }]


def _move_to(coordinate):
    x, y = int(coordinate[0]), int(coordinate[1])
    _xdotool(["mousemove", "--sync", str(x), str(y)])


def _current_position():
    # Shared by the cursor_position tool and _glide_to -- a glide has to interpolate from where
    # the cursor actually is, not from the screen origin (0, 0). Defaults to (0, 0) only if
    # xdotool's output is unparseable, which only ever happens before the very first move.
    out = _xdotool(["getmouselocation", "--shell"]).decode("utf-8", errors="replace")
    fields = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    try:
        return int(fields.get("X", 0)), int(fields.get("Y", 0))
    except ValueError:
        return 0, 0


def _glide_to(coordinate):
    # Interpolates from the CURRENT cursor position to the target, not from (0, 0). Gliding from
    # the origin every time -- an earlier draft's bug -- teleports the cursor to a fraction of the
    # way from the top-left corner before walking to the target, which is a stronger automation
    # tell than a single jump, not a weaker one, and made left_click_drag actively wrong (it
    # dragged toward the origin instead of toward the requested end point).
    start_x, start_y = _current_position()
    end_x, end_y = int(coordinate[0]), int(coordinate[1])
    steps = max(1, config.CU_LINKEDIN_MOUSE_STEPS)
    for step in range(1, steps + 1):
        x = start_x + int((end_x - start_x) * step / steps)
        y = start_y + int((end_y - start_y) * step / steps)
        _xdotool(["mousemove", "--sync", str(x), str(y)])


def _dispatch(name, params, rand):
    if name == "screenshot":
        return _screenshot_content(), False

    if name in _BUTTONS or name in _MULTI_CLICKS:
        coordinate = params.get("coordinate")
        if coordinate:
            _move_to(coordinate)
        modifiers = params.get("text")
        if modifiers:
            _xdotool(["keydown", modifiers])
        if name in _MULTI_CLICKS:
            _xdotool(["click", "--repeat", _MULTI_CLICKS[name], "1"])
        else:
            _xdotool(["click", _BUTTONS[name]])
        if modifiers:
            _xdotool(["keyup", modifiers])
        return "OK", False

    if name == "mouse_move":
        _glide_to(params["coordinate"])
        return "OK", False

    if name == "left_click_drag":
        _move_to(params["start_coordinate"])
        _xdotool(["mousedown", "1"])
        _glide_to(params["coordinate"])
        _xdotool(["mouseup", "1"])
        return "OK", False

    if name in ("left_mouse_down", "left_mouse_up"):
        _xdotool(["mousedown" if name == "left_mouse_down" else "mouseup", "1"])
        return "OK", False

    if name == "cursor_position":
        x, y = _current_position()
        return f"X={x}, Y={y}", False

    if name == "type":
        _xdotool(["type", "--delay", str(keystroke_delay_ms(rand)),
                  "--clearmodifiers", params.get("text", "")])
        return "OK", False

    if name == "key":
        _xdotool(["key", "--repeat", str(int(params.get("repeat", 1))),
                  "--clearmodifiers", params["text"]])
        return "OK", False

    if name == "hold_key":
        duration = min(float(params.get("duration", 1)), config.CU_LINKEDIN_MAX_WAIT_SECONDS)
        _xdotool(["keydown", "--clearmodifiers", params["text"]])
        time.sleep(duration)
        _xdotool(["keyup", "--clearmodifiers", params["text"]])
        return "OK", False

    if name == "scroll":
        coordinate = params.get("coordinate")
        if coordinate:
            _move_to(coordinate)
        button = _SCROLL_BUTTONS[params.get("scroll_direction", "down")]
        _xdotool(["click", "--repeat", str(int(params.get("scroll_amount", 1))), button])
        return "OK", False

    if name == "wait":
        time.sleep(min(float(params.get("duration", 1)), config.CU_LINKEDIN_MAX_WAIT_SECONDS))
        return "OK", False

    # zoom lands here deliberately: it is disabled in the toolset config (see _TOOLS) so the
    # model should never emit it, and this is the backstop if it ever does.
    return f"Unsupported action: {name}", True


def execute_action(name, params, rand=random.random):
    """Execute one computer-toolset member action against the X11 display. Returns
    (content, is_error) for the caller to wrap in a tool_result. Never raises."""
    try:
        return _dispatch(name, params or {}, rand)
    except Exception as exc:
        log.warning(f"[CU-LINKEDIN] | {name} | action failed: {exc}")
        return f"Action {name} failed: {exc}", True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_cu_linkedin_actions.py -v`
Expected: PASS (22 tests)

- [ ] **Step 5: Commit**

```bash
git add cu_linkedin.py config.py tests/test_cu_linkedin_actions.py
git commit -m "feat(cu_linkedin): X11 action-execution layer via xdotool/scrot"
```

---

### Task 3: Computer Use sampling loop + cost logging

The application owns the loop: screenshot → Claude → `tool_use` → execute → `tool_result` →
repeat until `stop_reason != "tool_use"`. Cost logging is wired in **in this same commit**, per
the spec ("log to `api_usage_log` via the existing `usage_tracking.log_usage()` from their first
commit") — there must never be a commit where this module calls Claude without recording spend.

**Files:**
- Modify: `cu_linkedin.py` (append `# ── Computer Use sampling loop ──`)
- Modify: `config.py` (`MODEL_PRICING`)
- Test: `tests/test_cu_linkedin_loop.py`

**Interfaces:**
- Consumes: `cu_linkedin.execute_action` (Task 2), `cu_linkedin.next_action_delay` /
  `session_exhausted` (Task 1), `usage_tracking.log_usage(module, action, model, usage,
  contact_id=None, job_application_id=None)`.
- Produces:
  - `cu_linkedin._TOOLS` — the toolset definition list
  - `cu_linkedin._execute_tool_uses(blocks, rand=random.random) -> (results, actions_executed)`
  - `cu_linkedin._prune_screenshots(messages) -> messages`
  - `cu_linkedin.run_session(task_prompt, rand=random.random, now=time.monotonic) -> str`
    (the final assistant text)

- [ ] **Step 1: Write the failing test**

Create `tests/test_cu_linkedin_loop.py`:

```python
"""Tests for cu_linkedin.py's Computer Use sampling loop. The Anthropic client and the X11
action layer are both mocked -- no API call, no display."""

from unittest.mock import MagicMock

import pytest

import config
import cu_linkedin
import usage_tracking


def _block(**fields):
    block = MagicMock()
    for key, value in fields.items():
        setattr(block, key, value)
    return block


def _tool_use(block_id, name, params=None):
    return _block(type="tool_use", id=block_id, name=name, input=params or {})


def _text(value):
    return _block(type="text", text=value)


def _response(content, stop_reason, input_tokens=100, output_tokens=20):
    resp = MagicMock()
    resp.content = content
    resp.stop_reason = stop_reason
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    return resp


@pytest.fixture(autouse=True)
def no_real_calls(mocker):
    mocker.patch.object(cu_linkedin.time, "sleep")
    mocker.patch.object(usage_tracking, "log_usage")


@pytest.fixture
def claude(mocker):
    client = MagicMock(name="anthropic_client")
    mocker.patch.object(cu_linkedin, "_claude", client)
    return client


# ── Toolset definition ─────────────────────────────────────────────────────────

def test_toolset_is_the_current_type_with_zoom_disabled():
    assert cu_linkedin._TOOLS == [{
        "type": "computer_toolset_20260801",
        "configs": {"zoom": {"enabled": False}},
    }]


# ── _execute_tool_uses ─────────────────────────────────────────────────────────

def test_results_carry_the_toolset_name_and_tool_use_id(mocker):
    mocker.patch.object(cu_linkedin, "execute_action", return_value=("OK", False))
    results, executed = cu_linkedin._execute_tool_uses([_tool_use("toolu_1", "left_click")])
    assert executed == 1
    assert results == [{
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "toolset_name": "computer",
        "content": "OK",
    }]


def test_batched_actions_run_sequentially_in_order(mocker):
    execute = mocker.patch.object(cu_linkedin, "execute_action", return_value=("OK", False))
    blocks = [
        _tool_use("a", "screenshot"),
        _tool_use("b", "left_click", {"coordinate": [1, 2]}),
        _tool_use("c", "screenshot"),
    ]
    results, executed = cu_linkedin._execute_tool_uses(blocks)
    assert executed == 3
    assert [c.args[0] for c in execute.call_args_list] == ["screenshot", "left_click", "screenshot"]
    assert [r["tool_use_id"] for r in results] == ["a", "b", "c"]


def test_a_failed_action_short_circuits_the_rest_of_the_batch(mocker):
    execute = mocker.patch.object(cu_linkedin, "execute_action",
                                  side_effect=[("boom", True), ("OK", False)])
    blocks = [_tool_use("a", "left_click"), _tool_use("b", "screenshot")]
    results, executed = cu_linkedin._execute_tool_uses(blocks)
    assert executed == 1
    assert execute.call_count == 1
    assert results[0]["is_error"] is True
    assert results[1] == {
        "type": "tool_result",
        "tool_use_id": "b",
        "toolset_name": "computer",
        "is_error": True,
        "content": "Not executed: an earlier computer action in this turn failed.",
    }


def test_a_paced_delay_is_taken_before_every_action(mocker):
    mocker.patch.object(cu_linkedin, "execute_action", return_value=("OK", False))
    mocker.patch.object(cu_linkedin, "next_action_delay", return_value=2.5)
    sleep = mocker.patch.object(cu_linkedin.time, "sleep")
    cu_linkedin._execute_tool_uses([_tool_use("a", "screenshot"), _tool_use("b", "screenshot")])
    assert sleep.call_args_list == [mocker.call(2.5), mocker.call(2.5)]


# ── _prune_screenshots ─────────────────────────────────────────────────────────

def _screenshot_result(tool_use_id):
    return {
        "type": "tool_result", "tool_use_id": tool_use_id, "toolset_name": "computer",
        "content": [{"type": "image",
                     "source": {"type": "base64", "media_type": "image/png", "data": "x"}}],
    }


def test_prune_keeps_only_the_most_recent_screenshots():
    messages = [{"role": "user", "content": [_screenshot_result(f"t{i}")]} for i in range(6)]
    cu_linkedin._prune_screenshots(messages)
    kept = [m for m in messages if isinstance(m["content"][0]["content"], list)]
    assert len(kept) == config.CU_LINKEDIN_SCREENSHOT_HISTORY
    assert [m["content"][0]["tool_use_id"] for m in kept] == ["t3", "t4", "t5"]
    assert messages[0]["content"][0]["content"] == "[screenshot pruned to save tokens]"


def test_prune_never_touches_text_results():
    messages = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "toolset_name": "computer", "content": "OK"},
    ]}]
    cu_linkedin._prune_screenshots(messages)
    assert messages[0]["content"][0]["content"] == "OK"


# ── run_session ────────────────────────────────────────────────────────────────

def test_run_session_returns_final_text_when_claude_stops(claude):
    claude.messages.create.return_value = _response([_text('[{"company": "Acme"}]')], "end_turn")
    assert cu_linkedin.run_session("go") == '[{"company": "Acme"}]'
    assert claude.messages.create.call_count == 1


def test_run_session_sends_the_toolset_and_configured_model(claude):
    claude.messages.create.return_value = _response([_text("done")], "end_turn")
    cu_linkedin.run_session("go")
    kwargs = claude.messages.create.call_args.kwargs
    assert kwargs["model"] == config.CU_LINKEDIN_MODEL
    assert kwargs["tools"] == cu_linkedin._TOOLS
    assert kwargs["messages"][0] == {"role": "user", "content": "go"}


def test_run_session_feeds_tool_results_back_and_loops(mocker, claude):
    # `messages` is one list mutated in place across turns inside run_session -- a plain
    # side_effect list plus `call_args_list[i].kwargs["messages"]` would record a REFERENCE to
    # that same list on every call, so by the time the loop finishes, every recorded call would
    # alias the identical final list (this was a real bug in an earlier draft of this test: it
    # could not pass, since messages[-1] after the loop is always the LAST thing appended,
    # regardless of which call index you look at). Snapshot a shallow copy of the list at each
    # call instead, capturing what was actually sent at that moment.
    mocker.patch.object(cu_linkedin, "execute_action", return_value=("OK", False))
    responses = iter([
        _response([_tool_use("a", "screenshot")], "tool_use"),
        _response([_text("all done")], "end_turn"),
    ])
    seen_messages = []

    def _create(*args, **kwargs):
        seen_messages.append(list(kwargs["messages"]))
        return next(responses)

    claude.messages.create.side_effect = _create
    assert cu_linkedin.run_session("go") == "all done"

    assert len(seen_messages) == 2
    second_call_messages = seen_messages[1]
    assert second_call_messages[-1]["role"] == "user"
    assert second_call_messages[-1]["content"][0]["tool_use_id"] == "a"


def test_run_session_logs_usage_for_every_turn(mocker, claude):
    log_usage = mocker.patch.object(usage_tracking, "log_usage")
    claude.messages.create.return_value = _response([_text("done")], "end_turn",
                                                    input_tokens=1500, output_tokens=70)
    cu_linkedin.run_session("go")
    log_usage.assert_called_once_with(
        "cu_linkedin", "session_turn", config.CU_LINKEDIN_MODEL,
        {"input_tokens": 1500, "output_tokens": 70},
    )


def test_run_session_wraps_up_without_tools_when_the_session_cap_is_hit(mocker, claude):
    mocker.patch.object(cu_linkedin, "execute_action", return_value=("OK", False))
    mocker.patch.object(cu_linkedin, "session_exhausted", return_value=True)
    claude.messages.create.side_effect = [
        _response([_tool_use("a", "screenshot")], "tool_use"),
        _response([_text("here is what I found")], "end_turn"),
    ]
    assert cu_linkedin.run_session("go") == "here is what I found"
    wrap_up = claude.messages.create.call_args_list[1].kwargs
    assert wrap_up["tool_choice"] == {"type": "none"}


def test_run_session_stops_at_max_turns(mocker, claude):
    mocker.patch.object(cu_linkedin, "execute_action", return_value=("OK", False))
    mocker.patch.object(config, "CU_LINKEDIN_MAX_TURNS", 2)
    claude.messages.create.side_effect = (
        [_response([_tool_use("a", "screenshot")], "tool_use")] * 2
        + [_response([_text("wrapped")], "end_turn")]
    )
    assert cu_linkedin.run_session("go") == "wrapped"
    assert claude.messages.create.call_count == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_cu_linkedin_loop.py -v`
Expected: FAIL — `AttributeError: module 'cu_linkedin' has no attribute '_TOOLS'`

- [ ] **Step 3: Add the model prices**

`usage_tracking.calculate_cost` raises `KeyError` for a model with no `MODEL_PRICING` entry.
`CU_LINKEDIN_MODEL` defaults to `claude-sonnet-5`, and `claude-opus-5` needs a price too since
it's the documented one-line upshift if sonnet-5 proves unreliable. Replace the `MODEL_PRICING`
block in `config.py` with:

```python
# Verified prices, not estimates. claude-sonnet-4-6 / claude-haiku-4-5-20251001 were checked
# against platform.claude.com's pricing page 2026-08-29; claude-opus-5 ($5/$25 per MTok) and
# claude-sonnet-5 ($2/$10 per MTok) against the same source 2026-09-17 when the Computer Use
# agent landed. usage_tracking.calculate_cost raises KeyError for any model not listed here --
# add its verified price rather than guessing before using a new model.
MODEL_PRICING = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
}
```

- [ ] **Step 4: Write the implementation**

Add to `cu_linkedin.py`'s imports: `import anthropic`, `import usage_tracking`, and after the
`log = logging.getLogger(__name__)` line:

```python
_claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=4)
```

Append this section:

```python
# ── Computer Use sampling loop ─────────────────────────────────────────────────

# zoom is disabled: at 1280x800 the full screenshot is already under the toolset's pixel limit,
# so zoom would only add a second image-returning member to implement for no legibility gain.
_TOOLS = [{"type": "computer_toolset_20260801", "configs": {"zoom": {"enabled": False}}}]

_SYSTEM = """You are operating a real Chrome window on a Linux desktop, already signed in to
LinkedIn as the operator. Your only job is to READ job postings and report them.

Hard rules:
- Never click Apply, Easy Apply, Connect, Follow, Message, Save, or any button that writes
  something to LinkedIn or to another person. You are read-only.
- Never type into a message box, comment box, or post composer.
- Never attempt to solve, bypass, or work around a CAPTCHA, a security check, or a login
  challenge. If you see one, stop immediately and reply with the exact text
  CAPTCHA_OR_CHALLENGE and nothing else.
- Move deliberately. Take a screenshot, decide one thing, act, then look again.
- When you have gathered what you were asked for, or you cannot make further progress, stop
  calling tools and reply with the JSON array described in the user's instructions."""

_WRAP_UP_PROMPT = (
    "Session limit reached. Stop browsing now and reply with ONLY the JSON array of the "
    "postings you have already collected, in the format you were given. If you collected "
    "none, reply with []."
)


def _final_text(resp):
    return "".join(b.text for b in resp.content
                   if getattr(b, "type", None) == "text").strip()


def _execute_tool_uses(blocks, rand=random.random):
    results = []
    executed = 0
    failed = False
    for block in blocks:
        if failed:
            results.append({
                "type": "tool_result", "tool_use_id": block.id, "toolset_name": "computer",
                "is_error": True,
                "content": "Not executed: an earlier computer action in this turn failed.",
            })
            continue
        time.sleep(next_action_delay(rand))
        content, is_error = execute_action(block.name, dict(block.input or {}), rand=rand)
        executed += 1
        result = {"type": "tool_result", "tool_use_id": block.id,
                  "toolset_name": "computer", "content": content}
        if is_error:
            result["is_error"] = True
            failed = True
        results.append(result)
    return results, executed


def _prune_screenshots(messages):
    # Screenshots are 1,000-1,800 tokens each and a session runs dozens of turns. Only the most
    # recent few are worth resending; older ones become a short text placeholder so the
    # tool_use/tool_result pairing stays intact.
    seen = 0
    for message in reversed(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            if not isinstance(block.get("content"), list):
                continue
            seen += 1
            if seen > config.CU_LINKEDIN_SCREENSHOT_HISTORY:
                block["content"] = "[screenshot pruned to save tokens]"
    return messages


def _call(messages, tool_choice=None, action="session_turn"):
    kwargs = dict(
        model=config.CU_LINKEDIN_MODEL,
        max_tokens=config.CU_LINKEDIN_MAX_TOKENS,
        system=_SYSTEM,
        tools=_TOOLS,
        messages=messages,
    )
    if tool_choice:
        kwargs["tool_choice"] = tool_choice
    resp = _claude.messages.create(**kwargs)
    # contact_id and job_application_id are both None on purpose: one session discovers many
    # postings, so there is no single row to attribute the spend to (same reasoning as
    # extract_voice.py).
    usage_tracking.log_usage(
        "cu_linkedin", action, config.CU_LINKEDIN_MODEL,
        {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
    )
    return resp


def _wrap_up(messages):
    messages.append({"role": "user", "content": _WRAP_UP_PROMPT})
    return _final_text(_call(messages, tool_choice={"type": "none"}, action="wrap_up"))


def run_session(task_prompt, rand=random.random, now=time.monotonic):
    """Drive one paced Computer Use session and return Claude's final text reply."""
    started = now()
    actions = 0
    messages = [{"role": "user", "content": task_prompt}]

    for turn in range(config.CU_LINKEDIN_MAX_TURNS):
        resp = _call(messages)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return _final_text(resp)

        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        results, executed = _execute_tool_uses(tool_uses, rand=rand)
        actions += executed
        messages.append({"role": "user", "content": results})
        _prune_screenshots(messages)

        if session_exhausted(actions, now() - started):
            log.info(f"[CU-LINKEDIN] | session cap reached | turn={turn} | actions={actions}")
            return _wrap_up(messages)

    log.info(f"[CU-LINKEDIN] | max turns reached | actions={actions}")
    return _wrap_up(messages)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_cu_linkedin_loop.py -v`
Expected: PASS (13 tests)

- [ ] **Step 6: Commit**

```bash
git add cu_linkedin.py config.py tests/test_cu_linkedin_loop.py
git commit -m "feat(cu_linkedin): Computer Use sampling loop with api_usage_log wiring"
```

---

### Task 4: Posting extraction and the `job_applications` write

**Files:**
- Modify: `cu_linkedin.py` (append `# ── Posting extraction and persistence ──`)
- Test: `tests/test_cu_linkedin_persist.py`

**Interfaces:**
- Consumes: `db.create_job_application(company, role, job_url=None, source=None, contact_id=None,
  applied_date=None, notes=None, posting_snapshot=None)` — returns the inserted row dict, or
  `None` when `job_url` already exists on another row (dedup; **not** an error).
- Produces:
  - `cu_linkedin._canonical_job_url(url) -> str | None`
  - `cu_linkedin.extract_postings(text) -> list` of dicts with keys
    `company`, `role`, `job_url`, `location`, `description`, `source`
  - `cu_linkedin.persist_postings(postings) -> (saved, skipped, errors)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cu_linkedin_persist.py`:

```python
"""Tests for cu_linkedin.py's posting extraction and job_applications persistence.
Supabase is mocked at db.create_job_application -- nothing here talks to a real project."""

import pytest

import config
import cu_linkedin
import db


# ── _canonical_job_url ─────────────────────────────────────────────────────────

def test_canonical_job_url_strips_linkedin_tracking_params():
    # LinkedIn appends a per-impression refId/trackingId. Dedup in db.create_job_application is
    # an exact match on job_url, so without this the same posting seen twice makes two rows.
    url = "https://www.linkedin.com/jobs/view/4123456789/?refId=abc&trackingId=xyz"
    assert cu_linkedin._canonical_job_url(url) == "https://www.linkedin.com/jobs/view/4123456789"


def test_canonical_job_url_strips_fragments_and_trailing_slash():
    assert cu_linkedin._canonical_job_url("https://x.com/jobs/1/#top") == "https://x.com/jobs/1"


@pytest.mark.parametrize("value", [None, "", "   ", 42, {"url": "x"}])
def test_canonical_job_url_returns_none_for_junk(value):
    assert cu_linkedin._canonical_job_url(value) is None


# ── extract_postings ───────────────────────────────────────────────────────────

def test_extract_postings_parses_a_plain_json_array():
    text = """[
      {"company": "Acme", "role": "Product Manager",
       "job_url": "https://www.linkedin.com/jobs/view/1?refId=z",
       "location": "Remote", "description": "Own the roadmap."}
    ]"""
    assert cu_linkedin.extract_postings(text) == [{
        "company": "Acme",
        "role": "Product Manager",
        "job_url": "https://www.linkedin.com/jobs/view/1",
        "location": "Remote",
        "description": "Own the roadmap.",
        "source": "linkedin",
    }]


def test_extract_postings_strips_a_markdown_json_fence():
    text = '```json\n[{"company": "Acme", "role": "PM", "job_url": "https://x/1"}]\n```'
    assert len(cu_linkedin.extract_postings(text)) == 1


@pytest.mark.parametrize("posting", [
    {"role": "PM", "job_url": "https://x/1"},
    {"company": "Acme", "job_url": "https://x/1"},
    {"company": "Acme", "role": "PM"},
    {"company": "", "role": "PM", "job_url": "https://x/1"},
    {"company": "Acme", "role": "   ", "job_url": "https://x/1"},
    {"company": None, "role": None, "job_url": None},
])
def test_extract_postings_skips_rows_missing_a_required_field(posting):
    import json
    assert cu_linkedin.extract_postings(json.dumps([posting])) == []


@pytest.mark.parametrize("text", [
    "", "   ", "CAPTCHA_OR_CHALLENGE", "not json at all", "{}", '{"company": "Acme"}',
    "[1, 2, 3]", '["a string"]', None,
])
def test_extract_postings_never_raises_on_junk(text):
    assert cu_linkedin.extract_postings(text) == []


# ── persist_postings ───────────────────────────────────────────────────────────

def _posting(company="Acme", role="PM", url="https://x/1"):
    return {"company": company, "role": role, "job_url": url,
            "location": "Remote", "description": "d", "source": "linkedin"}


def test_persist_postings_writes_with_the_linkedin_source(mocker):
    create = mocker.patch.object(db, "create_job_application", return_value={"id": 7})
    assert cu_linkedin.persist_postings([_posting()]) == (1, 0, 0)
    create.assert_called_once_with(
        company="Acme", role="PM", job_url="https://x/1",
        source="linkedin", posting_snapshot=_posting(),
    )


def test_persist_postings_counts_a_dedup_none_as_skipped_not_an_error(mocker):
    mocker.patch.object(db, "create_job_application", return_value=None)
    assert cu_linkedin.persist_postings([_posting()]) == (0, 1, 0)


def test_persist_postings_isolates_a_single_row_failure(mocker):
    mocker.patch.object(db, "create_job_application",
                        side_effect=[RuntimeError("boom"), {"id": 2}])
    saved, skipped, errors = cu_linkedin.persist_postings(
        [_posting(url="https://x/1"), _posting(url="https://x/2")]
    )
    assert (saved, skipped, errors) == (1, 0, 1)


def test_persist_postings_truncates_to_the_per_session_cap(mocker):
    create = mocker.patch.object(db, "create_job_application", return_value={"id": 1})
    postings = [_posting(url=f"https://x/{i}")
                for i in range(config.CU_LINKEDIN_MAX_POSTINGS_PER_SESSION + 10)]
    saved, _, _ = cu_linkedin.persist_postings(postings)
    assert saved == config.CU_LINKEDIN_MAX_POSTINGS_PER_SESSION
    assert create.call_count == config.CU_LINKEDIN_MAX_POSTINGS_PER_SESSION
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_cu_linkedin_persist.py -v`
Expected: FAIL — `AttributeError: module 'cu_linkedin' has no attribute '_canonical_job_url'`

- [ ] **Step 3: Write the implementation**

Add `import json` and `import db` to `cu_linkedin.py`'s imports, then append:

```python
# ── Posting extraction and persistence ─────────────────────────────────────────

def _strip_json_fence(text):
    # Claude sometimes wraps a JSON response in a ```json fence despite being told not to --
    # same handling as research.py's _generate_queries and resume_agent.py.
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else ""
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def _canonical_job_url(url):
    # LinkedIn posting URLs carry a per-impression ?refId=/?trackingId= query string. Dedup in
    # db.create_job_application is an exact match on job_url, so two sightings of one posting
    # would otherwise create two rows.
    if not isinstance(url, str):
        return None
    cleaned = url.strip().split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return cleaned or None


def _clean_text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def extract_postings(text):
    """Parse the session's final reply into clean posting dicts. Never raises -- returns []."""
    try:
        parsed = json.loads(_strip_json_fence(text))
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []

    postings = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        company = _clean_text(entry.get("company"))
        role = _clean_text(entry.get("role"))
        job_url = _canonical_job_url(entry.get("job_url"))
        # Governance: a posting missing any of the three required fields degrades to
        # not-observed. It is never inserted with a None column.
        if not (company and role and job_url):
            continue
        postings.append({
            "company": company,
            "role": role,
            "job_url": job_url,
            "location": _clean_text(entry.get("location")) or "",
            "description": _clean_text(entry.get("description")) or "",
            "source": "linkedin",
        })
    return postings


def persist_postings(postings):
    """Write each posting into job_applications at stage='saved'. Returns
    (saved, skipped, errors). One row's failure never stops the rest."""
    saved = skipped = errors = 0
    for posting in postings[:config.CU_LINKEDIN_MAX_POSTINGS_PER_SESSION]:
        try:
            row = db.create_job_application(
                company=posting["company"],
                role=posting["role"],
                job_url=posting["job_url"],
                source="linkedin",
                posting_snapshot=posting,
            )
            if row is None:
                skipped += 1
                log.info(f"[CU-LINKEDIN] | {posting['role']} | {posting['company']} | "
                         f"skipped (already tracked)")
            else:
                saved += 1
                log.info(f"[CU-LINKEDIN] | {posting['role']} | {posting['company']} | saved")
        except Exception as exc:
            errors += 1
            log.warning(f"[CU-LINKEDIN] | {posting.get('role')} | {posting.get('company')} | "
                        f"persist error: {exc}")
    return saved, skipped, errors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_cu_linkedin_persist.py -v`
Expected: PASS (24 tests)

- [ ] **Step 5: Commit**

```bash
git add cu_linkedin.py tests/test_cu_linkedin_persist.py
git commit -m "feat(cu_linkedin): posting extraction and job_applications persistence"
```

---

### Task 5: `run()`, CLI entry point, and the module's documentation

Ties the module together and discharges the CLAUDE.md half of the definition of done for
`cu_linkedin.py` itself.

**Files:**
- Modify: `cu_linkedin.py` (append `# ── Public entry point ──` and `__main__`)
- Modify: `CLAUDE.md`
- Test: `tests/test_cu_linkedin_run.py`

**Interfaces:**
- Consumes: `run_session` (Task 3), `extract_postings` / `persist_postings` (Task 4),
  `db.record_run(status, drafted, skipped, errors, elapsed, failure_reason=None, source="agent")`,
  `db.get_pause_scope() -> str` (returns `"none"`/`"agent"`/`"all"`, fails open to `"none"` on any
  DB error — same accessor `agent.py`/`monitor.py` already use).
- Produces: `cu_linkedin.run()` — returns `None`, never raises.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cu_linkedin_run.py`:

```python
"""Tests for cu_linkedin.run() and the module's import hygiene."""

import ast
import os

import pytest

import config
import cu_linkedin
import db


@pytest.fixture(autouse=True)
def no_real_calls(mocker):
    mocker.patch.object(db, "record_run")
    mocker.patch.object(db, "create_job_application", return_value={"id": 1})
    mocker.patch.object(db, "get_pause_scope", return_value="none")


# ── Import hygiene ─────────────────────────────────────────────────────────────

def test_cu_linkedin_never_imports_sentence_transformers_or_torch():
    # Checked statically, not via sys.modules: the suite already imports job_pick, so a
    # sys.modules check would pass vacuously. torch must never be co-resident with a
    # long-lived browser-agent process -- that is job_pick.py's oneshot unit's job.
    source = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "cu_linkedin.py")
    with open(source) as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "sentence_transformers" not in imported
    assert "torch" not in imported
    assert "job_pick" not in imported


def test_cu_linkedin_holds_no_linkedin_credential_constants():
    assert not hasattr(config, "CU_LINKEDIN_EMAIL")
    assert not hasattr(config, "CU_LINKEDIN_PASSWORD")


# ── run() ──────────────────────────────────────────────────────────────────────

def test_run_persists_what_the_session_found(mocker):
    mocker.patch.object(cu_linkedin, "run_session",
                        return_value='[{"company": "Acme", "role": "PM",'
                                     ' "job_url": "https://x/1"}]')
    record_run = mocker.patch.object(db, "record_run")
    cu_linkedin.run()
    args = record_run.call_args
    assert args.args[0] == "success"
    assert args.args[1] == 1
    assert args.kwargs["source"] == "cu_linkedin"


def test_run_records_a_failure_when_a_row_errors(mocker):
    mocker.patch.object(cu_linkedin, "run_session",
                        return_value='[{"company": "Acme", "role": "PM",'
                                     ' "job_url": "https://x/1"}]')
    mocker.patch.object(db, "create_job_application", side_effect=RuntimeError("boom"))
    record_run = mocker.patch.object(db, "record_run")
    cu_linkedin.run()
    assert record_run.call_args.args[0] == "failure"


def test_run_no_ops_when_disabled(mocker):
    mocker.patch.object(config, "CU_LINKEDIN_ENABLED", False)
    session = mocker.patch.object(cu_linkedin, "run_session")
    record_run = mocker.patch.object(db, "record_run")
    cu_linkedin.run()
    session.assert_not_called()
    record_run.assert_not_called()


@pytest.mark.parametrize("scope", ["agent", "all"])
def test_run_paused_exits_without_a_session(mocker, scope):
    # LinkedIn browsing is the single highest-consequence activity in this whole system -- it
    # risks the user's real account -- so it is the one thing the global pause switch must be
    # able to stop, matching the existing convention in agent.py and monitor.py. No record_run
    # call on the paused exit either, same rule monitor.py follows (this can fire far more often
    # than a real session would, and must not flood agent_runs).
    mocker.patch.object(db, "get_pause_scope", return_value=scope)
    session = mocker.patch.object(cu_linkedin, "run_session")
    record_run = mocker.patch.object(db, "record_run")
    cu_linkedin.run()
    session.assert_not_called()
    record_run.assert_not_called()


def test_run_not_paused_proceeds(mocker):
    mocker.patch.object(db, "get_pause_scope", return_value="none")
    session = mocker.patch.object(cu_linkedin, "run_session", return_value="[]")
    cu_linkedin.run()
    session.assert_called_once()


def test_run_flags_a_captcha_without_persisting_anything(mocker):
    mocker.patch.object(cu_linkedin, "run_session", return_value="CAPTCHA_OR_CHALLENGE")
    create = mocker.patch.object(db, "create_job_application")
    cu_linkedin.run()
    create.assert_not_called()


@pytest.mark.parametrize("failure", [
    RuntimeError("anthropic down"),
    ValueError("bad response"),
    KeyError("missing"),
    TimeoutError("x11 wedged"),
])
def test_run_never_raises(mocker, failure):
    mocker.patch.object(cu_linkedin, "run_session", side_effect=failure)
    cu_linkedin.run()


def test_run_survives_a_record_run_failure(mocker):
    mocker.patch.object(cu_linkedin, "run_session", return_value="[]")
    mocker.patch.object(db, "record_run", side_effect=RuntimeError("supabase down"))
    cu_linkedin.run()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_cu_linkedin_run.py -v`
Expected: FAIL — `AttributeError: module 'cu_linkedin' has no attribute 'run'`

- [ ] **Step 3: Write the implementation**

Append to `cu_linkedin.py`:

```python
# ── Public entry point ─────────────────────────────────────────────────────────

_TASK_PROMPT = """Open the LinkedIn Jobs tab that is already loaded in this Chrome window and
review the job recommendations there. For each posting you open, note the company, the role
title, the posting URL shown in the address bar, the location, and a one-paragraph summary of
the description.

Look at no more than {max_postings} postings, then stop.

When you are done, reply with ONLY a JSON array (no prose, no markdown fence) where each element
is an object with exactly these keys: "company", "role", "job_url", "location", "description".
If you found nothing, reply with []."""

_CAPTCHA_SENTINEL = "CAPTCHA_OR_CHALLENGE"


def run():
    """Run one paced LinkedIn discovery session and persist what it found. Never raises."""
    start = time.time()
    saved = skipped = errors = 0

    if not config.CU_LINKEDIN_ENABLED:
        log.info("[CU-LINKEDIN] | disabled via config.CU_LINKEDIN_ENABLED, skipping")
        return

    # LinkedIn browsing is the highest-consequence activity in this whole system -- it risks the
    # user's real account -- so it must respect the global pause switch, same as agent.py and
    # monitor.py. No record_run call on the paused exit, matching monitor.py's own rule: this
    # check can be hit far more often than a real session runs and must not flood agent_runs.
    if db.get_pause_scope() in ("agent", "all"):
        log.info("[CU-LINKEDIN] | PAUSED | skipping (pause_scope)")
        return

    log.info("[CU-LINKEDIN] | START")
    try:
        text = run_session(
            _TASK_PROMPT.format(max_postings=config.CU_LINKEDIN_MAX_POSTINGS_PER_SESSION)
        )
        if _CAPTCHA_SENTINEL in (text or ""):
            # Never solved, never bypassed, never retried with a workaround: a human VNCs in.
            log.warning("[CU-LINKEDIN] | CAPTCHA or login challenge -- needs a human at the VNC "
                        "console for display slot 0")
        else:
            postings = extract_postings(text)
            log.info(f"[CU-LINKEDIN] | extracted={len(postings)}")
            saved, skipped, errors = persist_postings(postings)
    except Exception as exc:
        errors += 1
        log.warning(f"[CU-LINKEDIN] | unexpected error: {exc}")

    log.info(f"[CU-LINKEDIN] | DONE | saved={saved} | skipped={skipped} | errors={errors}")
    try:
        db.record_run("failure" if errors else "success", saved, skipped, errors,
                      round(time.time() - start), source="cu_linkedin")
    except Exception as exc:
        log.warning(f"[CU-LINKEDIN] | record_run failed: {exc}")


if __name__ == "__main__":
    logging.basicConfig(
        filename="cu_linkedin.log",
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M",
    )
    run()
```

- [ ] **Step 4: Run the whole suite**

Run: `python3 -m pytest`
Expected: PASS, green, with the four new `tests/test_cu_linkedin_*.py` files included.

- [ ] **Step 5: Update CLAUDE.md**

Three edits:

1. In the **Module layout** code block, add `cu_linkedin.py` on its own line immediately after
   `jobright.py`.
2. In the **Logging format** section, extend the marker list to include `[CU-LINKEDIN]`:

```
The marker is one of: `START`, `DONE`, `PAUSED`, `[OUTREACH]`, `[APPLIED]`, `[NETWORKING]`,
`[CRITIC]`, `[RESEARCH]`, `[RESEARCH-Q]`, `[RESEARCH-T]`, `[RESEARCH-F]`,
`[RESEARCH-C]`, `[RESEARCH-A]`, `[CU-LINKEDIN]`, or a level tag from a warning/error.
```

3. In **System-wide Claude API cost tracking**, replace the parenthetical that claims two models
   are the only ones any config constant resolves to:

```
`config.MODEL_PRICING` prices (currently `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`,
`claude-opus-5` and `claude-sonnet-5` -- the model strings every config constant resolves to,
including `CU_LINKEDIN_MODEL`) were verified against
platform.claude.com/docs/en/about-claude/pricing, not guessed.
```

4. Add a new section immediately after the "JobRight puller" section:

```markdown
## LinkedIn computer-use ingestion (Beelink M1)

`cu_linkedin.py` drives the user's real, persistently-logged-in Chrome window on the Beelink's
X11 display slot 0 through Anthropic's Computer Use API (`computer_toolset_20260801`, model
`config.CU_LINKEDIN_MODEL`), reads LinkedIn job postings, and persists them into
`job_applications` at `stage='saved'`, `source='linkedin'` via the same dedup-by-`job_url`
`db.create_job_application` path `job_discovery.py`/`jobright.py` use. Log marker
`[CU-LINKEDIN]`, own log file (`cu_linkedin.log`). Best-effort: never raises past `run()`.

**Discovery only.** `linkedin.com/jobs` stays a permanently-excluded *apply* target in
`config.APPLY_AGENT_AGGREGATOR_DOMAINS` -- both facts hold simultaneously.

**The safety property is pacing, not evasion.** `next_action_delay` is randomized on every
action (never linear -- regular intervals are the most commonly cited detection trigger),
`session_exhausted` caps actions and wall-clock per session, and
`daily_cap_satisfied(CU_LINKEDIN_SESSIONS_PER_DAY, cu_linkedin.worst_case_views_per_session()) <=
CU_LINKEDIN_DAILY_VIEW_CAP` is asserted by `test_daily_cap_arithmetic_holds_for_shipped_config`.
`worst_case_views_per_session()` derives from `CU_LINKEDIN_MAX_ACTIONS_PER_SESSION`, **not** from
`CU_LINKEDIN_MAX_POSTINGS_PER_SESSION` -- the latter only caps what the model reports at the end
of a session, not how many postings it actually looked at while browsing, so it cannot be the
enforced quantity. The ~100/day figure is a self-imposed ceiling, not a documented LinkedIn limit:
their published 500/day figure governs *profile* views, an unrelated resource. Loosening
`MAX_ACTIONS_PER_SESSION`, `SESSIONS_PER_DAY`, or `WORST_CASE_ACTIONS_PER_POSTING` without the
others must fail the test above, and the timer's `OnCalendar=` firing count must keep matching
`CU_LINKEDIN_SESSIONS_PER_DAY`. On a CAPTCHA or login challenge the model replies
`CAPTCHA_OR_CHALLENGE` and `run()` logs a warning -- never solved, never bypassed.

**Respects the global pause switch.** `run()` checks `db.get_pause_scope()` and exits before doing
anything on `"agent"`/`"all"`, same convention as `agent.py`/`monitor.py` -- LinkedIn browsing is
the one activity in this system that risks the user's real account, so it is the one thing that
switch must be able to stop.

**Feeds `job_pick.py`'s auto-pick pipeline like any other source, deliberately.** `job_pick.run()`
has no `source` filter, so a `strong` verdict on a `source='linkedin'` row zero-taps real
`resume_agent` spend exactly like an `ats_scan`/`jobright` row does. This is an explicit decision
made during M1's design, not an overlooked coupling.

**No LinkedIn credentials exist anywhere.** Every `CU_LINKEDIN_*` constant is a plain literal
with no `os.environ.get`; there is no `CU_LINKEDIN_EMAIL`/`CU_LINKEDIN_PASSWORD`, by design. The
persistent Chrome profile's session cookie is the credential and the user logs in once by hand
over VNC.

**Never import `sentence_transformers`/`torch` here** (asserted statically by
`test_cu_linkedin_never_imports_sentence_transformers_or_torch`) -- torch must never be
co-resident with a long-lived browser-agent process on a 16GB box; that's `job_pick.py`'s
short-lived `oneshot` unit's job.

Cost is logged to `api_usage_log` from the first commit via
`usage_tracking.log_usage("cu_linkedin", "session_turn"|"wrap_up", ...)` with both
`contact_id` and `job_application_id` `None` (one session discovers many postings, so there is
no single row to attribute spend to -- same reasoning as `extract_voice.py`).
Screenshots are 1,000-1,800 tokens each; `_prune_screenshots` keeps only the last
`CU_LINKEDIN_SCREENSHOT_HISTORY` (3).

Sampling-loop details that are easy to get wrong: `tool_result` blocks must carry
`toolset_name: "computer"`; one `tool_result` per `tool_use` block, all in a **single** user
message; a batched turn executes sequentially and stops at the first failure, with every un-run
block answered `is_error: true` / `"Not executed: an earlier computer action in this turn
failed."`. At 1280x800 screenshots are **not** scaled (long edge is under the toolset's limit),
so Claude's coordinates apply to the screen directly -- don't add a scale factor.

Spec: docs/superpowers/specs/2026-09-17-beelink-24-7-automation-design.md (M1).
```

- [ ] **Step 6: Commit**

```bash
git add cu_linkedin.py tests/test_cu_linkedin_run.py CLAUDE.md
git commit -m "feat(cu_linkedin): run() entry point, CLI, and CLAUDE.md section"
```

---

### Task 6: Beelink deploy artifacts (systemd units, env template)

These are **real, complete repo artifacts**. They are never started or verified from this
environment — no `systemctl` step appears anywhere in this task. The static test is what makes
the two safety-critical properties enforceable from here.

**Files:**
- Create: `deploy/beelink/systemd/xvfb@.service`
- Create: `deploy/beelink/systemd/chrome-profile@.service`
- Create: `deploy/beelink/systemd/x11vnc@.service`
- Create: `deploy/beelink/systemd/novnc@.service`
- Create: `deploy/beelink/systemd/job-linkedin-ingest.service`
- Create: `deploy/beelink/systemd/job-linkedin-ingest.timer`
- Create: `deploy/beelink/systemd/notify-failure@.service`
- Create: `deploy/beelink/env/base.env.example`
- Modify: `CLAUDE.md` (module layout block)
- Test: `tests/test_beelink_units.py`

**Interfaces:**
- Consumes: `config.CU_LINKEDIN_SESSIONS_PER_DAY` (the timer's firing count must match it).
- Produces: no Python symbols. The units are consumed by Task 7's runbook.

- [ ] **Step 1: Write the failing test**

Create `tests/test_beelink_units.py`:

```python
"""Static guards on the Beelink deploy artifacts. These files cannot be started or verified from
this environment, so these assertions are the only enforcement of the two rules that matter."""

import glob
import os

import pytest

import config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SYSTEMD = os.path.join(_ROOT, "deploy", "beelink", "systemd")
_ENV_EXAMPLE = os.path.join(_ROOT, "deploy", "beelink", "env", "base.env.example")


def _unit_paths():
    return sorted(glob.glob(os.path.join(_SYSTEMD, "*.service"))
                  + glob.glob(os.path.join(_SYSTEMD, "*.timer")))


def _read(path):
    with open(path) as f:
        return f.read()


def _directives(path):
    # Comment lines are stripped: the chrome unit's comment deliberately NAMES the forbidden
    # flags to explain why they must never appear, and a naive substring scan over the whole
    # file would flag that comment as a violation of itself.
    return "\n".join(line for line in _read(path).splitlines()
                     if not line.lstrip().startswith("#"))


def test_every_expected_unit_exists():
    assert sorted(os.path.basename(p) for p in _unit_paths()) == [
        "chrome-profile@.service",
        "job-linkedin-ingest.service",
        "job-linkedin-ingest.timer",
        "notify-failure@.service",
        "novnc@.service",
        "x11vnc@.service",
        "xvfb@.service",
    ]


# ── The one flag rule that actually matters ────────────────────────────────────

def test_chrome_unit_launches_headful_with_the_persistent_profile():
    unit = _directives(os.path.join(_SYSTEMD, "chrome-profile@.service"))
    assert "--user-data-dir=/var/lib/job-agent/profiles/%i" in unit
    assert "--disable-dev-shm-usage" in unit


@pytest.mark.parametrize("flag", ["--headless", "--remote-debugging-port", "--remote-debugging"])
def test_no_unit_ever_sets_an_automation_detectable_chrome_flag(flag):
    for path in _unit_paths():
        assert flag not in _directives(path), path


# ── The ARMED gate stays absent, testably ──────────────────────────────────────

def test_every_service_explicitly_blanks_apply_agent_armed():
    for path in _unit_paths():
        if path.endswith(".service"):
            assert "Environment=APPLY_AGENT_ARMED=\n" in _read(path), path


def test_nothing_in_m1_arms_the_submit_path():
    for path in _unit_paths() + [_ENV_EXAMPLE]:
        content = _read(path)
        assert "APPLY_AGENT_ARMED=1" not in content, path
        assert "armed.env" not in content, path


# ── No LinkedIn credentials, anywhere ──────────────────────────────────────────

@pytest.mark.parametrize("key", [
    "LINKEDIN_EMAIL", "LINKEDIN_PASSWORD", "CU_LINKEDIN_EMAIL", "CU_LINKEDIN_PASSWORD",
])
def test_env_template_carries_no_linkedin_credentials(key):
    assert key not in _read(_ENV_EXAMPLE)


def test_env_template_lists_every_hard_required_secret():
    content = _read(_ENV_EXAMPLE)
    for key in ("ANTHROPIC_API_KEY", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD",
                "SUPABASE_URL", "SUPABASE_ANON_KEY"):
        assert f"{key}=" in content


def test_env_template_has_no_real_values():
    for line in _read(_ENV_EXAMPLE).splitlines():
        if line.strip() and not line.startswith("#"):
            assert line.endswith("="), line


# ── Hardening and pacing ───────────────────────────────────────────────────────

@pytest.mark.parametrize("directive", [
    "NoNewPrivileges=yes", "ProtectSystem=strict", "ProtectHome=yes",
])
def test_every_service_is_hardened(directive):
    for path in _unit_paths():
        if path.endswith(".service"):
            assert directive in _read(path), path


def test_the_ingest_unit_cannot_run_forever():
    # Type=oneshot: TimeoutStartSec= is the directive that actually bounds this unit's run.
    # RuntimeMaxSec= only governs post-activation runtime and is a silent no-op for oneshot --
    # asserting it here would manufacture confidence in a directive that does nothing.
    unit = _read(os.path.join(_SYSTEMD, "job-linkedin-ingest.service"))
    assert "Type=oneshot" in unit
    assert "TimeoutStartSec=1200" in unit
    assert "RuntimeMaxSec=" not in unit


def test_timer_firing_count_matches_the_configured_sessions_per_day():
    timer = _read(os.path.join(_SYSTEMD, "job-linkedin-ingest.timer"))
    oncalendar = [line for line in timer.splitlines() if line.startswith("OnCalendar=")]
    assert len(oncalendar) == 1
    hours = oncalendar[0].split("=", 1)[1].split(":")[0].split(",")
    assert len(hours) == config.CU_LINKEDIN_SESSIONS_PER_DAY


def test_timer_randomizes_its_start():
    assert "RandomizedDelaySec=1800" in _read(
        os.path.join(_SYSTEMD, "job-linkedin-ingest.timer"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_beelink_units.py -v`
Expected: FAIL — `AssertionError` on `test_every_expected_unit_exists` (the directory doesn't
exist, so the glob is empty).

- [ ] **Step 3: Write the unit files**

`deploy/beelink/systemd/xvfb@.service`:

```ini
# Virtual X display for slot %i. 1280x800 is chosen so screenshots stay under the Computer Use
# toolset's pixel limit and need no scaling -- see cu_linkedin.py's X11 section.
[Unit]
Description=Xvfb virtual display :%i
After=network.target
OnFailure=notify-failure@%n.service

[Service]
Type=simple
User=jobagent
Group=jobagent
ExecStart=/usr/bin/Xvfb :%i -screen 0 1280x800x24 -nolisten tcp
Restart=always
RestartSec=2
RuntimeMaxSec=infinity
Environment=APPLY_AGENT_ARMED=
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/job-agent

[Install]
WantedBy=multi-user.target
```

`deploy/beelink/systemd/chrome-profile@.service`:

```ini
# The LinkedIn browser for slot %i.
#
# NEVER add --headless or --remote-debugging-port to the ExecStart below. Those flags are what
# set Chrome's automation-detectable launch state; a CDP attachment alone does not, but the
# launch configuration does. This is why the LinkedIn profile cannot use Playwright,
# browser-use, or any other CDP-based tool -- input comes from real X11 events (xdotool) and
# screenshots from real X11 capture (scrot). tests/test_beelink_units.py enforces this.
#
# --disable-dev-shm-usage is required unless /dev/shm is mounted >= 2GB; the 64MB default
# crashes Chrome tabs.
#
# JoinsNamespaceOf= is load-bearing: PrivateTmp= gives each unit its own /tmp, and the X11
# socket lives in /tmp/.X11-unix. Without it this unit cannot see the display at all.
#
# HOME is overridden even though --user-data-dir is set explicitly: Chrome still touches $HOME
# directly for crashpad, ~/.pki, and ~/.config regardless of the profile directory. With
# ProtectHome=yes masking /home and no HOME= override, $HOME resolves to an inaccessible
# /home/jobagent and Chrome fails to start. Pointing it at the already-writable profile root
# keeps ProtectHome=yes intact instead of loosening it to ProtectHome=tmpfs.
[Unit]
Description=Headful Chrome on display :%i with the persistent LinkedIn profile
After=xvfb@%i.service
Requires=xvfb@%i.service
JoinsNamespaceOf=xvfb@%i.service
OnFailure=notify-failure@%n.service

[Service]
Type=simple
User=jobagent
Group=jobagent
Environment=DISPLAY=:%i
Environment=HOME=/var/lib/job-agent
Environment=APPLY_AGENT_ARMED=
ExecStart=/usr/bin/google-chrome \
    --user-data-dir=/var/lib/job-agent/profiles/%i \
    --window-position=0,0 \
    --window-size=1280,800 \
    --disable-dev-shm-usage \
    --no-first-run \
    --no-default-browser-check \
    https://www.linkedin.com/jobs/
Restart=always
RestartSec=5
# Daily recycle for a wedged renderer. The login session survives -- it lives in the profile
# directory on disk, not in process memory.
RuntimeMaxSec=86400
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/job-agent

[Install]
WantedBy=multi-user.target
```

`deploy/beelink/systemd/x11vnc@.service`:

```ini
# VNC server for slot %i, bound to localhost only. noVNC (below) is what reaches the LAN.
[Unit]
Description=x11vnc for display :%i
After=xvfb@%i.service
Requires=xvfb@%i.service
JoinsNamespaceOf=xvfb@%i.service
OnFailure=notify-failure@%n.service

[Service]
Type=simple
User=jobagent
Group=jobagent
Environment=APPLY_AGENT_ARMED=
ExecStart=/usr/bin/x11vnc -display :%i -rfbport 590%i -localhost -rfbauth /etc/job-agent/vncpasswd -forever -shared -noxdamage
Restart=always
RestartSec=5
RuntimeMaxSec=infinity
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/job-agent

[Install]
WantedBy=multi-user.target
```

`deploy/beelink/systemd/novnc@.service`:

```ini
# Browser-reachable VNC for slot %i. Reachable on the LAN interface only -- no port forwarding,
# no public exposure. The runbook's ufw rules are what actually confine it; adding Tailscale
# later is transparent to this unit.
#
# Port scheme (slots 0-3 only, per the hard concurrency cap -- this scheme needs a two-digit
# instance number to extend past slot 9): x11vnc listens on 590%i (5900-5903), noVNC's websocket
# front end listens on 608%i (6080-6083) and proxies to the matching x11vnc port. These are the
# conventional VNC/noVNC port bases (5900, 6080) plus the slot number, not the 59%i/60%i scheme
# from an earlier draft, which produced privileged ports (590, 600) a non-root unit cannot bind.
[Unit]
Description=noVNC web front end for display :%i
After=x11vnc@%i.service
Requires=x11vnc@%i.service
OnFailure=notify-failure@%n.service

[Service]
Type=simple
User=jobagent
Group=jobagent
Environment=APPLY_AGENT_ARMED=
ExecStart=/usr/bin/websockify --web /usr/share/novnc 608%i localhost:590%i
Restart=always
RestartSec=5
RuntimeMaxSec=infinity
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes

[Install]
WantedBy=multi-user.target
```

`deploy/beelink/systemd/job-linkedin-ingest.service`:

```ini
# One paced LinkedIn discovery session on display slot 0. Slot 0 is exactly one LinkedIn
# session, always: the pacing cap is per-account not per-process, and two simultaneous LinkedIn
# sessions is itself a detection signal.
[Unit]
Description=LinkedIn job discovery via Computer Use (display slot 0)
After=chrome-profile@0.service
Requires=chrome-profile@0.service
JoinsNamespaceOf=xvfb@0.service
OnFailure=notify-failure@%n.service

[Service]
Type=oneshot
User=jobagent
Group=jobagent
WorkingDirectory=/opt/job-agent
EnvironmentFile=/etc/job-agent/base.env
Environment=DISPLAY=:0
Environment=APPLY_AGENT_ARMED=
ExecStart=/opt/job-agent/.venv/bin/python cu_linkedin.py
# Replaces GitHub Actions' timeout-minutes: a wedged Chrome must not run forever. Comfortably
# above config.CU_LINKEDIN_MAX_SESSION_SECONDS (900) so the in-process cap fires first.
#
# Type=oneshot: the process runs during activation, so the real bound is TimeoutStartSec=, not
# RuntimeMaxSec= (which governs a unit's post-activation runtime and is a no-op for oneshot --
# using it here would silently let the distro default TimeoutStartSec, commonly 90s, kill every
# session mid-browse well before the in-process 900s cap ever fires).
TimeoutStartSec=1200
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/job-agent /var/lib/job-agent
```

`deploy/beelink/systemd/job-linkedin-ingest.timer`:

```ini
[Unit]
Description=Run LinkedIn discovery a few short times a day, at unpredictable minutes

[Timer]
# 3 firings/day must match config.CU_LINKEDIN_SESSIONS_PER_DAY. The real safety arithmetic is
# 3 x worst_case_views_per_session() (30) = 90/day, under config.CU_LINKEDIN_DAILY_VIEW_CAP (100)
# -- NOT 3 x MAX_POSTINGS_PER_SESSION, which only bounds what gets reported, not what gets
# looked at (see cu_linkedin.worst_case_views_per_session()). This is a self-imposed ceiling, not
# derived from LinkedIn's published 500/day figure, which governs profile views, a different
# resource. Changing the hour list without changing SESSIONS_PER_DAY fails
# test_timer_firing_count_matches_the_configured_sessions_per_day.
OnCalendar=09,13,17:00
# Handles the "avoid predictable timing" goal and the pacing goal at once.
RandomizedDelaySec=1800
Persistent=false

[Install]
WantedBy=timers.target
```

`deploy/beelink/systemd/notify-failure@.service`:

```ini
# OnFailure= target for every unit above. %i is the failed unit's name.
#
# notify_failure.py currently reads only GITHUB_WORKFLOW/GITHUB_RUN_ID/GITHUB_REPOSITORY (its
# GHA-era env vars) and has no FAILED_UNIT handling, so this unit deliberately does not set
# Environment=FAILED_UNIT=%i -- setting it here without teaching the script to read it would
# read as wired-up when it isn't, and every failure email would silently omit which unit failed.
# Wiring FAILED_UNIT into notify_failure.py (with its own test) is real, in-scope work but
# belongs to whichever milestone first exercises this unit for real -- M1 ships no timer that
# depends on it firing correctly. Until then, `journalctl -u notify-failure@*.service` on the box
# shows the instance name directly.
[Unit]
Description=Notify on failure of %i

[Service]
Type=oneshot
User=jobagent
Group=jobagent
WorkingDirectory=/opt/job-agent
EnvironmentFile=/etc/job-agent/base.env
Environment=APPLY_AGENT_ARMED=
ExecStart=/opt/job-agent/.venv/bin/python notify_failure.py
# Type=oneshot: the process runs during activation, so the real bound is TimeoutStartSec=, not
# RuntimeMaxSec= (which only applies once a unit is considered "started" -- a no-op here since
# the service is done by then). Using RuntimeMaxSec= on a oneshot unit silently does nothing.
TimeoutStartSec=120
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/job-agent
```

`deploy/beelink/env/base.env.example`:

```bash
# Template for /etc/job-agent/base.env on the Beelink (root:root, chmod 0600).
# Copy it there and fill in the values by hand -- this example file must stay valueless and
# is the only copy that ever lives in git.
#
# Two deliberate absences, both enforced by tests/test_beelink_units.py:
#
# 1. APPLY_AGENT_ARMED is NOT here and must never be. M1 ships no armed unit at all; when the
#    submit path moves to this box in M4 it gets its own /etc/job-agent/armed.env loaded by
#    exactly one unit. Every unit in deploy/beelink/systemd/ sets Environment=APPLY_AGENT_ARMED=
#    explicitly so the absence is testable rather than merely implicit.
#
# 2. There are NO LinkedIn credentials here, and there never will be. The persistent Chrome
#    profile's own session cookie is the credential -- the operator logs in once by hand over
#    VNC (see deploy/beelink/RUNBOOK.md).
#
# config.py reads these five with hard os.environ[...] lookups at import time, so every script
# on this box needs all five set regardless of whether it uses Gmail or Claude.
ANTHROPIC_API_KEY=
GMAIL_ADDRESS=
GMAIL_APP_PASSWORD=
SUPABASE_URL=
SUPABASE_ANON_KEY=
# Optional (os.environ.get): research.py's Tavily channel. cu_linkedin.py does not use it.
TAVILY_API_KEY=
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_beelink_units.py -v`
Expected: PASS (24 tests)

- [ ] **Step 5: Update CLAUDE.md's module layout**

In the **Module layout** code block, add a line for the new deploy tree after
`supabase/migrations/`:

```
deploy/beelink/
```

- [ ] **Step 6: Commit**

```bash
git add deploy/beelink tests/test_beelink_units.py CLAUDE.md
git commit -m "feat(beelink): systemd display-slot units, ingest timer, env template"
```

---

### Task 7: Host provisioning runbook (manual — cannot be executed here)

This task ships one real file (the runbook) and then stops. **Every command in the runbook runs on
a physical Beelink mini PC that is not reachable from this environment**, so no step below
verifies anything by running it. The repo-artifact half (Step 1) is normal work; Steps 2-5 are
the capability-blocked half and stay unchecked.

**Files:**
- Create: `deploy/beelink/RUNBOOK.md`

**Interfaces:**
- Consumes: the unit files from Task 6, `cu_linkedin.py` from Tasks 1-5.
- Produces: no code. The runbook is the handoff to the human operator.

- [ ] **Step 1: Write the runbook**

Create `deploy/beelink/RUNBOOK.md`:

````markdown
# Beelink host provisioning runbook (M1)

Every command here runs **on the Beelink itself**, by hand, by the operator. Nothing in CI or in
any agent session can run or verify these — the box is not reachable from anywhere else in this
repo's tooling. Work top to bottom; each section is independently re-runnable.

Assumed: a fresh Ubuntu Server install, a `jobagent` user, and network on the LAN.

## 1. Host baseline

```bash
# Never sleep. A job box that suspends is a job box that misses its timers.
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

# Security patches apply; reboots are scheduled by hand, never mid-task.
sudo apt-get install -y unattended-upgrades
echo 'Unattended-Upgrade::Automatic-Reboot "false";' \
  | sudo tee /etc/apt/apt.conf.d/52unattended-upgrades-local

sudo timedatectl set-ntp true

# Chrome's 64MB default /dev/shm crashes tabs. Either raise it here or rely on the
# --disable-dev-shm-usage flag already in chrome-profile@.service (both is fine).
echo 'tmpfs /dev/shm tmpfs defaults,size=2G 0 0' | sudo tee -a /etc/fstab
sudo mount -o remount /dev/shm
```

Also set **BIOS → "restore on AC power loss" = on**, so a real power outage comes back up.

## 2. Packages

**Assumes Ubuntu Server 22.04 LTS**, which ships Python 3.11 in its default repos. **If the box is
actually running 24.04 LTS or newer, `python3.11` is not installable from the default repos**
(24.04 ships 3.12 as `python3`) -- add the deadsnakes PPA first:

```bash
# Only if `apt-cache policy python3.11` shows nothing -- i.e. only on 24.04+, skip on 22.04:
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update
```

```bash
sudo apt-get update
sudo apt-get install -y \
  xvfb x11vnc novnc websockify xdotool scrot mutter tint2 \
  python3.11 python3.11-venv git curl smem gnupg

# Chrome (not Chromium): the profile has to look like the browser the operator really uses.
curl -fsSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
  -o /tmp/chrome.deb
sudo apt-get install -y /tmp/chrome.deb
```

## 3. Layout, secrets, and the repo

The box pulls **signed git tags only**, never `main`. `build-continue.yml` pushes unreviewed
AI-generated commits to `main` hourly; auto-pulling that onto the box that will eventually hold
the ARMED submit credential would defeat the isolation `APPLY_AGENT_ARMED` exists to guarantee.

**`git tag -v` needs the signer's public key in `jobagent`'s own GPG keyring first**, or every
verification fails with "no public key" on the very first attempt -- this is a real prerequisite
for the signed-tags-only deploy story, not an implementation detail to skip. Do this once, on your
own machine (wherever you already run `git tag -s`), then move the *public* key to the box:

```bash
# On your own machine, if you don't already have a signing key:
gpg --quick-generate-key "Your Name <you@example.com>" ed25519 sign 2y
git config --global user.signingkey <the key ID gpg just printed>
git config --global tag.gpgSign true   # optional: sign every tag by default

# Export the PUBLIC key only -- never move a private key onto the always-on box:
gpg --armor --export <the key ID> > signing-pubkey.asc
scp signing-pubkey.asc jobagent@<beelink-host>:/tmp/
```

```bash
# Back on the Beelink, as jobagent:
gpg --import /tmp/signing-pubkey.asc
rm /tmp/signing-pubkey.asc

sudo mkdir -p /opt/job-agent /var/lib/job-agent/profiles/0 /etc/job-agent
sudo chown -R jobagent:jobagent /opt/job-agent /var/lib/job-agent

sudo -u jobagent git clone https://github.com/<owner>/cold-email-agent.git /opt/job-agent
cd /opt/job-agent
sudo -u jobagent git fetch --tags
sudo -u jobagent git -c gpg.program=gpg tag -v <tag>   # must verify before checkout
sudo -u jobagent git checkout <tag>

sudo -u jobagent python3.11 -m venv /opt/job-agent/.venv
sudo -u jobagent /opt/job-agent/.venv/bin/pip install -r requirements.txt

sudo cp deploy/beelink/env/base.env.example /etc/job-agent/base.env
sudo chown root:root /etc/job-agent/base.env
sudo chmod 0600 /etc/job-agent/base.env
sudo nano /etc/job-agent/base.env   # fill in the five values by hand

# VNC password. Not a LinkedIn credential -- this only guards the console.
sudo x11vnc -storepasswd /etc/job-agent/vncpasswd
sudo chown jobagent:jobagent /etc/job-agent/vncpasswd
sudo chmod 0600 /etc/job-agent/vncpasswd
```

`x11vnc@.service` runs as `User=jobagent`, not root, so the file must be owned by `jobagent` --
`chmod` alone leaves it root-owned and unreadable by the service, which fails to start with
`-rfbauth` set. (This is unrelated to `/etc/job-agent/base.env` and `armed.env`, which stay
`root:root` on purpose -- systemd reads `EnvironmentFile=` as PID 1, before the service's own
user is dropped into.)

**Do not copy the repo's own `.env` to this box.** `config.load_dotenv()` would source it, and
that file is not what defines this host's environment.

## 4. Install the units

```bash
sudo cp /opt/job-agent/deploy/beelink/systemd/*.service /etc/systemd/system/
sudo cp /opt/job-agent/deploy/beelink/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable --now xvfb@0 chrome-profile@0 x11vnc@0 novnc@0
systemctl status xvfb@0 chrome-profile@0 x11vnc@0 novnc@0
```

## 5. Confine VNC to the LAN

**Allow SSH before enabling the firewall.** `ufw`'s default is deny-incoming; enabling it over an
SSH session without an explicit allow rule drops that session immediately, and on a headless box
with no other access path, that's a keyboard-and-monitor recovery trip. Do the SSH rule first,
always:

```bash
sudo ufw allow OpenSSH   # do this BEFORE `ufw enable`, or you will lock yourself out

# noVNC front ends, slots 0-3 (see the port scheme comment in novnc@.service: 6080-6083)
sudo ufw allow from 192.168.0.0/16 to any port 6080:6083 proto tcp

sudo ufw enable
sudo ufw status verbose   # confirm both the SSH and noVNC rules are listed before disconnecting
```

No port forwarding, no public exposure. Adding Tailscale later is a transparent additional
interface and needs no change here.

## 6. Log in to LinkedIn once, by hand

Open `http://<beelink-lan-ip>:6000/vnc.html` from a laptop on the same LAN, enter the VNC
password, and sign in to LinkedIn inside that Chrome window — including any 2FA. The session
cookie now lives in `/var/lib/job-agent/profiles/0` and survives restarts.

This is the **only** place a LinkedIn credential is ever entered. It is never typed by the agent,
never stored in `/etc/job-agent/base.env`, and never appears in this repo.

## 7. Validate real memory before adding load

Use `smem`/`ps_mem`, **not** `ps` — `ps` over-counts Chrome's shared mappings and will tell you
a slot costs several times what it does.

```bash
sudo smem -t -k -P 'chrome|Xvfb|x11vnc|websockify'
```

Target: 3 concurrent display slots steady-state (1 LinkedIn + 2 ATS later), hard cap 4. The box
is CPU-bound (4 E-cores, no hyperthreading) well before RAM runs out, so do not size slots from
RAM headroom alone.

## 8. First real run — manually, before the timer goes live

Same "run it manually before scheduling it" pattern that found 4 real bugs on Stage-1 visa
intel's first live run and 1 on Form D's, both despite green suites.

```bash
sudo systemctl start job-linkedin-ingest.service
journalctl -u job-linkedin-ingest.service -f
sudo -u jobagent tail -f /opt/job-agent/cu_linkedin.log
```

Watch the session live in the noVNC window while it runs. Confirm, before enabling the timer:

- the cursor moves in visible steps and pauses irregularly between actions (not a metronome);
- the agent never clicks Apply, Connect, Follow, Message, or Save;
- `job_applications` gained rows with `source='linkedin'` and clean `job_url`s with no
  `?refId=` tail;
- `api_usage_log` gained `module='cu_linkedin'` rows with real token counts;
- `agent_runs` gained one row with `source='cu_linkedin'`.

Then, and only then:

```bash
sudo systemctl enable --now job-linkedin-ingest.timer
systemctl list-timers job-linkedin-ingest.timer
```

## 9. If a CAPTCHA or login challenge appears

`cu_linkedin.log` will carry
`[CU-LINKEDIN] | CAPTCHA or login challenge -- needs a human at the VNC console for display
slot 0`. VNC in and solve it yourself, in the browser. Nothing in this system may ever solve,
bypass, or work around it — not in code, not by retrying, not by switching tools.
````

- [ ] **Step 2: Provision the host** <!-- blocked: this environment does not have the capability to perform this step. It requires shell access to a physical Beelink mini PC that does not exist as a reachable machine here — the operator has not set it up yet. Sections 1-5 of deploy/beelink/RUNBOOK.md (apt installs, systemd masking, BIOS setting, unit installation, ufw rules) all run on that box by hand. Leave unchecked and stop. -->

- [ ] **Step 3: Log in to LinkedIn once over VNC** <!-- blocked: this environment does not have the capability to perform this step. It requires a human entering the repo owner's real LinkedIn credentials and 2FA code into a browser window on the physical box. No agent may supply, guess, or automate that. Section 6 of the runbook. Leave unchecked and stop. -->

- [ ] **Step 4: Validate real memory with smem before adding load** <!-- blocked: this environment does not have the capability to perform this step. It measures live processes (Chrome, Xvfb, x11vnc, websockify) on the physical box. Section 7 of the runbook. Leave unchecked and stop. -->

- [ ] **Step 5: First live run, then enable the timer** <!-- blocked: this environment does not have the capability to perform this step. It drives the repo owner's real LinkedIn account through real Anthropic Computer Use spend and writes real rows to production Supabase, and it must be watched live in the noVNC window by a human. An unattended runner must never execute it. Sections 8-9 of the runbook. Leave unchecked and stop. -->

- [ ] **Step 6: Commit the runbook**

```bash
git add deploy/beelink/RUNBOOK.md
git commit -m "docs(beelink): host provisioning runbook for M1"
```

---

### Task 8: Memory entry and full-suite verification

Discharges the last third of this repo's definition of done.

**Files:**
- Create: `~/.claude/projects/-Users-kishoretheeraj-Documents-cold-email-agent/memory/project-beelink-m1.md`
- Modify: `~/.claude/projects/-Users-kishoretheeraj-Documents-cold-email-agent/memory/MEMORY.md`

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: nothing consumed by code.

- [ ] **Step 1: Run the full suite**

Run: `python3 -m pytest`
Expected: PASS — green, including the five new test files
(`test_cu_linkedin_pacing.py`, `test_cu_linkedin_actions.py`, `test_cu_linkedin_loop.py`,
`test_cu_linkedin_persist.py`, `test_cu_linkedin_run.py`, `test_beelink_units.py`).
If anything fails, fix it before writing the memory entry — a memory entry describing work that
doesn't pass is worse than none.

- [ ] **Step 2: Write the memory file**

Create `project-beelink-m1.md` in the memory directory:

```markdown
# Project: Beelink M1 — LinkedIn Computer-Use Ingest

Shipped 2026-09-17. First milestone of
docs/superpowers/specs/2026-09-17-beelink-24-7-automation-design.md.

## What shipped

- `cu_linkedin.py` — Anthropic Computer Use agent (`computer_toolset_20260801`, defaults to
  `claude-sonnet-5`, `claude-opus-5` a one-line upshift)
  driving the user's real logged-in Chrome on X11 display slot 0 via `xdotool`/`scrot`, writing
  postings into `job_applications` at `source='linkedin'`. Log marker `[CU-LINKEDIN]`, own
  `cu_linkedin.log`, `usage_tracking.log_usage` wired from the first commit.
- `deploy/beelink/` — systemd display-slot units (`xvfb@`, `chrome-profile@`, `x11vnc@`,
  `novnc@`), `job-linkedin-ingest.service`/`.timer`, `notify-failure@.service`,
  `base.env.example`, and `RUNBOOK.md`.
- `config.CU_LINKEDIN_*` (all plain literals, no env lookups) and `MODEL_PRICING` entries for
  `claude-opus-5` / `claude-sonnet-5`.

## Non-obvious things worth remembering

- **The daily ≤100-views cap is enforced by arithmetic over actions, not over postings
  persisted, and not by a live counter.** An early draft of this bounded
  `SESSIONS_PER_DAY * MAX_POSTINGS_PER_SESSION` — wrong, because
  `MAX_POSTINGS_PER_SESSION` only caps what the model *reports* at the end of a session, which
  is unrelated to how many postings it actually glanced at while browsing (a single screenshot of
  a results list can surface many at once). The real bound is
  `cu_linkedin.worst_case_views_per_session()` =
  `MAX_ACTIONS_PER_SESSION // WORST_CASE_ACTIONS_PER_POSTING` (2, a deliberately conservative,
  most-views-permissive floor), and `test_daily_cap_arithmetic_holds_for_shipped_config` checks
  `SESSIONS_PER_DAY * worst_case_views_per_session() <= DAILY_VIEW_CAP`, not the postings-based
  arithmetic. `test_timer_firing_count_matches_the_configured_sessions_per_day` still ties the
  timer's `OnCalendar=` hour list to `SESSIONS_PER_DAY`. Also: the cap is **not** calibrated
  against LinkedIn's published 500/day figure — that number governs *profile* views, a different,
  unrelated resource, and an earlier draft cited it anyway. There is no documented LinkedIn limit
  for job-posting views specifically; 100/day is a self-imposed judgment call. Deliberately
  rejected: accounting views through `agent_runs.drafted` — `record_run` fires only at the end,
  so a crashed session would under-count, which is the dangerous direction for an
  account-restriction property; a tightened worst-case action bound sidesteps needing a live
  counter at all for M1.
- **`JoinsNamespaceOf=xvfb@%i.service`** on the chrome/x11vnc/ingest units is load-bearing:
  `PrivateTmp=` gives each unit its own `/tmp`, and the X11 socket lives in `/tmp/.X11-unix`.
  Without it nothing can see the display.
- **Computer Use API details that are easy to get wrong:** `tool_result` blocks carry
  `toolset_name: "computer"`; one result per `tool_use` block, all in a single user message; a
  batched turn runs sequentially, stops at the first failure, and answers every un-run block
  `is_error: true` / "Not executed: an earlier computer action in this turn failed."; at
  1280x800 screenshots are **not** scaled, so no inverse coordinate transform exists to get
  wrong. The tool definition rejects `name`, `display_width_px`, `display_height_px`,
  `display_number` — only `configs`, `cache_control`, `allowed_callers` are accepted.
- `_canonical_job_url` strips `?refId=`/`?trackingId=` — dedup is exact-match on `job_url`, so
  without it one posting seen twice makes two rows. Green tests would not have caught that; the
  first live run would.
- `cu_linkedin.run()` checks `db.get_pause_scope()` and exits on `"agent"`/`"all"` before doing
  anything, same convention as `agent.py`/`monitor.py` — no `record_run` call on that exit, same
  rule `monitor.py` follows. This is deliberate: LinkedIn browsing risks the user's real account,
  making it the one thing in this system the global pause switch must be able to stop.
- **LinkedIn-discovered rows flow into `job_pick.py`'s auto-pick pipeline from day one, on
  purpose.** `job_pick.run()` has no `source` filter, so a `strong` verdict on a
  `source='linkedin'` row zero-taps real `resume_agent` spend exactly like an `ats_scan`/`jobright`
  row would. This was evaluated deliberately during M1's review, not left as an accidental side
  effect — it's what the platform's "99% automation, one approve tap" vision calls for. If that
  ever needs to change, it's a deliberate edit to `job_pick.py`, not evidence this plan missed
  something.
- `_glide_to` (mouse movement) interpolates from the **current** cursor position
  (`_current_position()`, via `xdotool getmouselocation`), not from the screen origin. An earlier
  draft glided from `(0, 0)` every time, which is a *stronger* automation tell than a single jump,
  not a weaker one, and made `left_click_drag` actively wrong (it dragged toward the origin
  corner instead of toward the requested end point).
- M1 ships **no armed unit at all**; every unit sets `Environment=APPLY_AGENT_ARMED=` so the
  absence is testable. `armed.env` arrives in M4, not before.

## Not done (later milestones)

M2 UI gaps (U1-U15), M3 `ats_agent.py`, M4 moving preview/submit + the RLS/`approved_at`
approval path to systemd, M5 moving `jobright_pull.yml`. Host provisioning itself (Task 7 steps
2-5) is unchecked and blocked on the physical box existing.
```

- [ ] **Step 3: Add the MEMORY.md index row**

Append to the end of the list in `MEMORY.md`:

```markdown
- [Project: Beelink M1](project-beelink-m1.md) — shipped 2026-09-17; cu_linkedin.py Computer Use agent + deploy/beelink systemd artifacts; daily view cap enforced by config arithmetic, not a counter; host provisioning still blocked on the physical box
```

The memory files (`project-beelink-m1.md`, `MEMORY.md`) live outside this repo, so Steps 2-3 need
no git commit of their own — `CLAUDE.md` was already committed in Task 5, Step 5. There is nothing
left to commit here unless Step 1's full-suite run surfaced a `CLAUDE.md` correction; if it did,
commit that fix now (`git add CLAUDE.md && git commit -m "docs: correct CLAUDE.md after M1
verification"`) rather than as an empty marker commit.

---

## Self-Review

**Spec coverage (M1 scope only).** Every M1 line item maps to a task:

| Spec requirement | Task |
|---|---|
| `cu_linkedin.py` writing into `job_applications` | 1-5 |
| Computer Use API driven directly; app owns the loop | 3 |
| `xdotool`/`scrot` against a real X11 display | 2 |
| Pacing: ≤100/day, randomized, never linear, session caps, ~25ms keystroke | 1, 2, 6 |
| CAPTCHA → stop and flag a human, never bypass | 3 (system prompt), 5 (`run()`), 7 (runbook §9) |
| Never `--headless` / `--remote-debugging-port` | 6 (unit + static test) |
| No LinkedIn credentials in any env file | 1 (no env lookup), 6 (template + test), 7 (§6) |
| systemd display slots (`xvfb@`/`chrome-profile@`/`x11vnc@`/`novnc@`) | 6 |
| `job-linkedin-ingest.timer`/`.service`, `RandomizedDelaySec=1800` | 6 |
| Unit hardening: `RuntimeMaxSec=`, `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome`, `ReadWritePaths` | 6 |
| `Environment=APPLY_AGENT_ARMED=` on every unit so absence is testable | 6 |
| `usage_tracking.log_usage` from the first commit | 3 |
| History pruning to the last 3 screenshots | 3 |
| Host baseline (no sleep, unattended-upgrades, timesyncd, /dev/shm, LAN-only VNC, bare metal) | 7 |
| Validate real memory with `smem`/`ps_mem` before adding load | 7 §7 |
| Live-verify before scheduling | 7 §8 |
| Signed-tag deploys, never auto-pull `main` | 7 §3 |
| `job_pick.py`'s torch never co-resident | 5 (static test), Global Constraints |

**Deliberately out of scope** (M2-M5, per the plan's brief): every UI gap U1-U15;
`ats_agent.py` and the removal of `browser-use`; moving `apply_agent.py`
preview/submit and `job_pick.py` to systemd; the `approved_at` column, RLS, and the
`approve_application` RPC; the `job-approval-dispatcher` and `job-apply-submit@` units and the
`armed.env` file; moving `jobright_pull.yml`. None of these are touched by any task above.

**Placeholder scan.** No "TBD", no "similar to Task N", no "add error handling" — every code step
carries the literal file content. The only intentionally-unexecuted steps are Task 7's Steps 2-5,
each carrying an explicit `<!-- blocked: ... -->` note in the same style as
`docs/superpowers/plans/2026-08-30-phase2.5-auto-apply.md`.

**Type/name consistency (checked across tasks).** `execute_action(name, params, rand=)` returns
`(content, is_error)` in Task 2 and is consumed with exactly that shape in Task 3.
`next_action_delay` / `keystroke_delay_ms` / `session_exhausted` / `daily_cap_satisfied` are
defined in Task 1 and used with unchanged signatures in Tasks 2, 3 and 6's timer test.
`run_session(task_prompt, rand=, now=)` returns a string in Task 3 and is consumed as a string by
`extract_postings` in Task 4 and `run()` in Task 5. `persist_postings` returns
`(saved, skipped, errors)` in Task 4 and is unpacked that way in Task 5.
`db.create_job_application` is called with the exact keyword names from `db.py:542`, and its
`None` return is treated as dedup-skip, not an error, matching `jobright.py`.
`db.record_run(status, drafted, skipped, errors, elapsed, ..., source=)` matches `db.py:188`.
`usage_tracking.log_usage(module, action, model, usage, ...)` matches `usage_tracking.py`.
Every config constant referenced in a test exists in Task 1's or Task 2's config block:
`CU_LINKEDIN_ENABLED`, `_MODEL`, `_MAX_TOKENS`, `_DISPLAY`, `_SUBPROCESS_TIMEOUT_SECONDS`,
`_MAX_TURNS`, `_MAX_ACTIONS_PER_SESSION`, `_MAX_SESSION_SECONDS`, `_MAX_POSTINGS_PER_SESSION`,
`_SESSIONS_PER_DAY`, `_DAILY_VIEW_CAP`, `_MIN_ACTION_DELAY_SECONDS`, `_MAX_ACTION_DELAY_SECONDS`,
`_KEYSTROKE_DELAY_MS`, `_KEYSTROKE_JITTER_MS`, `_SCREENSHOT_HISTORY`, `_MOUSE_STEPS`,
`_MAX_WAIT_SECONDS`.
