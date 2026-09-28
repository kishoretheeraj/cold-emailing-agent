import re
from collections import namedtuple

import dns.exception
import dns.name
import dns.resolver

from config import EMAIL_VERIFY_DNS_TIMEOUT_SECONDS

EmailVerifyResult = namedtuple("EmailVerifyResult", ["status", "reason"])

# ── Syntax ──────────────────────────────────────────────────────────────────

# Merge review 2026-09-28, finding 7: the previous pattern required the local part's FIRST
# character to come from a narrower class than the rest ([A-Za-z0-9] only), so any address
# starting with an underscore or apostrophe (e.g. "_team@..."; apostrophe was excluded
# everywhere, e.g. "o'connor@...") was rejected outright -- both are valid RFC 5322 atext
# characters. It also had no adjacency constraint on the local part, so "alice..smith@..."
# (an empty atom between two dots, invalid dot-atom syntax) matched.
#
# This pattern implements RFC 5322 section 3.2.3's atext set and section 3.4.1's dot-atom-text
# grammar for the unquoted local-part form (dot-atom-text = 1*atext *("." 1*atext)): one or more
# atext characters, optionally followed by more ("." + 1-or-more atext) groups -- which requires
# every dot-separated group to be non-empty, rejecting a leading, trailing, or doubled dot the
# same way the grammar does. This deliberately does not attempt the quoted-string local-part
# form (RFC 5322 section 3.2.4, e.g. "john doe"@example.com) -- vanishingly rare in practice, and
# a real candidate for degrading to "unknown" rather than guessing, per this function's own
# conservative posture, rather than adding an unverified quoted-string branch.
_ATEXT = r"[A-Za-z0-9!#$%&'*+\-/=?^_`{|}~]"
_LOCAL_PART = rf"{_ATEXT}+(?:\.{_ATEXT}+)*"
_DOMAIN = r"[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}"
_EMAIL_RE = re.compile(rf"^{_LOCAL_PART}@{_DOMAIN}$")

def _check_syntax(email):
    if not isinstance(email, str) or not email:
        return False
    return bool(_EMAIL_RE.match(email.strip()))


# ── Domain / DNS ──────────────────────────────────────────────────────────────

def _resolve(domain, rdtype):
    return dns.resolver.resolve(domain, rdtype, lifetime=EMAIL_VERIFY_DNS_TIMEOUT_SECONDS)

def _check_domain(domain):
    """
    Returns "valid" (MX or fallback A/AAAA resolves), "invalid" (conclusively
    cannot receive mail), or "unknown" (the lookup itself failed).
    """
    try:
        mx_records = list(_resolve(domain, "MX"))
        # Merge review 2026-09-28, finding 8: RFC 7505 section 3's "null MX" -- exactly one MX
        # record, preference 0, exchange the root domain "." -- is how a domain explicitly
        # publishes that it accepts no mail at all. The previous code treated any successful MX
        # resolution as "valid" without inspecting the record, so a null MX (a conclusively
        # undeliverable domain -- the exact case this gate exists to catch) read as valid. A
        # null MX is authoritative: RFC 7505 explicitly forbids falling back to A/AAAA when one
        # is published, so this returns "invalid" directly rather than falling through to the
        # A/AAAA fallback below.
        if len(mx_records) == 1 and mx_records[0].preference == 0 and mx_records[0].exchange == dns.name.root:
            return "invalid"
        return "valid"
    except dns.resolver.NXDOMAIN:
        return "invalid"
    except dns.resolver.NoAnswer:
        pass
    except (dns.exception.Timeout, dns.resolver.NoNameservers):
        return "unknown"
    except Exception:
        return "unknown"

    # No MX record published — RFC 5321 fallback: mail can still route to an
    # A/AAAA record. Try both; either resolving counts as reachable.
    for rdtype in ("A", "AAAA"):
        try:
            _resolve(domain, rdtype)
            return "valid"
        except dns.resolver.NXDOMAIN:
            continue
        except dns.resolver.NoAnswer:
            continue
        except (dns.exception.Timeout, dns.resolver.NoNameservers):
            return "unknown"
        except Exception:
            return "unknown"
    return "invalid"


# ── Public interface ─────────────────────────────────────────────────────────

def verify(email):
    """Bounce-risk check: syntax + MX/A DNS lookup. Never raises."""
    try:
        if not _check_syntax(email):
            return EmailVerifyResult("invalid", f"malformed address: {email!r}")
        domain = email.strip().rsplit("@", 1)[1]
        status = _check_domain(domain)
        if status == "valid":
            return EmailVerifyResult("valid", None)
        if status == "invalid":
            return EmailVerifyResult("invalid", f"domain has no mail route: {domain}")
        return EmailVerifyResult("unknown", f"DNS lookup failed for {domain}")
    except Exception as exc:
        return EmailVerifyResult("unknown", f"verify() error: {exc}")
