import importlib.util
import os

import pytest

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.py")
_SECRETS = ("ANTHROPIC_API_KEY", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "SUPABASE_URL", "SUPABASE_ANON_KEY")


def _fresh(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    spec = importlib.util.spec_from_file_location("config_fresh", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_import_succeeds_without_secrets(monkeypatch):
    for name in _SECRETS:
        monkeypatch.delenv(name, raising=False)
    module = _fresh(monkeypatch)
    assert module.EMAIL_MODEL


def test_missing_secret_raises_keyerror_on_access(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    module = _fresh(monkeypatch)
    with pytest.raises(KeyError):
        module.SUPABASE_URL


def test_secret_read_on_first_use_and_cached(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "a@example.com")
    module = _fresh(monkeypatch)
    assert module.GMAIL_ADDRESS == "a@example.com"
    monkeypatch.setenv("GMAIL_ADDRESS", "b@example.com")
    assert module.GMAIL_ADDRESS == "a@example.com"


def test_unknown_attribute_is_attribute_error(monkeypatch):
    module = _fresh(monkeypatch)
    with pytest.raises(AttributeError):
        module.NOT_A_SETTING


def test_require_secrets_names_every_missing_secret(monkeypatch):
    for name in _SECRETS:
        monkeypatch.setenv(name, "x")
    monkeypatch.delenv("SUPABASE_URL")
    monkeypatch.delenv("GMAIL_ADDRESS")
    module = _fresh(monkeypatch)
    with pytest.raises(SystemExit) as info:
        module.require_secrets()
    assert "SUPABASE_URL" in str(info.value) and "GMAIL_ADDRESS" in str(info.value)


def test_require_secrets_passes_when_all_set(monkeypatch):
    for name in _SECRETS:
        monkeypatch.setenv(name, "x")
    _fresh(monkeypatch).require_secrets()
