"""
Credential vault for ATS accounts.

`CredentialVault` is the contract the flows depend on; `FernetVaultAdapter` implements it on top
of credential_vault.Vault (same Fernet file, same file lock and atomic save), keyed
"platform:tenant". Rules it enforces:

- reserve() persists the PENDING password before the caller ever types it, so a crash mid-signup
  or mid-reset never loses the password of an account that may now exist.
- At most one signup and one password reset per platform:tenant, ever. The counters live in a
  0600 JSON sidecar (flocked, atomic, fsync'd) and are incremented BEFORE the reservation is
  written, so a crash can only over-count, never allow a second attempt.
- commit(..., 'verified') promotes pending to current and keeps the old as previous_password;
  commit(..., 'failed') clears pending and keeps the old. A discarded pending password is kept in
  retired_passwords for a human, since an account may exist under it.
- Nothing here logs a password.
"""

import logging
from datetime import datetime, timezone
from typing import Protocol

from credential_vault import Vault, VaultError, file_lock

from ._fileio import atomic_write_json, read_json
from .passwords import generate_password

log = logging.getLogger(__name__)

PURPOSES = ("signup", "reset")
OUTCOMES = ("verified", "failed")
ATTEMPT_LIMITS = {"signup": 1, "reset": 1}
_COUNTER = {"signup": "signups", "reset": "resets"}


class AttemptLimitExceeded(VaultError):
    pass


def scope_of(platform, tenant):
    return f"{platform}:{tenant}"


def can_attempt(counts, purpose):
    """True while `purpose` ('signup' or 'reset') has not used up its lifetime limit."""
    if purpose not in ATTEMPT_LIMITS:
        raise VaultError(f"unknown purpose: {purpose}")
    return int((counts or {}).get(_COUNTER[purpose], 0)) < ATTEMPT_LIMITS[purpose]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _empty_counts():
    return {"signups": 0, "resets": 0, "signin_failures": 0}


class CredentialVault(Protocol):
    def lookup(self, platform, tenant, email):
        """Entry dict (password, previous_password, pending_password, pending_purpose, status,
        email) or None when there is no usable credential."""
        ...

    def reserve(self, platform, tenant, email, purpose):
        """Atomically count the attempt and persist a pending password; returns a reservation
        dict that includes the password to type. Raises AttemptLimitExceeded."""
        ...

    def commit(self, reservation, outcome):
        """outcome 'verified' promotes the pending password; 'failed' clears it."""
        ...

    def attempts(self, platform, tenant):
        """{'signups': int, 'resets': int, 'signin_failures': int}"""
        ...

    def record_signin_failure(self, platform, tenant):
        ...

    def mark_status(self, platform, tenant, status):
        ...


class FernetVaultAdapter(Vault):
    def __init__(self, path, key, attempts_path=None):
        super().__init__(path, key)
        self.attempts_path = attempts_path or f"{path}.attempts.json"

    # ── Attempt counters ───────────────────────────────────────────────────────

    def attempts(self, platform, tenant):
        counts = read_json(self.attempts_path, {}).get(scope_of(platform, tenant), {})
        return {k: int(counts.get(k, 0)) for k in _empty_counts()}

    def record_signin_failure(self, platform, tenant):
        scope = scope_of(platform, tenant)
        with file_lock(self.attempts_path):
            data = read_json(self.attempts_path, {})
            counts = data.setdefault(scope, _empty_counts())
            counts["signin_failures"] = int(counts.get("signin_failures", 0)) + 1
            atomic_write_json(self.attempts_path, data)
            return counts["signin_failures"]

    # ── Credentials ────────────────────────────────────────────────────────────

    def lookup(self, platform, tenant, email):
        entry = self.get(scope_of(platform, tenant))
        if not entry:
            return None
        if email and (entry.get("email") or "").lower() != email.lower():
            return None
        if not (entry.get("password") or entry.get("pending_password")):
            return None
        return entry

    def mark_status(self, platform, tenant, status):
        self.mark(scope_of(platform, tenant), status)

    def reserve(self, platform, tenant, email, purpose):
        if purpose not in PURPOSES:
            raise VaultError(f"unknown purpose: {purpose}")
        scope = scope_of(platform, tenant)
        with file_lock(self.attempts_path):
            data = read_json(self.attempts_path, {})
            counts = data.setdefault(scope, _empty_counts())
            if not can_attempt(counts, purpose):
                raise AttemptLimitExceeded(f"{purpose} limit reached for {scope}")
            counts[_COUNTER[purpose]] = int(counts.get(_COUNTER[purpose], 0)) + 1
            atomic_write_json(self.attempts_path, data)
            password = generate_password(email=email)
            with file_lock(self.path):
                entries = self._load()
                entry = entries.get(scope) or {
                    "email": email, "password": None, "login_url": None, "status": "reserved",
                    "created_at": _now(), "previous_password": None, "pending_password": None,
                    "pending_purpose": None, "attempts": {},
                }
                if entry.get("pending_password"):
                    # An unresolved earlier reservation may belong to a real account: keep it.
                    entry.setdefault("retired_passwords", []).append(entry["pending_password"])
                entry["email"] = entry.get("email") or email
                entry["pending_password"] = password
                entry["pending_purpose"] = purpose
                entry["pending_at"] = _now()
                entry["attempts"] = dict(counts)
                entries[scope] = entry
                self._save(entries)
        return {"platform": platform, "tenant": tenant, "scope": scope, "email": email,
                "purpose": purpose, "password": password}

    def commit(self, reservation, outcome):
        if outcome not in OUTCOMES:
            raise VaultError(f"unknown outcome: {outcome}")
        scope = reservation.get("scope") or scope_of(reservation["platform"], reservation["tenant"])
        with file_lock(self.path):
            entries = self._load()
            entry = entries.get(scope)
            if not entry or not entry.get("pending_password"):
                raise VaultError(f"no pending password for {scope}")
            expected = reservation.get("password")
            if expected and expected != entry["pending_password"]:
                raise VaultError(f"stale reservation for {scope}")
            pending = entry["pending_password"]
            if outcome == "verified":
                if entry.get("password"):
                    entry["previous_password"] = entry["password"]
                entry["password"] = pending
                entry["status"] = "active"
            else:
                entry.setdefault("retired_passwords", []).append(pending)
            entry["pending_password"] = None
            entry["pending_purpose"] = None
            entry["pending_at"] = None
            entry["updated_at"] = _now()
            entries[scope] = entry
            self._save(entries)
        log.info(f"[ATS] | vault | {scope} | commit | outcome={outcome}")
