"""scripts/stress/capacity.py: the OnCalendar arithmetic, the funnel, and the shipped units."""

import math
import sys

import pytest

sys.path.insert(0, "scripts/stress")
import capacity  # noqa: E402


@pytest.mark.parametrize("expr,runs", [
    ("*-*-* 00/2:13:00", 12),
    ("*:*:00", 1440),
    ("*:0/20", 72),
    ("*:0/30", 48),
    ("09,13,17:00", 3),
    ("*-*-* 08:15:00", 1),
    ("6/6:00", 3),
])
def test_firings_per_day(expr, runs):
    assert capacity.firings_per_day(expr) == runs


@pytest.mark.parametrize("expr", ["Mon *-*-* 10:00", "*:*:0/10", "daily", "10"])
def test_unsupported_calendars_are_refused_not_guessed(expr):
    with pytest.raises((ValueError, KeyError)):
        capacity.firings_per_day(expr)


def test_shipped_units_are_read_from_the_repo():
    facts = capacity.repo_facts()
    units = facts["units"]
    assert units["job-sourcing"]["runs_per_day"] == 12
    assert units["job-pick"]["runs_per_day"] == 12
    assert units["resume-worker"]["runs_per_day"] == 48
    assert units["apply-prepare"]["runs_per_day"] == 72
    assert units["apply-submit"]["runs_per_day"] == 1440
    assert all(u["timeout_seconds"] for u in units.values())


def _facts(**over):
    units = {u: {"runs_per_day": 24, "timeout_seconds": 3600} for u in
             ("job-sourcing", "job-pick", "resume-worker", "apply-prepare", "apply-submit")}
    facts = {"units": units, "job_pick_max_per_run": 100, "job_pick_judge_batch": 10, "resume_batch": 5,
             "prepare_batch": 5, "submit_batch": 3, "daily_submit_cap": 50}
    facts.update(over)
    return facts


def _all_ones(**over):
    a = {k: (1.0 if isinstance(v, float) else v) for k, v in capacity.ASSUMPTIONS.items()}
    a["new_postings_per_day"] = 10_000
    a.update(over)
    return a


def test_a_perfect_funnel_needs_one_row_per_submission():
    result = capacity.model(_facts(), _all_ones(), 50)
    assert result["stages"]["resume"]["demand"] == 50
    assert result["stages"]["supply"]["demand"] == 50
    assert result["bottleneck"] == "daily_cap"
    assert result["achievable_per_day"] == 50


def test_rates_compound_backwards_through_the_funnel():
    result = capacity.model(_facts(), _all_ones(approve_rate=0.5, strong_rate=0.25), 10)
    # 10 submits / 0.5 approved = 20 prepared; 20 resumes / 0.25 strong = 80 judged
    assert result["stages"]["prepare"]["demand"] == 20
    assert result["stages"]["judge"]["demand"] == 80


def test_supply_short_of_demand_is_the_bottleneck():
    result = capacity.model(_facts(), _all_ones(new_postings_per_day=25), 50)
    assert result["bottleneck"] == "supply"
    assert result["achievable_per_day"] == 25


def test_a_unit_timeout_caps_rows_per_run():
    facts = _facts()
    facts["units"]["resume-worker"] = {"runs_per_day": 2, "timeout_seconds": 600}
    result = capacity.model(facts, _all_ones(resume_seconds_per_row=300), 50)
    assert result["stages"]["resume"]["ceiling"] == 4          # 2 runs x min(5, 600 // 300)
    assert result["bottleneck"] == "resume"
    assert result["achievable_per_day"] == 4


def test_prepare_and_submit_share_the_display():
    facts = _facts(daily_submit_cap=0)
    facts["units"]["apply-prepare"]["timeout_seconds"] = 10**9
    result = capacity.model(facts, _all_ones(prepare_seconds_per_row=3600, submit_seconds_per_row=0), 30)
    assert result["stages"]["display"]["demand"] == 30 * 3600
    assert result["bottleneck"] == "display"
    assert result["achievable_per_day"] == 24
    assert result["stages"]["daily_cap"]["ceiling"] == math.inf


def test_subscription_calls_are_counted():
    result = capacity.model(_facts(), _all_ones(resume_calls_per_row=2, prepare_calls_per_row=1), 50)
    assert result["subscription_calls_per_day"] == 5 + 100 + 50


def test_cli_exits_nonzero_when_the_target_is_out_of_reach(capsys):
    assert capacity.main(["--target", "50", "--new-postings-per-day", "10"]) == 1
    out = capsys.readouterr().out
    assert "bottleneck: supply" in out and "assumptions" in out


def test_cli_json(capsys):
    assert capacity.main(["--target", "1", "--json", "--new-postings-per-day", "100000"]) == 0
    assert '"bottleneck"' in capsys.readouterr().out
