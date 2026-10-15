"""
Per-tenant application-site passwords for the Beelink apply worker (spec 2026-10-08 §5).

One entry per tenant (ats_sessions.tenant_key): login email, a generated password, the login
URL, a status, and when it was created. The whole file is Fernet-encrypted with VAULT_KEY
(from /etc/job-agent/vault.env, root-owned, loaded only by the apply units) and written 0600.

`reserve()` generates the password and writes it to disk BEFORE anything is typed into a signup
form, so a crash mid-signup never loses the password of an account that may now exist. An
existing entry is never replaced: a failed login goes to a human, never to a second account or a
reset. Passwords never appear in logs, reprs or exceptions.

Secure retrieval (item 9) adds, without changing the file format: `read_key_file`, `Vault.reveal`
(used only by the SSH+TOTP gated vault_reveal.py), `Vault.rotate_key` (vault_rotate.py),
`Vault.list_tenants`, and `assert_no_plaintext_credentials` (a check that no legacy plaintext
credential file exists).
"""

import contextlib
import fcntl
import json
import logging
import os
import secrets
import string
import time
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

SPECIALS = "!@#$%^*-_=+?"
_ALPHABET = string.ascii_letters + string.digits + SPECIALS
_STATUSES = ("reserved", "active", "rejected")
KEY_ENV_NAME = "VAULT_KEY"

# Legacy plaintext credential files that must never exist (the vault is the only store).
VAULT_PATHS_AUDIT = [
    "/etc/job-agent/ats_accounts.json",
    "/opt/jobagent24/ats_accounts.json",
    "/var/lib/job-agent/ats_accounts.json",
]


class VaultError(Exception):
    pass


@contextlib.contextmanager
def file_lock(path):
    """Exclusive cross-process lock for a read-modify-write on `path` (flock on a sidecar file;
    closing the descriptor releases it, so a crashed holder never wedges the vault)."""
    os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
    fd = os.open(f"{path}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def generate_password(length=24):
    """Random password with at least one lowercase, uppercase, digit and special character."""
    required = [secrets.choice(string.ascii_lowercase), secrets.choice(string.ascii_uppercase),
                secrets.choice(string.digits), secrets.choice(SPECIALS)]
    rest = [secrets.choice(_ALPHABET) for _ in range(length - len(required))]
    chars = required + rest
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


# ── Key files and plaintext audit ──────────────────────────────────────────────

def _extract_key(text):
    # A Fernet key ends in "=" padding, so an env-style file is matched by its VAULT_KEY= line
    # prefix rather than split on "=".
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if line.startswith(f"{KEY_ENV_NAME}="):
            return line[len(KEY_ENV_NAME) + 1:].strip().strip("\"'")
    return text.strip()


def read_key_file(path):
    """The vault key stored in `path` as bytes. Accepts a bare key or an env-style
    `VAULT_KEY=...` line (the /etc/job-agent/vault.env format). Raises VaultError if missing or empty.
    Refuses key files readable or writable by anyone outside owner/group-read (the documented
    mode is 0440 root:jobagent); a world-readable key file is a misconfiguration, not a key."""
    if os.path.islink(path):
        log.warning(f"vault key file is a symlink: {path}")
    try:
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            mode = st.st_mode & 0o777
            if mode & 0o027:
                raise VaultError(f"vault key file has insecure permissions ({oct(mode)}): {path}")
            text = f.read().decode()
    except (OSError, UnicodeDecodeError):
        raise VaultError(f"vault key file is missing or unreadable: {path}") from None
    key = _extract_key(text)
    if not key:
        raise VaultError(f"vault key file is empty: {path}") from None
    return key.encode()


def assert_no_plaintext_credentials(extra_paths=()):
    """Raise VaultError naming any legacy plaintext credential file that exists."""
    found = [p for p in list(VAULT_PATHS_AUDIT) + list(extra_paths) if os.path.exists(p)]
    if found:
        raise VaultError("plaintext credential file(s) present: " + ", ".join(found))


def fsync_dir(path):
    """fsync the directory holding `path` so a rename is durable. Raises OSError on failure:
    for a credential store, an undetected lost rename can mean permanent key loss, so callers
    treat this as fatal except where a boolean failure is the contract (vault_audit)."""
    fd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# ── Vault ──────────────────────────────────────────────────────────────────────

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

    def _save(self, entries, fernet=None):
        blob = (fernet or self._fernet).encrypt(json.dumps(entries, sort_keys=True).encode())
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, mode=0o700, exist_ok=True)
        tmp = f"{self.path}.{os.getpid()}.{time.time_ns()}.tmp"
        try:
            st = os.stat(self.path)
            keep_uid, keep_gid = st.st_uid, st.st_gid
        except OSError:
            keep_uid, keep_gid = None, None
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            if keep_uid is not None:
                os.fchown(fd, keep_uid, keep_gid)  # a root-run rotation must not orphan the vault file
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            fsync_dir(self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def get(self, tenant):
        """The tenant's entry (a copy), or None."""
        entry = self._load().get(tenant)
        return dict(entry) if entry else None

    def reveal(self, tenant):
        """The tenant's full entry (a copy, password included) for the gated retrieval CLI only.
        Raises VaultError when there is no entry."""
        entry = self._load().get(tenant)
        if not entry:
            raise VaultError(f"no vault entry for {tenant}")
        return dict(entry)

    def list_tenants(self):
        """Sorted tenant keys; no secrets."""
        return sorted(self._load())

    def rotate_key(self, new_key):
        """Re-encrypt every entry under `new_key`; the file is replaced atomically (fsync, 0600)
        under the same lock as reserve/mark. The old key stops working once this returns."""
        try:
            new_fernet = Fernet(new_key.encode() if isinstance(new_key, str) else new_key)
        except (ValueError, TypeError, AttributeError):
            raise VaultError("new vault key is not a valid Fernet key") from None
        with file_lock(self.path):
            entries = self._load()
            if os.path.exists(self.path):
                self._save(entries, fernet=new_fernet)
            self._fernet = new_fernet
            self.key = new_key
        log.info(f"vault | rotate_key | {self.path} | tenants={len(entries)}")

    def reserve(self, tenant, email, login_url):
        """Generate and persist a password for a new account on this tenant, then return it."""
        with file_lock(self.path):
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
        with file_lock(self.path):
            entries = self._load()
            if tenant not in entries:
                raise VaultError(f"no vault entry for {tenant}")
            entries[tenant]["status"] = status
            entries[tenant]["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save(entries)
