"""
Drives the user's real, persistently-logged-in Chrome window on the Beelink's X11 display slot 0
via Anthropic's Computer Use API, reads LinkedIn job postings, and persists them into
job_applications at stage='saved', source='linkedin'.

Discovery only. This module never applies to anything, never sends a connection request, and
never messages anyone -- linkedin.com/jobs stays a permanently-excluded apply target in
config.APPLY_AGENT_AGGREGATOR_DOMAINS.

The safety property is pacing, not evasion: randomized per-action delay, capped session length,
capped postings per session. Nothing here patches, strips, or falsifies anything about the
browser. If a CAPTCHA or login challenge appears, the loop stops and flags the human.

No LinkedIn credentials exist anywhere in this module or in config.py -- the Chrome profile's
own session cookie is the credential.

Usage: python3 cu_linkedin.py
"""

import base64
import json
import logging
import os
import random
import re
import subprocess
import tempfile
import time

import anthropic

import config
import db
import usage_tracking

log = logging.getLogger(__name__)

_claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=4)


# ── Pacing ─────────────────────────────────────────────────────────────────────

def next_action_delay(rand=random.random):
    """Seconds to wait before the next X11 action -- uniformly random inside the configured
    band, never a constant."""
    lo = config.CU_LINKEDIN_MIN_ACTION_DELAY_SECONDS
    hi = config.CU_LINKEDIN_MAX_ACTION_DELAY_SECONDS
    return lo + (hi - lo) * rand()


def keystroke_delay_ms(rand=random.random):
    """Per-keystroke delay in milliseconds for `xdotool type --delay`, jittered around the
    configured base in both directions."""
    jitter = config.CU_LINKEDIN_KEYSTROKE_JITTER_MS * (2 * rand() - 1)
    return max(1, int(round(config.CU_LINKEDIN_KEYSTROKE_DELAY_MS + jitter)))


def session_exhausted(actions_taken, elapsed_seconds):
    """True once this session has hit either its action cap or its wall-clock cap."""
    return (actions_taken >= config.CU_LINKEDIN_MAX_ACTIONS_PER_SESSION
            or elapsed_seconds >= config.CU_LINKEDIN_MAX_SESSION_SECONDS)


def daily_cap_satisfied(sessions_per_day, per_session_cap):
    """True when the declared schedule stays under the per-account daily view ceiling."""
    return sessions_per_day * per_session_cap <= config.CU_LINKEDIN_DAILY_VIEW_CAP


def worst_case_views_per_session():
    """The most postings a single session could plausibly have glanced at, derived from the
    action cap rather than from CU_LINKEDIN_MAX_POSTINGS_PER_SESSION -- the latter only caps what
    gets reported/persisted, not what got looked at. This is the quantity daily_cap_satisfied
    must be checked against to make the daily view cap a real bound rather than a bound on a
    different, looser thing."""
    return (config.CU_LINKEDIN_MAX_ACTIONS_PER_SESSION
            // config.CU_LINKEDIN_WORST_CASE_ACTIONS_PER_POSTING)


# ── X11 action execution ───────────────────────────────────────────────────────

# The display is 1280x800 (see deploy/beelink/systemd/xvfb@.service). Long edge 1280 is under
# the toolset's 2576px limit, so screenshots are sent unscaled and Claude's coordinates apply
# directly to the screen. Do NOT add a scale factor here -- there is no inverse transform to get
# wrong.
_BUTTONS = {"left_click": "1", "middle_click": "2", "right_click": "3"}
_MULTI_CLICKS = {"double_click": "2", "triple_click": "3"}
_SCROLL_BUTTONS = {"up": "4", "down": "5", "left": "6", "right": "7"}


def _x11_env():
    return dict(os.environ, DISPLAY=config.CU_LINKEDIN_DISPLAY)


def _run(command):
    proc = subprocess.run(
        command, check=True, capture_output=True,
        timeout=config.CU_LINKEDIN_SUBPROCESS_TIMEOUT_SECONDS, env=_x11_env(),
    )
    return proc.stdout or b""


def _xdotool(args):
    return _run(["xdotool"] + args)


def _read_png(path):
    with open(path, "rb") as f:
        return f.read()


def _screenshot_content():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "screen.png")
        _run(["scrot", "--overwrite", path])
        data = _read_png(path)
    return [{
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(data).decode("ascii"),
        },
    }]


