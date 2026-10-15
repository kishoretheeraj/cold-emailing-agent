"""Shared Claude-on-subscription helper for the v2 pipeline.

Every v2 model call goes through claude_subscription.complete (never the Messages API, never an API key)
and is logged to api_usage_log with billing='subscription'.
"""

import json
import re

import claude_subscription
import usage_tracking

HAIKU_MODEL = "claude-haiku-4-5-20251001"


# ── Calls ──────────────────────────────────────────────────────────────────────

def call_claude(prompt: str, system: str, model: str, action: str, max_output_tokens: int | None = None) -> str:
    """Run one subscription completion and record its usage. Raises claude_subscription errors as-is."""
    text, usage = claude_subscription.complete(
        prompt, system=system, model=model, max_output_tokens=max_output_tokens)
    usage_tracking.log_usage("resume_v2", action, model, usage, billing="subscription")
    return text


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_json_object(text: str) -> dict:
    """Parse a JSON object out of a model reply, tolerating a markdown fence or surrounding prose.
    Raises ValueError when no object can be recovered."""
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        value = json.loads(body)
    except ValueError:
        start, end = body.find("{"), body.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in model reply")
        value = json.loads(body[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("model reply is not a JSON object")
    return value


def tagged_json(tag: str, value) -> str:
    """Wrap a JSON value in <tag> delimiters; any '</' inside is escaped so data cannot close the tag."""
    body = json.dumps(value, indent=1, ensure_ascii=False).replace("</", "<\\/")
    return f"<{tag}>\n{body}\n</{tag}>"
