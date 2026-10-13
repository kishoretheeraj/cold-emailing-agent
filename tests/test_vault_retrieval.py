"""Secure vault retrieval (item 9): TTY and TOTP gates, audit log, rotation, file modes, no secret in logs."""

import io
import json
import logging
import os
import stat
import sys
import time
from types import SimpleNamespace

import pyotp
import pytest
from cryptography.fernet import Fernet

import credential_vault
import vault_audit
import vault_reveal
import vault_rotate

TENANT = "acme.wd5.myworkdayjobs.com"


class _FakeStdout(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def _audit_lines(env):
    with open(env.audit_path) as f:
        return [json.loads(line) for line in f.read().splitlines()]


@pytest.fixture
def env(tmp_path, monkeypatch):
    old_umask = os.umask(0o022)
    key = Fernet.generate_key().decode()
    vault_path = str(tmp_path / "vault.bin")
    vault = credential_vault.Vault(vault_path, key)
    entry = vault.reserve(TENANT, "me@example.com", "https://acme.example/login")
    secret = pyotp.random_base32()
    secret_file = tmp_path / "totp.secret"
    secret_file.write_text(secret + "\n")
    os.chmod(secret_file, 0o400)
    audit_path = str(tmp_path / "logs" / "vault_audit.jsonl")
    monkeypatch.setenv("APPLY_VAULT_PATH", vault_path)
    monkeypatch.setenv("VAULT_KEY", key)
    monkeypatch.delenv("VAULT_KEY_FILE", raising=False)
    monkeypatch.setenv("TOTP_SECRET_FILE", str(secret_file))
    monkeypatch.setenv("VAULT_AUDIT_LOG", audit_path)
    monkeypatch.setenv("VAULT_TOTP_THROTTLE_FILE", str(tmp_path / "throttle.json"))
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.5 51515 10.0.0.1 22")
    monkeypatch.setattr(vault_reveal.time, "sleep", lambda s: None)
    yield SimpleNamespace(vault=vault, key=key, entry=entry, secret=secret, audit_path=audit_path,
                          vault_path=vault_path)
    os.umask(old_umask)


def _reveal(monkeypatch, tenant, tty, code=None):
    out = _FakeStdout(tty)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(vault_reveal.getpass, "getpass", lambda prompt="": code)
    with pytest.raises(SystemExit) as exc:
        vault_reveal.main([tenant])
    return exc.value.code, out.getvalue()


# ── Reveal gates ───────────────────────────────────────────────────────────────

def test_reveal_refused_without_a_tty(env, monkeypatch):
    def no_prompt(prompt=""):
        raise AssertionError("must not prompt without a TTY")

    out = _FakeStdout(False)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(vault_reveal.getpass, "getpass", no_prompt)
    with pytest.raises(SystemExit) as exc:
        vault_reveal.main([TENANT])
    assert exc.value.code == 2
    assert env.entry["password"] not in out.getvalue()
    lines = _audit_lines(env)
    assert len(lines) == 1
    assert lines[0]["result"] == "denied" and lines[0]["method"] == "tty"
    assert lines[0]["tenant"] == TENANT
    assert lines[0]["ssh_client_ip"] == "10.0.0.5"
    assert env.entry["password"] not in open(env.audit_path).read()


def test_wrong_totp_is_denied(env, monkeypatch):
    totp = pyotp.TOTP(env.secret)
    now = time.time()
    valid = {totp.at(now + 30 * i) for i in (-1, 0, 1)}
    wrong = next(c for c in ("000000", "111111", "222222") if c not in valid)
    code, out = _reveal(monkeypatch, TENANT, True, wrong)
    assert code == 3
    assert env.entry["password"] not in out
    lines = _audit_lines(env)
    assert [l["result"] for l in lines] == ["denied"]
    assert env.entry["password"] not in open(env.audit_path).read()


def test_missing_totp_secret_file_is_denied(env, monkeypatch):
    monkeypatch.setenv("TOTP_SECRET_FILE", os.path.join(os.path.dirname(env.vault_path), "nope"))
    code, out = _reveal(monkeypatch, TENANT, True, "123456")
    assert code == 3
    assert env.entry["password"] not in out
    assert _audit_lines(env)[0]["result"] == "denied"


def test_correct_totp_shows_entry_and_audits_ok(env, monkeypatch):
    code, out = _reveal(monkeypatch, TENANT, True, pyotp.TOTP(env.secret).now())
    assert code == 0
    assert "me@example.com" in out and TENANT in out
    assert out.count(vault_reveal._ALT_ON) == 1
    assert out.endswith(vault_reveal._ALT_OFF)
    lines = _audit_lines(env)
    assert [(l["action"], l["result"], l["method"]) for l in lines] == [("reveal", "ok", "token")]
    assert env.entry["password"] not in open(env.audit_path).read()


def test_alternate_screen_always_exits(env, monkeypatch):
    def interrupted(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(vault_reveal.time, "sleep", interrupted)
    out = _FakeStdout(True)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(vault_reveal.getpass, "getpass", lambda prompt="": pyotp.TOTP(env.secret).now())
    with pytest.raises(KeyboardInterrupt):
        vault_reveal.main([TENANT])
    assert out.getvalue().endswith(vault_reveal._ALT_OFF)


def test_unknown_tenant_is_a_vault_error(env, monkeypatch):
    code, out = _reveal(monkeypatch, "missing.example.com", True, pyotp.TOTP(env.secret).now())
    assert code == 4
    assert env.entry["password"] not in out
    assert _audit_lines(env)[0]["result"] == "error"


def test_password_never_reaches_log_records(env, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    code, _ = _reveal(monkeypatch, TENANT, True, pyotp.TOTP(env.secret).now())
    assert code == 0
    assert env.entry["password"] not in caplog.text
    for record in caplog.records:
        assert env.entry["password"] not in record.getMessage()


# ── Rotation ───────────────────────────────────────────────────────────────────

def test_rotate_key_round_trip(env):
    new_key = Fernet.generate_key().decode()
    env.vault.get(TENANT)  # decrypts with the old key
    env.vault.rotate_key(new_key)
    with pytest.raises(credential_vault.VaultError):
        credential_vault.Vault(env.vault_path, env.key).get(TENANT)
    again = credential_vault.Vault(env.vault_path, new_key).get(TENANT)
    assert again["password"] == env.entry["password"]
    assert _mode(env.vault_path) == 0o600
    assert credential_vault.Vault(env.vault_path, new_key).list_tenants() == [TENANT]


def test_reveal_and_list_tenants(env):
    assert env.vault.reveal(TENANT)["password"] == env.entry["password"]
    with pytest.raises(credential_vault.VaultError, match="no vault entry"):
        env.vault.reveal("missing")


def test_cli_init_rotate_restore(env, tmp_path, monkeypatch, capsys):
    key_file = str(tmp_path / "keys" / "vault.key")
    assert vault_rotate.main(["--init", "--key-file", key_file]) == 0
    printed = capsys.readouterr().out
    old = credential_vault.read_key_file(key_file)
    assert printed.strip() == f"wrote {key_file}"
    assert old.decode() not in printed
    assert _mode(key_file) == 0o440
    assert vault_rotate.main(["--init", "--key-file", key_file]) == 1  # refuses without --force

    vault = credential_vault.Vault(env.vault_path, env.key)
    vault.rotate_key(old)  # re-encrypt under the CLI-created key so it opens the fixture vault
    monkeypatch.setenv("VAULT_KEY", old.decode())
    assert vault_rotate.main(["--rotate", "--key-file", key_file, "--vault", env.vault_path]) == 0
    printed = capsys.readouterr().out
    new = credential_vault.read_key_file(key_file)
    assert printed.strip() == f"rotated; old key at {key_file}.prev (valid 24h)"
    assert new != old and new.decode() not in printed
    assert credential_vault.read_key_file(key_file + ".prev") == old
    assert _mode(key_file) == 0o440 and _mode(key_file + ".prev") == 0o440
    assert credential_vault.Vault(env.vault_path, new).get(TENANT)["email"] == "me@example.com"
    with pytest.raises(credential_vault.VaultError):
        credential_vault.Vault(env.vault_path, old).get(TENANT)

    assert vault_rotate.main(["--restore", "--key-file", key_file, "--vault", env.vault_path]) == 0
    assert capsys.readouterr().out.strip() == "restored"
    assert credential_vault.read_key_file(key_file) == old
    assert credential_vault.Vault(env.vault_path, old).get(TENANT)["email"] == "me@example.com"


def test_restore_refuses_an_expired_previous_key(env, tmp_path, monkeypatch):
    key_file = str(tmp_path / "keys" / "vault.key")
    vault_rotate.main(["--init", "--key-file", key_file])
    old = credential_vault.read_key_file(key_file)
    credential_vault.Vault(env.vault_path, env.key).rotate_key(old)
    monkeypatch.setenv("VAULT_KEY", old.decode())
    vault_rotate.main(["--rotate", "--key-file", key_file, "--vault", env.vault_path])
    current = credential_vault.read_key_file(key_file)
    stale = time.time() - 2 * 24 * 3600
    os.utime(key_file + ".prev", (stale, stale))
    assert vault_rotate.main(["--restore", "--key-file", key_file, "--vault", env.vault_path]) == 1
    assert credential_vault.read_key_file(key_file) == current


def test_env_style_key_file_stays_env_style(env, tmp_path, monkeypatch):
    key_file = str(tmp_path / "keys" / "vault.env")
    vault_rotate.main(["--init", "--env-format", "--key-file", key_file])
    old = credential_vault.read_key_file(key_file)
    credential_vault.Vault(env.vault_path, env.key).rotate_key(old)
    monkeypatch.delenv("VAULT_KEY", raising=False)
    assert vault_rotate.main(["--rotate", "--key-file", key_file, "--vault", env.vault_path]) == 0
    text = open(key_file).read()
    assert text.startswith("VAULT_KEY=") and text.count("\n") == 1
    assert credential_vault.read_key_file(key_file) != old


def test_read_key_file_errors(tmp_path):
    with pytest.raises(credential_vault.VaultError):
        credential_vault.read_key_file(str(tmp_path / "missing"))
    empty = tmp_path / "empty"
    empty.write_text("  \n")
    os.chmod(empty, 0o400)
    with pytest.raises(credential_vault.VaultError, match="empty"):
        credential_vault.read_key_file(str(empty))
    bare = tmp_path / "bare"
    bare.write_text(" abc= \n")
    os.chmod(bare, 0o400)
    assert credential_vault.read_key_file(str(bare)) == b"abc="


def test_read_key_file_refuses_insecure_permissions(tmp_path):
    world_readable = tmp_path / "world"
    world_readable.write_text(Fernet.generate_key().decode())
    os.chmod(world_readable, 0o644)
    with pytest.raises(credential_vault.VaultError, match="insecure permissions"):
        credential_vault.read_key_file(str(world_readable))
    group_ok = tmp_path / "group"
    group_ok.write_text(Fernet.generate_key().decode())
    os.chmod(group_ok, 0o440)  # the documented root:jobagent mode stays accepted
    assert credential_vault.read_key_file(str(group_ok))


# ── Plaintext audit and file modes ─────────────────────────────────────────────

def test_assert_no_plaintext_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(credential_vault, "VAULT_PATHS_AUDIT", [str(tmp_path / "ats_accounts.json")])
    credential_vault.assert_no_plaintext_credentials()
    legacy = tmp_path / "ats_accounts.json"
    legacy.write_text("{}")
    with pytest.raises(credential_vault.VaultError, match="ats_accounts.json"):
        credential_vault.assert_no_plaintext_credentials()
    legacy.unlink()
    extra = tmp_path / "other.json"
    extra.write_text("{}")
    with pytest.raises(credential_vault.VaultError, match="other.json"):
        credential_vault.assert_no_plaintext_credentials(extra_paths=[str(extra)])


def test_default_audit_paths_are_the_documented_ones():
    assert credential_vault.VAULT_PATHS_AUDIT == [
        "/etc/job-agent/ats_accounts.json",
        "/opt/jobagent24/ats_accounts.json",
        "/var/lib/job-agent/ats_accounts.json",
    ]


def test_file_modes(env, tmp_path):
    key_file = str(tmp_path / "keys" / "vault.key")
    vault_rotate.main(["--init", "--key-file", key_file])
    assert _mode(key_file) == 0o440
    assert _mode(env.vault_path) == 0o600
    vault_audit.audit("reveal", TENANT, "ok", "token", log_path=env.audit_path)
    assert _mode(env.audit_path) == 0o640
    assert _mode(os.path.dirname(env.audit_path)) == 0o750


def test_audit_never_raises_and_has_no_secret_fields(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("x")
    assert vault_audit.audit("reveal", TENANT, "ok", "token",
                             log_path=str(blocked / "nested" / "log")) is False  # parent is a file
    path = str(tmp_path / "a.jsonl")
    assert vault_audit.audit("reveal", TENANT, "bogus", "bogus", log_path=path) is True
    record = json.loads(open(path).read())
    assert set(record) == {"ts", "action", "tenant", "unix_user", "ssh_client_ip", "result", "method"}
    assert record["result"] == "error" and record["method"] == "tty"


def test_display_is_refused_when_the_audit_write_fails(env, monkeypatch):
    monkeypatch.setattr(vault_audit, "audit", lambda *a, **k: False)
    code, out = _reveal(monkeypatch, TENANT, True, pyotp.TOTP(env.secret).now())
    assert code == 5
    assert env.entry["password"] not in out


def test_insecure_totp_secret_permissions_are_denied(env, monkeypatch, tmp_path):
    loose = tmp_path / "loose.secret"
    loose.write_text(pyotp.random_base32())
    os.chmod(loose, 0o644)
    monkeypatch.setenv("TOTP_SECRET_FILE", str(loose))
    code, out = _reveal(monkeypatch, TENANT, True, "123456")
    assert code == 3
    assert env.entry["password"] not in out


def test_control_characters_are_stripped_from_display():
    evil = "acme\x1b[?1049l\x1b[2J.example.com"
    cleaned = vault_reveal._clean(evil)
    assert "\x1b" not in cleaned and cleaned == "acme[?1049l[2J.example.com"
    assert vault_reveal._clean("plain.tenant.com") == "plain.tenant.com"


def test_totp_throttle_blocks_after_max_failures(env, monkeypatch, capsys):
    assert vault_reveal._throttle_consume() is not None
    for _ in range(9):
        assert vault_reveal._throttle_consume() is not None
    assert vault_reveal._throttle_consume() is None  # 11th concurrent session is refused
    out = _FakeStdout(True)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(vault_reveal.getpass, "getpass", lambda prompt="": "000000")
    with pytest.raises(SystemExit) as exc:
        vault_reveal.main([TENANT])
    assert exc.value.code == 3
    assert "too many attempts" in capsys.readouterr().err


def test_throttle_refund_on_success(env, monkeypatch):
    assert vault_reveal._throttle_consume() is not None
    assert vault_reveal._throttle_consume() is not None
    vault_reveal._throttle_refund()
    state = vault_reveal._throttle_state(vault_reveal._throttle_path())
    assert len(state) == 1  # one consumed slot refunded, one failure kept


def test_throttle_consume_fails_closed_on_broken_state(env, monkeypatch, tmp_path):
    broken = tmp_path / "dir_as_file"
    broken.write_text("x")
    monkeypatch.setenv("VAULT_TOTP_THROTTLE_FILE", str(broken / "throttle.json"))
    assert vault_reveal._throttle_consume() is None


def test_throttle_consume_denies_on_corrupt_state(env, monkeypatch, tmp_path):
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not valid json")
    monkeypatch.setenv("VAULT_TOTP_THROTTLE_FILE", str(corrupt))
    assert vault_reveal._throttle_consume() is None  # corrupt is not silently treated as empty
    wrong_shape = tmp_path / "shape.json"
    wrong_shape.write_text("[1, 2, 3]")
    monkeypatch.setenv("VAULT_TOTP_THROTTLE_FILE", str(wrong_shape))
    assert vault_reveal._throttle_consume() is None
    for name, body in (("empty_obj", "{}"),
                       ("non_list", '{"failures": "corrupt"}'),
                       ("non_numeric", '{"failures": ["bad", null]}'),
                       ("non_finite", '{"failures": [1e999]}')):
        p = tmp_path / f"{name}.json"
        p.write_text(body)
        monkeypatch.setenv("VAULT_TOTP_THROTTLE_FILE", str(p))
        assert vault_reveal._throttle_consume() is None, name


def test_expired_slot_is_rejected_before_verification(env, monkeypatch, capsys):
    monkeypatch.setattr(vault_reveal, "_slot_expired", lambda slot: True)
    code, out = _reveal(monkeypatch, TENANT, True, pyotp.TOTP(env.secret).now())
    assert code == 3
    assert "session expired" in capsys.readouterr().err
    assert env.entry["password"] not in out


def test_throttle_window_expiry_prunes_old_failures(env, tmp_path, monkeypatch):
    throttle = str(tmp_path / "old_throttle.json")
    monkeypatch.setenv("VAULT_TOTP_THROTTLE_FILE", throttle)
    old = time.time() - 700
    with open(throttle, "w") as f:
        json.dump({"failures": [old] * 10}, f)
    assert vault_reveal._throttle_consume() is not None  # expired failures do not count
