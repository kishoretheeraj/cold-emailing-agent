#!/usr/bin/env python3
"""
Vault key lifecycle CLI (item 9): create, rotate and restore the Fernet key.

  --init     generate a key file (0440, parent dir 0750); refuses to overwrite without --force.
  --rotate   re-encrypt the vault under a fresh key. The old key file is kept as <key-file>.prev
             for 24 hours so a bad rotation can be undone with --restore.
  --restore  put <key-file>.prev back (only while it is under 24 hours old). If the vault was
             already rotated, it is re-encrypted back to the previous key first.

Crash safety during --rotate: .prev and .next (the new key) are on disk BEFORE the vault is
re-encrypted, and .next is renamed over the key file only afterwards, so at no point does the only
copy of a working key live in memory. A key file in the env style (`VAULT_KEY=...`, as in
/etc/job-agent/vault.env) stays in that style. Key material is never printed.
"""

import argparse
import base64
import os
import sys
import time

from credential_vault import KEY_ENV_NAME, Vault, VaultError, fsync_dir, file_lock, read_key_file

PREV_TTL_SECONDS = 24 * 3600
DEFAULT_VAULT_PATH = "/var/lib/job-agent/vault.bin"


# ── File helpers ───────────────────────────────────────────────────────────────

def _write_atomic(path, data, mode=0o440):
    os.makedirs(os.path.dirname(path) or ".", mode=0o750, exist_ok=True)
    try:
        st = os.stat(path)
        keep = (st.st_uid, st.st_gid, st.st_mode & 0o777)
    except OSError:
        keep = None
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        if keep is not None:
            os.fchown(fd, keep[0], keep[1])
            os.fchmod(fd, keep[2])  # a replaced key file keeps its documented 0440, not the umask
        else:
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        fsync_dir(path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _new_key():
    # Same format as Fernet.generate_key(): 32 random bytes, urlsafe base64.
    return base64.urlsafe_b64encode(os.urandom(32))


def _is_env_format(raw):
    for line in raw.decode(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if line.startswith(f"{KEY_ENV_NAME}="):
            return True
    return False


def _render_key(raw_old, new_key):
    if raw_old is None or not _is_env_format(raw_old):
        return new_key + b"\n"
    out, done = [], False
    for line in raw_old.decode().splitlines():
        stripped = line.strip()
        prefix = "export " if stripped.startswith("export ") else ""
        if not done and stripped[len(prefix):].startswith(f"{KEY_ENV_NAME}="):
            out.append(f"{prefix}{KEY_ENV_NAME}={new_key.decode()}")
            done = True
        else:
            out.append(line)
    return ("\n".join(out) + "\n").encode()


def _match_metadata(src, dst):
    """Copy ownership and mode from the installed key file onto `dst`, so a root-run rotation
    does not install the new key as root:root and lock out the jobagent reader."""
    st = os.stat(src)
    os.chown(dst, st.st_uid, st.st_gid)
    os.chmod(dst, st.st_mode & 0o777)
    fd = os.open(dst, os.O_RDONLY)
    try:
        os.fsync(fd)  # the inode changes above must be durable too
    finally:
        os.close(fd)


def _read_raw(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _old_key(args):
    if args.old_key_file:
        return read_key_file(args.old_key_file)
    env = os.environ.get(KEY_ENV_NAME)
    if env:
        return env.strip().encode()
    return read_key_file(args.key_file)


# ── Commands ───────────────────────────────────────────────────────────────────

def _init(args):
    if os.path.exists(args.key_file) and not args.force:
        print(f"refusing: {args.key_file} already exists (use --force to replace it)", file=sys.stderr)
        return 1
    key = _new_key()
    data = f"{KEY_ENV_NAME}={key.decode()}\n".encode() if args.env_format else key + b"\n"
    _write_atomic(args.key_file, data)
    print(f"wrote {args.key_file}")
    return 0


def _rotate(args):
    if not os.path.exists(args.vault):
        print(f"refusing: no vault at {args.vault}", file=sys.stderr)
        return 1
    # One rotation at a time: without this, two concurrent rotations can overwrite each
    # other's .next key file and permanently lose the working key.
    with file_lock(args.vault + ".rotlock"):
        return _rotate_locked(args)


def _rotate_locked(args):
    old_key = _old_key(args)
    vault = Vault(args.vault, old_key)
    vault.list_tenants()  # proves the old key opens the vault before anything is changed
    new_key = _new_key()
    raw_old = _read_raw(args.key_file)
    same_file = False
    if raw_old is not None:
        try:
            same_file = read_key_file(args.key_file) == old_key
        except VaultError:
            same_file = False
    prev_bytes = raw_old if same_file else old_key + b"\n"
    prev_path = f"{args.key_file}.prev"
    next_path = f"{args.key_file}.next"
    _write_atomic(prev_path, prev_bytes)
    _write_atomic(next_path, _render_key(raw_old if same_file else None, new_key))
    if raw_old is not None:
        _match_metadata(args.key_file, next_path)  # .next installs with the key file's identity
    vault.rotate_key(new_key)
    os.replace(next_path, args.key_file)
    fsync_dir(args.key_file)
    print(f"rotated; old key at {prev_path} (valid 24h)")
    return 0


def _current_key(args):
    if os.path.exists(args.key_file):
        return read_key_file(args.key_file)
    env = os.environ.get(KEY_ENV_NAME)
    if env:
        return env.strip().encode()
    raise VaultError("no current key available to roll the vault back")


def _restore(args):
    prev_path = f"{args.key_file}.prev"
    if not os.path.exists(prev_path):
        print(f"no previous key at {prev_path}", file=sys.stderr)
        return 1
    if time.time() - os.stat(prev_path).st_mtime >= PREV_TTL_SECONDS:
        print("previous key is older than 24h; not restoring", file=sys.stderr)
        return 1
    with file_lock(args.vault + ".rotlock"):
        return _restore_locked(args, prev_path)


def _restore_locked(args, prev_path):
    prev_key = read_key_file(prev_path)
    try:
        Vault(args.vault, prev_key).list_tenants()
    except VaultError:
        # The vault was rotated: re-encrypt it back to the previous key using the current one.
        # The current key may be the installed key file, or .next if rotation was interrupted
        # between re-encryption and key installation.
        candidates = []
        try:
            candidates.append(_current_key(args))
        except VaultError:
            pass
        candidates.append(_next_key(args))
        current = None
        for candidate in candidates:
            if candidate is None:
                continue
            try:
                Vault(args.vault, candidate).list_tenants()
                current = Vault(args.vault, candidate)
                break
            except VaultError:
                continue
        if current is None:
            raise VaultError("no working key found: cannot roll the vault back")
        current.rotate_key(prev_key)
    _write_atomic(args.key_file, _read_raw(prev_path))
    print("restored")
    return 0


def _next_key(args):
    next_path = f"{args.key_file}.next"
    if os.path.exists(next_path):
        try:
            return read_key_file(next_path)
        except VaultError:
            return None
    return None


# ── Entry point ────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(description="Create, rotate or restore the vault key.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--init", action="store_true")
    mode.add_argument("--rotate", action="store_true")
    mode.add_argument("--restore", action="store_true")
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--vault", default=os.environ.get("APPLY_VAULT_PATH", DEFAULT_VAULT_PATH))
    parser.add_argument("--old-key-file")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--env-format", action="store_true",
                        help="with --init: write VAULT_KEY=<key> (systemd EnvironmentFile style)")
    args = parser.parse_args(argv)
    try:
        if args.init:
            return _init(args)
        if args.rotate:
            return _rotate(args)
        return _restore(args)
    except VaultError as exc:
        print(f"vault error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
