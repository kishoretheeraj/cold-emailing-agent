"""Tests for cu_linkedin.py's X11 action-execution layer. Every subprocess call is mocked --
no real X display, no real xdotool, no real scrot."""

import base64
import subprocess
from unittest.mock import MagicMock

import pytest

import config
import cu_linkedin


@pytest.fixture
def run(mocker):
    proc = MagicMock(name="completed_process")
    proc.stdout = b""
    return mocker.patch.object(cu_linkedin.subprocess, "run", return_value=proc)


def _commands(run):
    return [call.args[0] for call in run.call_args_list]


# ── screenshot ─────────────────────────────────────────────────────────────────

def test_screenshot_returns_a_base64_image_content_block(mocker, run):
    mocker.patch.object(cu_linkedin, "_read_png", return_value=b"\x89PNGfake")
    content, is_error = cu_linkedin.execute_action("screenshot", {})
    assert is_error is False
    assert content == [{
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(b"\x89PNGfake").decode("ascii"),
        },
    }]
    assert _commands(run)[0][0] == "scrot"


def test_every_subprocess_call_targets_the_configured_display(mocker, run):
    mocker.patch.object(cu_linkedin, "_read_png", return_value=b"png")
    cu_linkedin.execute_action("screenshot", {})
    assert run.call_args.kwargs["env"]["DISPLAY"] == config.CU_LINKEDIN_DISPLAY
    assert run.call_args.kwargs["timeout"] == config.CU_LINKEDIN_SUBPROCESS_TIMEOUT_SECONDS


# ── clicks and movement ────────────────────────────────────────────────────────

def test_left_click_moves_then_clicks_button_one(run):
    content, is_error = cu_linkedin.execute_action("left_click", {"coordinate": [640, 480]})
    assert (content, is_error) == ("OK", False)
    assert _commands(run) == [
        ["xdotool", "mousemove", "--sync", "640", "480"],
        ["xdotool", "click", "1"],
    ]


def test_left_click_without_coordinate_clicks_in_place(run):
    cu_linkedin.execute_action("left_click", {})
    assert _commands(run) == [["xdotool", "click", "1"]]


def test_left_click_with_modifier_text_holds_then_releases_it(run):
    cu_linkedin.execute_action("left_click", {"coordinate": [1, 2], "text": "ctrl+shift"})
    assert _commands(run) == [
        ["xdotool", "mousemove", "--sync", "1", "2"],
        ["xdotool", "keydown", "ctrl+shift"],
        ["xdotool", "click", "1"],
        ["xdotool", "keyup", "ctrl+shift"],
    ]


@pytest.mark.parametrize("name,button", [
    ("right_click", "3"),
    ("middle_click", "2"),
])
def test_other_buttons_map_to_their_xdotool_button_numbers(run, name, button):
    cu_linkedin.execute_action(name, {})
    assert _commands(run) == [["xdotool", "click", button]]


@pytest.mark.parametrize("name,repeat", [("double_click", "2"), ("triple_click", "3")])
def test_multi_clicks_use_repeat(run, name, repeat):
    cu_linkedin.execute_action(name, {})
    assert _commands(run) == [["xdotool", "click", "--repeat", repeat, "1"]]


def test_mouse_move_interpolates_instead_of_jumping(run):
    # `run`'s mocked stdout is always b"", so _current_position() falls back to (0, 0) here --
    # this test only proves the step count and the final target, not the from-current-position
    # behavior (see test_mouse_move_interpolates_from_the_current_position_not_the_origin below
    # for that). The leading command is the getmouselocation query _glide_to issues before moving.
    cu_linkedin.execute_action("mouse_move", {"coordinate": [100, 200]})
    commands = _commands(run)
    assert commands[0] == ["xdotool", "getmouselocation", "--shell"]
    assert len(commands) == config.CU_LINKEDIN_MOUSE_STEPS + 1
    assert commands[-1] == ["xdotool", "mousemove", "--sync", "100", "200"]


def test_mouse_move_interpolates_from_the_current_position_not_the_origin(mocker):
    # The bug this guards against: an earlier draft interpolated from (0, 0) unconditionally,
    # which teleports the cursor to a fraction of the way from the screen's top-left corner
    # before walking to the target -- a stronger automation tell than a single jump, not a
    # weaker one. Mock a real-looking non-origin current position and assert every intermediate
    # step lies on the line from THAT point to the target, never on the line from (0, 0).
    start_x, start_y = 500, 300
    target = (520, 260)
    located = MagicMock(name="getmouselocation")
    located.stdout = f"X={start_x}\nY={start_y}\nSCREEN=0\nWINDOW=1\n".encode()
    moved = MagicMock(name="mousemove")
    moved.stdout = b""
    run = mocker.patch.object(
        cu_linkedin.subprocess, "run",
        side_effect=[located] + [moved] * config.CU_LINKEDIN_MOUSE_STEPS,
    )
    cu_linkedin.execute_action("mouse_move", {"coordinate": list(target)})
    commands = _commands(run)
    assert commands[0] == ["xdotool", "getmouselocation", "--shell"]
    move_commands = commands[1:]
    assert len(move_commands) == config.CU_LINKEDIN_MOUSE_STEPS

    steps = config.CU_LINKEDIN_MOUSE_STEPS
    expected = [
        ["xdotool", "mousemove", "--sync",
         str(start_x + int((target[0] - start_x) * step / steps)),
         str(start_y + int((target[1] - start_y) * step / steps))]
        for step in range(1, steps + 1)
    ]
    assert move_commands == expected
    assert move_commands[-1] == ["xdotool", "mousemove", "--sync", "520", "260"]  # the target
    # An origin-based glide's first step would land near (130, 65) (1/4 of the way from (0,0) to
    # the target) -- nothing here does, because the glide starts from (500, 300), not (0, 0).
    assert move_commands[0] != ["xdotool", "mousemove", "--sync", "130", "65"]


