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
        "apply-prepare.service",
        "apply-prepare.timer",
        "apply-submit.service",
        "apply-submit.timer",
        "chrome-profile@.service",
        "job-linkedin-ingest.service",
        "job-linkedin-ingest.timer",
        "job-pick.service",
        "job-pick.timer",
        "job-sourcing.service",
        "job-sourcing.timer",
        "notify-failure@.service",
        "novnc@.service",
        "resume-worker.service",
        "resume-worker.timer",
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

_ARMED_UNIT = "apply-submit.service"


def test_every_service_explicitly_blanks_apply_agent_armed():
    for path in _unit_paths():
        if path.endswith(".service") and os.path.basename(path) != _ARMED_UNIT:
            assert "Environment=APPLY_AGENT_ARMED=\n" in _read(path), path


def test_apply_submit_is_the_only_armed_unit():
    unit = _directives(os.path.join(_SYSTEMD, _ARMED_UNIT))
    armed = [line for line in unit.splitlines() if "APPLY_AGENT_ARMED" in line]
    assert armed == ["Environment=APPLY_AGENT_ARMED=1"]
    assert "ExecStart=/opt/job-agent/.venv/bin/python apply_worker.py --submit" in unit


def test_nothing_else_arms_the_submit_path():
    for path in _unit_paths() + [_ENV_EXAMPLE]:
        if os.path.basename(path) == _ARMED_UNIT:
            continue
        content = _directives(path)
        assert "APPLY_AGENT_ARMED=1" not in content, path
        assert "armed.env" not in content, path


# ── No LinkedIn credentials, anywhere ──────────────────────────────────────────

@pytest.mark.parametrize("key", [
    "LINKEDIN_EMAIL", "LINKEDIN_PASSWORD", "CU_LINKEDIN_EMAIL", "CU_LINKEDIN_PASSWORD",
])
def test_env_template_carries_no_linkedin_credentials(key):
    assert key not in _directives(_ENV_EXAMPLE)


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
    unit = _directives(os.path.join(_SYSTEMD, "job-linkedin-ingest.service"))
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


# ── Exposure and memory (docs/beelink-server.md rules 4 and 6) ─────────────────

def test_novnc_binds_loopback_only():
    # A bare port makes websockify listen on every interface, LAN included. The box exposes
    # nothing beyond the tailnet, and VNC is reached only through an SSH tunnel.
    unit = _directives(os.path.join(_SYSTEMD, "novnc@.service"))
    execstart = [line for line in unit.splitlines() if line.startswith("ExecStart=")]
    assert len(execstart) == 1
    assert " 127.0.0.1:608%i " in execstart[0]


def test_x11vnc_binds_loopback_only():
    assert " -localhost " in _directives(os.path.join(_SYSTEMD, "x11vnc@.service"))


def test_every_service_has_a_hard_memory_cap():
    for path in _unit_paths():
        if path.endswith(".service"):
            assert "\nMemoryMax=" in _directives(path), path


def _memory_max_bytes(path):
    value = [line for line in _directives(path).splitlines()
             if line.startswith("MemoryMax=")][0].split("=", 1)[1]
    return int(value[:-1]) * {"M": 2**20, "G": 2**30}[value[-1]]


def test_one_display_slot_fits_the_real_memory_budget():
    # The box reports ~12.6 GiB MemTotal (the iGPU reserves the rest of the 16 GB), and the
    # rules reserve ~2 GiB for the OS and Docker. Three concurrent slots must fit what's left.
    slot = sum(_memory_max_bytes(os.path.join(_SYSTEMD, name)) for name in (
        "xvfb@.service", "chrome-profile@.service", "x11vnc@.service", "novnc@.service"))
    assert 3 * slot <= int(10.5 * 2**30)


# ── provision-debian.sh ────────────────────────────────────────────────────────

_PROVISION = os.path.join(_ROOT, "deploy", "beelink", "provision-debian.sh")


def _provision_code():
    return "\n".join(line for line in _read(_PROVISION).splitlines()
                     if not line.lstrip().startswith("#"))


