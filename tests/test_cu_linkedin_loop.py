"""Tests for cu_linkedin.py's Computer Use sampling loop. The Anthropic client and the X11
action layer are both mocked -- no API call, no display."""

from unittest.mock import MagicMock

import pytest

import config
import cu_linkedin
import usage_tracking


def _block(**fields):
    block = MagicMock()
    for key, value in fields.items():
        setattr(block, key, value)
    return block


def _tool_use(block_id, name, params=None):
    return _block(type="tool_use", id=block_id, name=name, input=params or {})


def _text(value):
    return _block(type="text", text=value)


def _response(content, stop_reason, input_tokens=100, output_tokens=20):
    resp = MagicMock()
    resp.content = content
    resp.stop_reason = stop_reason
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    return resp


@pytest.fixture(autouse=True)
def no_real_calls(mocker):
    mocker.patch.object(cu_linkedin.time, "sleep")
    mocker.patch.object(usage_tracking, "log_usage")


@pytest.fixture
def claude(mocker):
    client = MagicMock(name="anthropic_client")
    mocker.patch.object(cu_linkedin, "_claude", client)
    return client


# ── Toolset definition ─────────────────────────────────────────────────────────

def test_toolset_is_the_current_type_with_zoom_disabled():
    assert cu_linkedin._TOOLS == [{
        "type": "computer_toolset_20260801",
        "configs": {"zoom": {"enabled": False}},
    }]


# ── _execute_tool_uses ─────────────────────────────────────────────────────────

def test_results_carry_the_toolset_name_and_tool_use_id(mocker):
    mocker.patch.object(cu_linkedin, "execute_action", return_value=("OK", False))
    results, executed = cu_linkedin._execute_tool_uses([_tool_use("toolu_1", "left_click")])
    assert executed == 1
    assert results == [{
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "toolset_name": "computer",
        "content": "OK",
    }]


def test_batched_actions_run_sequentially_in_order(mocker):
    execute = mocker.patch.object(cu_linkedin, "execute_action", return_value=("OK", False))
    blocks = [
        _tool_use("a", "screenshot"),
        _tool_use("b", "left_click", {"coordinate": [1, 2]}),
        _tool_use("c", "screenshot"),
    ]
    results, executed = cu_linkedin._execute_tool_uses(blocks)
    assert executed == 3
    assert [c.args[0] for c in execute.call_args_list] == ["screenshot", "left_click", "screenshot"]
    assert [r["tool_use_id"] for r in results] == ["a", "b", "c"]


def test_a_failed_action_short_circuits_the_rest_of_the_batch(mocker):
    execute = mocker.patch.object(cu_linkedin, "execute_action",
                                  side_effect=[("boom", True), ("OK", False)])
    blocks = [_tool_use("a", "left_click"), _tool_use("b", "screenshot")]
    results, executed = cu_linkedin._execute_tool_uses(blocks)
    assert executed == 1
    assert execute.call_count == 1
    assert results[0]["is_error"] is True
    assert results[1] == {
        "type": "tool_result",
        "tool_use_id": "b",
        "toolset_name": "computer",
        "is_error": True,
        "content": "Not executed: an earlier computer action in this turn failed.",
    }


def test_a_paced_delay_is_taken_before_every_action(mocker):
    mocker.patch.object(cu_linkedin, "execute_action", return_value=("OK", False))
    mocker.patch.object(cu_linkedin, "next_action_delay", return_value=2.5)
    sleep = mocker.patch.object(cu_linkedin.time, "sleep")
    cu_linkedin._execute_tool_uses([_tool_use("a", "screenshot"), _tool_use("b", "screenshot")])
    assert sleep.call_args_list == [mocker.call(2.5), mocker.call(2.5)]


# ── _prune_screenshots ─────────────────────────────────────────────────────────

def _screenshot_result(tool_use_id):
    return {
        "type": "tool_result", "tool_use_id": tool_use_id, "toolset_name": "computer",
        "content": [{"type": "image",
                     "source": {"type": "base64", "media_type": "image/png", "data": "x"}}],
    }


