"""
Shared Claude API cost calculation and logging, used by every module that calls Claude
(emailer.py's _call_claude, resume_agent.py's own client) to write a fact-based record to
api_usage_log. Real, not estimated -- callers always pass the actual token counts from the
Anthropic API response's usage field.
"""

import logging

import config
import db

log = logging.getLogger(__name__)


def calculate_cost(model, input_tokens, output_tokens):
    """Raises KeyError if model isn't in config.MODEL_PRICING -- a missing price should surface
    immediately, not silently produce a wrong (zero or guessed) cost."""
    input_price, output_price = config.MODEL_PRICING[model]
    return input_tokens / 1_000_000 * input_price + output_tokens / 1_000_000 * output_price


def log_usage(module, action, model, usage, contact_id=None, job_application_id=None):
    """Compute cost and write one api_usage_log row. Best-effort -- never raises, matching this
    repo's enrichment-never-costs-a-draft posture (the caller is mid-generation; a logging or
    pricing-table gap must not block or fail it)."""
    try:
        cost = calculate_cost(model, usage["input_tokens"], usage["output_tokens"])
        db.log_api_usage(
            module=module, action=action, model=model,
            input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
            cost_usd=cost, contact_id=contact_id, job_application_id=job_application_id,
        )
    except Exception as exc:
        log.warning(f"[USAGE] | {module} | {action} | log_usage failed: {exc}")
