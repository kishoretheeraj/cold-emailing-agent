"""Tests for resume_lint.py. Every check is a pure function -- no mocking needed."""

import resume_lint


# ── check_em_dashes ───────────────────────────────────────────────────────────

def test_check_em_dashes_flags_em_dash():
    assert resume_lint.check_em_dashes("Led the team — shipped on time") != []


def test_check_em_dashes_passes_clean_text():
    assert resume_lint.check_em_dashes("Led the team, shipped on time") == []


# ── check_jargon ───────────────────────────────────────────────────────────────

def test_check_jargon_flags_banned_term_case_insensitively():
    jargon_map = {"NBFI": "digital lending company"}
    violations = resume_lint.check_jargon("Worked at an nbfi startup", jargon_map)
    assert len(violations) == 1
    assert "digital lending company" in violations[0]


def test_check_jargon_passes_clean_text():
    jargon_map = {"NBFI": "digital lending company"}
    assert resume_lint.check_jargon("Worked at a digital lending company", jargon_map) == []


# ── _extract_numbers ──────────────────────────────────────────────────────────

def test_extract_numbers_finds_dollar_and_percent_and_multiplier():
    result = resume_lint._extract_numbers("$20K/year, 32% reduction, 30x speedup")
    assert "$20K" in result
    assert "32%" in result
    assert "30x" in result


# ── check_metrics_whitelist ────────────────────────────────────────────────────

_METRICS = [
    {"id": "vendor_cost", "role": "APM", "text": "$20K/year vendor cost eliminated",
     "resolved": None, "conflicting_values": ["$120K/year (draft)"]},
    {"id": "audit_hours", "role": "APM", "text": "110 verified hours/month saved",
     "resolved": True, "conflicting_values": []},
]


def test_check_metrics_whitelist_flags_unresolved_conflict_number():
    violations = resume_lint.check_metrics_whitelist("Eliminated $120K/year in vendor cost", _METRICS)
    assert len(violations) == 1
    assert "vendor_cost" in violations[0]


def test_check_metrics_whitelist_flags_own_text_number_when_unresolved():
    violations = resume_lint.check_metrics_whitelist("Eliminated $20K/year in vendor cost", _METRICS)
    assert len(violations) == 1
    assert "vendor_cost" in violations[0]


def test_check_metrics_whitelist_passes_resolved_metric():
    violations = resume_lint.check_metrics_whitelist("Saved 110 verified hours/month", _METRICS)
    assert violations == []


def test_check_metrics_whitelist_passes_text_with_no_numbers():
    assert resume_lint.check_metrics_whitelist("Led cross-functional collaboration", _METRICS) == []


# ── check_cover_letter ─────────────────────────────────────────────────────────

_RESUME_TEXT = "Reduced loan processing time by 32% through AI-assisted document review."


def test_check_cover_letter_flags_number_shared_with_resume():
    cl = "I drove a 32% improvement in processing efficiency at my last role."
    violations = resume_lint.check_cover_letter(cl, _RESUME_TEXT)
    assert any("32%" in v for v in violations)


def test_check_cover_letter_flags_shared_six_word_phrase():
    cl = "I focused on loan processing time by 32% through automation last year."
    violations = resume_lint.check_cover_letter(cl, _RESUME_TEXT)
    assert any("shared 6-word phrase" in v for v in violations)


def test_check_cover_letter_flags_three_capability_enumeration():
    cl = ("First, I bring analytical rigor. Second, I bring stakeholder empathy. "
          "Third, I bring execution speed. " + "Filler word. " * 30)
    violations = resume_lint.check_cover_letter(cl, "")
    assert any("enumerates exactly three capabilities" in v for v in violations)


def test_check_cover_letter_flags_banned_opener():
    cl = "I am writing to apply for this exciting role at your company. " + "Filler. " * 30
    violations = resume_lint.check_cover_letter(cl, "")
    assert any("banned phrase" in v for v in violations)


def test_check_cover_letter_flags_word_count_over_limit():
    cl = "word " * 301
    violations = resume_lint.check_cover_letter(cl, "")
    assert any("word count" in v for v in violations)


def test_check_cover_letter_flags_closing_hedge_stack():
    cl = "Body sentence here. " + "Filler word. " * 30 + \
         "I might possibly perhaps be a good fit if you think it could work."
    violations = resume_lint.check_cover_letter(cl, "")
    assert any("hedge words" in v for v in violations)


def test_check_cover_letter_passes_clean_letter():
    cl = ("The rubric row where two labelers disagreed taught me that most labeling "
          "error is rubric debt, not labeler error. I'd bring that same instinct to "
          "your team's data quality work. " + "Additional context sentence. " * 20 +
          "I look forward to discussing this further.")
    violations = resume_lint.check_cover_letter(cl, _RESUME_TEXT)
    assert violations == []
