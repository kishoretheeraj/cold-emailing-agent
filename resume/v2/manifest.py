"""Build manifest for a tailored resume: what was built, from what, and the PDF's hash.

The pipeline re-hashes the PDF against this manifest right before upload, so a swapped or edited file
never reaches an employer.
"""

import hashlib
import hmac
from datetime import datetime, timezone

from resume.v2.cache import atomic_write_json

MANIFEST_VERSION = "resume.v2.manifest/1"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(path, data):
    """Write data plus manifest_version and ts atomically (0600). Returns the dict that was written."""
    out = {**data, "manifest_version": MANIFEST_VERSION,
           "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    atomic_write_json(path, out)
    return out


def verify_pdf_hash(manifest, pdf_path):
    """True only when the file exists and its sha256 equals manifest["pdf_sha256"]."""
    expected = manifest.get("pdf_sha256") if isinstance(manifest, dict) else None
    if not isinstance(expected, str) or not expected:
        return False
    try:
        actual = sha256_file(pdf_path)
    except OSError:
        return False
    return hmac.compare_digest(actual, expected)
