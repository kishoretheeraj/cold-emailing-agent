"""job_sourcing: triage, dedup and the board registry wired together (spec 2026-10-08 fifty-a-day
§3.3). Sources and the database are mocked; tests/test_stress_local.py runs it against Postgres."""

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

import db
import job_filters
import job_sourcing

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
PREFS = job_filters.load_preferences({})
NEEDS = {"requires_visa_sponsorship": "Yes"}


def _job(**over):
    base = {"company": "Figma", "title": "Associate Product Manager",
            "url": "https://boards.greenhouse.io/figma/jobs/1", "location": "SF",
            "posted_at": (NOW - timedelta(days=2)).isoformat(), "description": "Own the roadmap.",
            "sponsorship": "Other", "source": "greenhouse"}
    return {**base, **over}


@pytest.fixture
def saver(mocker):
    return mocker.patch.object(db, "save_job_application", return_value=({"id": 1}, None))


# ── save ───────────────────────────────────────────────────────────────────────

def test_save_writes_a_passing_posting_with_its_snapshot(saver):
    stats = Counter()
    assert job_sourcing.save(_job(), PREFS, NEEDS, stats, now=NOW) == "saved"
    kwargs = saver.call_args.kwargs
    assert kwargs["company"] == "Figma" and kwargs["role"] == "Associate Product Manager"
    assert kwargs["job_url"] == "https://boards.greenhouse.io/figma/jobs/1"
    assert kwargs["source"] == "greenhouse" and kwargs["location"] == "SF"
    assert kwargs["posting_snapshot"]["description"] == "Own the roadmap."
    assert stats == Counter(saved=1)


@pytest.mark.parametrize("over,outcome", [
    ({"title": "Senior Product Manager"}, "skipped_seniority"),
    ({"location": "London, UK"}, "skipped_location"),
    ({"posted_at": (NOW - timedelta(days=60)).isoformat()}, "skipped_stale"),
    ({"sponsorship": "Does Not Offer Sponsorship"}, "skipped_no_sponsorship"),
])
def test_save_never_writes_a_filtered_posting(saver, over, outcome):
    stats = Counter()
    assert job_sourcing.save(_job(**over), PREFS, NEEDS, stats, now=NOW) == outcome
    saver.assert_not_called()
    assert stats[outcome] == 1


def test_save_counts_duplicates_by_reason(saver):
    saver.return_value = (None, "same_role")
    stats = Counter()
    assert job_sourcing.save(_job(), PREFS, NEEDS, stats, now=NOW) == "duplicate_same_role"


def test_workday_detail_is_fetched_only_for_postings_that_pass_the_cheap_cuts(saver, mocker):
    def add_detail(job):
        job["description"] = "We are unable to sponsor visas for this role."
        return job
    detail = mocker.patch.object(job_sourcing.job_sources, "add_detail", side_effect=add_detail)
    stats = Counter()
    wd = dict(_job(source="workday", description="", url="https://a.wd1.myworkdayjobs.com/E/job/X/PM_R1"))
    assert job_sourcing.save(dict(wd, title="Store Manager"), PREFS, NEEDS, stats, now=NOW) == "skipped_title"
    detail.assert_not_called()
    assert job_sourcing.save(wd, PREFS, NEEDS, stats, now=NOW) == "skipped_no_sponsorship"
    detail.assert_called_once()
    saver.assert_not_called()


def test_one_failing_save_is_counted_not_raised(saver):
    saver.side_effect = RuntimeError("db down")
    stats = Counter()
    assert job_sourcing.save(_job(), PREFS, NEEDS, stats, now=NOW) == "error"
    assert stats["error"] == 1


# ── settings ───────────────────────────────────────────────────────────────────

def test_settings_come_from_the_prompts_table(mocker):
    mocker.patch.object(db, "load_prompts", return_value={
        "job_search_preferences": json.dumps({"daily_submit_cap": 20}),
        "applicant_eligibility": json.dumps({"requires_visa_sponsorship": "Yes"})})
    prefs, eligibility = job_sourcing.load_search_settings()
    assert prefs["daily_submit_cap"] == 20
    assert eligibility == {"requires_visa_sponsorship": "Yes"}


@pytest.mark.parametrize("prompts", [RuntimeError("down"), {"applicant_eligibility": "not json"},
                                     {"applicant_eligibility": "[1]"}])
