"""Tests for the SDK-based _call_claude wrapper."""
import logging
import pytest
import anthropic


def _make_response(text, cache_read=0, cache_created=0):
    """Build a minimal mock SDK response object."""
    content_block = type("Block", (), {"text": text})()
    usage = type("Usage", (), {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_created,
    })()
    return type("Resp", (), {"content": [content_block], "usage": usage})()


def test_call_claude_basic(mocker):
    mock_create = mocker.patch("emailer._claude.messages.create",
                               return_value=_make_response("hello world"))
    from emailer import _call_claude
    result = _call_claude("Say hi")

    assert result == "hello world"
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["messages"] == [{"role": "user", "content": "Say hi"}]
    assert "system" not in call_kwargs


def test_call_claude_with_system(mocker):
    mock_create = mocker.patch("emailer._claude.messages.create",
                               return_value=_make_response("hi"))
    from emailer import _call_claude
    _call_claude("Say hi", system="You are a helpful assistant.")

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["system"] == [
        {
            "type": "text",
            "text": "You are a helpful assistant.",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_call_claude_no_system_when_none(mocker):
    mock_create = mocker.patch("emailer._claude.messages.create",
                               return_value=_make_response("hi"))
    from emailer import _call_claude
    _call_claude("Say hi", system=None)

    call_kwargs = mock_create.call_args.kwargs
    assert "system" not in call_kwargs


def test_call_claude_empty_response_raises(mocker):
    mocker.patch("emailer._claude.messages.create",
                 return_value=_make_response("   "))
    from emailer import _call_claude
    with pytest.raises(ValueError, match="Claude returned empty text"):
        _call_claude("Say hi")


def test_call_claude_custom_model_and_tokens(mocker):
    mock_create = mocker.patch("emailer._claude.messages.create",
                               return_value=_make_response("ok"))
    from emailer import _call_claude
    _call_claude("ping", model="claude-haiku-4-5-20251001", max_tokens=300)

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"
    assert call_kwargs["max_tokens"] == 300


def test_call_claude_sdk_error_propagates(mocker):
    mocker.patch("emailer._claude.messages.create",
                 side_effect=anthropic.APIConnectionError(request=None))
    from emailer import _call_claude
    with pytest.raises(anthropic.APIConnectionError):
        _call_claude("ping")


def test_call_claude_logs_cache_hit(mocker, caplog):
    mocker.patch("emailer._claude.messages.create",
                 return_value=_make_response("hi", cache_read=512, cache_created=0))
    from emailer import _call_claude
    with caplog.at_level(logging.INFO, logger="emailer"):
        _call_claude("Say hi", system="stable system")
    assert any(
        "[CACHE]" in r.message and "cache_read=512" in r.message
        for r in caplog.records
    )


def test_call_claude_logs_cache_creation(mocker, caplog):
    mocker.patch("emailer._claude.messages.create",
                 return_value=_make_response("hi", cache_read=0, cache_created=1024))
    from emailer import _call_claude
    with caplog.at_level(logging.INFO, logger="emailer"):
        _call_claude("Say hi", system="stable system")
    assert any(
        "[CACHE]" in r.message and "cache_created=1024" in r.message
        for r in caplog.records
    )


def test_call_claude_no_cache_log_when_zero(mocker, caplog):
    mocker.patch("emailer._claude.messages.create",
                 return_value=_make_response("hi", cache_read=0, cache_created=0))
    from emailer import _call_claude
    with caplog.at_level(logging.INFO, logger="emailer"):
        _call_claude("Say hi")
    assert not any("[CACHE]" in r.message for r in caplog.records)


def test_call_claude_logs_usage_when_module_passed(mocker):
    mocker.patch("emailer._claude.messages.create", return_value=_make_response("hi"))
    log_usage = mocker.patch("emailer.usage_tracking.log_usage")
    from emailer import _call_claude
    _call_claude("Say hi", model="claude-sonnet-4-6", module="emailer", action="first_touch", contact_id=7)
    log_usage.assert_called_once_with(
        "emailer", "first_touch", "claude-sonnet-4-6",
        {"input_tokens": 100, "output_tokens": 20}, contact_id=7,
    )


def test_call_claude_does_not_log_usage_when_module_omitted(mocker):
    mocker.patch("emailer._claude.messages.create", return_value=_make_response("hi"))
    log_usage = mocker.patch("emailer.usage_tracking.log_usage")
    from emailer import _call_claude
    _call_claude("Say hi")
    log_usage.assert_not_called()
