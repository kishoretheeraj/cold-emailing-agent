"""
Shared flow types and the logging helper.

step() is the only way flows log, and it drops any field that could carry a secret.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

OUTCOMES = ("signed_in", "account_created", "password_reset", "needs_review", "blocked", "failed")
_SENSITIVE_KEY_PARTS = ("password", "secret", "token", "otp")


@dataclass
class FlowContext:
    page: Any
    platform: str
    tenant: str
    email: str
    vault: Any
    backoff: Any
    dry_run: bool = False
    state: dict = field(default_factory=dict)

    @property
    def scope(self):
        return f"{self.platform}:{self.tenant}"


@dataclass
class FlowResult:
    outcome: str
    detail: str = ""

    def __post_init__(self):
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown flow outcome: {self.outcome}")

    def as_dict(self):
        return {"outcome": self.outcome, "detail": self.detail}


class FlowAbort(Exception):
    """Stops a flow with a result (selector missing, throttled, backoff active)."""

    def __init__(self, outcome, detail):
        super().__init__(f"{outcome}: {detail}")
        self.outcome = outcome
        self.detail = detail

    def result(self):
        return FlowResult(self.outcome, self.detail)


def _scrub(value):
    """Recursively redact sensitive keys inside nested dicts/lists; a reservation dict passed
    as a step field must not leak its password through str()."""
    if isinstance(value, dict):
        return {k: ("<redacted>" if any(part in k.lower() for part in _SENSITIVE_KEY_PARTS)
                    else _scrub(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def step(ctx, name, **fields):
    """Log one flow step. Fields whose key mentions a password or secret are dropped; nested
    structures are scrubbed recursively."""
    kept = []
    for key, value in fields.items():
        lowered = key.lower()
        if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
            continue
        text = str(_scrub(value)).replace("\n", " ")[:200]
        kept.append(f"{key}={text}")
    extra = " | " + " ".join(kept) if kept else ""
    log.info(f"[ATS] | {ctx.platform} | {ctx.tenant} | {name}{extra}")
