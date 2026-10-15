"""Anti-watermark hook: a required pipeline stage that runs two opaque, operator-configured commands.

Commands come only from the environment (RESUME_DEWATERMARK_CMD, RESUME_PARAPHRASE_CMD). Each is run with
shlex.split (no shell), reads {"texts": [...]} on stdin and writes {"texts": [...]} on stdout. Order:
dewatermark -> paraphrase -> dewatermark. This module knows nothing about what the commands do.

The stage is never skipped: an unset command or a step that still fails after one retry raises WmError, and
unprocessed text is never returned as a success.
"""

import json
import os
import shlex
import subprocess
import time

DEWATERMARK_ENV = "RESUME_DEWATERMARK_CMD"
PARAPHRASE_ENV = "RESUME_PARAPHRASE_CMD"
STEP_TIMEOUT_S = 60
_ATTEMPTS = 2


class WmError(Exception):
    """Carries a short reason code; the message never includes letter text or command output."""

    def __init__(self, code="wm_failed"):
        super().__init__(code)
        self.code = code


# ── Steps ──────────────────────────────────────────────────────────────────────

def _argv(env_name):
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        raise WmError("wm_unavailable")
    try:
        argv = shlex.split(raw)
    except ValueError:
        raise WmError("wm_unavailable")
    if not argv:
        raise WmError("wm_unavailable")
    return argv


def _run_step(argv, texts, deadline):
    payload = json.dumps({"texts": texts}, ensure_ascii=False)
    for _ in range(_ATTEMPTS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WmError("wm_timeout")
        try:
            proc = subprocess.run(argv, input=payload, capture_output=True, text=True,
                                  timeout=min(STEP_TIMEOUT_S, remaining), check=False)
            if proc.returncode != 0:
                continue
            out = json.loads(proc.stdout)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            continue
        result = out.get("texts") if isinstance(out, dict) else None
        if (isinstance(result, list) and len(result) == len(texts)
                and all(isinstance(t, str) and t.strip() for t in result)):
            return result
    raise WmError("wm_step_failed")


# ── Public ─────────────────────────────────────────────────────────────────────

def apply_wm(texts, timeout_s=150):
    """Run the three-step pipeline over `texts`. Returns (final_texts, True) or raises WmError."""
    steps = [_argv(DEWATERMARK_ENV), _argv(PARAPHRASE_ENV), _argv(DEWATERMARK_ENV)]
    deadline = time.monotonic() + timeout_s
    current = [str(t) for t in texts]
    for argv in steps:
        current = _run_step(argv, current, deadline)
    return current, True