def test_provision_never_enables_the_ingest_timer():
    # RUNBOOK.md section 8: a watched manual run comes before the timer goes live.
    code = _provision_code()
    assert "job-linkedin-ingest" not in code.split("systemctl enable", 1)[1].split("\n", 1)[0]
    assert "enable --now job-linkedin" not in code
    assert "enable job-linkedin" not in code


def test_provision_verifies_the_tag_before_checking_it_out():
    code = _provision_code()
    assert "allowedSignersFile" in code
    assert code.index("tag -v") < code.index("checkout")
    assert "checkout -q main" not in code and "pull" not in code


def test_provision_never_writes_a_secret_value():
    code = _provision_code()
    for key in ("ANTHROPIC_API_KEY", "GMAIL_APP_PASSWORD", "SUPABASE_ANON_KEY",
                "APPLY_AGENT_ARMED"):
        assert key not in code, key
    assert "CLAUDE_CODE_OAUTH_TOKEN=" not in code


def test_provision_never_grants_the_agent_user_root_equivalent_groups():
    code = _provision_code()
    assert "usermod" not in code
    assert "useradd --system --user-group" in code
    assert "-G" not in code.split("useradd", 1)[1].split("\n", 1)[0]


# ── resume-worker ──────────────────────────────────────────────────────────────

_WORKER = os.path.join(_SYSTEMD, "resume-worker.service")
_CLAUDE_ENV_EXAMPLE = os.path.join(_ROOT, "deploy", "beelink", "env", "claude.env.example")


def test_resume_worker_runs_drain_as_jobagent_with_its_own_token_file():
    unit = _directives(_WORKER)
    assert "User=jobagent" in unit and "Type=oneshot" in unit
    assert "ExecStart=/opt/job-agent/.venv/bin/python resume_agent.py --drain" in unit
    assert "EnvironmentFile=/etc/job-agent/base.env" in unit
    assert "EnvironmentFile=/etc/job-agent/claude.env" in unit
    assert "Environment=CLAUDE_CLI_PATH=/var/lib/job-agent/.local/bin/claude" in unit
    assert "Environment=RESUME_CLAUDE_BACKEND=subscription" in unit
    assert "TimeoutStartSec=7200" in unit
    assert "OnFailure=notify-failure@%n.service" in unit


def test_only_the_workers_that_call_claude_load_the_claude_token():
    # apply-submit never calls Claude (it replays the reviewed answers), so it must not hold it.
    for path in _unit_paths():
        if os.path.basename(path) not in ("resume-worker.service", "apply-prepare.service", "job-pick.service"):
            assert "claude.env" not in _read(path), path


def test_claude_env_template_is_valueless_and_token_only():
    lines = [l for l in _read(_CLAUDE_ENV_EXAMPLE).splitlines() if l and not l.startswith("#")]
    assert lines == ["CLAUDE_CODE_OAUTH_TOKEN="]


def test_resume_worker_timer_cadence():
    timer = _read(os.path.join(_SYSTEMD, "resume-worker.timer"))
    assert "OnCalendar=*:0/30" in timer
    assert "RandomizedDelaySec=300" in timer


def test_provision_installs_worker_prereqs_but_never_enables_the_worker_timer():
    code = _provision_code()
    assert "libreoffice-writer-nogui" in code
    assert "claude.ai/install.sh" in code
    assert "/usr/local/share/fonts/calibri" in code
    assert "fc-match" in code
    assert "enable --now resume-worker" not in code and "enable resume-worker" not in code


# ── apply worker (first-ten-applications Phase D) ─────────────────────────────

_PREPARE = os.path.join(_SYSTEMD, "apply-prepare.service")
_SUBMIT = os.path.join(_SYSTEMD, _ARMED_UNIT)
_APPROVAL_ENV_EXAMPLE = os.path.join(_ROOT, "deploy", "beelink", "env", "approval.env.example")


