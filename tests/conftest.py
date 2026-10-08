"""
Shared test fixtures.

Sets fake environment variables BEFORE any module under test is imported.
config.py reads os.environ at import time and raises KeyError on missing keys,
so this must run during conftest collection — not inside test functions.
"""

import os
import sys

# Fake credentials. Tests must mock all outbound calls; these never travel.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("GMAIL_ADDRESS", "test@example.com")
os.environ.setdefault("GMAIL_APP_PASSWORD", "test password")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "sb_publishable_test_key")
os.environ.setdefault("TAVILY_API_KEY", "test-tavily-key")
# Gmail OAuth vars — absent by default so tests verify graceful degradation.
# Individual tests that need them set them explicitly via monkeypatch.

# Make the project root importable so `import agent`, `import db`, etc. work.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


import pytest


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch):
    # agent.run() calls email_verify.verify(), which resolves MX records. Unmocked, a test did a
    # real lookup and passed or failed depending on the machine's network. Failing fast here makes
    # verify() return "unknown" (never a block); tests that need DNS answers patch resolve().
    import dns.resolver

    def _blocked(*args, **kwargs):
        raise dns.resolver.NoNameservers()

    monkeypatch.setattr(dns.resolver, "resolve", _blocked)


@pytest.fixture(autouse=True)
def _no_real_lease_recovery_in_monitor(request):
    # monitor.run() calls recover_stale_leases best-effort; unmocked, it would hit the fake
    # Supabase URL and sit in db._retry's backoff sleeps for every run() test.
    if "monitor" not in request.module.__name__ and "paused" not in request.module.__name__:
        yield
        return
    import monitor
    original = monitor.recover_stale_leases
    monitor.recover_stale_leases = lambda *a, **k: 0
    try:
        yield
    finally:
        monitor.recover_stale_leases = original


@pytest.fixture(autouse=True)
def _no_real_quality_gate(request):
    # The preview's quality gate downloads both PDFs from Storage; tests of the gate itself (module
    # names containing "quality") exercise it, every other preview test sees a clean report.
    if "quality" in request.module.__name__:
        yield
        return
    import apply_agent
    original = apply_agent._quality_report
    apply_agent._quality_report = lambda job: {"problems": [], "coverage": None}
    try:
        yield
    finally:
        apply_agent._quality_report = original
