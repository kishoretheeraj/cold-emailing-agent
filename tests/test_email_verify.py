"""Tests for email_verify -- bounce-risk pre-flight check. All DNS is mocked."""

import dns.exception
import dns.resolver
import pytest

import email_verify


# ── Syntax ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("email", [
    "dana@example.com",
    "first.last@sub.example.co",
    "a+tag@example.io",
])
def test_verify_valid_syntax_reaches_dns(mocker, email):
    mocker.patch("dns.resolver.resolve", return_value=[mocker.MagicMock()])
    result = email_verify.verify(email)
    assert result.status == "valid"


@pytest.mark.parametrize("email", [
    "",
    None,
    "no-at-sign",
    "@no-local-part.com",
    "trailing@dot.",
    "spaces in@email.com",
    "two@@signs.com",
    123,
    "alice..smith@example.com",  # finding 7: consecutive dots, invalid dot-atom syntax
    ".leading@example.com",
    "trailing.@example.com",
])
def test_verify_malformed_syntax_is_invalid_without_dns(mocker, email):
    resolve = mocker.patch("dns.resolver.resolve")
    result = email_verify.verify(email)
    assert result.status == "invalid"
    resolve.assert_not_called()


# Merge review 2026-09-28, finding 7: the syntax gate used to reject any local part starting
# with an underscore, and rejected apostrophes anywhere -- both valid RFC 5322 atext characters
# -- so a valid contact could silently never receive a first-touch draft. With working DNS
# mocked, these must all reach the "valid" branch, not be rejected on syntax alone.
@pytest.mark.parametrize("email", [
    "o'connor@example.com",
    "_team@example.com",
    "d'angelo_smith@example.com",
    "dana@example.com",
    "first.last@sub.example.co",
    "a+tag@example.io",
])
def test_verify_valid_punctuation_and_ordinary_addresses_reach_dns_and_are_valid(mocker, email):
    mocker.patch("dns.resolver.resolve", return_value=[mocker.MagicMock()])
    result = email_verify.verify(email)
    assert result.status == "valid"


# ── Domain / DNS ────────────────────────────────────────────────────────────

def test_verify_mx_hit_is_valid(mocker):
    mocker.patch("dns.resolver.resolve", return_value=[mocker.MagicMock()])
    result = email_verify.verify("dana@example.com")
    assert result.status == "valid"


def test_verify_no_mx_but_a_record_falls_back_to_valid(mocker):
    def resolve_side_effect(domain, rdtype, **kwargs):
        if rdtype == "MX":
            raise dns.resolver.NoAnswer()
        return [mocker.MagicMock()]
    mocker.patch("dns.resolver.resolve", side_effect=resolve_side_effect)
    result = email_verify.verify("dana@example.com")
    assert result.status == "valid"


# Merge review 2026-09-28, finding 8: RFC 7505's "null MX" -- exactly one MX record, preference
# 0, exchange "." -- is how a domain explicitly publishes that it accepts no mail. The previous
# code treated any successful MX resolution as valid without inspecting the record. Uses a real
# dnspython MX rdata object parsed from "0 ." (not a mock standing in for one), per the review's
# own reproduction.
def test_verify_null_mx_is_invalid_and_does_not_fall_back_to_a_record(mocker):
    import dns.rdata
    import dns.rdataclass
    import dns.rdatatype
    null_mx = dns.rdata.from_text(dns.rdataclass.IN, dns.rdatatype.MX, "0 .")

    resolve = mocker.patch("dns.resolver.resolve", return_value=[null_mx])
    result = email_verify.verify("dana@example.com")

    assert result.status == "invalid"
    # Only the MX lookup should have run -- a null MX is authoritative and must not fall
    # through to an A/AAAA lookup.
    resolve.assert_called_once()
    assert resolve.call_args[0][1] == "MX"


def test_verify_ordinary_mx_with_real_record_shape_is_still_valid(mocker):
    # Regression guard alongside the null-MX test above: a normal, non-null MX record (real
    # dnspython shape, not a bare mock) must still read as valid.
    import dns.rdata
    import dns.rdataclass
    import dns.rdatatype
    ordinary_mx = dns.rdata.from_text(dns.rdataclass.IN, dns.rdatatype.MX, "10 mail.example.com.")

    mocker.patch("dns.resolver.resolve", return_value=[ordinary_mx])
    result = email_verify.verify("dana@example.com")

    assert result.status == "valid"


def test_verify_no_mx_and_no_a_record_is_invalid(mocker):
    def resolve_side_effect(domain, rdtype, **kwargs):
        if rdtype == "MX":
            raise dns.resolver.NoAnswer()
        raise dns.resolver.NXDOMAIN()
    mocker.patch("dns.resolver.resolve", side_effect=resolve_side_effect)
    result = email_verify.verify("dana@example.com")
    assert result.status == "invalid"


def test_verify_nxdomain_on_mx_is_invalid(mocker):
    mocker.patch("dns.resolver.resolve", side_effect=dns.resolver.NXDOMAIN())
    result = email_verify.verify("dana@nonexistent-domain-xyz.test")
    assert result.status == "invalid"


@pytest.mark.parametrize("exc", [
    dns.exception.Timeout(),
    dns.resolver.NoNameservers(),
    RuntimeError("resolver blew up"),
])
def test_verify_resolver_failure_is_unknown(mocker, exc):
    mocker.patch("dns.resolver.resolve", side_effect=exc)
    result = email_verify.verify("dana@example.com")
    assert result.status == "unknown"


def test_verify_never_raises(mocker):
    mocker.patch("dns.resolver.resolve", side_effect=Exception("anything"))
    result = email_verify.verify("dana@example.com")
    assert result.status == "unknown"


def test_verify_result_is_a_namedtuple_with_status_and_reason(mocker):
    mocker.patch("dns.resolver.resolve", return_value=[mocker.MagicMock()])
    result = email_verify.verify("dana@example.com")
    assert result.status == "valid"
    assert result.reason is None
