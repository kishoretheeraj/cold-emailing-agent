"""Tests for the SDK-based _call_claude wrapper."""
import pytest
import anthropic


def _make_response(text):
    """Build a minimal mock SDK response object."""
    content_block = type("Block", (), {"text": text})()
    return type("Resp", (), {"content": [content_block]})()


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
