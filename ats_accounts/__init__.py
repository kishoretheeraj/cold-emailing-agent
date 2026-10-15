"""
ATS account handling: detect an account wall on an application page and, where policy allows,
sign in, create an account or recover a password.

Greenhouse, Lever and Ashby never need an account to apply, so a wall there is never answered
with a speculative signup: it is reported as needs_review. Workday is handled by
flows.workday behind an explicit env flag and tenant allowlist. Passwords are generated here,
persisted in the Fernet vault before they are typed, and never logged.
"""

import logging

from . import detect
from .backoff import BackoffExhausted, BackoffStore
from .detect import WallKind, detect_account_wall, detect_duplicate_account_message
from .flows.base import FlowContext, FlowResult
from .passwords import generate_password
from .vault import AttemptLimitExceeded, CredentialVault, FernetVaultAdapter

log = logging.getLogger(__name__)

NO_ACCOUNT_PLATFORMS = ("greenhouse", "lever", "ashby")
SUPPORTED_PLATFORMS = NO_ACCOUNT_PLATFORMS + ("workday",)

__all__ = [
    "handle_account_wall",
    "generate_password",
    "CredentialVault",
    "FernetVaultAdapter",
    "AttemptLimitExceeded",
    "BackoffStore",
    "BackoffExhausted",
    "WallKind",
    "detect_account_wall",
    "detect_duplicate_account_message",
    "FlowContext",
    "FlowResult",
    "SUPPORTED_PLATFORMS",
]


def handle_account_wall(page, platform, tenant_host, email, ctx=None):
    """{'outcome': ..., 'detail': ...} for whatever account wall `page` is showing. Never raises."""
    platform = (platform or "").strip().lower()
    try:
        if platform in NO_ACCOUNT_PLATFORMS:
            kind = detect.detect_account_wall(page)
            if kind != WallKind.NONE:
                log.info(f"[ATS] | {platform} | {tenant_host} | unexpected_account_wall | kind={kind}")
                return {"outcome": "needs_review", "detail": "unexpected_account_wall:" + kind}
            return {"outcome": "no_account_needed"}
        if platform == "workday":
            from .flows import workday
            return workday.handle(page, tenant_host, email, ctx)
        return {"outcome": "needs_review", "detail": "unsupported_platform:" + platform}
    except Exception as exc:
        log.warning(f"[ATS] | {platform} | {tenant_host} | handle_account_wall failed | {type(exc).__name__}")
        return {"outcome": "failed", "detail": "exception:" + type(exc).__name__}
