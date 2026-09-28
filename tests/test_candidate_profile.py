"""Tests for candidate_profile.py -- the shared real-candidate-facts text used by both
job_pick.py's LLM judge and apply_agent.py's screening-answer prompt (merge review
2026-09-28, finding 3). Reads the real resume/data/master.json + metrics.json files, same as
job_pick.py's own pre-existing profile-text test."""

import candidate_profile


def test_profile_text_includes_real_role_and_project_bullets():
    text = candidate_profile.profile_text()
    assert "Protium Finance" in text or "Associate Product Manager" in text
    assert len(text) > 50


def test_profile_text_is_cached_across_calls():
    first = candidate_profile.profile_text()
    second = candidate_profile.profile_text()
    assert first is second
