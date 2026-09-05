import re
from collections import namedtuple

import dns.exception
import dns.resolver

from config import EMAIL_VERIFY_DNS_TIMEOUT_SECONDS

EmailVerifyResult = namedtuple("EmailVerifyResult", ["status", "reason"])

# ── Syntax ──────────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$"
)

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
        _resolve(domain, "MX")
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
