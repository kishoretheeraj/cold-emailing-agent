import logging
import os
import threading

import pytest

from answer_bank_vault import AnswerBankVault, generate_key
from credential_vault import VaultError


@pytest.fixture
def key():
    return generate_key()


@pytest.fixture
def vault(tmp_path, key):
    return AnswerBankVault(str(tmp_path / "answers.enc"), key)


def test_round_trip(vault):
    vault.set("phone", "+1 555 0100")
    vault.set("sponsorship", "Yes")
    assert vault.get("phone") == "+1 555 0100"
    assert vault.get("sponsorship") == "Yes"


def test_round_trip_survives_new_instance(tmp_path, key):
    path = str(tmp_path / "a.enc")
    AnswerBankVault(path, key).set("email", "a@example.com")
    assert AnswerBankVault(path, key).get("email") == "a@example.com"


def test_get_missing_key_returns_none(vault):
    assert vault.get("nope") is None
    vault.set("a", "1")
    assert vault.get("nope") is None


def test_overwrite_and_delete(vault):
    vault.set("a", "1")
    vault.set("a", "2")
    assert vault.get("a") == "2"
    vault.delete("a")
    assert vault.get("a") is None
    vault.delete("a")  # missing key is a no-op


def test_list_keys_never_exposes_values(vault):
    vault.set("work_auth", "SECRET-VALUE-1")
    vault.set("phone", "SECRET-VALUE-2")
    keys = vault.list_keys()
    assert keys == ["phone", "work_auth"]
    assert "SECRET-VALUE" not in repr(keys)


def test_list_keys_empty_when_no_file(vault):
    assert vault.list_keys() == []


@pytest.mark.parametrize("bad", ["", "not-a-key", None, 123, "short"])
def test_bad_key_raises_vault_error(tmp_path, bad):
    with pytest.raises(VaultError):
        AnswerBankVault(str(tmp_path / "a.enc"), bad)


def test_wrong_key_cannot_read(tmp_path, key):
    path = str(tmp_path / "a.enc")
    AnswerBankVault(path, key).set("a", "1")
    with pytest.raises(VaultError):
        AnswerBankVault(path, generate_key()).get("a")


def test_file_is_ciphertext_and_mode_600(vault):
    vault.set("phone", "PLAINTEXT-PHONE")
    with open(vault.path, "rb") as f:
        assert b"PLAINTEXT-PHONE" not in f.read()
    assert os.stat(vault.path).st_mode & 0o777 == 0o600


def test_rewrite_keeps_mode_600_and_leaves_no_tmp(vault):
    vault.set("a", "1")
    vault.set("b", "2")
    assert os.stat(vault.path).st_mode & 0o777 == 0o600
    assert not os.path.exists(vault.path + ".tmp")


def test_repr_and_logs_never_contain_values_or_key(vault, key, caplog):
    caplog.set_level(logging.INFO)
    vault.set("phone", "LEAKY-VALUE")
    vault.delete("phone")
    assert "LEAKY-VALUE" not in caplog.text
    assert key not in repr(vault)
    assert "phone" in caplog.text


def test_set_rejects_bad_arguments(vault):
    with pytest.raises(VaultError):
        vault.set("", "x")
    with pytest.raises(VaultError):
        vault.set("a", 5)


def test_export_import_round_trip(tmp_path, key):
    src = AnswerBankVault(str(tmp_path / "src.enc"), key)
    src.set("a", "1")
    src.set("b", "2")
    blob = src.export_encrypted()
    dst = AnswerBankVault(str(tmp_path / "dst.enc"), key)
    dst.import_encrypted(blob)
    assert dst.get("a") == "1" and dst.list_keys() == ["a", "b"]
    assert os.stat(dst.path).st_mode & 0o777 == 0o600


def test_export_with_no_file_is_importable_empty_bank(vault, tmp_path, key):
    blob = vault.export_encrypted()
    other = AnswerBankVault(str(tmp_path / "o.enc"), key)
    other.import_encrypted(blob)
    assert other.list_keys() == []


def test_import_rejects_foreign_or_garbage_data_and_keeps_existing(vault, tmp_path):
    vault.set("a", "1")
    foreign = AnswerBankVault(str(tmp_path / "f.enc"), generate_key())
    foreign.set("x", "y")
    with pytest.raises(VaultError):
        vault.import_encrypted(foreign.export_encrypted())
    with pytest.raises(VaultError):
        vault.import_encrypted(b"garbage")
    assert vault.get("a") == "1"


def test_concurrent_sets_do_not_lose_writes(vault):
    def worker(i):
        vault.set(f"k{i}", f"v{i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert vault.list_keys() == sorted(f"k{i}" for i in range(20))
    assert all(vault.get(f"k{i}") == f"v{i}" for i in range(20))