@pytest.mark.parametrize("path", [_PREPARE, _SUBMIT])
def test_apply_units_drive_headful_chrome_on_display_1(path):
    unit = _directives(path)
    for line in ("Type=oneshot", "User=jobagent", "Environment=DISPLAY=:1",
                 "Requires=xvfb@1.service", "JoinsNamespaceOf=xvfb@1.service",
                 "Environment=APPLY_BROWSER_HEADLESS=0", "Environment=APPLY_BROWSER_CHANNEL=chrome",
                 "Environment=APPLY_GENERIC_ADAPTER=none", "Environment=APPLY_TAKEOVER_ENABLED=1",
                 "Environment=APPLY_WORKDAY_ENABLED=1", "EnvironmentFile=/etc/job-agent/vault.env",
                 "EnvironmentFile=/etc/job-agent/base.env", "OnFailure=notify-failure@%n.service"):
        assert line in unit, (path, line)
    # PrivateTmp gives each unit its own /tmp, so the shared lock must live elsewhere.
    assert "Environment=APPLY_DISPLAY_LOCK=/var/lib/job-agent/display1.lock" in unit
    timeout = [l for l in unit.splitlines() if l.startswith("TimeoutStartSec=")]
    assert len(timeout) == 1 and int(timeout[0].split("=")[1]) <= 5400


def test_prepare_runs_on_the_subscription_and_never_holds_the_signing_key():
    unit = _directives(_PREPARE)
    assert "ExecStart=/opt/job-agent/.venv/bin/python apply_worker.py --prepare" in unit
    assert "EnvironmentFile=/etc/job-agent/claude.env" in unit
    assert "Environment=APPLY_CLAUDE_BACKEND=subscription" in unit
    assert "approval.env" not in unit


def test_only_apply_submit_loads_the_approval_signing_key():
    for path in _unit_paths():
        if os.path.basename(path) != _ARMED_UNIT:
            assert "approval.env" not in _read(path), path
    assert "EnvironmentFile=/etc/job-agent/approval.env" in _directives(_SUBMIT)


def test_approval_env_template_is_valueless_and_key_only():
    lines = [l for l in _read(_APPROVAL_ENV_EXAMPLE).splitlines() if l and not l.startswith("#")]
    assert lines == ["APPROVAL_SIGNING_KEY="]


def test_apply_timers():
    prepare = _read(os.path.join(_SYSTEMD, "apply-prepare.timer"))
    submit = _read(os.path.join(_SYSTEMD, "apply-submit.timer"))
    assert "OnCalendar=*:0/20" in prepare and "Unit=apply-prepare.service" in prepare
    assert "OnCalendar=*:*:00" in submit and "Unit=apply-submit.service" in submit


def test_provision_never_enables_the_apply_timers():
    code = _provision_code()
    for unit in ("apply-prepare", "apply-submit"):
        assert f"enable --now {unit}" not in code and f"enable {unit}" not in code


def test_provision_installs_the_beelink_requirements_and_the_approval_env_template():
    code = _provision_code()
    assert "requirements-beelink.txt" in code
    assert "approval.env.example" in code


def test_takeover_view_is_tailnet_only_through_tailscale_serve():
    # noVNC stays bound to 127.0.0.1; tailscale serve publishes it over HTTPS to the tailnet only
    # (never --funnel, which would put it on the internet).
    code = _provision_code()
    assert "tailscale serve --bg --https=8443 http://127.0.0.1:6081" in code
    assert "funnel" not in code


def test_beelink_requirements_pin_playwright_and_skip_browser_use():
    with open(os.path.join(_ROOT, "requirements-beelink.txt")) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    assert "-r requirements.txt" in lines
    assert any(l.startswith("playwright==") for l in lines)
    assert not any("browser-use" in l or "langchain" in l for l in lines)