def test_prune_keeps_only_the_most_recent_screenshots():
    messages = [{"role": "user", "content": [_screenshot_result(f"t{i}")]} for i in range(6)]
    cu_linkedin._prune_screenshots(messages)
    kept = [m for m in messages if isinstance(m["content"][0]["content"], list)]
    assert len(kept) == config.CU_LINKEDIN_SCREENSHOT_HISTORY
    assert [m["content"][0]["tool_use_id"] for m in kept] == ["t3", "t4", "t5"]
    assert messages[0]["content"][0]["content"] == "[screenshot pruned to save tokens]"


def test_prune_never_touches_text_results():
    messages = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "toolset_name": "computer", "content": "OK"},
    ]}]
    cu_linkedin._prune_screenshots(messages)
    assert messages[0]["content"][0]["content"] == "OK"


# ── run_session ────────────────────────────────────────────────────────────────

def test_run_session_returns_final_text_when_claude_stops(claude):
    claude.messages.create.return_value = _response([_text('[{"company": "Acme"}]')], "end_turn")
    assert cu_linkedin.run_session("go") == '[{"company": "Acme"}]'
    assert claude.messages.create.call_count == 1


def test_run_session_sends_the_toolset_and_configured_model(claude):
    claude.messages.create.return_value = _response([_text("done")], "end_turn")
    cu_linkedin.run_session("go")
    kwargs = claude.messages.create.call_args.kwargs
    assert kwargs["model"] == config.CU_LINKEDIN_MODEL
    assert kwargs["tools"] == cu_linkedin._TOOLS
    assert kwargs["messages"][0] == {"role": "user", "content": "go"}


def test_run_session_feeds_tool_results_back_and_loops(mocker, claude):
    # `messages` is one list mutated in place across turns inside run_session -- a plain
    # side_effect list plus `call_args_list[i].kwargs["messages"]` would record a REFERENCE to
    # that same list on every call, so by the time the loop finishes, every recorded call would
    # alias the identical final list (this was a real bug in an earlier draft of this test: it
    # could not pass, since messages[-1] after the loop is always the LAST thing appended,
    # regardless of which call index you look at). Snapshot a shallow copy of the list at each
    # call instead, capturing what was actually sent at that moment.
    mocker.patch.object(cu_linkedin, "execute_action", return_value=("OK", False))
    responses = iter([
        _response([_tool_use("a", "screenshot")], "tool_use"),
        _response([_text("all done")], "end_turn"),
    ])
    seen_messages = []

    def _create(*args, **kwargs):
        seen_messages.append(list(kwargs["messages"]))
        return next(responses)

    claude.messages.create.side_effect = _create
    assert cu_linkedin.run_session("go") == "all done"

    assert len(seen_messages) == 2
    second_call_messages = seen_messages[1]
    assert second_call_messages[-1]["role"] == "user"
    assert second_call_messages[-1]["content"][0]["tool_use_id"] == "a"


def test_run_session_logs_usage_for_every_turn(mocker, claude):
    log_usage = mocker.patch.object(usage_tracking, "log_usage")
    claude.messages.create.return_value = _response([_text("done")], "end_turn",
                                                    input_tokens=1500, output_tokens=70)
    cu_linkedin.run_session("go")
    log_usage.assert_called_once_with(
        "cu_linkedin", "session_turn", config.CU_LINKEDIN_MODEL,
        {"input_tokens": 1500, "output_tokens": 70},
    )


def test_run_session_wraps_up_without_tools_when_the_session_cap_is_hit(mocker, claude):
    mocker.patch.object(cu_linkedin, "execute_action", return_value=("OK", False))
    mocker.patch.object(cu_linkedin, "session_exhausted", return_value=True)
    claude.messages.create.side_effect = [
        _response([_tool_use("a", "screenshot")], "tool_use"),
        _response([_text("here is what I found")], "end_turn"),
    ]
    assert cu_linkedin.run_session("go") == "here is what I found"
    wrap_up = claude.messages.create.call_args_list[1].kwargs
    assert wrap_up["tool_choice"] == {"type": "none"}


def test_run_session_stops_at_max_turns(mocker, claude):
    mocker.patch.object(cu_linkedin, "execute_action", return_value=("OK", False))
    mocker.patch.object(config, "CU_LINKEDIN_MAX_TURNS", 2)
    claude.messages.create.side_effect = (
        [_response([_tool_use("a", "screenshot")], "tool_use")] * 2
        + [_response([_text("wrapped")], "end_turn")]
    )
    assert cu_linkedin.run_session("go") == "wrapped"
    assert claude.messages.create.call_count == 3