def _move_to(coordinate):
    x, y = int(coordinate[0]), int(coordinate[1])
    _xdotool(["mousemove", "--sync", str(x), str(y)])


def _current_position():
    # Shared by the cursor_position tool and _glide_to -- a glide has to interpolate from where
    # the cursor actually is, not from the screen origin (0, 0). Defaults to (0, 0) only if
    # xdotool's output is unparseable, which only ever happens before the very first move.
    out = _xdotool(["getmouselocation", "--shell"]).decode("utf-8", errors="replace")
    fields = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    try:
        return int(fields.get("X", 0)), int(fields.get("Y", 0))
    except ValueError:
        return 0, 0


def _glide_to(coordinate):
    # Interpolates from the CURRENT cursor position to the target, not from (0, 0). Gliding from
    # the origin every time -- an earlier draft's bug -- teleports the cursor to a fraction of the
    # way from the top-left corner before walking to the target, which is a stronger automation
    # tell than a single jump, not a weaker one, and made left_click_drag actively wrong (it
    # dragged toward the origin instead of toward the requested end point).
    start_x, start_y = _current_position()
    end_x, end_y = int(coordinate[0]), int(coordinate[1])
    steps = max(1, config.CU_LINKEDIN_MOUSE_STEPS)
    for step in range(1, steps + 1):
        x = start_x + int((end_x - start_x) * step / steps)
        y = start_y + int((end_y - start_y) * step / steps)
        _xdotool(["mousemove", "--sync", str(x), str(y)])


def _dispatch(name, params, rand):
    if name == "screenshot":
        return _screenshot_content(), False

    if name in _BUTTONS or name in _MULTI_CLICKS:
        coordinate = params.get("coordinate")
        if coordinate:
            _glide_to(coordinate)
        modifiers = params.get("text")
        if modifiers:
            _xdotool(["keydown", modifiers])
        if name in _MULTI_CLICKS:
            _xdotool(["click", "--repeat", _MULTI_CLICKS[name], "1"])
        else:
            _xdotool(["click", _BUTTONS[name]])
        if modifiers:
            _xdotool(["keyup", modifiers])
        return "OK", False

    if name == "mouse_move":
        _glide_to(params["coordinate"])
        return "OK", False

    if name == "left_click_drag":
        _move_to(params["start_coordinate"])
        _xdotool(["mousedown", "1"])
        _glide_to(params["coordinate"])
        _xdotool(["mouseup", "1"])
        return "OK", False

    if name in ("left_mouse_down", "left_mouse_up"):
        _xdotool(["mousedown" if name == "left_mouse_down" else "mouseup", "1"])
        return "OK", False

    if name == "cursor_position":
        x, y = _current_position()
        return f"X={x}, Y={y}", False

    if name == "type":
        _xdotool(["type", "--delay", str(keystroke_delay_ms(rand)),
                  "--clearmodifiers", params.get("text", "")])
        return "OK", False

    if name == "key":
        _xdotool(["key", "--repeat", str(int(params.get("repeat", 1))),
                  "--clearmodifiers", params["text"]])
        return "OK", False

    if name == "hold_key":
        duration = min(float(params.get("duration", 1)), config.CU_LINKEDIN_MAX_WAIT_SECONDS)
        _xdotool(["keydown", "--clearmodifiers", params["text"]])
        time.sleep(duration)
        _xdotool(["keyup", "--clearmodifiers", params["text"]])
        return "OK", False

    if name == "scroll":
        coordinate = params.get("coordinate")
        if coordinate:
            _glide_to(coordinate)
        button = _SCROLL_BUTTONS[params.get("scroll_direction", "down")]
        _xdotool(["click", "--repeat", str(int(params.get("scroll_amount", 1))), button])
        return "OK", False

    if name == "wait":
        time.sleep(min(float(params.get("duration", 1)), config.CU_LINKEDIN_MAX_WAIT_SECONDS))
        return "OK", False

    # zoom lands here deliberately: it is disabled in the toolset config (see _TOOLS) so the
    # model should never emit it, and this is the backstop if it ever does.
    return f"Unsupported action: {name}", True