def test_memory_fits_with_the_apply_worker_running():
    # LinkedIn slot 0, the takeover display slot 1, one apply worker at a time (display lock),
    # and the resume worker, within the ~10.5 GiB left after the OS.
    stack = ("xvfb@.service", "x11vnc@.service", "novnc@.service")
    total = (sum(_memory_max_bytes(os.path.join(_SYSTEMD, n)) for n in stack + ("chrome-profile@.service",))
             + sum(_memory_max_bytes(os.path.join(_SYSTEMD, n)) for n in stack)
             + max(_memory_max_bytes(_PREPARE), _memory_max_bytes(_SUBMIT))
             + _memory_max_bytes(os.path.join(_SYSTEMD, "resume-worker.service"))
             + _memory_max_bytes(os.path.join(_SYSTEMD, "job-pick.service"))
             + _memory_max_bytes(os.path.join(_SYSTEMD, "job-sourcing.service")))
    assert total <= int(10.5 * 2**30)


_VAULT_ENV_EXAMPLE = os.path.join(_ROOT, "deploy", "beelink", "env", "vault.env.example")


def test_only_the_apply_units_load_the_vault_key():
    for path in _unit_paths():
        name = os.path.basename(path)
        loads = "EnvironmentFile=/etc/job-agent/vault.env" in _directives(path)
        assert loads == (name in ("apply-prepare.service", "apply-submit.service")), name
        if name not in ("apply-prepare.service", "apply-submit.service"):
            assert "vault.env" not in _read(path), name


def test_vault_env_template_is_valueless_and_key_only():
    lines = [l for l in _read(_VAULT_ENV_EXAMPLE).splitlines() if l and not l.startswith("#")]
    assert lines == ["VAULT_KEY="]


def test_provision_creates_the_vault_env_template_but_never_a_key():
    code = _provision_code()
    assert "vault.env.example" in code
    assert "Fernet.generate_key" not in code and "VAULT_KEY=" not in code


# ── Sourcing and scoring (fifty-a-day) ─────────────────────────────────────────

def test_sourcing_needs_no_model_and_no_secrets_beyond_base():
    unit = _directives(os.path.join(_SYSTEMD, "job-sourcing.service"))
    assert "ExecStart=/opt/job-agent/.venv/bin/python job_sourcing.py" in unit
    assert [l for l in unit.splitlines() if l.startswith("EnvironmentFile=")] == ["EnvironmentFile=/etc/job-agent/base.env"]


def test_job_pick_judges_on_the_subscription_and_skips_unsubmittable_sites():
    unit = _directives(os.path.join(_SYSTEMD, "job-pick.service"))
    assert "ExecStart=/opt/job-agent/.venv/bin/python job_pick.py" in unit
    for line in ("EnvironmentFile=/etc/job-agent/claude.env", "Environment=JOB_PICK_BACKEND=subscription",
                 "Environment=RESUME_CLAUDE_BACKEND=subscription", "Environment=APPLY_GENERIC_ADAPTER=none"):
        assert line in unit


def test_the_resume_worker_skips_sites_nothing_here_can_submit():
    # fifty-a-day F15: documents were built for Oracle/iCIMS rows the apply worker can never send.
    assert "Environment=APPLY_GENERIC_ADAPTER=none" in _directives(os.path.join(_SYSTEMD, "resume-worker.service"))


def test_scoring_runs_after_sourcing_every_two_hours():
    sourcing = _read(os.path.join(_SYSTEMD, "job-sourcing.timer"))
    pick = _read(os.path.join(_SYSTEMD, "job-pick.timer"))
    assert "OnCalendar=*-*-* 00/2:13:00" in sourcing and "OnCalendar=*-*-* 00/2:43:00" in pick


def test_provision_never_enables_the_sourcing_or_pick_timers():
    code = _provision_code()
    assert "enable --now job-sourcing" not in code and "enable --now job-pick" not in code


def test_beelink_requirements_install_cpu_only_torch_for_job_pick():
    with open(os.path.join(_ROOT, "requirements-beelink.txt")) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    assert "--extra-index-url https://download.pytorch.org/whl/cpu" in lines
    assert lines.index("torch") < lines.index("sentence-transformers>=3.0.0")
