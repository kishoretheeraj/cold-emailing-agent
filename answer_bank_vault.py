"""Encrypted answer bank for the 24/7 job application system.

Stores candidate answers (name, email, phone, LinkedIn, work auth, sponsorship, etc.)
encrypted at rest using Fernet (AES-128-CBC + HMAC).

VAULT_KEY from environment (same as credential_vault.py).
File mode 600. Values never appear in logs, reprs or exceptions; only keys may be named.
"""

import json
import logging
import os
import threading

from cryptography.fernet import Fernet, InvalidToken

from credential_vault import VaultError

log = logging.getLogger(__name__)


def generate_key() -> str:
    """A fresh Fernet key for initial setup (store it as VAULT_KEY, never in git)."""
    return Fernet.generate_key().decode()


class AnswerBankVault:
    """Fernet-encrypted {question_key: value} store backed by one 0600 file."""

    def __init__(self, path: str, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except (ValueError, TypeError, AttributeError):
            raise VaultError("VAULT_KEY is missing or not a valid Fernet key") from None
        self.path = path
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"AnswerBankVault(path={self.path!r})"

    # ── Storage ────────────────────────────────────────────────────────────────

    def _decode(self, blob: bytes) -> dict:
        try:
            data = json.loads(self._fernet.decrypt(blob))
        except InvalidToken:
            raise VaultError("could not decrypt the answer bank: wrong VAULT_KEY or a damaged file") from None
        except ValueError:
            raise VaultError("answer bank decrypted but is not valid JSON") from None
        if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
            raise VaultError("answer bank has an unexpected shape")
        return data

    def _load(self) -> dict:
        try:
            with open(self.path, "rb") as f:
                blob = f.read()
        except FileNotFoundError:
            return {}
        return self._decode(blob)

    def _write_blob(self, blob: bytes) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, mode=0o700, exist_ok=True)
        tmp = f"{self.path}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def _save(self, entries: dict) -> None:
        self._write_blob(self._fernet.encrypt(json.dumps(entries, sort_keys=True).encode()))

    # ── Public API ─────────────────────────────────────────────────────────────

    def get(self, question_key: str) -> str | None:
        """The stored answer, or None when the key is absent."""
        with self._lock:
            return self._load().get(question_key)

    def set(self, question_key: str, value: str) -> None:
        """Encrypt and persist one answer (file mode 600). Only the key is logged."""
        if not isinstance(question_key, str) or not question_key:
            raise VaultError("question_key must be a non-empty string")
        if not isinstance(value, str):
            raise VaultError("value must be a string")
        with self._lock:
            entries = self._load()
            entries[question_key] = value
            self._save(entries)
        log.info(f"[ANSWER-BANK] set | {question_key}")

    def delete(self, question_key: str) -> None:
        """Remove one answer; a missing key is a no-op."""
        with self._lock:
            entries = self._load()
            if question_key not in entries:
                return
            del entries[question_key]
            self._save(entries)
        log.info(f"[ANSWER-BANK] delete | {question_key}")

    def list_keys(self) -> list[str]:
        """Sorted question keys only, never values."""
        with self._lock:
            return sorted(self._load())

    def export_encrypted(self) -> bytes:
        """The encrypted blob for backup or sync (useless without VAULT_KEY)."""
        with self._lock:
            try:
                with open(self.path, "rb") as f:
                    return f.read()
            except FileNotFoundError:
                return self._fernet.encrypt(b"{}")

    def import_encrypted(self, data: bytes) -> None:
        """Replace the bank with an exported blob; refuses data this key cannot decrypt."""
        with self._lock:
            self._decode(data)
            self._write_blob(data)
        log.info("[ANSWER-BANK] import | replaced")