def execute_action(name, params, rand=random.random):
    """Execute one computer-toolset member action against the X11 display. Returns
    (content, is_error) for the caller to wrap in a tool_result. Never raises."""
    try:
        return _dispatch(name, params or {}, rand)
    except Exception as exc:
        log.warning(f"[CU-LINKEDIN] | {name} | action failed: {exc}")
        return f"Action {name} failed: {exc}", True


# ── Computer Use sampling loop ─────────────────────────────────────────────────

# zoom is disabled: at 1280x800 the full screenshot is already under the toolset's pixel limit,
# so zoom would only add a second image-returning member to implement for no legibility gain.
_TOOLS = [{"type": "computer_toolset_20260801", "configs": {"zoom": {"enabled": False}}}]

_SYSTEM = """You are operating a real Chrome window on a Linux desktop, already signed in to
LinkedIn as the operator. Your only job is to READ job postings and report them.

Hard rules:
- Never click Apply, Easy Apply, Connect, Follow, Message, Save, or any button that writes
  something to LinkedIn or to another person. You are read-only.
- Never type into a message box, comment box, or post composer.
- Never attempt to solve, bypass, or work around a CAPTCHA, a security check, or a login
  challenge. If you see one, stop immediately and reply with the exact text
  CAPTCHA_OR_CHALLENGE and nothing else.
- Move deliberately. Take a screenshot, decide one thing, act, then look again.
- When you have gathered what you were asked for, or you cannot make further progress, stop
  calling tools and reply with the JSON array described in the user's instructions."""

_WRAP_UP_PROMPT = (
    "Session limit reached. Stop browsing now and reply with ONLY the JSON array of the "
    "postings you have already collected, in the format you were given. If you collected "
    "none, reply with []."
)


def _final_text(resp):
    return "".join(b.text for b in resp.content
                   if getattr(b, "type", None) == "text").strip()


def _truncation_errors(resp, context):
    # A truncated final answer parses to [] and the session silently records as a success with 0
    # saved -- log it and feed it into the same error count run() reports, so a wedged/truncating
    # session is visible instead of looking identical to "found nothing today".
    if resp.stop_reason == "max_tokens":
        log.warning(f"[CU-LINKEDIN] | {context} | stop_reason=max_tokens, response likely "
                    f"truncated")
        return 1
    return 0


def _execute_tool_uses(blocks, rand=random.random):
    results = []
    executed = 0
    errors = 0
    failed = False
    for block in blocks:
        toolset_name = getattr(block, "toolset_name", None) or "computer"
        if failed:
            results.append({
                "type": "tool_result", "tool_use_id": block.id, "toolset_name": toolset_name,
                "is_error": True,
                "content": "Not executed: an earlier computer action in this turn failed.",
            })
            continue
        time.sleep(next_action_delay(rand))
        content, is_error = execute_action(block.name, dict(block.input or {}), rand=rand)
        executed += 1
        result = {"type": "tool_result", "tool_use_id": block.id,
                  "toolset_name": toolset_name, "content": content}
        if is_error:
            result["is_error"] = True
            failed = True
            errors += 1
        results.append(result)
    return results, executed, errors


