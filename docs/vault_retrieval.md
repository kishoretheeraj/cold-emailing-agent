# Vault retrieval runbook

The credential vault (`credential_vault.py`) is one Fernet-encrypted file, mode 0600. Reading a
password back is possible only through `vault_reveal.py`: SSH session, a TTY, and a TOTP code. Every
attempt is written to an audit log. No secret goes to logs, chat or URLs.

Paths (unchanged): vault `APPLY_VAULT_PATH` (default `/var/lib/job-agent/vault.bin`), key from env
`VAULT_KEY` or the key file (`VAULT_KEY_FILE`, default `/etc/job-agent/vault.env`, either a bare key
or a `VAULT_KEY=...` line).

## 1. Install (once)

1. Copy `credential_vault.py`, `vault_audit.py`, `vault_reveal.py`, `vault_rotate.py` to
   `/opt/jobagent24/`; `chmod 0755 /opt/jobagent24/vault_reveal.py`. `pip install pyotp cryptography`.
2. The reveal script runs as `jobagent`, so that user must be able to read the key file:
   `chgrp jobagent /etc/job-agent/vault.env && chmod 0440 /etc/job-agent/vault.env`.
3. Audit log directory: `install -d -m 0750 -o jobagent -g jobagent /var/log/jobagent24`.
   The log is `/var/log/jobagent24/vault_audit.jsonl` (override with `VAULT_AUDIT_LOG`), mode 0640.
4. Sudoers: `visudo -cf deploy/beelink/sudoers.d/jobagent24-vault`, then
   `install -m 0440 -o root -g root deploy/beelink/sudoers.d/jobagent24-vault /etc/sudoers.d/jobagent24-vault`.

## 2. TOTP secret

Generate on a trusted machine: `python3 -c "import pyotp; print(pyotp.random_base32())"`. Add it to
your authenticator app (issuer `jobagent24-vault`), then store it on the Beelink:

`echo '<secret>' | sudo -u jobagent tee /etc/jobagent24/totp.secret >/dev/null && sudo chmod 0400 /etc/jobagent24/totp.secret`

(Directory `/etc/jobagent24` is 0750, owned by jobagent.) Do not paste the secret anywhere else. A
missing or unreadable secret file means every attempt fails with "authentication failed".

## 3. Retrieve a password

From the Mac (the `-t` is required, there is no TTY otherwise):

`ssh -t kishore@beelink sudo -u jobagent /opt/jobagent24/vault_reveal.py <tenant>`

Enter the TOTP code. The entry shows on the alternate screen for 30 seconds (`--ttl`, max 300) and is
erased; nothing remains in scrollback. Exit codes: 0 ok, 2 not a TTY, 3 authentication failed, 4 vault
error (for example no such tenant), 5 audit write failed (the entry is never shown without a
persisted audit line).

The TTY gate stops casual piping, not a determined operator recording their own PTY. The real
protection is the TOTP code plus the audit trail; the alternate screen only keeps the secret out of
scrollback on a normal terminal.

Read the audit log: `sudo tail -n 20 /var/log/jobagent24/vault_audit.jsonl`. Each line has `ts`, `action`,
`tenant`, `unix_user`, `ssh_client_ip`, `result` (ok/denied/error), `method`. Review denied lines.

## 4. Key lifecycle (`vault_rotate.py`)

Run as root (or as the user that owns the key file), with the apply units stopped so nothing writes
mid-rotation.

- Create (new box only): `vault_rotate.py --init --key-file /etc/job-agent/vault.key`
  (`--env-format` writes `VAULT_KEY=...` for a systemd EnvironmentFile). Refuses to overwrite without `--force`.
- Rotate: `vault_rotate.py --rotate --key-file /etc/job-agent/vault.env --vault /var/lib/job-agent/vault.bin`.
  The old key is read from `--old-key-file`, else `$VAULT_KEY`, else the key file itself. The vault is
  re-encrypted, the new key is written (0440, same file style), and the old key is kept as `<key-file>.prev`.
  Then restart the apply units and run one reveal to confirm.
- Restore, within 24 hours: `vault_rotate.py --restore --key-file ... --vault ...`. If the vault was
  already rotated it is re-encrypted back to the previous key. After 24 hours it refuses; delete `.prev`
  once you are sure the new key works.
- If a rotation is interrupted, `<key-file>.next` holds the new key and `<key-file>.prev` the old one.
  Whichever opens the vault is the right one: `.next` if the vault was already re-encrypted.

## 5. If the key is lost

1. Check `<key-file>.prev` and `.next`, backups of `/etc/job-agent/`, and the environment of a running
   apply unit (`systemctl show -p Environment` will not show EnvironmentFile values; read
   `/proc/<pid>/environ` as root of a live apply process if one is still running).
2. Within 24 hours of a rotation: `--restore` as above.
3. Otherwise the vault cannot be decrypted (Fernet is authenticated encryption; there is no backdoor).
   Move the old file aside, `--init` a new key, and recover accounts through each site's "forgot
   password" flow. Entries are recreated by the apply worker as accounts are reserved again.

## 6. No plaintext credentials

`credential_vault.assert_no_plaintext_credentials()` raises if `ats_accounts.json` exists at any legacy
path (`VAULT_PATHS_AUDIT`). Call it from a health check or run:
`python3 -c "import credential_vault as c; c.assert_no_plaintext_credentials(); print('clean')"`.
