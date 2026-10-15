"""Content cache for resume v2 builds, keyed by everything that shapes the output.

The cache holds post-watermark content plus the requirements and ranked bullets that produced it, so a hit
skips every model call and only re-renders. Files are mode 0600 in a 0700 directory per key.
"""

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

DEFAULT_CACHE_ROOT = "/var/lib/jobagent24/cache/resume"
CACHE_FILE = "cache.json"
_KEY_RE = re.compile(r"[0-9a-f]{64}")


# ── Atomic writes ──────────────────────────────────────────────────────────────

def atomic_write_text(path, text, mode=0o600):
    """Write via a temp file in the same directory, fsync, then rename; fsync the directory too."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def atomic_write_json(path, obj, mode=0o600):
    atomic_write_text(path, json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False), mode)


# ── Cache ──────────────────────────────────────────────────────────────────────

def cache_key(master_md_sha, template_version, prompt_version, normalized_jd_sha, model_ids):
    """sha256 hex of the pipe-joined inputs. model_ids may be a string or a dict (dumped with sorted keys)."""
    if not isinstance(model_ids, str):
        model_ids = json.dumps(model_ids, sort_keys=True)
    raw = "|".join(str(p) for p in (master_md_sha, template_version, prompt_version, normalized_jd_sha, model_ids))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _dir(key, root=None):
    if not _KEY_RE.fullmatch(str(key)):
        raise ValueError("cache key must be 64 hex chars")
    base = root or os.environ.get("RESUME_V2_CACHE_DIR") or DEFAULT_CACHE_ROOT
    return Path(base) / key


def cache_dir(key, root=None):
    """The key's directory, created with mode 0700."""
    path = _dir(key, root)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def load_cached(key, root=None):
    """The stored payload, or None when absent or unreadable."""
    try:
        data = json.loads((_dir(key, root) / CACHE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_cached(key, payload, root=None):
    atomic_write_json(cache_dir(key, root) / CACHE_FILE, payload)