def _prune_screenshots(messages):
    # Screenshots are 1,000-1,800 tokens each and a session runs dozens of turns. Only the most
    # recent few are worth resending; older ones become a short text placeholder so the
    # tool_use/tool_result pairing stays intact.
    seen = 0
    for message in reversed(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            if not isinstance(block.get("content"), list):
                continue
            seen += 1
            if seen > config.CU_LINKEDIN_SCREENSHOT_HISTORY:
                block["content"] = "[screenshot pruned to save tokens]"
    return messages


def _call(messages, tool_choice=None, action="session_turn"):
    kwargs = dict(
        model=config.CU_LINKEDIN_MODEL,
        max_tokens=config.CU_LINKEDIN_MAX_TOKENS,
        system=_SYSTEM,
        tools=_TOOLS,
        messages=messages,
    )
    if tool_choice:
        kwargs["tool_choice"] = tool_choice
    resp = _claude.messages.create(**kwargs)
    # contact_id and job_application_id are both None on purpose: one session discovers many
    # postings, so there is no single row to attribute the spend to (same reasoning as
    # extract_voice.py).
    usage_tracking.log_usage(
        "cu_linkedin", action, config.CU_LINKEDIN_MODEL,
        {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
    )
    return resp


def _wrap_up(messages):
    messages.append({"role": "user", "content": _WRAP_UP_PROMPT})
    resp = _call(messages, tool_choice={"type": "none"}, action="wrap_up")
    return _final_text(resp), _truncation_errors(resp, "wrap_up")


def run_session(task_prompt, rand=random.random, now=time.monotonic):
    """Drive one paced Computer Use session and return (final_text, actions_taken, errors)."""
    started = now()
    actions = 0
    errors = 0
    messages = [{"role": "user", "content": task_prompt}]

    for turn in range(config.CU_LINKEDIN_MAX_TURNS):
        resp = _call(messages)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            errors += _truncation_errors(resp, "final answer")
            return _final_text(resp), actions, errors

        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        results, executed, action_errors = _execute_tool_uses(tool_uses, rand=rand)
        actions += executed
        errors += action_errors
        messages.append({"role": "user", "content": results})
        _prune_screenshots(messages)

        if session_exhausted(actions, now() - started):
            log.info(f"[CU-LINKEDIN] | session cap reached | turn={turn} | actions={actions}")
            text, wrap_up_errors = _wrap_up(messages)
            return text, actions, errors + wrap_up_errors

    log.info(f"[CU-LINKEDIN] | max turns reached | actions={actions}")
    text, wrap_up_errors = _wrap_up(messages)
    return text, actions, errors + wrap_up_errors


# ── Posting extraction and persistence ─────────────────────────────────────────

def _strip_json_fence(text):
    # Claude sometimes wraps a JSON response in a ```json fence despite being told not to --
    # same handling as research.py's _generate_queries and resume_agent.py.
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else ""
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


_JOBS_VIEW_ID_RE = re.compile(r"/jobs/view/(\d+)")
_CURRENT_JOB_ID_RE = re.compile(r"(?:^|&)currentJobId=(\d+)")


def _canonical_job_url(url):
    # LinkedIn posting URLs carry a per-impression ?refId=/?trackingId= query string. Dedup in
    # db.create_job_application is an exact match on job_url, so two sightings of one posting
    # would otherwise create two rows -- but on LinkedIn's Jobs surfaces, clicking a result in the
    # list pane commonly leaves the address bar on .../jobs/search/?currentJobId=<id> or
    # .../jobs/collections/recommended/?currentJobId=<id>, where the posting id lives ONLY in the
    # query string. The old blind split("?")[0] stripped that id along with the tracking noise,
    # collapsing every posting seen in a session onto the same bare "recommended" URL and
    # poisoning the exact-match dedup (25 real postings -> 1 garbage row + 24 false "already
    # tracked" skips, permanently, since that one URL stays poisoned for every future session
    # too). A URL that resolves to neither the canonical /jobs/view/<id> shape nor a recoverable
    # currentJobId isn't a posting -- return None so the caller skips it instead of persisting a
    # garbage or missing job_url (governance: "a posting that can't be parsed is skipped, never
    # inserted with None fields").
    if not isinstance(url, str):
        return None
    cleaned = url.strip().split("#", 1)[0]
    if not cleaned:
        return None
    path, _, query = cleaned.partition("?")
    path = path.rstrip("/")

    match = _JOBS_VIEW_ID_RE.search(path)
    if match:
        return f"https://www.linkedin.com/jobs/view/{match.group(1)}"

    match = _CURRENT_JOB_ID_RE.search(query)
    if match:
        return f"https://www.linkedin.com/jobs/view/{match.group(1)}"

    return None


def _clean_text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def extract_postings(text):
    """Parse the session's final reply into clean posting dicts. Never raises -- returns []."""
    try:
        parsed = json.loads(_strip_json_fence(text))
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []

    postings = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        company = _clean_text(entry.get("company"))
        role = _clean_text(entry.get("role"))
        job_url = _canonical_job_url(entry.get("job_url"))
        # Governance: a posting missing any of the three required fields degrades to
        # not-observed. It is never inserted with a None column.
        if not (company and role and job_url):
            continue
        postings.append({
            "company": company,
            "role": role,
            "job_url": job_url,
            "location": _clean_text(entry.get("location")) or "",
            "description": _clean_text(entry.get("description")) or "",
            "source": "linkedin",
        })
    return postings


def persist_postings(postings):
    """Write each posting into job_applications at stage='saved'. Returns
    (saved, skipped, errors). One row's failure never stops the rest."""
    saved = skipped = errors = 0
    for posting in postings[:config.CU_LINKEDIN_MAX_POSTINGS_PER_SESSION]:
        try:
            row = db.create_job_application(
                company=posting["company"],
                role=posting["role"],
                job_url=posting["job_url"],
                source="linkedin",
                posting_snapshot=posting,
            )
            if row is None:
                skipped += 1
                log.info(f"[CU-LINKEDIN] | {posting['role']} | {posting['company']} | "
                         f"skipped (already tracked)")
            else:
                saved += 1
                log.info(f"[CU-LINKEDIN] | {posting['role']} | {posting['company']} | saved")
        except Exception as exc:
            errors += 1
            log.warning(f"[CU-LINKEDIN] | {posting.get('role')} | {posting.get('company')} | "
                        f"persist error: {exc}")
    return saved, skipped, errors


# ── Public entry point ─────────────────────────────────────────────────────────

_TASK_PROMPT = """Open the LinkedIn Jobs tab that is already loaded in this Chrome window and
review the job recommendations there. For each posting you open, note the company, the role
title, the posting URL shown in the address bar, the location, and a one-paragraph summary of
the description.

Look at no more than {max_postings} postings, then stop.

When you are done, reply with ONLY a JSON array (no prose, no markdown fence) where each element
is an object with exactly these keys: "company", "role", "job_url", "location", "description".
If you found nothing, reply with []."""

_CAPTCHA_SENTINEL = "CAPTCHA_OR_CHALLENGE"


def run():
    """Run one paced LinkedIn discovery session and persist what it found. Never raises."""
    start = time.time()
    saved = skipped = errors = 0

    if not config.CU_LINKEDIN_ENABLED:
        log.info("[CU-LINKEDIN] | disabled via config.CU_LINKEDIN_ENABLED, skipping")
        return

    # LinkedIn browsing is the highest-consequence activity in this whole system -- it risks the
    # user's real account -- so it must respect the global pause switch, same as agent.py and
    # monitor.py. No record_run call on the paused exit, matching monitor.py's own rule: this
    # check can be hit far more often than a real session runs and must not flood agent_runs.
    if db.get_pause_scope() in ("agent", "all"):
        log.info("[CU-LINKEDIN] | PAUSED | skipping (pause_scope)")
        return

    log.info("[CU-LINKEDIN] | START")
    try:
        text, _actions, session_errors = run_session(
            _TASK_PROMPT.format(max_postings=config.CU_LINKEDIN_MAX_POSTINGS_PER_SESSION)
        )
        errors += session_errors
        if _CAPTCHA_SENTINEL in (text or ""):
            # Never solved, never bypassed, never retried with a workaround: a human VNCs in.
            log.warning("[CU-LINKEDIN] | CAPTCHA or login challenge -- needs a human at the VNC "
                        "console for display slot 0")
        else:
            postings = extract_postings(text)
            log.info(f"[CU-LINKEDIN] | extracted={len(postings)}")
            if (text or "").strip() and not postings:
                # A non-empty reply that yields zero postings could be a genuine "nothing found"
                # session or a broken extraction path -- these must not look identical on the
                # first live run. Never log the text itself (may be large, this is a log line, not
                # a debug dump), only its length.
                log.info(f"[CU-LINKEDIN] | extraction yielded 0 postings from a non-empty reply "
                         f"| reply_len={len(text)}")
            saved, skipped, persist_errors = persist_postings(postings)
            errors += persist_errors
    except Exception as exc:
        errors += 1
        log.warning(f"[CU-LINKEDIN] | unexpected error: {exc}")

    log.info(f"[CU-LINKEDIN] | DONE | saved={saved} | skipped={skipped} | errors={errors}")
    try:
        db.record_run("failure" if errors else "success", saved, skipped, errors,
                      round(time.time() - start), source="cu_linkedin")
    except Exception as exc:
        log.warning(f"[CU-LINKEDIN] | record_run failed: {exc}")


if __name__ == "__main__":
    logging.basicConfig(
        filename="cu_linkedin.log",
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M",
    )
    run()
