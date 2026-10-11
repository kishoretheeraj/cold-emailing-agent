import glob
import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SYSTEMD = os.path.join(_ROOT, "deploy", "beelink", "systemd")
_ROLES = ("discovery", "screening", "resume", "apply", "monitor", "api")
_TIMERS = ("discovery", "screening", "resume", "apply")
_ARMED_SETTER = "APPLY_AGENT_" + "ARMED=1"


def _read(name):
    with open(os.path.join(_SYSTEMD, name)) as f:
        return f.read()


def _directives(name):
    return "\n".join(l for l in _read(name).splitlines() if not l.lstrip().startswith("#"))


def test_every_role_has_a_unit_and_only_four_have_timers():
    assert sorted(os.path.basename(p) for p in glob.glob(os.path.join(_SYSTEMD, "jobagent24-*"))) == sorted(
        [f"jobagent24-{r}.service" for r in _ROLES] + [f"jobagent24-{r}.timer" for r in _TIMERS])


@pytest.mark.parametrize("role", _ROLES)
def test_unit_lives_only_in_the_new_namespace(role):
    text = _directives(f"jobagent24-{role}.service")
    assert "WorkingDirectory=/opt/jobagent24" in text
    assert not re.search(r"/opt/job-agent\b|/etc/job-agent\b|/var/lib/job-agent/(?!\.local/bin/claude)", text)
    assert "EnvironmentFile=/etc/jobagent24/base.env" in text
    assert "ExecStart=/opt/jobagent24/.venv/bin/" in text
    assert "config.require_secrets()" in text


@pytest.mark.parametrize("role", _ROLES)
def test_unit_is_hardened_capped_and_never_armed(role):
    raw = _read(f"jobagent24-{role}.service")
    for line in ("NoNewPrivileges=yes", "ProtectSystem=strict", "ProtectHome=yes", "User=jobagent"):
        assert line in raw
    assert "\nMemoryMax=" in raw
    assert "Environment=APPLY_AGENT_ARMED=\n" in raw
    assert _ARMED_SETTER not in raw
    assert "vault.env" not in raw


@pytest.mark.parametrize("role", _TIMERS)
def test_timer_targets_its_service(role):
    timer = _read(f"jobagent24-{role}.timer")
    assert f"Unit=jobagent24-{role}.service" in timer and "OnCalendar=" in timer


def test_api_unit_runs_uvicorn_on_loopback_port_8024():
    text = _directives("jobagent24-api.service")
    assert "uvicorn api.dashboard:app --host 127.0.0.1 --port 8024" in text
    assert "EnvironmentFile=/etc/jobagent24/dashboard.env" in text


def test_apply_unit_has_its_own_display_lock_sessions_and_never_submits():
    text = _directives("jobagent24-apply.service")
    assert "Environment=DISPLAY=:2" in text and "Requires=xvfb@2.service" in text
    assert "JoinsNamespaceOf=xvfb@2.service" in text
    assert "APPLY_DISPLAY_LOCK=/var/lib/jobagent24/display2.lock" in text
    assert "APPLY_SESSIONS_DIR=/var/lib/jobagent24/sessions" in text
    assert "APPLY_RESUME_V2=1" in text and "--prepare" in text and "--submit" not in text


def test_timers_are_offset_from_the_old_ones():
    old = {"discovery": "job-sourcing.timer", "screening": "job-pick.timer",
           "resume": "resume-worker.timer", "apply": "apply-prepare.timer"}
    for role, old_timer in old.items():
        new_cal = re.search(r"OnCalendar=(.*)", _read(f"jobagent24-{role}.timer")).group(1)
        old_cal = re.search(r"OnCalendar=(.*)", _read(old_timer)).group(1)
        assert new_cal != old_cal


def test_provision_script_is_namespaced_and_leaves_the_old_stack_alone():
    with open(os.path.join(_ROOT, "deploy", "beelink", "provision-jobagent24.sh")) as f:
        text = f.read()
    assert "APP=/opt/jobagent24" in text and "ETC=/etc/jobagent24" in text
    assert not re.search(r"/opt/job-agent\b", text)
    assert "rm -rf" not in text
    # the only stops are of this namespace's own units
    stops = re.findall(r"systemctl stop (\S+)", text)
    assert stops and all(s.startswith('"jobagent24-') for s in stops)
    assert "--enable-timers" in text and "uvicorn" in text
    assert _ARMED_SETTER not in text


def _provision():
    with open(os.path.join(_ROOT, "deploy", "beelink", "provision-jobagent24.sh")) as f:
        return f.read()


def test_provision_quiesces_before_touching_code_and_verifies_the_api_after():
    text = _provision()
    order = [text.index(s) for s in (
        "flock -n 9", "systemctl stop", "checkout -q", 'install -q -r "$APP/requirements-beelink.txt"',
        "systemctl daemon-reload", "systemctl restart jobagent24-api.service", "/api/health")]
    assert order == sorted(order)


def test_provision_restarts_only_what_was_running_and_fails_on_an_unhealthy_api():
    text = _provision()
    assert "WAS_ACTIVE_TIMERS" in text and '[ "$WAS_API_ACTIVE" -eq 1 ]' in text
    assert '"status":"ok"' in text
    assert "exit 1" in text.split("/api/health")[1]


import subprocess


def test_provision_verifies_every_stop_and_restarts_timers_only_after_the_api_is_healthy():
    text = _provision()
    assert text.index("require_stopped()") < text.index("systemctl stop")
    assert text.count('require_stopped "jobagent24-$name.timer"') == 1
    assert text.count('require_stopped "jobagent24-$name.service"') == 1
    # verification happens before any code is touched
    assert text.index('require_stopped "jobagent24-$name.service"') < text.index("checkout -q")
    # the timer restart block comes after the api health check, never before
    assert text.index("/api/health") < text.index('[ "$ENABLE_TIMERS" -eq 1 ]')
    assert text.index('echo "jobagent24-api not healthy') < text.index('[ "$ENABLE_TIMERS" -eq 1 ]')


@pytest.mark.parametrize("state,code", [
    ("inactive", 0), ("failed", 0), ("unknown", 0),
    ("active", 1), ("activating", 1), ("deactivating", 1), ("reloading", 1),
])
def test_require_stopped_aborts_unless_the_unit_is_down(tmp_path, state, code):
    func = re.search(r"require_stopped\(\) \{.*?\n\}", _provision(), re.S).group(0)
    fake = tmp_path / "systemctl"
    fake.write_text('#!/bin/sh\necho "$FAKE_STATE"\n[ "$FAKE_STATE" = active ]\n')
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "FAKE_STATE": state}
    run = subprocess.run(["bash", "-c", f"set -euo pipefail\n{func}\nrequire_stopped jobagent24-x.timer"],
                         env=env, capture_output=True, text=True)
    assert run.returncode == code
    if code:
        assert state in run.stderr