def test_settings_fall_back_to_defaults(mocker, prompts):
    if isinstance(prompts, Exception):
        mocker.patch.object(db, "load_prompts", side_effect=prompts)
    else:
        mocker.patch.object(db, "load_prompts", return_value=prompts)
    prefs, eligibility = job_sourcing.load_search_settings()
    assert prefs == job_filters.DEFAULT_PREFERENCES and eligibility == {}


# ── run ────────────────────────────────────────────────────────────────────────

@pytest.fixture
def stack(mocker, saver):
    mocker.patch.object(db, "get_pause_scope", return_value="none")
    mocker.patch.object(db, "load_prompts", return_value={})
    mocker.patch.object(db, "get_rows_missing_identity", return_value=[])
    mocker.patch.object(db, "add_job_boards")
    mocker.patch.object(db, "get_sourcing_boards", return_value=[])
    mocker.patch.object(db, "record_board_scan")
    mocker.patch.object(db, "record_run")
    mocker.patch.object(job_sourcing.time, "sleep")
    simplify = mocker.patch.object(job_sourcing.job_sources, "fetch_simplify", return_value=[])
    board = mocker.patch.object(job_sourcing.job_sources, "fetch_board", return_value=([], "ok"))
    return MagicMock(simplify=simplify, board=board, saver=saver)


def test_paused_run_does_nothing(stack, mocker):
    db.get_pause_scope.return_value = "agent"
    assert job_sourcing.run(now=NOW) == Counter()
    stack.simplify.assert_not_called()
    stack.saver.assert_not_called()


def test_run_learns_boards_saves_category_rows_and_sweeps(stack):
    stack.simplify.return_value = [
        dict(_job(), category="Product"),
        dict(_job(company="Palantir", title="Software Engineer", url="https://jobs.lever.co/palantir/"
                  "0a1b2c3d-1111-2222-3333-444455556666"), category="Software"),
    ]
    db.get_sourcing_boards.return_value = [{"id": 7, "platform": "ashby", "board": "fireworks", "company": "Fireworks AI"}]
    stack.board.return_value = ([_job(company="Fireworks AI", url="https://jobs.ashbyhq.com/fireworks/"
                                      "69375f41-258a-4c25-ad78-5985606c438a", source="ashby")], "ok")
    stats = job_sourcing.run(now=NOW)
    boards = {(b["platform"], b["board"]) for b in db.add_job_boards.call_args.args[0]}
    assert boards == {("greenhouse", "figma"), ("lever", "palantir")}
    assert stats["saved"] == 2                       # the Product row and the swept Ashby row
    db.record_board_scan.assert_called_once_with(db.get_sourcing_boards.return_value[0], "ok", 1,
                                                 job_sourcing.config.SOURCING_BOARD_DEAD_AFTER_FAILURES)
    run_args = db.record_run.call_args
    assert run_args.args[0] == "success" and run_args.args[1] == 2 and run_args.kwargs["source"] == "job_sourcing"


def test_a_job_seen_twice_in_one_run_is_saved_once(stack):
    stack.simplify.return_value = [dict(_job(), category="Product")]
    db.get_sourcing_boards.return_value = [{"id": 1, "platform": "greenhouse", "board": "figma"}]
    stack.board.return_value = ([_job(url="https://job-boards.greenhouse.io/figma/jobs/1?gh_src=x")], "ok")
    stats = job_sourcing.run(now=NOW)
    assert stack.saver.call_count == 1
    assert stats["duplicate_in_run"] == 1


def test_run_backfills_identity_first(stack, mocker):
    db.get_rows_missing_identity.return_value = [{"id": 1}, {"id": 2}]
    mocker.patch.object(db, "backfill_identity", side_effect=["set", "withdrawn"])
    stats = job_sourcing.run(now=NOW)
    assert stats["backfill_set"] == 1 and stats["backfill_withdrawn"] == 1


def test_run_survives_every_database_failure(stack):
    for name in ("get_rows_missing_identity", "add_job_boards", "get_sourcing_boards", "record_run"):
        getattr(db, name).side_effect = RuntimeError("supabase down")
    stack.simplify.return_value = [dict(_job(), category="Product")]
    stack.saver.side_effect = RuntimeError("supabase down")
    stats = job_sourcing.run(now=NOW)
    assert stats["error"] >= 3


def test_a_board_scan_that_cannot_be_recorded_does_not_stop_the_sweep(stack):
    db.get_sourcing_boards.return_value = [{"id": 1, "platform": "lever", "board": "a"},
                                           {"id": 2, "platform": "lever", "board": "b"}]
    db.record_board_scan.side_effect = RuntimeError("write failed")
    job_sourcing.run(now=NOW)
    assert stack.board.call_count == 2
