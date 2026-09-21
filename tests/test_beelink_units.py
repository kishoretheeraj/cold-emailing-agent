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
