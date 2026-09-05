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
])
def test_verify_malformed_syntax_is_invalid_without_dns(mocker, email):
    resolve = mocker.patch("dns.resolver.resolve")
    result = email_verify.verify(email)
    assert result.status == "invalid"
    resolve.assert_not_called()


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
