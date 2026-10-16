"""
Password generation for ATS accounts.

Every password has at least two uppercase, two lowercase, two digit and two special characters
and, when an email is given, contains no run of three or more characters of its local part.
Generated with `secrets`; never logged by anything in this package.
"""

import secrets
import string

SPECIALS = "!@#$%^&*-_+="

_ALPHABET = string.ascii_letters + string.digits + SPECIALS
_MIN_PER_CLASS = 2
_MAX_TRIES = 10000
_MIN_LENGTH = 4 * _MIN_PER_CLASS


def _classes_ok(password):
    return (sum(c in string.ascii_uppercase for c in password) >= _MIN_PER_CLASS
            and sum(c in string.ascii_lowercase for c in password) >= _MIN_PER_CLASS
            and sum(c in string.digits for c in password) >= _MIN_PER_CLASS
            and sum(c in SPECIALS for c in password) >= _MIN_PER_CLASS)


def _contains_local_part(password, email):
    local = (email or "").split("@")[0].lower()
    if len(local) < 3:
        return False
    lowered = password.lower()
    # Any shared substring of length >= 3 contains a shared run of exactly 3.
    return any(local[i:i + 3] in lowered for i in range(len(local) - 2))


def generate_password(length=24, email=None):
    """Random password meeting the class minimums and free of the email's local part."""
    if length < _MIN_LENGTH:
        raise ValueError(f"password length must be at least {_MIN_LENGTH}")
    for _ in range(_MAX_TRIES):
        candidate = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        if not _classes_ok(candidate):
            continue
        if email and _contains_local_part(candidate, email):
            continue
        return candidate
    raise RuntimeError("could not generate a password meeting the rules")
