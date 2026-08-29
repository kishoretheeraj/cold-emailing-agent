"""Tests for usage_tracking.py. calculate_cost is pure; log_usage wraps db.log_api_usage
best-effort (never raises, matching this repo's enrichment-never-costs-a-draft posture)."""

import pytest

import config
import db
import usage_tracking


# ── calculate_cost ───────────────────────────────────────────────────────────

def test_calculate_cost_sonnet():
    cost = usage_tracking.calculate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert cost == pytest.approx(3.0 + 15.0)


def test_calculate_cost_haiku():
    cost = usage_tracking.calculate_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
    assert cost == pytest.approx(1.0 + 5.0)


def test_calculate_cost_raises_on_unknown_model():
    with pytest.raises(KeyError):
        usage_tracking.calculate_cost("some-unpriced-model", 100, 100)


# ── log_usage ──────────────────────────────────────────────────────────────

def test_log_usage_computes_cost_and_calls_db(mocker):
    log_api_usage = mocker.patch.object(db, "log_api_usage", return_value={"id": 1})
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    usage_tracking.log_usage("emailer", "first_touch", "claude-sonnet-4-6", usage, contact_id=5)
    log_api_usage.assert_called_once_with(
        module="emailer", action="first_touch", model="claude-sonnet-4-6",
        input_tokens=1_000_000, output_tokens=1_000_000, cost_usd=pytest.approx(18.0),
        contact_id=5, job_application_id=None,
    )


def test_log_usage_never_raises_on_db_failure(mocker):
    mocker.patch.object(db, "log_api_usage", side_effect=RuntimeError("db down"))
    usage = {"input_tokens": 100, "output_tokens": 100}
    usage_tracking.log_usage("emailer", "first_touch", "claude-sonnet-4-6", usage)  # must not raise


def test_log_usage_never_raises_on_unknown_model(mocker):
    log_api_usage = mocker.patch.object(db, "log_api_usage")
    usage = {"input_tokens": 100, "output_tokens": 100}
    usage_tracking.log_usage("emailer", "first_touch", "some-unpriced-model", usage)  # must not raise
    log_api_usage.assert_not_called()
