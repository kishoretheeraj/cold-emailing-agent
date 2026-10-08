"""
Per-tenant application-site passwords for the Beelink apply worker (spec 2026-10-08 §5).

One entry per tenant (ats_sessions.tenant_key): login email, a generated password, the login
URL, a status, and when it was created. The whole file is Fernet-encrypted with VAULT_KEY
(from /etc/job-agent/vault.env, root-owned, loaded only by the apply units) and written 0600.

`reserve()` generates the password and writes it to disk BEFORE anything is typed into a signup
form, so a crash mid-signup never loses the password of an account that may now exist. An
existing entry is never replaced: a failed login goes to a human, never to a second account or a
reset. Passwords never appear in logs, reprs or exceptions.
"""

import json
import os
import secrets
import string
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

SPECIALS = "!@#$%^*-_=+?"
_ALPHABET = string.ascii_letters + string.digits + SPECIALS
_STATUSES = ("reserved", "active", "rejected")


class VaultError(Exception):
    pass


def generate_password(length=24):
    """Random password with at least one lowercase, uppercase, digit and special character."""
    required = [secrets.choice(string.ascii_lowercase), secrets.choice(string.ascii_uppercase),
                secrets.choice(string.digits), secrets.choice(SPECIALS)]
    rest = [secrets.choice(_ALPHABET) for _ in range(length - len(required))]
    chars = required + rest
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


class Vault:
    def __init__(self, path, key):
        try:
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except (ValueError, TypeError, AttributeError):
            raise VaultError("VAULT_KEY is missing or not a valid Fernet key") from None
        self.path = path
        self.key = key

    def __repr__(self):
        return f"Vault(path={self.path!r})"

    def _load(self):
        try:
            with open(self.path, "rb") as f:
                blob = f.read()
        except FileNotFoundError:
            return {}
        try:
            return json.loads(self._fernet.decrypt(blob))
        except InvalidToken:
            raise VaultError("could not decrypt the vault: wrong VAULT_KEY or a damaged file") from None

    def _save(self, entries):
        blob = self._fernet.encrypt(json.dumps(entries, sort_keys=True).encode())
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, mode=0o700, exist_ok=True)
        tmp = f"{self.path}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def get(self, tenant):
        """The tenant's entry (a copy), or None."""
        entry = self._load().get(tenant)
        return dict(entry) if entry else None

    def reserve(self, tenant, email, login_url):
        """Generate and persist a password for a new account on this tenant, then return it."""
        entries = self._load()
        if tenant in entries:
            raise VaultError(f"vault already has an account for {tenant}; never creating a second one")
        entries[tenant] = {
            "email": email, "password": generate_password(), "login_url": login_url,
            "status": "reserved", "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save(entries)
        return dict(entries[tenant])

    def mark(self, tenant, status):
        """Record what happened to the account: active (works), rejected (a human must look)."""
        if status not in _STATUSES:
            raise VaultError(f"unknown vault status: {status}")
        entries = self._load()
        if tenant not in entries:
            raise VaultError(f"no vault entry for {tenant}")
        entries[tenant]["status"] = status
        entries[tenant]["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save(entries)
