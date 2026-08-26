"""Tests for emailer.hash_prompt_set -- the decision-context prompt fingerprint."""

import re

import pytest

import emailer


_HEX16 = re.compile(r"^[0-9a-f]{16}$")


def test_hash_is_16_char_lowercase_hex():
    assert _HEX16.match(emailer.hash_prompt_set({"outreach_prompt": "hello"}))


def test_hash_is_deterministic_for_the_same_dict():
    prompts = {"outreach_prompt": "a", "subject_prompt": "b"}
    assert emailer.hash_prompt_set(prompts) == emailer.hash_prompt_set(prompts)


def test_hash_ignores_key_insertion_order():
    a = {"outreach_prompt": "a", "subject_prompt": "b"}
    b = {"subject_prompt": "b", "outreach_prompt": "a"}
    assert emailer.hash_prompt_set(a) == emailer.hash_prompt_set(b)


def test_hash_changes_when_a_value_changes():
    before = emailer.hash_prompt_set({"outreach_prompt": "a"})
    after = emailer.hash_prompt_set({"outreach_prompt": "a "})
    assert before != after


def test_hash_changes_when_a_key_is_added():
    before = emailer.hash_prompt_set({"outreach_prompt": "a"})
    after = emailer.hash_prompt_set({"outreach_prompt": "a", "voice_dna": ""})
    assert before != after


@pytest.mark.parametrize("prompts", [{}, None])
def test_hash_handles_empty_and_none(prompts):
    assert _HEX16.match(emailer.hash_prompt_set(prompts))


def test_empty_and_none_hash_identically():
    # None is the "prompts table unreachable" fallback in agent.run(); it must
    # group with the empty-dict case rather than crash or produce a third value.
    assert emailer.hash_prompt_set(None) == emailer.hash_prompt_set({})


def test_hash_handles_unserializable_values():
    # default=str keeps a non-JSON value (a date, an object) from raising.
    assert _HEX16.match(emailer.hash_prompt_set({"k": object()}))


def test_hash_is_pure_and_does_not_mutate_its_input():
    prompts = {"outreach_prompt": "a"}
    emailer.hash_prompt_set(prompts)
    assert prompts == {"outreach_prompt": "a"}
