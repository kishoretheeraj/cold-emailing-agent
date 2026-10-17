"""credential_vault.py: per-tenant passwords, generated, encrypted at rest, never logged."""

import logging
import os
import stat
import string

import pytest
from cryptography.fernet import Fernet

import credential_vault


@pytest.fixture
def vault(tmp_path):
    return credential_vault.Vault(str(tmp_path / "vault.bin"), Fernet.generate_key().decode())


def test_generated_passwords_meet_common_ats_rules():
    for _ in range(200):
        pw = credential_vault.generate_password()
        assert len(pw) == 24
        assert any(c in string.ascii_lowercase for c in pw)
        assert any(c in string.ascii_uppercase for c in pw)
        assert any(c in string.digits for c in pw)
        assert any(c in credential_vault.SPECIALS for c in pw)
        # Characters some tenants reject or that break shell/URL quoting are never used.
        assert not set(pw) & set(" \"'\\`<>&;|")


def test_generated_passwords_differ():
    assert len({credential_vault.generate_password() for _ in range(100)}) == 100


def test_reserve_persists_before_returning(vault, tmp_path):
    entry = vault.reserve("acme.wd5.myworkdayjobs.com", "me@example.com",
                          "https://acme.wd5.myworkdayjobs.com/External")
    assert entry["status"] == "reserved"
    # a fresh Vault object (as after a crash) sees the same password
    again = credential_vault.Vault(vault.path, vault.key).get("acme.wd5.myworkdayjobs.com")
    assert again["password"] == entry["password"]
    assert again["email"] == "me@example.com"


def test_reserve_never_replaces_an_existing_entry(vault):
    first = vault.reserve("t", "me@example.com", "u")
    with pytest.raises(credential_vault.VaultError, match="already has"):
        vault.reserve("t", "me@example.com", "u")
    assert vault.get("t")["password"] == first["password"]


def test_mark_records_the_account_status(vault):
    vault.reserve("t", "me@example.com", "u")
    vault.mark("t", "active")
    assert vault.get("t")["status"] == "active"
    with pytest.raises(credential_vault.VaultError):
        vault.mark("t", "deleted")
    with pytest.raises(credential_vault.VaultError):
        vault.mark("missing", "active")


def test_the_file_is_encrypted_and_private(vault):
    entry = vault.reserve("acme.wd5.myworkdayjobs.com", "me@example.com", "u")
    raw = open(vault.path, "rb").read()
    assert entry["password"].encode() not in raw
    assert b"acme" not in raw and b"me@example.com" not in raw
    assert stat.S_IMODE(os.stat(vault.path).st_mode) == 0o600


def test_a_wrong_key_fails_loudly_instead_of_returning_nothing(vault):
    vault.reserve("t", "me@example.com", "u")
    other = credential_vault.Vault(vault.path, Fernet.generate_key().decode())
    with pytest.raises(credential_vault.VaultError, match="decrypt"):
        other.get("t")


def test_an_invalid_key_is_rejected(tmp_path):
    with pytest.raises(credential_vault.VaultError):
        credential_vault.Vault(str(tmp_path / "v"), "not-a-fernet-key")
    with pytest.raises(credential_vault.VaultError):
        credential_vault.Vault(str(tmp_path / "v"), "")


def test_missing_file_is_an_empty_vault(vault):
    assert vault.get("anything") is None


def test_passwords_never_reach_logs_or_reprs(vault, caplog):
    caplog.set_level(logging.DEBUG)
    entry = vault.reserve("t", "me@example.com", "u")
    vault.mark("t", "active")
    vault.get("t")
    assert entry["password"] not in caplog.text
    assert entry["password"] not in repr(vault)
    assert vault.key not in repr(vault)


def test_concurrent_reserves_from_two_processes_never_lose_an_entry(tmp_path):
    # Audit 2026-10-14 F6: reserve() is load-modify-save. Without a lock, a second writer that
    # loads before the first one saves overwrites it, and the first tenant's password (already
    # typed into a signup form) is gone. Two Vault objects stand in for two processes.
    import threading

    key = Fernet.generate_key().decode()
    path = str(tmp_path / "vault.bin")
    first, second = credential_vault.Vault(path, key), credential_vault.Vault(path, key)
    loaded, go = threading.Event(), threading.Event()
    original_load = first._load

    def paused_load():
        entries = original_load()
        loaded.set()
        go.wait(5)
        return entries

    first._load = paused_load
    a = threading.Thread(target=first.reserve, args=("tenant-a", "a@example.com", "https://a"))
    b = threading.Thread(target=second.reserve, args=("tenant-b", "b@example.com", "https://b"))
    a.start()
    assert loaded.wait(5)
    b.start()
    b.join(2)  # without a lock b finishes here, between a's load and a's save
    go.set()
    a.join(5)
    b.join(5)
    reread = credential_vault.Vault(path, key)
    assert reread.get("tenant-a") and reread.get("tenant-b")
