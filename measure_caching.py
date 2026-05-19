"""
Token size audit for every Claude call path in this agent.

Usage:
    python3 measure_caching.py

Loads live prompts from Supabase (falls back to config.py defaults if the DB
is unreachable), counts tokens for the system block of each call site via the
Anthropic count_tokens endpoint, and prints a table showing which system prompts
are above the minimum cache threshold.

Cache thresholds (prompt caching documentation):
  Sonnet 4.6 (claude-sonnet-4-6)       : 1024 tokens
  Haiku  4.5 (claude-haiku-4-5-20251001): 1024 tokens  (conservative; may be higher)
"""

import sys
from dotenv import load_dotenv
load_dotenv()

import anthropic
import config

# ── Threshold map ─────────────────────────────────────────────────────────────
CACHE_MIN = {
    config.EMAIL_MODEL:               1024,
    config.REPLY_RESPONSE_MODEL:      1024,
    config.REPLY_CLASSIFICATION_MODEL: 1024,
    config.RESEARCH_QUERY_MODEL:      1024,
    config.RESEARCH_CURATE_MODEL:     1024,
}

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _count_system_tokens(model, system_text):
    """Return the token count for a system block using the count_tokens endpoint."""
    resp = _client.messages.count_tokens(
        model=model,
        system=system_text,
        messages=[{"role": "user", "content": "x"}],
    )
    # Subtract 1 (the "x" user turn) to isolate the system token count.
    return max(0, resp.input_tokens - 1)


def _load_prompts():
    try:
        from db import load_prompts
        prompts = load_prompts() or {}
        print("Prompts loaded from Supabase.\n")
        return prompts
    except Exception as exc:
        print(f"Supabase unavailable ({exc}), using config.py defaults.\n")
        return {}


def _row(call_site, model, system_tokens, threshold, system_preview):
    status = "ACTIVE" if system_tokens >= threshold else f"below ({threshold} needed)"
    preview = (system_preview[:60] + "...") if len(system_preview) > 60 else system_preview
    preview = preview.replace("\n", " ")
    return call_site, model.split("-")[1], system_tokens, status, preview


def main():
    prompts = _load_prompts()
    sender_profile = prompts.get("sender_profile", config.SENDER_PROFILE)

    rows = []
    errors = []

    call_sites = [
        # (label, model, system_text)
        ("_generate_outreach",       config.EMAIL_MODEL,               sender_profile),
        ("_generate_applied_intro",  config.EMAIL_MODEL,               sender_profile),
        ("_generate_applied_followup", config.EMAIL_MODEL,             sender_profile),
        ("_generate_subject",        config.EMAIL_MODEL,               sender_profile),
        ("_run_critic",              config.EMAIL_MODEL,               sender_profile),
        ("_generate_reply_body",     config.REPLY_RESPONSE_MODEL,      sender_profile),
        ("_generate_queries",        config.RESEARCH_QUERY_MODEL,      sender_profile),
    ]

    print(f"{'Call site':<30} {'Model':<8} {'Sys tokens':>10} {'Cache status':<22} System preview")
    print("-" * 110)

    for label, model, system_text in call_sites:
        try:
            tok = _count_system_tokens(model, system_text)
            threshold = CACHE_MIN.get(model, 1024)
            row = _row(label, model, tok, threshold, system_text)
            rows.append(row)
            status_flag = "✓" if row[3] == "ACTIVE" else "✗"
            print(f"{row[0]:<30} {row[1]:<8} {row[2]:>10} {status_flag} {row[3]:<20} {row[4]}")
        except Exception as exc:
            errors.append((label, str(exc)))
            print(f"{label:<30} ERROR: {exc}")

    no_system_sites = [
        ("_curate_brief",      config.RESEARCH_CURATE_MODEL,      "no system (all content contact-specific)"),
        ("_classify_reply",    config.REPLY_CLASSIFICATION_MODEL, "no system (instructions < threshold; variable reply body)"),
    ]

    print()
    print("Call sites with no system prompt (caching not applicable):")
    for label, model, reason in no_system_sites:
        short_model = model.split("-")[1]
        print(f"  {label:<28} [{short_model}]  {reason}")

    print()
    active = sum(1 for r in rows if r[3] == "ACTIVE")
    total  = len(rows)
    print(f"Summary: {active}/{total} system-bearing call sites above cache threshold.")

    if active < total:
        print()
        print("To activate caching on the dormant sites, grow the sender_profile in")
        print("Supabase (/prompts → Sender Profile) until it exceeds the threshold.")
        print("All call sites already have cache_control: ephemeral wired — no code")
        print("change needed once the threshold is crossed.")

    if errors:
        print()
        print("Errors during token counting:")
        for label, msg in errors:
            print(f"  {label}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
