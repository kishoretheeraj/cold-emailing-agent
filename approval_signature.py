"""
Verifies that an approval came from the operator's logged-in contact-manager, not from anyone
holding the public anon key (spec 2026-10-08 §9.1).

The submit route signs `approval:v1:<id>:<revision_hash>:<signed_at_ms>` with HMAC-SHA256 under
APPROVAL_SIGNING_KEY (server-side only, never in the browser) and passes the signature and
epoch-ms to approve_application, which stores them. A worker recomputes the HMAC from the row and
also requires the signing time to sit within MAX_SKEW_SECONDS of approved_at, so an old signature
replayed into a later approval is refused. An approval is also refused once past its
approval_expires_at (7 days after approval, set by the RPC). Mirrored by contact-manager/src/lib/approvalSignature.ts;
both test files pin the same vector.
"""

import hashlib
import hmac
import os
from datetime import datetime, timezone

MAX_SKEW_SECONDS = 300
_MIN_KEY_LENGTH = 32


def _key(key):
    return os.environ.get("APPROVAL_SIGNING_KEY") if key is None else key


def key_configured(key=None):
    """True when a signing key of at least 32 characters is available."""
    key = _key(key)
    return bool(key) and len(key) >= _MIN_KEY_LENGTH


def sign(key, application_id, revision_hash, signed_at_ms):
    """Hex HMAC-SHA256 of the canonical approval message."""
    message = f"approval:v1:{int(application_id)}:{revision_hash}:{int(signed_at_ms)}"
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def _as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _epoch_ms(timestamp):
    try:
        return int(datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def verify(job, key=None, now_ms=None):
    """None when the row's approval is validly signed, else a short reason."""
    key = _key(key)
    if not key_configured(key):
        return "APPROVAL_SIGNING_KEY is not configured"
    signature = job.get("approval_signature")
    signed_at_ms = _as_int(job.get("approval_signed_at_ms"))
    revision_hash = job.get("approved_revision_hash")
    if not signature or signed_at_ms is None or not revision_hash or job.get("id") is None:
        return "approval is not signed"
    if not hmac.compare_digest(sign(key, job["id"], revision_hash, signed_at_ms), str(signature)):
        return "approval signature does not match"
    approved_ms = _epoch_ms(job.get("approved_at"))
    if approved_ms is None or abs(approved_ms - signed_at_ms) > MAX_SKEW_SECONDS * 1000:
        return "approval signature is stale"
    expires_ms = _epoch_ms(job.get("approval_expires_at"))
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000) if now_ms is None else now_ms
    if expires_ms is None or now_ms > expires_ms:
        return "approval has expired"
    return None