def test_left_click_drag_presses_moves_and_releases(run):
    # As with test_mouse_move_interpolates_instead_of_jumping, `run`'s mocked stdout is b"", so
    # the glide's internal getmouselocation call falls back to (0, 0) -- this test proves the
    # press/glide/release sequencing, not from-current-position behavior specifically.
    cu_linkedin.execute_action("left_click_drag",
                               {"start_coordinate": [1, 2], "coordinate": [3, 4]})
    commands = _commands(run)
    assert commands[0] == ["xdotool", "mousemove", "--sync", "1", "2"]
    assert commands[1] == ["xdotool", "mousedown", "1"]
    assert commands[2] == ["xdotool", "getmouselocation", "--shell"]
    assert commands[-2] == ["xdotool", "mousemove", "--sync", "3", "4"]
    assert commands[-1] == ["xdotool", "mouseup", "1"]


@pytest.mark.parametrize("name,verb", [
    ("left_mouse_down", "mousedown"),
    ("left_mouse_up", "mouseup"),
])
def test_explicit_mouse_button_state(run, name, verb):
    cu_linkedin.execute_action(name, {})
    assert _commands(run) == [["xdotool", verb, "1"]]


def test_cursor_position_parses_the_shell_output(mocker):
    proc = MagicMock()
    proc.stdout = b"X=511\nY=744\nSCREEN=0\nWINDOW=1234\n"
    mocker.patch.object(cu_linkedin.subprocess, "run", return_value=proc)
    content, is_error = cu_linkedin.execute_action("cursor_position", {})
    assert (content, is_error) == ("X=511, Y=744", False)


# ── keyboard ───────────────────────────────────────────────────────────────────

def test_type_uses_a_jittered_keystroke_delay(run):
    cu_linkedin.execute_action("type", {"text": "product manager"}, rand=lambda: 0.5)
    assert _commands(run) == [[
        "xdotool", "type", "--delay", str(config.CU_LINKEDIN_KEYSTROKE_DELAY_MS),
        "--clearmodifiers", "product manager",
    ]]


def test_key_passes_the_chord_and_repeat_count(run):
    cu_linkedin.execute_action("key", {"text": "ctrl+l", "repeat": 2})
    assert _commands(run) == [["xdotool", "key", "--repeat", "2", "--clearmodifiers", "ctrl+l"]]


def test_key_defaults_repeat_to_one(run):
    cu_linkedin.execute_action("key", {"text": "Return"})
    assert _commands(run) == [["xdotool", "key", "--repeat", "1", "--clearmodifiers", "Return"]]


def test_hold_key_presses_sleeps_then_releases(mocker, run):
    sleep = mocker.patch.object(cu_linkedin.time, "sleep")
    cu_linkedin.execute_action("hold_key", {"text": "shift", "duration": 2})
    assert _commands(run) == [
        ["xdotool", "keydown", "--clearmodifiers", "shift"],
        ["xdotool", "keyup", "--clearmodifiers", "shift"],
    ]
    sleep.assert_called_once_with(2)


# ── scroll and wait ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("direction,button", [
    ("up", "4"), ("down", "5"), ("left", "6"), ("right", "7"),
])
def test_scroll_maps_direction_to_a_wheel_button(run, direction, button):
    cu_linkedin.execute_action("scroll", {
        "coordinate": [10, 20], "scroll_direction": direction, "scroll_amount": 3,
    })
    assert _commands(run) == [
        ["xdotool", "mousemove", "--sync", "10", "20"],
        ["xdotool", "click", "--repeat", "3", button],
    ]


def test_wait_sleeps_but_is_clamped(mocker, run):
    sleep = mocker.patch.object(cu_linkedin.time, "sleep")
    content, is_error = cu_linkedin.execute_action("wait", {"duration": 9999})
    assert (content, is_error) == ("OK", False)
    sleep.assert_called_once_with(config.CU_LINKEDIN_MAX_WAIT_SECONDS)


# ── failure posture ────────────────────────────────────────────────────────────

def test_unknown_action_is_an_error_not_a_raise(run):
    content, is_error = cu_linkedin.execute_action("teleport", {})
    assert is_error is True
    assert "teleport" in content
    run.assert_not_called()


def test_zoom_is_rejected_because_the_toolset_config_disables_it(run):
    content, is_error = cu_linkedin.execute_action("zoom", {"region": [0, 0, 10, 10]})
    assert is_error is True
    run.assert_not_called()


def test_subprocess_failure_degrades_to_an_error_result(mocker):
    mocker.patch.object(cu_linkedin.subprocess, "run",
                        side_effect=subprocess.CalledProcessError(1, "xdotool"))
    content, is_error = cu_linkedin.execute_action("left_click", {"coordinate": [1, 1]})
    assert is_error is True
    assert "left_click" in content


def test_subprocess_timeout_degrades_to_an_error_result(mocker):
    mocker.patch.object(cu_linkedin.subprocess, "run",
                        side_effect=subprocess.TimeoutExpired("xdotool", 30))
    content, is_error = cu_linkedin.execute_action("type", {"text": "hi"})
    assert is_error is True
