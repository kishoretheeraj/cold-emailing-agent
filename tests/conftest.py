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
