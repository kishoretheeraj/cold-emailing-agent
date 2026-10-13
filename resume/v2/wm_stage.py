"""Anti-watermark stage for resume text, run as external commands.

The operator configures two shell commands via RESUME_DEWATERMARK_CMD and RESUME_PARAPHRASE_CMD. Each
command reads {"texts": [...]} as JSON on stdin and writes {"texts": [...]} (same length) on stdout.
The stage runs dewatermark -> paraphrase -> dewatermark. It is required: a missing command or a step that
fails twice raises WmStageError, and the stage is never skipped silently.

Availability is checked up front (no subprocess runs when any command is unset). Inside a step, the command
is resolved from the environment on EVERY attempt, so command resolution is part of the two-attempt policy:
a command that is unset or unparseable on an attempt records "wm_unavailable" and ends the step without
executing anything.
Error messages carry step names and reason codes only, never text.
"""

import json
import logging
import os
import shlex
import subprocess
import time

log = logging.getLogger("resume_v2")

STEP_TIMEOUT_S = 60
ATTEMPTS = 2
_STEPS = (
    ("dewatermark", "RESUME_DEWATERMARK_CMD"),
    ("paraphrase", "RESUME_PARAPHRASE_CMD"),
    ("dewatermark", "RESUME_DEWATERMARK_CMD"),
)
_UNAVAILABLE = "wm_unavailable"


class WmStageError(Exception):
    """The anti-watermark stage is unavailable or a step failed after one retry."""


# ── Steps ──────────────────────────────────────────────────────────────────────

def _resolve(env):
    """argv for the command in environment variable `env`, or None when unset, blank or unparseable."""
    cmd = (os.environ.get(env) or "").strip()
    if not cmd:
        return None
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return None
    return argv or None


def _attempt(argv, texts, timeout):
    """One subprocess call. Returns (texts, None) on success or (None, reason_code)."""
    try:
        proc = subprocess.run(argv, input=json.dumps({"texts": texts}), capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "os_error"
    if proc.returncode != 0:
        return None, f"exit_{proc.returncode}"
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None, "bad_json"
    out = data.get("texts") if isinstance(data, dict) else None
    if not isinstance(out, list):
        return None, "bad_shape"
    if len(out) != len(texts):
        return None, "length_mismatch"
    if not all(isinstance(t, str) for t in out):
        return None, "non_string"
    return out, None


def _run_step(name, env, texts, deadline):
    reason = "unknown"
    for _ in range(ATTEMPTS):
        argv = _resolve(env)
        if argv is None:
            reason = _UNAVAILABLE
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WmStageError("wm_timeout")
        out, reason = _attempt(argv, texts, min(STEP_TIMEOUT_S, remaining))
        if reason is None:
            return out
        log.warning(f"resume_v2 | wm | step={name} | attempt failed | {reason}")
    if reason == _UNAVAILABLE:
        raise WmStageError(_UNAVAILABLE)
    raise WmStageError(f"wm_step_failed:{name}:{reason}")


# ── Public ─────────────────────────────────────────────────────────────────────

def run_anti_watermark(texts, timeout_s=150):
    """Run the three-step external stage over a list of strings. Returns (final_texts, {"wm_applied": True}).
    Raises WmStageError when a command is unset ("wm_unavailable") or a step fails twice."""
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        raise WmStageError("wm_bad_input")
    for _name, env in _STEPS:
        if _resolve(env) is None:
            raise WmStageError(_UNAVAILABLE)
    deadline = time.monotonic() + timeout_s
    current = texts
    for name, env in _STEPS:
        current = _run_step(name, env, current, deadline)
    return current, {"wm_applied": True}
