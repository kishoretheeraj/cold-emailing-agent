"""apply_worker.py scheduling (first-ten-applications Phase D). apply_agent's per-row work and every
db call are mocked; the display lock is real (a temp file), since flock semantics are the point."""

import fcntl
from datetime import datetime

import pytest

import apply_worker
import claude_subscription
import config


@pytest.fixture(autouse=True)
def _wired(mocker, tmp_path):
    mocker.patch.object(config, "APPLY_DISPLAY_LOCK", str(tmp_path / "display1.lock"))
    mocker.patch.object(apply_worker.db, "get_pause_scope", return_value="none")
    mocker.patch.object(apply_worker.db, "recover_stale_leases", return_value=0)
    mocker.patch.object(apply_worker.db, "get_approved_application_ids", return_value=[])
    mocker.patch.object(apply_worker.db, "get_job_application", return_value={"automation_status": "ready_for_review"})
    mocker.patch.object(apply_worker.db, "log_application_run", return_value=1)
    mocker.patch.object(apply_worker.apply_agent, "preview_candidates", return_value=[])
    mocker.patch.object(apply_worker.apply_agent, "_process_one_preview", return_value="filled")
    mocker.patch.object(apply_worker.apply_agent, "submit")
    mocker.patch.object(apply_worker.job_sourcing, "load_search_settings",
                        return_value=(apply_worker.job_sourcing.job_filters.load_preferences({}), {}))
    mocker.patch.object(apply_worker.db, "count_submit_attempts_since", return_value=0)


def _jobs(*ids):
    return [{"id": i, "company": f"Co{i}"} for i in ids]


# ── display lock ───────────────────────────────────────────────────────────────

def test_lock_is_exclusive_and_released(tmp_path):
    with apply_worker.display_lock(blocking=False) as first:
        assert first is True
        with apply_worker.display_lock(blocking=False) as second:
            assert second is False
    with apply_worker.display_lock(blocking=False) as again:
        assert again is True


