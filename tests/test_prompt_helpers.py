"""Tests for the prompt-loading helpers in emailer.py.

Each helper checks the Supabase prompts dict first, then falls back to the
config.py constant and logs a warning. Tests use a sentinel value to prove
the dict value was used (not the fallback), and verify the fallback fires
with a warning when the key is absent.
"""

import logging
import pytest

import emailer
from config import TIER_INSTRUCTIONS, TEMPLATE_INSTRUCTIONS, DARTMOUTH_INSTRUCTION


# ── get_tier_instruction ────────────────────────────────────────────────────

@pytest.mark.parametrize("tier", [1, 2, 3])
def test_get_tier_instruction_uses_db_value(tier):
    sentinel = f"SENTINEL_TIER_{tier}"
    prompts = {f"tier_{tier}_instruction": sentinel}
    assert emailer.get_tier_instruction(prompts, tier) == sentinel


@pytest.mark.parametrize("tier", [1, 2, 3])
def test_get_tier_instruction_fallback_on_missing(tier):
    result = emailer.get_tier_instruction({}, tier)
    assert result == TIER_INSTRUCTIONS[tier]


@pytest.mark.parametrize("tier", [1, 2, 3])
def test_get_tier_instruction_logs_warning_on_fallback(tier, caplog):
    with caplog.at_level(logging.WARNING, logger="emailer"):
        emailer.get_tier_instruction({}, tier)
    assert f"tier_{tier}_instruction" in caplog.text
    assert "fallback" in caplog.text


def test_get_tier_instruction_unknown_tier_falls_back_to_tier2():
    # tier=99 not in TIER_INSTRUCTIONS; helper should return TIER_INSTRUCTIONS[2]
    result = emailer.get_tier_instruction({}, 99)
    assert result == TIER_INSTRUCTIONS[2]


# ── get_template_instruction ────────────────────────────────────────────────

_TEMPLATE_KEY_MAP = {
    "cold_intro":  "outreach_first_touch_instruction",
    "follow_up_1": "outreach_followup1_instruction",
    "follow_up_2": "outreach_followup2_instruction",
    "breakup":     "outreach_breakup_instruction",
}


@pytest.mark.parametrize("template,prompt_key", _TEMPLATE_KEY_MAP.items())
def test_get_template_instruction_uses_db_value(template, prompt_key):
    sentinel = f"SENTINEL_{prompt_key}"
    prompts = {prompt_key: sentinel}
    assert emailer.get_template_instruction(prompts, template) == sentinel


@pytest.mark.parametrize("template", _TEMPLATE_KEY_MAP)
def test_get_template_instruction_fallback_on_missing(template):
    result = emailer.get_template_instruction({}, template)
    assert result == TEMPLATE_INSTRUCTIONS[template]


@pytest.mark.parametrize("template", _TEMPLATE_KEY_MAP)
def test_get_template_instruction_logs_warning_on_fallback(template, caplog):
    with caplog.at_level(logging.WARNING, logger="emailer"):
        emailer.get_template_instruction({}, template)
    assert "fallback" in caplog.text


def test_get_template_instruction_unknown_template_returns_empty():
    result = emailer.get_template_instruction({}, "nonexistent_template")
    assert result == ""


# ── get_dartmouth_instruction ───────────────────────────────────────────────

def test_get_dartmouth_instruction_dart_false_always_empty():
    # Should return "" regardless of dict contents.
    assert emailer.get_dartmouth_instruction({"dartmouth_instruction": "x"}, False) == ""
    assert emailer.get_dartmouth_instruction({}, False) == ""


def test_get_dartmouth_instruction_uses_db_value():
    sentinel = "SENTINEL_DARTMOUTH"
    assert emailer.get_dartmouth_instruction({"dartmouth_instruction": sentinel}, True) == sentinel


def test_get_dartmouth_instruction_fallback_on_missing():
    result = emailer.get_dartmouth_instruction({}, True)
    assert result == DARTMOUTH_INSTRUCTION


def test_get_dartmouth_instruction_logs_warning_on_fallback(caplog):
    with caplog.at_level(logging.WARNING, logger="emailer"):
        emailer.get_dartmouth_instruction({}, True)
    assert "dartmouth_instruction" in caplog.text
    assert "fallback" in caplog.text
