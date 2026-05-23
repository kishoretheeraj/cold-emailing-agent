"""Unit tests for gmail._lookup_gmail_draft_id."""

import pytest
from unittest.mock import MagicMock, patch


def _make_api_client(messages_list_result, drafts_pages):
    """Return a mock Gmail API client for _lookup_gmail_draft_id tests."""
    client = MagicMock()

    # messages.list
    client.users.return_value.messages.return_value.list.return_value.execute.return_value = (
        messages_list_result
    )

    # drafts.list — support multiple pages
    pages = iter(drafts_pages)
    client.users.return_value.drafts.return_value.list.return_value.execute.side_effect = (
        lambda: next(pages)
    )

    return client


def test_lookup_returns_none_when_no_api_client(mocker):
    mocker.patch("gmail._get_gmail_api_client", return_value=None)
    from gmail import _lookup_gmail_draft_id
    assert _lookup_gmail_draft_id("<abc@gmail.com>") is None


def test_lookup_returns_none_when_message_not_found(mocker):
    client = _make_api_client(
        messages_list_result={"messages": []},
        drafts_pages=[{"drafts": []}],
    )
    mocker.patch("gmail._get_gmail_api_client", return_value=client)
    from gmail import _lookup_gmail_draft_id
    assert _lookup_gmail_draft_id("<abc@gmail.com>") is None


def test_lookup_uses_targeted_search_query(mocker):
    client = _make_api_client(
        messages_list_result={"messages": []},
        drafts_pages=[{"drafts": []}],
    )
    mocker.patch("gmail._get_gmail_api_client", return_value=client)
    from gmail import _lookup_gmail_draft_id
    _lookup_gmail_draft_id("<abc@gmail.com>")

    call_kwargs = client.users.return_value.messages.return_value.list.call_args.kwargs
    assert "in:draft rfc822msgid:abc@gmail.com" in call_kwargs.get("q", "")
    assert call_kwargs.get("maxResults") == 1


def test_lookup_finds_matching_draft(mocker):
    target_msg_id = "msg-hex-123"
    client = _make_api_client(
        messages_list_result={"messages": [{"id": target_msg_id}]},
        drafts_pages=[
            {
                "drafts": [
                    {"id": "draft-wrong", "message": {"id": "msg-other"}},
                    {"id": "draft-correct", "message": {"id": target_msg_id}},
                ],
                # no nextPageToken → single page
            }
        ],
    )
    mocker.patch("gmail._get_gmail_api_client", return_value=client)
    from gmail import _lookup_gmail_draft_id
    result = _lookup_gmail_draft_id("<abc@gmail.com>")
    assert result == "draft-correct"


def test_lookup_paginates_drafts(mocker):
    target_msg_id = "msg-page2"
    client = _make_api_client(
        messages_list_result={"messages": [{"id": target_msg_id}]},
        drafts_pages=[
            {"drafts": [{"id": "d1", "message": {"id": "other"}}], "nextPageToken": "tok2"},
            {"drafts": [{"id": "d2", "message": {"id": target_msg_id}}]},
        ],
    )
    mocker.patch("gmail._get_gmail_api_client", return_value=client)
    from gmail import _lookup_gmail_draft_id
    result = _lookup_gmail_draft_id("<abc@gmail.com>")
    assert result == "d2"


def test_lookup_returns_none_on_api_exception(mocker):
    client = MagicMock()
    client.users.return_value.messages.return_value.list.return_value.execute.side_effect = Exception("API error")
    mocker.patch("gmail._get_gmail_api_client", return_value=client)
    from gmail import _lookup_gmail_draft_id
    # Must not raise — returns None
    assert _lookup_gmail_draft_id("<abc@gmail.com>") is None