def test_blocking_lock_gives_up_after_its_timeout(mocker):
    sleeps = mocker.patch.object(apply_worker.time, "sleep")
    clock = iter([0, 0, 1, 2, 3, 99])
    mocker.patch.object(apply_worker.time, "monotonic", side_effect=lambda: next(clock))
    with open(config.APPLY_DISPLAY_LOCK, "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with apply_worker.display_lock(blocking=True, timeout_seconds=3) as got:
            assert got is False
    assert sleeps.call_count >= 1


# ── prepare ────────────────────────────────────────────────────────────────────

def test_prepare_processes_candidates_and_logs_each_outcome():
    apply_worker.apply_agent.preview_candidates.return_value = _jobs(1, 2)
    assert apply_worker.run_prepare(limit=5) == 0
    assert apply_worker.apply_agent._process_one_preview.call_count == 2
    calls = apply_worker.db.log_application_run.call_args_list
    assert [c.kwargs["application_id"] for c in calls] == [1, 2]
    assert all(c.kwargs["kind"] == "prepare" and c.kwargs["outcome"] == "ready" for c in calls)
    assert isinstance(calls[0].kwargs["started_at"], datetime)


def test_prepare_respects_the_limit():
    apply_worker.apply_agent.preview_candidates.return_value = _jobs(1, 2, 3)
    apply_worker.run_prepare(limit=2)
    assert apply_worker.apply_agent._process_one_preview.call_count == 2


def test_skipped_rows_do_not_use_up_the_limit_or_the_run_log():
    # fifty-a-day F4: rows the host can only skip used to fill the whole batch, every run.
    apply_worker.apply_agent.preview_candidates.return_value = _jobs(1, 2, 3, 4, 5)
    apply_worker.apply_agent._process_one_preview.side_effect = ["skipped", "skipped", "filled", "blocked", "filled"]
    apply_worker.run_prepare(limit=2)
    assert apply_worker.apply_agent._process_one_preview.call_count == 4
    assert [c.kwargs["application_id"] for c in apply_worker.db.log_application_run.call_args_list] == [3, 4]


def test_a_failed_row_counts_toward_the_limit():
    apply_worker.apply_agent.preview_candidates.return_value = _jobs(1, 2, 3)
    apply_worker.apply_agent._process_one_preview.side_effect = [RuntimeError("x"), "filled", "filled"]
    apply_worker.run_prepare(limit=2)
    assert apply_worker.apply_agent._process_one_preview.call_count == 2


@pytest.mark.parametrize("scope", ["agent", "all"])
def test_prepare_does_nothing_while_paused(scope):
    apply_worker.db.get_pause_scope.return_value = scope
    apply_worker.apply_agent.preview_candidates.return_value = _jobs(1)
    assert apply_worker.run_prepare(limit=5) == 0
    apply_worker.apply_agent._process_one_preview.assert_not_called()


def test_prepare_skips_the_run_while_the_display_is_busy():
    apply_worker.apply_agent.preview_candidates.return_value = _jobs(1)
    with open(config.APPLY_DISPLAY_LOCK, "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert apply_worker.run_prepare(limit=5) == 0
    apply_worker.apply_agent._process_one_preview.assert_not_called()


def test_prepare_yields_to_a_waiting_approval_between_rows():
    apply_worker.apply_agent.preview_candidates.return_value = _jobs(1, 2, 3)
    apply_worker.db.get_approved_application_ids.side_effect = [[], [7]]
    apply_worker.run_prepare(limit=5)
    assert apply_worker.apply_agent._process_one_preview.call_count == 1


def test_prepare_isolates_one_row_failure_and_logs_its_status():
    apply_worker.apply_agent.preview_candidates.return_value = _jobs(1, 2)
    apply_worker.apply_agent._process_one_preview.side_effect = [RuntimeError("page crashed"), "filled"]
    apply_worker.db.get_job_application.side_effect = [
        {"automation_status": "failed_retryable"}, {"automation_status": "ready_for_review"}]
    assert apply_worker.run_prepare(limit=5) == 1
    first, second = apply_worker.db.log_application_run.call_args_list
    assert first.kwargs["outcome"] == "failed_retryable"
    assert first.kwargs["error_class"] == "RuntimeError"
    assert "page crashed" in first.kwargs["stop_reason"]
    assert second.kwargs["outcome"] == "ready"


@pytest.mark.parametrize("exc", [
    claude_subscription.ClaudeUsageLimitError("usage limit"),
    claude_subscription.ClaudeSubscriptionError("token missing"),
])
def test_prepare_stops_on_a_subscription_failure(exc):
    apply_worker.apply_agent.preview_candidates.return_value = _jobs(1, 2)
    apply_worker.apply_agent._process_one_preview.side_effect = exc
    assert apply_worker.run_prepare(limit=5) == 1
    assert apply_worker.apply_agent._process_one_preview.call_count == 1


@pytest.mark.parametrize("status,outcome", [
    ("needs_input", "needs_input"), ("unsupported", "unsupported"), ("idle", "skipped"),
    ("preparing", "skipped"),
])
def test_prepare_outcome_comes_from_the_row_status(status, outcome):
    apply_worker.apply_agent.preview_candidates.return_value = _jobs(1)
    apply_worker.db.get_job_application.return_value = {"automation_status": status}
    apply_worker.run_prepare(limit=5)
    assert apply_worker.db.log_application_run.call_args.kwargs["outcome"] == outcome


def test_prepare_never_needs_or_uses_the_armed_flag(mocker):
    mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": ""})
    apply_worker.apply_agent.preview_candidates.return_value = _jobs(1)
    apply_worker.run_prepare(limit=5)
    apply_worker.apply_agent.submit.assert_not_called()


# ── submit ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def armed(mocker):
    mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": "1"})


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", " 1"])
def test_submit_refuses_unless_armed_is_exactly_1(mocker, value):
    env = {} if value is None else {"APPLY_AGENT_ARMED": value}
    mocker.patch.dict("os.environ", env, clear=False)
    if value is None:
        import os
        os.environ.pop("APPLY_AGENT_ARMED", None)
    apply_worker.db.get_approved_application_ids.return_value = [5]
    assert apply_worker.run_submit(limit=3) == 1
    apply_worker.apply_agent.submit.assert_not_called()


def test_submit_runs_approved_rows_in_order(armed):
    apply_worker.db.get_approved_application_ids.return_value = [5, 6]
    apply_worker.db.get_job_application.return_value = {"automation_status": "submitted"}
    assert apply_worker.run_submit(limit=3) == 0
    assert [c.args[0] for c in apply_worker.apply_agent.submit.call_args_list] == [5, 6]
    outcomes = [c.kwargs["outcome"] for c in apply_worker.db.log_application_run.call_args_list]
    assert outcomes == ["submitted", "submitted"]


def test_submit_with_nothing_approved_never_takes_the_lock(armed):
    with open(config.APPLY_DISPLAY_LOCK, "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert apply_worker.run_submit(limit=3) == 0
    apply_worker.apply_agent.submit.assert_not_called()


def test_submit_waits_and_gives_up_if_the_display_stays_busy(armed, mocker):
    mocker.patch.object(config, "APPLY_SUBMIT_LOCK_WAIT_SECONDS", 0)
    apply_worker.db.get_approved_application_ids.return_value = [5]
    with open(config.APPLY_DISPLAY_LOCK, "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert apply_worker.run_submit(limit=3) == 0
    apply_worker.apply_agent.submit.assert_not_called()


@pytest.mark.parametrize("scope", ["agent", "all"])
def test_submit_does_nothing_while_paused(armed, scope):
    apply_worker.db.get_pause_scope.return_value = scope
    apply_worker.db.get_approved_application_ids.return_value = [5]
    assert apply_worker.run_submit(limit=3) == 0
    apply_worker.apply_agent.submit.assert_not_called()


def test_submit_isolates_a_failed_row_and_logs_its_real_status(armed):
    apply_worker.db.get_approved_application_ids.return_value = [5, 6]
    apply_worker.apply_agent.submit.side_effect = [RuntimeError("no confirmation"), None]
    apply_worker.db.get_job_application.side_effect = [
        {"automation_status": "needs_confirmation"}, {"automation_status": "submitted"}]
    assert apply_worker.run_submit(limit=3) == 1
    outcomes = [c.kwargs["outcome"] for c in apply_worker.db.log_application_run.call_args_list]
    assert outcomes == ["needs_confirmation", "submitted"]


def test_submit_respects_the_limit(armed):
    apply_worker.db.get_approved_application_ids.return_value = [5, 6, 7]
    apply_worker.run_submit(limit=1)
    assert apply_worker.apply_agent.submit.call_count == 1


# ── entry point ────────────────────────────────────────────────────────────────

def test_main_requires_exactly_one_mode():
    with pytest.raises(SystemExit):
        apply_worker.main([])
    with pytest.raises(SystemExit):
        apply_worker.main(["--prepare", "--submit"])


def test_main_returns_the_error_count(mocker):
    mocker.patch.object(apply_worker, "run_prepare", return_value=2)
    assert apply_worker.main(["--prepare"]) == 2
    apply_worker.run_prepare.assert_called_once_with(config.APPLY_PREPARE_BATCH)



# ── daily cap (fifty-a-day F14) ────────────────────────────────────────────────

def _cap(cap):
    prefs = apply_worker.job_sourcing.job_filters.load_preferences({})
    prefs["daily_submit_cap"] = cap
    apply_worker.job_sourcing.load_search_settings.return_value = (prefs, {})


def test_submit_stops_at_the_daily_cap(armed):
    _cap(50)
    apply_worker.db.get_approved_application_ids.return_value = [1, 2, 3]
    apply_worker.db.count_submit_attempts_since.return_value = 50
    assert apply_worker.run_submit(limit=3) == 0
    apply_worker.apply_agent.submit.assert_not_called()


def test_submit_takes_only_what_is_left_of_the_day(armed):
    _cap(50)
    apply_worker.db.get_approved_application_ids.return_value = [1, 2, 3]
    apply_worker.db.count_submit_attempts_since.return_value = 49
    apply_worker.run_submit(limit=3)
    assert [c.args[0] for c in apply_worker.apply_agent.submit.call_args_list] == [1]


def test_the_day_starts_at_midnight_new_york(armed):
    _cap(50)
    apply_worker.db.get_approved_application_ids.return_value = [1]
    apply_worker.run_submit(limit=1)
    since = apply_worker.db.count_submit_attempts_since.call_args.args[0]
    from zoneinfo import ZoneInfo
    local = since.astimezone(ZoneInfo("America/New_York"))
    assert (local.hour, local.minute) == (0, 0) and since.utcoffset().total_seconds() == 0


def test_an_unreadable_count_submits_nothing(armed):
    _cap(50)
    apply_worker.db.get_approved_application_ids.return_value = [1]
    apply_worker.db.count_submit_attempts_since.side_effect = RuntimeError("db down")
    apply_worker.run_submit(limit=1)
    apply_worker.apply_agent.submit.assert_not_called()


def test_a_cap_of_zero_means_no_cap(armed):
    _cap(0)
    apply_worker.db.get_approved_application_ids.return_value = [1, 2]
    apply_worker.run_submit(limit=2)
    assert apply_worker.apply_agent.submit.call_count == 2
    apply_worker.db.count_submit_attempts_since.assert_not_called()
