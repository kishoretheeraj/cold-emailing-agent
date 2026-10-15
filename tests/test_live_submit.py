"""Workstream C: live_submit() and its exactly-once guarantee.

Covers submit_guard (keys, ledger, atomic reservation, pacing, redaction, outcome classification),
the fail-closed gates, the click seam (_click_and_observe) being called exactly once, the observation
loop, the reconcile CLI, and structural checks on the source. Pure tests: no network, no browser.
playwright is stubbed because apply_pipeline imports it at module level.
"""

import hashlib
import inspect
import json
import os
import re
import stat
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_sentinel = object()
_had = {name: sys.modules.get(name, _sentinel) for name in ("playwright", "playwright.sync_api")}
_pw = MagicMock()
_pw_sync = MagicMock()
_pw_sync.Error = Exception
_pw_sync.TimeoutError = Exception
sys.modules["playwright"] = _pw
sys.modules["playwright.sync_api"] = _pw_sync
try:
    import apply_pipeline as ap  # noqa: E402
finally:
    for _name, _prev in _had.items():
        if _prev is _sentinel:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _prev
del _sentinel, _had, _pw, _pw_sync, _name, _prev

import reconcile_submits as rs  # noqa: E402
import submit_guard  # noqa: E402
import universal_filler  # noqa: E402
from submit_guard import LedgerError, SubmitConflict, SubmitLedger  # noqa: E402

URL = "https://boards.example.com/acme/jobs/123?gh_src=abc"
GOOD_EVIDENCE = {"requests": [["POST", "/acme/jobs/123/apply", 200]], "final_url_path": "/acme/confirmation",
                 "url_changed": True, "form_present": False, "platform": "greenhouse", "text_hit": True}
DOMAINS = ("mail.example.com",)


# ── Keys ───────────────────────────────────────────────────────────────────────

def test_idempotency_key_is_deterministic_and_distinct():
    key = submit_guard.idempotency_key("job-1", "att-1", "pk")
    assert key == hashlib.sha256(b"job-1|att-1|pk").hexdigest()[:32]
    assert key == submit_guard.idempotency_key("job-1", "att-1", "pk")
    assert key != submit_guard.idempotency_key("job-2", "att-1", "pk")
    assert key != submit_guard.idempotency_key("job-1", "att-2", "pk")
    assert key != submit_guard.idempotency_key("job-1", "att-1", "pk2")


@pytest.mark.parametrize("args", [("", "a", "p"), ("j", None, "p"), ("j", "a", ""), (1, "a", "p"), ("j", "a", b"p")])
def test_idempotency_key_rejects_bad_inputs(args):
    with pytest.raises(ValueError):
        submit_guard.idempotency_key(*args)


@pytest.mark.parametrize("url,expected", [
    ("HTTPS://Jobs.Example.com:443/apply/1?x=1#frag", "https://jobs.example.com/apply/1"),
    ("http://a.com:80", "http://a.com/"),
    ("https://a.com:8443/p/", "https://a.com:8443/p/"),
    ("https://a.com", "https://a.com/"),
    ("ftp://a.com/x", None),
    ("not a url", None),
    ("", None),
    ("   ", None),
    (None, None),
    (42, None),
])
def test_canonical_apply_url(url, expected):
    assert submit_guard.canonical_apply_url(url) == expected


def test_posting_key_ignores_query_and_propagates_none():
    assert submit_guard.posting_key(URL) == submit_guard.posting_key("https://BOARDS.example.com:443/acme/jobs/123#x")
    assert submit_guard.posting_key(URL) != submit_guard.posting_key("https://boards.example.com/acme/jobs/124")
    assert submit_guard.posting_key("nope") is None
    assert len(submit_guard.posting_key(URL)) == 64


def test_redact_text():
    out = submit_guard.redact_text("mail a.b@c.com or +1 603-322-0535, order 123456 and 12345")
    assert "@" not in out and "[email]" in out and "[phone]" in out
    assert "[redacted]" in out and "12345" in out and "123456" not in out
    assert len(submit_guard.redact_text("x" * 5000)) == 4000
    assert submit_guard.redact_text("x" * 50, limit=10) == "x" * 10
    assert submit_guard.redact_text(None) == "" and submit_guard.redact_text(b"bytes") == ""


def test_sanitize_path_replaces_long_opaque_runs_only():
    assert submit_guard.sanitize_path("/apply/" + "a" * 25) == "/apply/[token]"
    assert submit_guard.sanitize_path("/v1/ab_" + "Z9-" * 8 + "/submit") == "/v1/[token]/submit"
    assert submit_guard.sanitize_path("/acme/jobs/123/apply") == "/acme/jobs/123/apply"
    assert submit_guard.sanitize_path("/" + "a" * 19) == "/" + "a" * 19
    assert submit_guard.sanitize_path(None) == ""


# ── Ledger ─────────────────────────────────────────────────────────────────────

def test_ledger_lifecycle_intent_then_outcome_closes(tmp_path):
    led = SubmitLedger(str(tmp_path / "state" / "l.jsonl"))
    assert not led.has_live_intent_for_job("j1")
    led.append_intent("k1", "j1", "a1", "p1", ts=1000.0)
    assert led.has_live_intent_for_job("j1") and led.has_live_intent_for_posting("p1")
    assert led.is_blocked_for_job("j1") and led.is_blocked_for_posting("p1")
    led.append_outcome("k1", "confirmed", {"x": 1})
    assert not led.has_live_intent_for_job("j1") and not led.has_live_intent_for_posting("p1")
    assert led.is_blocked_for_job("j1") and led.is_blocked_for_posting("p1")  # confirmed stays blocked


@pytest.mark.parametrize("result,blocked", [("confirmed", True), ("ambiguous", True),
                                            ("rejected", False), ("not_sent", False)])
def test_only_rejected_and_not_sent_free_a_job(tmp_path, result, blocked):
    led = SubmitLedger(str(tmp_path / "l.jsonl"))
    led.append_intent("k", "j", "a", "p")
    led.append_outcome("k", result, {})
    assert led.is_blocked_for_job("j") is blocked
    assert led.is_blocked_for_posting("p") is blocked


def test_human_override_paths(tmp_path):
    led = SubmitLedger(str(tmp_path / "l.jsonl"))
    led.append_intent("k", "j", "a", "p")
    led.append_outcome("k", "ambiguous", {})
    led.append_human_override("k", "submitted_confirmed", "saw the email")
    assert led.is_blocked_for_job("j")  # a confirmed send keeps the job blocked
    led.append_human_override("k", "not_sent_cleared", "checked by hand")
    assert not led.is_blocked_for_job("j") and not led.is_blocked_for_posting("p")
    assert not led.has_live_intent_for_job("j")


def test_not_sent_cleared_makes_an_unresolved_intent_not_live(tmp_path):
    led = SubmitLedger(str(tmp_path / "l.jsonl"))
    led.append_intent("k", "j", "a", "p")
    assert led.has_live_intent_for_job("j")
    led.append_human_override("k", "not_sent_cleared", "crash before the click")
    assert not led.has_live_intent_for_job("j") and not led.has_live_intent_for_posting("p")


@pytest.mark.parametrize("prior", ["rejected", "not_sent", "ambiguous"])
def test_submitted_confirmed_is_authoritative_over_any_prior_state(tmp_path, prior):
    led = SubmitLedger(str(tmp_path / "l.jsonl"))
    led.append_intent("k", "j", "a", "p", ts=1000.0)
    led.append_outcome("k", prior, {})
    led.append_human_override("k", "not_sent_cleared", "thought nothing was sent")
    assert not led.is_blocked_for_job("j")
    led.append_human_override("k", "submitted_confirmed", "the receipt arrived after all")
    entry = led.entries()["k"]
    assert entry["outcome"] == "confirmed" and entry["cleared"] is False and entry["human_confirmed"] is True
    assert led.is_blocked_for_job("j") and led.is_blocked_for_posting("p")
    assert led.intents_last_24h(1500.0) == 1  # a hand-confirmed send counts toward the cap
    led.append_outcome("k", "rejected", {})  # a late pipeline record cannot undo a human confirmation
    assert led.is_blocked_for_job("j")


def test_a_freed_key_can_be_reserved_again(tmp_path):
    led = SubmitLedger(str(tmp_path / "l.jsonl"))
    led.append_intent("k", "j", "a", "p")
    led.append_outcome("k", "not_sent", {})
    assert not led.is_blocked_for_job("j")
    led.append_intent("k", "j", "a", "p")
    assert led.has_live_intent_for_job("j") and led.is_blocked_for_job("j")


def test_ledger_validation_and_blank_lines(tmp_path):
    path = tmp_path / "l.jsonl"
    led = SubmitLedger(str(path))
    with pytest.raises(ValueError):
        led.append_outcome("k", "maybe", {})
    with pytest.raises(ValueError):
        led.append_human_override("k", "reopen", "why")
    assert led.records() == []  # no file yet: an empty ledger, not an error
    led.append_intent("k", "j", "a", "p")
    with open(path, "a") as fh:
        fh.write("\n  \n")  # blank lines are harmless
    led.append_outcome("k", "confirmed", {})
    assert [r["type"] for r in led.records()] == ["intent", "outcome"]


@pytest.mark.parametrize("junk", ["{not json\n", "[1, 2]\n", "42\n", '"text"\n'])
def test_malformed_ledger_fails_closed(tmp_path, junk):
    path = tmp_path / "l.jsonl"
    led = SubmitLedger(str(path))
    led.append_intent("k", "j", "a", "p")
    with open(path, "a") as fh:
        fh.write(junk)
    before = path.read_text()
    for call in (led.records, led.entries, lambda: led.is_blocked_for_job("j"), lambda: led.rate_ok(1000.0),
                 lambda: led.reserve_intent("k2", "j2", "a", "p2", 10, 0),
                 lambda: led.recheck_reservation("k", "j", "p"),
                 lambda: led.recheck_and_dispatch("k", "j", "p", 0)):
        with pytest.raises(LedgerError):
            call()
    assert path.read_text() == before  # a failed reservation or dispatch wrote nothing


_DROP = object()
_INTENT = {"type": "intent", "key": "k", "job_id": "j", "posting_key": "p", "attempt_id": "a", "ts": 1000.0}
_OUTCOME = {"type": "outcome", "key": "k", "result": "confirmed", "ts": 1000.0}
_OVERRIDE = {"type": "human_override", "key": "k", "action": "not_sent_cleared", "ts": 1000.0}
_DISPATCH = {"type": "dispatch", "key": "k", "ts": 1000.0}


def _line(base, **changes):
    rec = dict(base)
    for name, value in changes.items():
        if value is _DROP:
            rec.pop(name, None)
        else:
            rec[name] = value
    return json.dumps(rec)


BAD_LINES = {
    "intent_without_key": _line(_INTENT, key=_DROP),
    "intent_key_not_a_string": _line(_INTENT, key=123),
    "intent_empty_key": _line(_INTENT, key=""),
    "intent_without_job_id": _line(_INTENT, job_id=_DROP),
    "intent_empty_job_id": _line(_INTENT, job_id=""),
    "intent_without_posting_key": _line(_INTENT, posting_key=_DROP),
    "intent_attempt_id_not_a_string": _line(_INTENT, attempt_id=5),
    "intent_without_attempt_id": _line(_INTENT, attempt_id=_DROP),
    "intent_without_ts": _line(_INTENT, ts=_DROP),
    "intent_zero_ts": _line(_INTENT, ts=0),
    "intent_negative_ts": _line(_INTENT, ts=-5),
    "intent_string_ts": _line(_INTENT, ts="1000"),
    "intent_bool_ts": _line(_INTENT, ts=True),
    "intent_nan_ts": _line(_INTENT, ts=float("nan")),
    "intent_infinite_ts": _line(_INTENT, ts=float("inf")),
    "outcome_without_key": _line(_OUTCOME, key=_DROP),
    "outcome_unknown_result": _line(_OUTCOME, result="maybe"),
    "outcome_without_result": _line(_OUTCOME, result=_DROP),
    "outcome_result_is_a_list": _line(_OUTCOME, result=[]),
    "outcome_result_is_a_dict": _line(_OUTCOME, result={}),
    "outcome_result_is_a_number": _line(_OUTCOME, result=5),
    "outcome_result_is_null": _line(_OUTCOME, result=None),
    "outcome_without_ts": _line(_OUTCOME, ts=_DROP),
    "override_without_key": _line(_OVERRIDE, key=_DROP),
    "override_unknown_action": _line(_OVERRIDE, action="reopen"),
    "override_action_is_a_dict": _line(_OVERRIDE, action={}),
    "override_action_is_a_list": _line(_OVERRIDE, action=["not_sent_cleared"]),
    "override_action_is_a_number": _line(_OVERRIDE, action=1),
    "override_without_action": _line(_OVERRIDE, action=_DROP),
    "dispatch_without_key": _line(_DISPATCH, key=_DROP),
    "dispatch_without_ts": _line(_DISPATCH, ts=_DROP),
    "unknown_future_type": _line(_DISPATCH, type="future"),
    "missing_type": _line(_DISPATCH, type=_DROP),
}


@pytest.mark.parametrize("line", list(BAD_LINES.values()), ids=list(BAD_LINES))
def test_structurally_invalid_records_fail_closed(tmp_path, line):
    path = tmp_path / "l.jsonl"
    led = SubmitLedger(str(path))
    led.append_intent("good", "jg", "a", "pg")
    with open(path, "a") as fh:
        fh.write(line + "\n")
    before = path.read_text()
    for call in (led.records, led.entries, lambda: led.is_blocked_for_job("jg"), led.last_dispatch_ts,
                 lambda: led.reserve_intent("k2", "j2", "a", "p2", 10, 0),
                 lambda: led.recheck_reservation("good", "jg", "pg"),
                 lambda: led.recheck_and_dispatch("good", "jg", "pg", 0)):
        with pytest.raises(LedgerError):
            call()
    assert path.read_text() == before


@pytest.mark.parametrize("field,value,base", [
    ("result", [], _OUTCOME), ("result", {}, _OUTCOME), ("action", {}, _OVERRIDE), ("action", [], _OVERRIDE)])
def test_unhashable_enum_values_raise_ledger_error_not_type_error(tmp_path, field, value, base):
    path = tmp_path / "l.jsonl"
    path.write_text(_line(base, **{field: value}) + "\n")
    led = SubmitLedger(str(path))
    with pytest.raises(LedgerError) as exc:
        led.records()
    assert not isinstance(exc.value, TypeError)
    with pytest.raises(LedgerError):
        led.entries()


def test_the_writer_refuses_records_the_reader_would_reject(tmp_path):
    path = tmp_path / "l.jsonl"
    led = SubmitLedger(str(path))
    for call in (lambda: led.append_intent("", "j", "a", "p"), lambda: led.append_intent("k", "j", "a", None),
                 lambda: led.append_intent("k", "j", "a", "p", ts=0), lambda: led.append_outcome("", "confirmed", {}),
                 lambda: led.append_dispatch(""), lambda: led.append_dispatch("k", ts=-1),
                 lambda: led.append_human_override("", "not_sent_cleared", "r"),
                 lambda: led.reserve_intent("", "j", "a", "p", 10, 0),
                 lambda: led.recheck_and_dispatch("", "j", "p", 0)):
        with pytest.raises(ValueError):
            call()
    assert not path.exists()  # nothing reached the disk, so the ledger stays readable


def test_unreadable_ledger_path_fails_closed(tmp_path):
    (tmp_path / "dir.jsonl").mkdir()
    led = SubmitLedger(str(tmp_path / "dir.jsonl"))
    with pytest.raises(LedgerError):
        led.records()
    with pytest.raises(LedgerError):
        led.entries()
    with pytest.raises(LedgerError):
        led.reserve_intent("k", "j", "a", "p", 10, 0)
    with pytest.raises(LedgerError):
        led.recheck_and_dispatch("k", "j", "p", 0)


def test_ledger_is_private_and_default_path_comes_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLY_SUBMIT_LEDGER_PATH", str(tmp_path / "d" / "env.jsonl"))
    led = SubmitLedger()
    led.append_intent("k", "j", "a", "p")
    assert led.path == str(tmp_path / "d" / "env.jsonl")
    assert stat.S_IMODE(os.stat(led.path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "d").st_mode) == 0o700


# ── Dispatch records and spacing ───────────────────────────────────────────────

def test_dispatch_records_do_not_count_and_the_last_dispatch_is_the_max(tmp_path):
    led = SubmitLedger(str(tmp_path / "l.jsonl"))
    assert led.last_dispatch_ts() is None
    led.append_intent("k", "j", "a", "p", ts=1000.0)
    led.append_dispatch("k", ts=1100.0)
    led.append_dispatch("k", ts=1050.0)
    assert led.last_dispatch_ts() == 1100.0
    assert led.entries()["k"]["dispatch_ts"] == 1100.0
    assert led.intents_last_24h(1200.0) == 1  # a dispatch is not another intent
    assert [r["type"] for r in led.records()] == ["intent", "dispatch", "dispatch"]
    assert led.is_blocked_for_job("j")


def test_spacing_is_measured_from_the_dispatch_not_the_reservation(tmp_path):
    led = SubmitLedger(str(tmp_path / "l.jsonl"))
    assert led.reserve_intent("k1", "j1", "a", "p1", 10, 180, now=1000.0) == (1000.0, 0.0)
    # the gap wait delayed this click until 1180: spacing for the next job runs from there
    led.append_dispatch("k1", ts=1180.0)
    ts, wait_s = led.reserve_intent("k2", "j2", "a", "p2", 10, 180, now=1190.0)
    assert (ts, wait_s) == (1190.0, 170.0)  # from the dispatch (1180), not the stale reservation (1000 -> 0)


def test_spacing_falls_back_to_the_last_intent_before_any_dispatch(tmp_path):
    led = SubmitLedger(str(tmp_path / "l.jsonl"))
    led.reserve_intent("k1", "j1", "a", "p1", 10, 180, now=1000.0)
    assert led.reserve_intent("k2", "j2", "a", "p2", 10, 180, now=1100.0)[1] == 80.0


def test_recheck_reservation_raises_a_gap_conflict_after_a_recent_dispatch(tmp_path):
    led = SubmitLedger(str(tmp_path / "l.jsonl"))
    led.append_intent("mine", "j", "a", "p", ts=1000.0)
    led.append_dispatch("other", ts=1100.0)
    led.recheck_reservation("mine", "j", "p")  # no spacing given: the gap is not checked
    led.recheck_reservation("mine", "j", "p", 180, now=1280.0)  # exactly spacing_s later is allowed
    with pytest.raises(SubmitConflict) as exc:
        led.recheck_reservation("mine", "j", "p", 180, now=1150.0)
    assert exc.value.reason == "rate_limit_gap"


# ── Atomic recheck + dispatch ──────────────────────────────────────────────────

def test_recheck_and_dispatch_records_the_dispatch_and_paces_the_next_caller(tmp_path):
    led = SubmitLedger(str(tmp_path / "l.jsonl"))
    led.append_intent("mine", "j", "a", "p", ts=1000.0)
    assert led.recheck_and_dispatch("mine", "j", "p", 180, now=1100.0) == 1100.0
    assert led.last_dispatch_ts() == 1100.0 and led.entries()["mine"]["dispatch_ts"] == 1100.0
    with pytest.raises(SubmitConflict) as exc:  # a second caller inside the gap is refused ...
        led.recheck_and_dispatch("mine", "j", "p", 180, now=1200.0)
    assert exc.value.reason == "rate_limit_gap"
    assert [r["type"] for r in led.records()] == ["intent", "dispatch"]  # ... and writes nothing
    assert led.recheck_and_dispatch("mine", "j", "p", 180, now=1280.0) == 1280.0  # exactly the gap later: allowed
    assert [r["type"] for r in led.records()] == ["intent", "dispatch", "dispatch"]
    assert led.last_dispatch_ts() == 1280.0


def test_recheck_and_dispatch_refuses_another_blocking_intent_and_writes_nothing(tmp_path):
    led = SubmitLedger(str(tmp_path / "l.jsonl"))
    led.append_intent("mine", "j", "a", "p", ts=1000.0)
    led.append_intent("same-job", "j", "a2", "p2", ts=1001.0)
    with pytest.raises(SubmitConflict) as exc:
        led.recheck_and_dispatch("mine", "j", "p", 0, now=1100.0)
    assert exc.value.reason == "duplicate_submit_intent job"
    led.append_outcome("same-job", "rejected", {})  # freed: no longer blocks
    led.append_intent("same-posting", "j9", "a3", "p", ts=1002.0)
    with pytest.raises(SubmitConflict) as exc:
        led.recheck_and_dispatch("mine", "j", "p", 0, now=1100.0)
    assert exc.value.reason == "duplicate_submit_intent posting"
    assert "dispatch" not in [r["type"] for r in led.records()]
    led.append_outcome("same-posting", "not_sent", {})
    assert led.recheck_and_dispatch("mine", "j", "p", 0, now=1100.0) == 1100.0


def test_concurrent_recheck_and_dispatch_admits_exactly_one(tmp_path):
    led = SubmitLedger(str(tmp_path / "l.jsonl"))
    for i in range(8):
        led.append_intent(f"k{i}", f"job{i}", "a", f"posting{i}", ts=1000.0 + i)
    results, lock = [], threading.Lock()

    def worker(i):
        try:
            led.recheck_and_dispatch(f"k{i}", f"job{i}", f"posting{i}", 180, now=2000.0)
            outcome = "ok"
        except SubmitConflict as exc:
            outcome = exc.reason
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1 and results.count("rate_limit_gap") == 7
    assert [r["type"] for r in led.records()].count("dispatch") == 1


# ── Atomic reservation ─────────────────────────────────────────────────────────

def test_reserve_intent_blocks_duplicates_enforces_the_cap_and_reports_the_gap(tmp_path):
    led = SubmitLedger(str(tmp_path / "state" / "l.jsonl"))
    assert led.reserve_intent("k1", "j1", "a1", "p1", 2, 100, now=1000.0) == (1000.0, 0.0)
    assert stat.S_IMODE(os.stat(led.path).st_mode) == 0o600
    with pytest.raises(SubmitConflict) as exc:
        led.reserve_intent("k2", "j1", "a2", "p2", 2, 100, now=1001.0)
    assert exc.value.reason == "duplicate_submit_intent job"
    with pytest.raises(SubmitConflict) as exc:
        led.reserve_intent("k3", "j3", "a3", "p1", 2, 100, now=1001.0)
    assert exc.value.reason == "duplicate_submit_intent posting"
    ts, wait_s = led.reserve_intent("k4", "j4", "a4", "p4", 2, 100, now=1030.0)
    assert ts == 1030.0 and wait_s == 70.0  # the gap is returned, never raised
    with pytest.raises(SubmitConflict) as exc:
        led.reserve_intent("k5", "j5", "a5", "p5", 2, 100, now=1031.0)
    assert exc.value.reason == "rate_limit_daily"
    assert [r["key"] for r in led.records()] == ["k1", "k4"]  # conflicts append nothing


def test_reserve_intent_ignores_freed_and_old_entries(tmp_path):
    led = SubmitLedger(str(tmp_path / "l.jsonl"))
    led.append_intent("old", "jo", "a", "po", ts=1000.0)
    led.append_outcome("old", "rejected", {})
    led.append_intent("gone", "jg", "a", "pg", ts=1000.0)
    led.append_outcome("gone", "not_sent", {})
    # the rejected one is freed for retry but still counts toward the cap/gap (it reached the site)
    ts, wait_s = led.reserve_intent("k", "jo", "a2", "po", 10, 50, now=1020.0)
    assert (ts, wait_s) == (1020.0, 30.0)
    assert led.reserve_intent("k9", "j9", "a", "p9", 10, 50, now=1000.0 + 2 * submit_guard.DAY_S)[1] == 0.0


def test_recheck_reservation_ignores_its_own_key_but_not_others(tmp_path):
    led = SubmitLedger(str(tmp_path / "l.jsonl"))
    led.append_intent("mine", "j", "a", "p")
    led.recheck_reservation("mine", "j", "p")  # nothing else blocks
    led.append_intent("other", "j", "a2", "p2")
    with pytest.raises(SubmitConflict) as exc:
        led.recheck_reservation("mine", "j", "p")
    assert exc.value.reason == "duplicate_submit_intent job"
    led.append_outcome("other", "rejected", {})  # freed: no longer blocks
    led.recheck_reservation("mine", "j", "p")
    led.append_intent("third", "j9", "a", "p")
    with pytest.raises(SubmitConflict) as exc:
        led.recheck_reservation("mine", "j", "p")
    assert exc.value.reason == "duplicate_submit_intent posting"


def test_concurrent_reservations_admit_exactly_one(tmp_path):
    led = SubmitLedger(str(tmp_path / "l.jsonl"))
    results, lock = [], threading.Lock()

    def worker(i):
        try:
            led.reserve_intent(f"k{i}", "job", f"a{i}", "posting", 10, 0, now=1000.0)
            outcome = "ok"
        except SubmitConflict:
            outcome = "conflict"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1 and results.count("conflict") == 7
    assert len(led.records()) == 1


# ── Rate limits ────────────────────────────────────────────────────────────────

def _fill(led, n, now, age_start=3600, step=100, prefix="k"):
    for i in range(n):
        led.append_intent(f"{prefix}{i}", f"{prefix}j{i}", "a", f"{prefix}p{i}", ts=now - age_start - i * step)


def test_rate_limit_daily_at_ten(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLY_MAX_SUBMITS_PER_DAY", raising=False)
    monkeypatch.delenv("APPLY_SUBMIT_SPACING_S", raising=False)
    led, now = SubmitLedger(str(tmp_path / "l.jsonl")), 1_000_000.0
    _fill(led, 9, now)
    assert led.rate_ok(now) == (True, None)
    led.append_intent("k9", "j9", "a", "p9", ts=now - 3000)
    assert led.intents_last_24h(now) == 10
    assert led.rate_ok(now) == (False, "rate_limit_daily")


def test_rate_limit_env_overrides_and_garbage(tmp_path, monkeypatch):
    led, now = SubmitLedger(str(tmp_path / "l.jsonl")), 1_000_000.0
    _fill(led, 2, now)
    monkeypatch.setenv("APPLY_MAX_SUBMITS_PER_DAY", "2")
    assert led.rate_ok(now) == (False, "rate_limit_daily")
    monkeypatch.setenv("APPLY_MAX_SUBMITS_PER_DAY", "3")
    assert led.rate_ok(now) == (True, None)
    for bad in ("zero", "0", "-4", ""):
        monkeypatch.setenv("APPLY_MAX_SUBMITS_PER_DAY", bad)
        monkeypatch.setenv("APPLY_SUBMIT_SPACING_S", bad)
        assert submit_guard.submit_pacing_from_env() == (10, 180)
    monkeypatch.setenv("APPLY_MAX_SUBMITS_PER_DAY", "7")
    monkeypatch.setenv("APPLY_SUBMIT_SPACING_S", "45")
    assert submit_guard.submit_pacing_from_env() == (7, 45)


def test_rate_limit_gap(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLY_MAX_SUBMITS_PER_DAY", raising=False)
    monkeypatch.delenv("APPLY_SUBMIT_SPACING_S", raising=False)
    led, now = SubmitLedger(str(tmp_path / "l.jsonl")), 1_000_000.0
    led.append_intent("k", "j", "a", "p", ts=now - 30)
    assert led.rate_ok(now) == (False, "rate_limit_gap")
    monkeypatch.setenv("APPLY_SUBMIT_SPACING_S", "10")
    assert led.rate_ok(now) == (True, None)
    assert led.last_intent_ts() == now - 30


def test_old_not_sent_and_cleared_intents_do_not_count(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLY_MAX_SUBMITS_PER_DAY", raising=False)
    led, now = SubmitLedger(str(tmp_path / "l.jsonl")), 1_000_000.0
    _fill(led, 10, now, age_start=90_000, prefix="old")  # older than 24h
    assert led.intents_last_24h(now) == 0 and led.rate_ok(now) == (True, None)
    _fill(led, 10, now, prefix="new")
    assert led.rate_ok(now) == (False, "rate_limit_daily")
    led.append_outcome("new0", "not_sent", {})
    led.append_human_override("new1", "not_sent_cleared", "none went out")
    assert led.intents_last_24h(now) == 8 and led.rate_ok(now) == (True, None)
    led.append_outcome("new2", "rejected", {})  # rejected did reach the site: still counted
    assert led.intents_last_24h(now) == 8


@pytest.mark.parametrize("last,spacing,now,expected", [
    (None, 180, 1000.0, 0.0), (1000.0, 180, 1100.0, 80.0), (1000.0, 180, 1180.0, 0.0), (1000.0, 180, 5000.0, 0.0)])
def test_submit_gap_wait_s(last, spacing, now, expected):
    assert ap._submit_gap_wait_s(last, spacing, now) == expected


# ── Outcome classification ─────────────────────────────────────────────────────

OK_POST = ("POST", "https://x.test/jobs/1/apply?t=1", 200)
SUB = "https://x.test/api/apply"
APPLY = "https://x.test/jobs/1/apply"
DONE = "https://x.test/done"
CASES = [
    ("text_alone_no_requests", [], APPLY, False, "Thank you for applying!", False, "ambiguous"),
    ("text_alone_url_changed_no_requests", [], DONE, True, "Thank you for applying", False, "ambiguous"),
    ("422_on_submit_path_with_form", [("POST", SUB, 422)], APPLY, False, "", True, "rejected"),
    ("400_with_form_beats_text", [("POST", SUB, 400)], APPLY, False, "Application received", True, "rejected"),
    ("422_form_gone", [("POST", SUB, 422)], APPLY, False, "", False, "ambiguous"),
    ("422_form_unknown", [("POST", SUB, 422)], APPLY, False, "", None, "ambiguous"),
    ("422_on_analytics_with_form", [("POST", "https://x.test/analytics/ping", 422)], APPLY, False, "", True, "ambiguous"),
    ("422_on_draft_save_with_form", [("POST", "https://x.test/application/draft", 422)], APPLY, False, "", True, "ambiguous"),
    ("422_on_autosave_with_form", [("POST", "https://x.test/apply/autosave", 422)], APPLY, False, "", True, "ambiguous"),
    ("no_requests_unchanged_form", [], APPLY, False, "", True, "provably_not_sent"),
    ("no_requests_form_unknown", [], APPLY, False, "", None, "ambiguous"),
    ("no_requests_url_changed", [], DONE, True, "", True, "ambiguous"),
    ("no_requests_unchanged_no_form", [], APPLY, False, "", False, "ambiguous"),
    ("5xx_is_not_rejected", [("POST", SUB, 503)], APPLY, False, "", True, "ambiguous"),
    ("200_confirmation_request_path_form_gone", [("POST", "https://x.test/apply/confirmation/9", 200)], APPLY, False, "", False, "confirmed"),
    ("200_confirmation_request_path_form_still_there", [("POST", "https://x.test/apply/confirmation/9", 200)], APPLY, False, "", True, "ambiguous"),
    ("302_final_url_confirmation", [("POST", SUB, 302)], "https://x.test/jobs/confirmation", True, "", False, "confirmed"),
    ("200_apply_then_final_url_confirmation_form_gone", [("POST", "https://x.test/jobs/123/apply", 200)], "https://x.test/jobs/123/confirmation", True, "", False, "confirmed"),
    ("200_thanks_path", [OK_POST], "https://x.test/jobs/thanks", True, "", False, "confirmed"),
    ("200_thanks_path_form_unknown", [OK_POST], "https://x.test/apply/thanks", True, "", None, "ambiguous"),
    ("200_text_form_gone_url_unchanged", [OK_POST], APPLY, False, "Thank you for applying", False, "ambiguous"),
    ("200_text_url_changed_form_gone", [OK_POST], DONE, True, "Your application has been submitted", False, "confirmed"),
    ("200_text_url_changed_form_still_there", [OK_POST], DONE, True, "Your application has been submitted", True, "ambiguous"),
    ("200_text_url_changed_form_unknown", [OK_POST], DONE, True, "Your application has been submitted", None, "ambiguous"),
    ("200_text_form_still_there", [OK_POST], APPLY, False, "Thank you for applying", True, "ambiguous"),
    ("200_text_form_unknown", [OK_POST], APPLY, False, "Thank you for applying", None, "ambiguous"),
    ("200_on_tracker_with_text_form_gone", [("POST", "https://x.test/api/track", 200)], DONE, True, "Thank you for applying", False, "ambiguous"),
    ("200_on_tracker_with_thanks_url", [("POST", "https://x.test/api/track", 200)], "https://x.test/thanks", True, "", False, "ambiguous"),
    ("500_on_analytics_confirmation_200_on_track_text_form_gone",
     [("POST", "https://x.test/analytics/confirmation", 500), ("POST", "https://x.test/api/track", 200)],
     DONE, True, "Thank you for applying", False, "ambiguous"),
    ("200_alone", [OK_POST], APPLY, False, "", True, "ambiguous"),
    ("404_on_submit_then_200_with_form_is_conflicting", [("POST", "https://x.test/a/apply", 404), OK_POST], APPLY, False, "", True, "ambiguous"),
    ("422_and_200_on_the_same_submit_path_with_form", [("POST", "https://x.test/apply/submit", 422), ("POST", "https://x.test/apply/submit", 200)], APPLY, False, "", True, "ambiguous"),
    ("422_then_200_on_apply_form_gone_final_thanks_is_conflicting",
     [("POST", APPLY, 422), ("POST", APPLY, 200)], "https://x.test/thanks", True, "", False, "ambiguous"),
    ("200_then_422_on_apply_form_gone_final_confirmation_is_conflicting",
     [("POST", APPLY, 200), ("POST", APPLY, 422)], "https://x.test/jobs/1/confirmation", True, "Thank you for applying", False, "ambiguous"),
    ("conflicting_responses_with_form_unknown", [("POST", APPLY, 400), ("POST", APPLY, 302)], "https://x.test/thanks", True, "", None, "ambiguous"),
]


@pytest.mark.parametrize("reqs,final_url,changed,text,form,expected", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_classify_submit_outcome(reqs, final_url, changed, text, form, expected):
    kind, evidence = submit_guard.classify_submit_outcome(reqs, final_url, changed, text, form, "greenhouse")
    assert kind == expected
    assert set(evidence) == {"requests", "final_url_path", "url_changed", "form_present", "platform", "text_hit"}


def test_a_4xx_on_a_non_submit_path_does_not_make_a_confirmation_conflicting():
    kind, _evidence = submit_guard.classify_submit_outcome(
        [("POST", APPLY, 200), ("POST", "https://x.test/analytics/event", 422)], "https://x.test/thanks", True, "", False, "greenhouse")
    assert kind == "confirmed"  # only plausible submit paths take part in the conflict test


@pytest.mark.parametrize("path,plausible", [
    ("/jobs/1/apply", True), ("/api/Submit", True), ("/v1/applications", True), ("/candidate/9", True),
    ("/analytics/ping", False), ("/api/track", False), ("/draft/save", False), ("", False), (None, False),
    ("/application/draft", False), ("/apply/autosave", False), ("/apply/beacon", False),
    ("/analytics/apply", False), ("/api/tracking/submit", False)])
def test_is_plausible_submit_path(path, plausible):
    assert submit_guard.is_plausible_submit_path(path) is plausible


def test_classifier_evidence_carries_sanitized_paths_only():
    _kind, evidence = submit_guard.classify_submit_outcome(
        [("post", "https://x.test/a/b?token=SECRET&e=me@x.com", 200)], "https://x.test/done?session=ABC#f", True, "", False, "lever")
    assert evidence["requests"] == [("POST", "/a/b", 200)] and evidence["final_url_path"] == "/done"
    assert "SECRET" not in repr(evidence) and "session" not in repr(evidence) and "@" not in repr(evidence)
    opaque = "A1b2C3d4E5f6G7h8I9j0K1l2"
    _kind, evidence = submit_guard.classify_submit_outcome(
        [("POST", f"https://x.test/candidates/{opaque}/apply", 200)], f"https://x.test/c/{opaque}/thanks", True, "", None, "lever")
    assert opaque not in repr(evidence)
    assert evidence["requests"] == [("POST", "/candidates/[token]/apply", 200)]
    assert evidence["final_url_path"] == "/c/[token]/thanks" and evidence["form_present"] is None


def test_compact_evidence_has_no_query_or_pii():
    _kind, evidence = submit_guard.classify_submit_outcome(
        [("POST", "https://x.test/api/apply?email=me@x.com", 200)], "https://x.test/jobs/confirmation", True, "", False, "lever")
    out = ap._compact_evidence(dict(evidence, click_error="TimeoutError"))
    assert "reqs=POST:200" in out and "path=/jobs/confirmation" in out and "click_error=TimeoutError" in out
    assert "?" not in out and "@" not in out
    assert ap._compact_evidence(None) == ""
    leaky = ap._compact_evidence({"error": "bad code 123456 for me@x.com"})
    assert "123456" not in leaky and "@" not in leaky


def test_scrub_evidence_redacts_nested_strings_and_keeps_structure():
    scrubbed = ap._scrub_evidence({"error": "ref 123456", "requests": [["POST", "/apply/" + "a" * 30, 503]],
                                   "url_changed": True, "n": 3})
    assert scrubbed["error"] == "ref [code]" and scrubbed["requests"] == [["POST", "/apply/[token]", 503]]
    assert scrubbed["url_changed"] is True and scrubbed["n"] == 3


# ── Form presence probe ────────────────────────────────────────────────────────

@pytest.mark.parametrize("result,expected", [
    (1, True), (3, True), (0, False),
    (None, None), ("2", None), (True, None), (-1, None), (1.0, None), (MagicMock(), None)])
def test_form_present_is_tri_state(result, expected):
    target = SimpleNamespace(evaluate=lambda js: result)
    assert ap._form_present(target) is expected


def test_form_present_is_unknown_when_the_probe_raises():
    def boom(js):
        raise RuntimeError("page closed")

    assert ap._form_present(SimpleNamespace(evaluate=boom)) is None


def test_form_present_no_longer_depends_on_final_control(monkeypatch):
    # final_control() raises no_submit for zero AND for several controls, and swallows read errors
    monkeypatch.setattr(universal_filler, "final_control", lambda t: pytest.fail("must not be consulted"))
    assert ap._form_present(SimpleNamespace(evaluate=lambda js: 2)) is True


def test_the_probe_embeds_the_strict_submit_pattern_and_ignores_disabled_state():
    assert json.dumps(universal_filler._STRICT_SUBMIT.pattern) in ap._SUBMIT_PROBE_JS
    for needle in ("button", "input[type=submit]", "input[type=button]", "getBoundingClientRect"):
        assert needle in ap._SUBMIT_PROBE_JS
    assert "disabled" not in ap._SUBMIT_PROBE_JS  # a disabled Submit still means the form is present


# ── live_submit fixtures ───────────────────────────────────────────────────────

class FakeLoc:
    def __init__(self, order=None, visible=True, enabled=True, error=None):
        self.order, self.visible, self.enabled, self.error, self.clicks = order, visible, enabled, error, []

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def click(self, timeout=None):
        self.clicks.append(timeout)
        if self.order is not None:
            self.order.append("click")
        if self.error:
            raise self.error


class FakeStop:
    def __init__(self):
        self.waits, self.stopped = [], False

    def wait(self, seconds):
        self.waits.append(seconds)
        return False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True


class SpyLedger:
    def __init__(self, order=None, error=None):
        self.order, self.error, self.keys, self.calls = order, error, [], []

    def recheck_and_dispatch(self, key, job_id, posting_key, spacing_s, now=None):
        if self.error:
            raise self.error
        self.keys.append(key)
        self.calls.append((key, job_id, posting_key, spacing_s))
        if self.order is not None:
            self.order.append("dispatch")
        return 1.0


@pytest.fixture
def live(monkeypatch, tmp_path):
    flag = tmp_path / "pipeline_live_ok"
    flag.write_text("")
    env = SimpleNamespace(
        ledger_path=str(tmp_path / "state" / "ledger.jsonl"), art_dir=str(tmp_path / "artifacts"),
        stop=FakeStop(), phases=[], reconciles=[], rearmed=[], loc=FakeLoc(),
        scope=SimpleNamespace(mutating=0, check=lambda: None, blocked=0),
        page=MagicMock(), target=MagicMock(), tmp=tmp_path, flag=str(flag))
    env.target.url = URL
    monkeypatch.setattr(ap, "DRY_RUN", False)
    monkeypatch.setattr(ap, "LIVE_OK_FILE", env.flag)
    monkeypatch.setattr(ap, "SUBMIT_LEDGER_PATH", env.ledger_path)
    monkeypatch.setattr(ap, "SUBMIT_ARTIFACTS_DIR", env.art_dir)
    monkeypatch.setattr(ap, "STOP", env.stop)
    monkeypatch.setenv("APPLY_AGENT_ARMED", "1")
    monkeypatch.setenv("APPLY_SUBMIT_SPACING_S", "1")
    monkeypatch.delenv("APPLY_MAX_SUBMITS_PER_DAY", raising=False)
    monkeypatch.setattr(ap, "live_preconditions", lambda facts: [])
    monkeypatch.setattr(ap, "set_phase", lambda jid, attempt, phase: env.phases.append(phase))
    monkeypatch.setattr(ap, "_mark_reconcile", lambda jid, attempt, why: env.reconciles.append((jid, attempt, why)))
    monkeypatch.setattr(ap, "assert_destination", lambda *a, **k: None)
    monkeypatch.setattr(ap, "check_challenge", lambda *a, **k: None)
    monkeypatch.setattr(ap, "build_inventory", lambda target: [])
    monkeypatch.setattr(universal_filler, "final_control", lambda target: (3, "Submit application", 0))
    monkeypatch.setattr(universal_filler, "control", lambda target, n: env.loc)
    monkeypatch.setattr(universal_filler, "lift_guard", lambda page: None)
    monkeypatch.setattr(universal_filler, "rearm_guard", lambda page: env.rearmed.append(page))
    return env


def run_live(env, jid="job-1", attempt="att-1", url=URL, facts=None, domains=()):
    ctx = ap.RunCtx(jid, url, env.scope, "greenhouse")
    ctx.verif_domains = domains
    return ap.live_submit(env.target, env.page, ctx, facts if facts is not None else {}, attempt)


def stub_seam(monkeypatch, outcome):
    calls = []

    def seam(target, page, ctx, jid, seen, loc, ledger, key):
        ctx.click_dispatched = True  # what the real seam sets right before the page guard is lifted
        calls.append((jid, loc, key, ledger))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(ap, "_click_and_observe", seam)
    return calls


def ledger_of(env):
    return SubmitLedger(env.ledger_path)


def outcomes_of(env):
    return [r["result"] for r in ledger_of(env).records() if r["type"] == "outcome"]


# ── Fail-closed gates ──────────────────────────────────────────────────────────

def test_dry_run_refuses_live_submit(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    monkeypatch.setattr(ap, "DRY_RUN", True)
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live)
    assert exc.value.status == "failed" and exc.value.detail == "invariant_live_submit_in_dry_run"
    assert calls == [] and ledger_of(live).records() == []


def test_unarmed_environment_refuses(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    monkeypatch.setenv("APPLY_DRY_RUN", "0")
    monkeypatch.delenv("APPLY_AGENT_ARMED", raising=False)
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live)
    assert exc.value.detail == "live_not_armed" and calls == [] and ledger_of(live).records() == []


def test_missing_flag_file_refuses(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    monkeypatch.setattr(ap, "LIVE_OK_FILE", str(live.tmp / "does-not-exist"))
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live)
    assert exc.value.detail == "live_flag_file_missing" and calls == []


def test_dry_run_defaults_on_in_source():
    source = open(ap.__file__, encoding="utf-8").read()
    assert 'DRY_RUN = os.environ.get("APPLY_DRY_RUN", "1") != "0"' in source


def test_missing_preconditions_end_as_needs_review_without_a_click(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    monkeypatch.setattr(ap, "live_preconditions", lambda facts: ["verify_confirmed", "required_unfilled"])
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live)
    assert exc.value.status == "needs_review"
    assert exc.value.detail == "live_precondition_missing=verify_confirmed,required_unfilled"
    assert calls == [] and ledger_of(live).records() == []


def test_missing_attempt_id_fails(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live, attempt=None)
    assert exc.value.detail == "attempt_id_missing" and calls == []


def test_unusable_control_closes_the_reserved_intent_as_not_sent(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    live.loc.enabled = False
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live)
    assert exc.value.status == "needs_review" and exc.value.detail == "submit_control_changed"

    def stop(target):
        raise universal_filler.Stop("multi_step", "multi")

    monkeypatch.setattr(universal_filler, "final_control", stop)
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live, attempt="att-2")
    assert exc.value.detail == "submit_control_multi_step"
    assert calls == [] and outcomes_of(live) == ["not_sent", "not_sent"]
    assert not ledger_of(live).is_blocked_for_job("job-1")  # nothing was clicked: the job is not blocked


@pytest.mark.parametrize("kind", ["directory", "malformed"])
def test_unreadable_ledger_fails_closed_without_a_click(live, monkeypatch, kind):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    if kind == "directory":
        os.makedirs(live.ledger_path)  # a directory where the ledger file should be
    else:
        os.makedirs(os.path.dirname(live.ledger_path))
        with open(live.ledger_path, "w") as fh:
            fh.write("{not json\n")
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live)
    assert exc.value.status == "failed" and exc.value.detail == "ledger_unreadable"
    assert calls == [] and live.loc.clicks == [] and live.phases == []


def test_an_intent_without_a_key_in_the_ledger_fails_closed_without_a_click(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    os.makedirs(os.path.dirname(live.ledger_path))
    with open(live.ledger_path, "w") as fh:  # a record that used to be silently dropped by the fold
        fh.write(json.dumps({"type": "intent", "job_id": "job-1", "attempt_id": "a",
                             "posting_key": submit_guard.posting_key(URL), "ts": 1000.0}) + "\n")
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live)
    assert exc.value.detail == "ledger_unreadable" and calls == []


def test_an_unhashable_result_in_the_ledger_fails_closed_without_a_click(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    os.makedirs(os.path.dirname(live.ledger_path))
    with open(live.ledger_path, "w") as fh:
        fh.write(_line(_OUTCOME, result=[]) + "\n")
    with pytest.raises(ap.JobEnd) as exc:  # a TypeError here would escape the ledger_unreadable guard
        run_live(live)
    assert exc.value.detail == "ledger_unreadable" and calls == []


# ── Exactly once ───────────────────────────────────────────────────────────────

def test_second_call_is_a_duplicate_and_the_click_seam_runs_once(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    status, detail = run_live(live)
    assert status == "submitted" and detail.startswith("live_submit_confirmed")
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live, attempt="att-2")
    assert exc.value.status == "needs_review" and exc.value.detail == "duplicate_submit_intent job"
    assert len(calls) == 1 and calls[0][1] is live.loc
    assert [r["type"] for r in ledger_of(live).records()] == ["intent", "outcome"]
    assert live.phases == ["submit_clicked"]


def test_the_seam_receives_the_ledger_and_the_idempotency_key(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    run_live(live)
    _jid, _loc, key, ledger = calls[0]
    assert key == submit_guard.idempotency_key("job-1", "att-1", submit_guard.posting_key(URL))
    assert isinstance(ledger, SubmitLedger) and ledger.path == live.ledger_path


def test_same_posting_under_another_job_id_is_a_duplicate(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    ledger_of(live).append_intent("old", "other-job", "a0", submit_guard.posting_key(URL))
    ledger_of(live).append_outcome("old", "ambiguous", {})
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live, jid="job-2")
    assert exc.value.status == "needs_review" and exc.value.detail == "duplicate_submit_intent posting"
    assert calls == []


def test_a_conflict_appearing_during_the_gap_wait_stops_the_click(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    ledger_of(live).append_intent("e0", "x0", "a", "px0", ts=time.time() - 0.5)  # forces a gap wait
    real_wait = live.stop.wait

    def wait(seconds):
        # another worker reserves the same job while this one sleeps
        ledger_of(live).append_intent("rival", "job-1", "a-rival", "other-posting")
        return real_wait(seconds)

    monkeypatch.setattr(live.stop, "wait", wait)
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live)
    assert exc.value.status == "needs_review" and exc.value.detail == "duplicate_submit_intent job"
    assert calls == [] and outcomes_of(live) == ["not_sent"]  # only this attempt's own intent was closed


def test_a_dispatch_inside_the_spacing_after_the_wait_stops_the_click(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    # a concurrent dispatcher clicked just now; FakeStop does not sleep, so the gap is still open at recheck
    ledger_of(live).append_dispatch("someone-else", ts=time.time())
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live)
    assert exc.value.status == "failed" and exc.value.detail == "rate_limit_gap"
    assert calls == [] and outcomes_of(live) == ["not_sent"]
    assert not ledger_of(live).is_blocked_for_job("job-1")


def test_intent_phase_and_artifact_exist_before_the_click(live, monkeypatch):
    seen_state = {}

    def seam(target, page, ctx, jid, seen, loc, ledger, key):
        seen_state["types"] = [r["type"] for r in ledger_of(live).records()]
        seen_state["phases"] = list(live.phases)
        seen_state["artifact"] = os.path.join(live.art_dir, "job-1", "submit_attempted.json")
        seen_state["artifact_exists"] = os.path.exists(seen_state["artifact"])
        seen_state["listener"] = page.on.called
        return "confirmed", GOOD_EVIDENCE

    monkeypatch.setattr(ap, "_click_and_observe", seam)
    run_live(live)
    assert seen_state["types"] == ["intent"] and seen_state["phases"] == ["submit_clicked"]
    assert seen_state["artifact_exists"] and seen_state["listener"]
    artifact = seen_state["artifact"]
    assert stat.S_IMODE(os.stat(artifact).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(artifact)).st_mode) == 0o700
    for text in (open(artifact).read(), open(live.ledger_path).read()):
        assert "gh_src" not in text  # the query string never reaches disk
    assert len(live.rearmed) == 1
    live.page.remove_listener.assert_called_once()


def test_the_real_seam_records_the_dispatch_between_intent_and_outcome(live, monkeypatch):
    order = []
    live.loc.order = order
    real = SubmitLedger.recheck_and_dispatch
    monkeypatch.setattr(SubmitLedger, "recheck_and_dispatch",
                        lambda self, *a, **k: order.append("dispatch") or real(self, *a, **k))
    monkeypatch.setattr(ap, "_observe_outcome", lambda *a, **k: ("confirmed", GOOD_EVIDENCE))
    status, _detail = run_live(live)
    assert status == "submitted" and order == ["dispatch", "click"]
    assert [r["type"] for r in ledger_of(live).records()] == ["intent", "dispatch", "outcome"]
    assert ledger_of(live).last_dispatch_ts() is not None


def test_a_rival_dispatch_landing_after_the_recheck_still_stops_the_click(live, monkeypatch):
    # the standalone recheck passed, then another worker dispatched before this one's atomic final check
    real_recheck = SubmitLedger.recheck_reservation

    def recheck_then_rival(self, *a, **k):
        real_recheck(self, *a, **k)
        ledger_of(live).append_dispatch("rival", ts=time.time())

    monkeypatch.setattr(SubmitLedger, "recheck_reservation", recheck_then_rival)
    monkeypatch.setattr(ap, "_observe_outcome", lambda *a, **k: pytest.fail("must not observe"))
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live)
    assert exc.value.status == "failed" and exc.value.detail == "rate_limit_gap"
    assert live.loc.clicks == []
    assert outcomes_of(live) == ["not_sent"] and not ledger_of(live).is_blocked_for_job("job-1")


def test_rejected_frees_the_job_but_an_unproven_not_sent_does_not(live, monkeypatch):
    first = stub_seam(monkeypatch, ("rejected", {"requests": [["POST", "/a/apply", 422]]}))
    status, detail = run_live(live)
    assert status == "needs_review" and detail.startswith("submit_rejected")
    assert len(first) == 1 and not ledger_of(live).is_blocked_for_job("job-1")
    # a post-click "provably_not_sent" is never believed: silence does not prove nothing was sent
    second = stub_seam(monkeypatch, ("provably_not_sent", {"requests": []}))
    status, detail = run_live(live, attempt="att-2")
    assert status == "submitted_unconfirmed" and detail.startswith("submit_ambiguous")
    assert len(second) == 1
    assert live.reconciles == [("job-1", "att-2", "submit_ambiguous")]
    assert outcomes_of(live) == ["rejected", "ambiguous"]
    assert ledger_of(live).is_blocked_for_job("job-1")


def test_ambiguous_blocks_and_marks_reconcile(live, monkeypatch):
    stub_seam(monkeypatch, ("ambiguous", {"requests": [["POST", "/a", 503]], "observe": "timeout"}))
    status, detail = run_live(live)
    assert status == "submitted_unconfirmed" and detail.startswith("submit_ambiguous")
    assert "observe=timeout" in detail
    assert live.reconciles == [("job-1", "att-1", "submit_ambiguous")]
    assert ledger_of(live).is_blocked_for_job("job-1")


def test_click_failure_is_ambiguous_not_retried(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("click_failed", {"click_error": "TimeoutError"}))
    status, detail = run_live(live)
    assert status == "submitted_unconfirmed" and detail.startswith("submit_click_failed")
    assert "click_error=TimeoutError" in detail and len(calls) == 1
    assert live.reconciles == [("job-1", "att-1", "submit_click_failed")]
    assert outcomes_of(live) == ["ambiguous"]
    assert ledger_of(live).is_blocked_for_job("job-1")


def test_unexpected_error_after_the_click_is_ambiguous_and_leaks_nothing(live, monkeypatch):
    stub_seam(monkeypatch, RuntimeError("boom 123456 secret"))
    status, detail = run_live(live)
    assert status == "submitted_unconfirmed" and "error=RuntimeError" in detail
    assert "boom" not in detail and "secret" not in detail
    assert ledger_of(live).is_blocked_for_job("job-1")


def test_shutdown_after_the_click_records_ambiguous_and_reraises(live, monkeypatch):
    stub_seam(monkeypatch, ap.Shutdown())
    with pytest.raises(ap.Shutdown):
        run_live(live)
    assert outcomes_of(live) == ["ambiguous"]
    assert ledger_of(live).is_blocked_for_job("job-1")
    assert len(live.rearmed) == 1


def test_failure_before_the_click_closes_the_intent_as_not_sent(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))

    def lost(jid, attempt, phase):
        raise ap.JobEnd("failed", "claim_lost")

    monkeypatch.setattr(ap, "set_phase", lost)
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live)
    assert exc.value.detail == "claim_lost" and calls == []
    assert outcomes_of(live) == ["not_sent"]
    assert not ledger_of(live).is_blocked_for_job("job-1")


def test_evidence_is_scrubbed_before_the_ledger_and_the_detail(live, monkeypatch):
    stub_seam(monkeypatch, ("ambiguous", {"error": "ref 123456", "observe": "timeout",
                                          "requests": [["POST", "/apply/" + "a" * 30, 503]]}))
    status, detail = run_live(live)
    text = open(live.ledger_path).read()
    assert "123456" not in text and "a" * 30 not in text and "[token]" in text
    assert "123456" not in detail and "a" * 30 not in detail


def test_confirmation_text_is_scrubbed_with_the_secret_aware_redactor(live, monkeypatch):
    monkeypatch.setattr(ap, "_SECRETS", {"zebra-private-note"})  # a registered runtime secret
    target = SimpleNamespace(evaluate=lambda js: "Thank you zebra-private-note, write me@x.com, ref 987654")
    ap._close_intent(ledger_of(live), "k", "confirmed", {}, "job-1", page=live.page, target=target)
    text = open(os.path.join(live.art_dir, "job-1", "confirmation.txt")).read()
    assert "zebra-private-note" not in text and "me@x.com" not in text and "987654" not in text
    assert "[redacted]" in text


def test_daily_cap_is_fatal_and_gap_waits(live, monkeypatch):
    calls = stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    monkeypatch.setenv("APPLY_MAX_SUBMITS_PER_DAY", "2")
    monkeypatch.setenv("APPLY_SUBMIT_SPACING_S", "180")
    now = time.time()
    ledger_of(live).append_intent("e1", "x1", "a", "px1", ts=now - 5000)
    ledger_of(live).append_intent("e2", "x2", "a", "px2", ts=now - 4000)
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live)
    assert exc.value.status == "failed" and exc.value.detail == "rate_limit_daily" and calls == []

    monkeypatch.setenv("APPLY_MAX_SUBMITS_PER_DAY", "10")
    ledger_of(live).append_intent("e3", "x3", "a", "px3", ts=time.time() - 10)
    assert run_live(live)[0] == "submitted"
    assert any(150 < w < 175 for w in live.stop.waits)  # rate_limit_gap is waited out, not fatal


# ── Arming, STOP and the code step ─────────────────────────────────────────────

def test_arming_is_rechecked_immediately_before_the_click(live, monkeypatch):
    real_gate, gate_calls = ap.live_gate, []

    def gate():
        gate_calls.append(1)
        if len(gate_calls) >= 2:  # the call at the top of _click_and_observe, after the gap wait
            raise ap.JobEnd("failed", "live_flag_file_missing")
        real_gate()

    monkeypatch.setattr(ap, "live_gate", gate)
    monkeypatch.setattr(ap, "_observe_outcome", lambda *a, **k: pytest.fail("must not observe"))
    with pytest.raises(ap.JobEnd) as exc:
        run_live(live)
    assert exc.value.status == "failed" and exc.value.detail == "live_flag_file_missing"
    assert len(gate_calls) == 2 and live.loc.clicks == []
    assert outcomes_of(live) == ["not_sent"] and not ledger_of(live).is_blocked_for_job("job-1")
    assert ledger_of(live).last_dispatch_ts() is None  # no dispatch was recorded for a click that never happened


def test_stop_before_the_dispatch_boundary_raises_shutdown_without_clicking(live, monkeypatch):
    monkeypatch.setattr(ap, "_observe_outcome", lambda *a, **k: pytest.fail("must not observe"))
    live.stop.set()
    try:
        with pytest.raises(ap.Shutdown):
            run_live(live)
    finally:
        live.stop.stopped = False
    assert live.loc.clicks == []
    assert outcomes_of(live) == ["not_sent"] and not ledger_of(live).is_blocked_for_job("job-1")


@pytest.mark.parametrize("found,expected", [
    (({"selector": "#c"}, ""), True),
    ((None, "missing"), False),
    ((None, "ambiguous"), None),
])
def test_post_click_code_needed_is_tri_state(monkeypatch, found, expected):
    monkeypatch.setattr(ap, "build_inventory", lambda target: [])
    monkeypatch.setattr(ap, "find_code_field", lambda inv: found)
    assert ap._post_click_code_needed(MagicMock(), SimpleNamespace(verif_domains=())) is expected


def test_post_click_code_needed_is_unknown_when_the_page_cannot_be_read(monkeypatch):
    def boom(target):
        raise RuntimeError("detached")

    monkeypatch.setattr(ap, "build_inventory", boom)
    assert ap._post_click_code_needed(MagicMock(), SimpleNamespace(verif_domains=("x.test",))) is None


@pytest.mark.parametrize("seam_kind", ["confirmed", "ambiguous"])
def test_a_remaining_code_field_blocks_confirmation_until_accepted(live, monkeypatch, seam_kind):
    stub_seam(monkeypatch, (seam_kind, GOOD_EVIDENCE))
    steps = []
    monkeypatch.setattr(ap, "_post_click_code_needed", lambda target, ctx: True)
    monkeypatch.setattr(ap, "_post_click_code_step",
                        lambda *a: steps.append(1) or ("ambiguous", {"code_step": "no_code"}, False))
    status, detail = run_live(live, domains=DOMAINS)
    assert steps == [1] and status == "submitted_unconfirmed" and "code_step=no_code" in detail
    assert ledger_of(live).is_blocked_for_job("job-1")


def test_an_accepted_code_lets_the_submit_count_as_confirmed(live, monkeypatch):
    stub_seam(monkeypatch, ("ambiguous", {"requests": []}))
    needed = iter([True, False])  # a code field first, gone once the code was accepted
    monkeypatch.setattr(ap, "_post_click_code_needed", lambda target, ctx: next(needed))
    monkeypatch.setattr(ap, "_post_click_code_step", lambda *a: ("confirmed", GOOD_EVIDENCE, True))
    status, detail = run_live(live, domains=DOMAINS)
    assert status == "submitted" and detail.startswith("live_submit_confirmed")


def test_a_surprise_code_gate_without_mail_access_is_never_confirmed(live, monkeypatch):
    stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    monkeypatch.setattr(ap, "_post_click_code_needed", lambda target, ctx: True)
    monkeypatch.setattr(ap, "_post_click_code_step", lambda *a: pytest.fail("no mailbox is configured"))
    status, detail = run_live(live, domains=())
    assert status == "submitted_unconfirmed" and "code_step=unserviceable" in detail
    assert ledger_of(live).is_blocked_for_job("job-1")


def test_a_rejected_code_is_never_confirmed(live, monkeypatch):
    stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    monkeypatch.setattr(ap, "_post_click_code_needed", lambda target, ctx: True)
    # the observation after the code step looked confirmed, but the code itself was not accepted
    monkeypatch.setattr(ap, "_post_click_code_step", lambda *a: ("confirmed", GOOD_EVIDENCE, False))
    status, detail = run_live(live, domains=DOMAINS)
    assert status == "submitted_unconfirmed" and detail.startswith("submit_ambiguous")
    assert outcomes_of(live) == ["ambiguous"] and ledger_of(live).is_blocked_for_job("job-1")


def test_an_unreadable_page_cannot_keep_an_early_confirmation(live, monkeypatch):
    stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    monkeypatch.setattr(ap, "_post_click_code_needed", lambda target, ctx: None)
    monkeypatch.setattr(ap, "_post_click_code_step", lambda *a: pytest.fail("nothing to enter a code into"))
    status, detail = run_live(live, domains=DOMAINS)
    assert status == "submitted_unconfirmed" and "code_step=inspection_failed" in detail


def test_an_unreadable_page_leaves_an_ambiguous_submit_ambiguous(live, monkeypatch):
    stub_seam(monkeypatch, ("ambiguous", {"requests": []}))
    monkeypatch.setattr(ap, "_post_click_code_needed", lambda target, ctx: None)
    status, detail = run_live(live, domains=DOMAINS)
    assert status == "submitted_unconfirmed" and detail.startswith("submit_ambiguous")


@pytest.mark.parametrize("after", [True, None])
def test_a_code_field_still_present_after_acceptance_is_ambiguous(live, monkeypatch, after):
    stub_seam(monkeypatch, ("ambiguous", {"requests": []}))
    needed = iter([True, after])
    monkeypatch.setattr(ap, "_post_click_code_needed", lambda target, ctx: next(needed))
    monkeypatch.setattr(ap, "_post_click_code_step", lambda *a: ("confirmed", GOOD_EVIDENCE, True))
    status, detail = run_live(live, domains=DOMAINS)
    assert status == "submitted_unconfirmed" and "code_step=code_field_remains" in detail


def test_no_code_step_when_no_code_field_remains(live, monkeypatch):
    stub_seam(monkeypatch, ("confirmed", GOOD_EVIDENCE))
    monkeypatch.setattr(ap, "_post_click_code_step", lambda *a: pytest.fail("no code field is present"))
    assert run_live(live)[0] == "submitted"


def _step_ctx():
    return SimpleNamespace(verif_domains=DOMAINS, click_since=None, verify_timeout=60, scope=None, url=URL)


@pytest.mark.parametrize("stale,state,accepted", [(1, 0, False), (0, 1, True), (1, 1, True), (0, 0, False)])
def test_code_step_accepts_only_this_codes_own_acceptance(monkeypatch, stale, state, accepted):
    monkeypatch.setattr(ap, "STOP", FakeStop())
    monkeypatch.setattr(ap, "_SECRETS", set())
    monkeypatch.setattr(ap.email_verification, "wait_for_verification", lambda *a, **k: {"code": "654321"})
    monkeypatch.setattr(ap, "_observe_outcome", lambda *a, **k: ("confirmed", {"requests": []}))

    def accept(target, page, ctx, scope, jid, url, facts, code, timeout, mark):
        facts["verify_confirmed"] = state

    monkeypatch.setattr(ap, "_accept_otp_code", accept)
    facts = {"verify_confirmed": stale}  # an earlier, pre-click verification must not vouch for this code
    kind, evidence, got = ap._post_click_code_step(MagicMock(), MagicMock(), _step_ctx(), "j", [], facts, {})
    assert got is accepted and kind == "confirmed" and evidence["code_step"] == "attempted"
    assert facts == {"verify_confirmed": stale}


def test_code_step_reports_no_code_and_rejection_as_not_accepted(monkeypatch):
    monkeypatch.setattr(ap, "STOP", FakeStop())
    monkeypatch.setattr(ap, "_SECRETS", set())
    monkeypatch.setattr(ap, "_observe_outcome", lambda *a, **k: pytest.fail("must not observe"))
    monkeypatch.setattr(ap.email_verification, "wait_for_verification", lambda *a, **k: None)
    kind, evidence, accepted = ap._post_click_code_step(MagicMock(), MagicMock(), _step_ctx(), "j", [], {}, {})
    assert (kind, accepted, evidence["code_step"]) == ("ambiguous", False, "no_code")

    monkeypatch.setattr(ap.email_verification, "wait_for_verification", lambda *a, **k: {"code": "654321"})

    def rejected(*a, **k):
        raise ap.JobEnd("failed", "verify_rejected")

    monkeypatch.setattr(ap, "_accept_otp_code", rejected)
    kind, evidence, accepted = ap._post_click_code_step(MagicMock(), MagicMock(), _step_ctx(), "j", [], {}, {})
    assert (kind, accepted, evidence["code_step"]) == ("ambiguous", False, "failed_JobEnd")


def test_code_step_checks_stop_before_its_blocking_wait(monkeypatch):
    monkeypatch.setattr(ap, "STOP", SimpleNamespace(is_set=lambda: True, wait=lambda s: True))
    monkeypatch.setattr(ap.email_verification, "wait_for_verification",
                        lambda *a, **k: pytest.fail("must not start a blocking wait after a stop"))
    ctx = SimpleNamespace(verif_domains=("x.test",), click_since=None, verify_timeout=60)
    with pytest.raises(ap.Shutdown):
        ap._post_click_code_step(MagicMock(), MagicMock(), ctx, "j", [], {}, {})


# ── Click seam and observation ─────────────────────────────────────────────────

@pytest.fixture
def armed_seam(monkeypatch):
    monkeypatch.setattr(ap, "DRY_RUN", False)
    monkeypatch.setattr(ap, "live_gate", lambda: None)
    monkeypatch.setattr(ap, "STOP", FakeStop())
    monkeypatch.delenv("APPLY_SUBMIT_SPACING_S", raising=False)


def test_click_and_observe_records_the_dispatch_then_lifts_the_guard_then_clicks_once(monkeypatch, armed_seam):
    order = []
    loc = FakeLoc(order=order)
    ledger = SpyLedger(order=order)
    monkeypatch.setattr(universal_filler, "lift_guard", lambda page: order.append("lift"))
    monkeypatch.setattr(ap, "_observe_outcome", lambda *a, **k: ("confirmed", {"observed": True}))
    target = MagicMock()
    target.url = URL
    ctx = ap.RunCtx("job-1", URL, SimpleNamespace(mutating=4), "greenhouse")
    assert ap._click_and_observe(target, MagicMock(), ctx, "job-1", [], loc, ledger, "key-1") == ("confirmed", {"observed": True})
    assert order == ["dispatch", "lift", "click"] and loc.clicks == [15000] and ledger.keys == ["key-1"]
    # the atomic final check is given this job, this posting and the configured spacing
    assert ledger.calls == [("key-1", "job-1", submit_guard.posting_key(URL), submit_guard.DEFAULT_SPACING_S)]
    assert ctx.submit_mut0 == 4 and ctx.pre_click_url == "https://boards.example.com/acme/jobs/123"
    assert ctx.click_since is not None and ctx.click_dispatched is True


def test_click_and_observe_passes_the_configured_spacing(monkeypatch, armed_seam):
    monkeypatch.setenv("APPLY_SUBMIT_SPACING_S", "45")
    ledger = SpyLedger()
    monkeypatch.setattr(universal_filler, "lift_guard", lambda page: None)
    monkeypatch.setattr(ap, "_observe_outcome", lambda *a, **k: ("confirmed", {}))
    target = MagicMock()
    target.url = URL
    ctx = ap.RunCtx("job-1", URL, SimpleNamespace(mutating=0), "greenhouse")
    ap._click_and_observe(target, MagicMock(), ctx, "job-1", [], FakeLoc(), ledger, "k")
    assert ledger.calls[0][3] == 45


def test_click_error_is_reported_without_a_second_click(monkeypatch, armed_seam):
    loc = FakeLoc(error=RuntimeError("detached"))
    ledger = SpyLedger()
    monkeypatch.setattr(universal_filler, "lift_guard", lambda page: None)
    monkeypatch.setattr(ap, "_observe_outcome", lambda *a, **k: pytest.fail("must not observe after a failed click"))
    ctx = ap.RunCtx("job-1", URL, SimpleNamespace(mutating=0), "greenhouse")
    target = MagicMock()
    target.url = URL
    assert ap._click_and_observe(target, MagicMock(), ctx, "job-1", [], loc, ledger, "k") == ("click_failed", {"click_error": "RuntimeError"})
    assert len(loc.clicks) == 1 and ledger.keys == ["k"]


def test_an_unwritable_dispatch_record_stops_before_the_guard_lifts(monkeypatch, armed_seam):
    loc = FakeLoc()
    monkeypatch.setattr(universal_filler, "lift_guard", lambda page: pytest.fail("guard must stay armed"))
    ctx = ap.RunCtx("job-1", URL, SimpleNamespace(mutating=0), "greenhouse")
    ctx.click_dispatched = False
    target = MagicMock()
    target.url = URL
    for error in (OSError("disk full"), LedgerError("ledger_unwritable")):
        with pytest.raises(ap.JobEnd) as exc:
            ap._click_and_observe(target, MagicMock(), ctx, "job-1", [], loc, SpyLedger(error=error), "k")
        assert exc.value.status == "failed" and exc.value.detail == "ledger_unwritable"
    assert loc.clicks == [] and ctx.click_dispatched is False  # live_submit closes the intent as not_sent


@pytest.mark.parametrize("reason", ["rate_limit_gap", "duplicate_submit_intent job", "duplicate_submit_intent posting"])
def test_a_conflict_at_the_atomic_final_check_stops_before_the_guard_lifts(monkeypatch, armed_seam, reason):
    loc = FakeLoc()
    monkeypatch.setattr(universal_filler, "lift_guard", lambda page: pytest.fail("guard must stay armed"))
    monkeypatch.setattr(ap, "_observe_outcome", lambda *a, **k: pytest.fail("must not observe"))
    ctx = ap.RunCtx("job-1", URL, SimpleNamespace(mutating=0), "greenhouse")
    ctx.click_dispatched = False
    target = MagicMock()
    target.url = URL
    with pytest.raises(ap.JobEnd) as exc:
        ap._click_and_observe(target, MagicMock(), ctx, "job-1", [], loc, SpyLedger(error=SubmitConflict(reason)), "k")
    assert exc.value.status == "failed" and exc.value.detail == reason
    assert loc.clicks == [] and ctx.click_dispatched is False


def test_click_seam_refuses_in_dry_run_before_touching_anything(monkeypatch, armed_seam):
    monkeypatch.setattr(ap, "DRY_RUN", True)
    loc = FakeLoc()
    ledger = SpyLedger()
    monkeypatch.setattr(universal_filler, "lift_guard", lambda page: pytest.fail("guard must stay armed"))
    ctx = ap.RunCtx("job-1", URL, SimpleNamespace(mutating=0), "greenhouse")
    with pytest.raises(ap.JobEnd) as exc:
        ap._click_and_observe(MagicMock(), MagicMock(), ctx, "job-1", [], loc, ledger, "k")
    assert exc.value.detail == "invariant_live_submit_in_dry_run" and loc.clicks == [] and ledger.keys == []
    assert ctx.pre_click_url is None


def _obs_ctx(scope):
    return SimpleNamespace(url=URL, scope=scope, platform="greenhouse", submit_mut0=0, pre_click_url=None)


def test_quiet_observation_is_ambiguous_never_not_sent(monkeypatch):
    polls = []
    monkeypatch.setattr(ap, "STOP", FakeStop())
    monkeypatch.setattr(ap, "assert_destination", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_page_text", lambda target: "")
    monkeypatch.setattr(ap, "_form_present", lambda target: polls.append(1) or True)
    target = MagicMock()
    target.url = URL
    ctx = _obs_ctx(SimpleNamespace(mutating=0))
    ctx.pre_click_url = submit_guard.canonical_apply_url(URL)
    # no requests, URL unchanged, form still present: the classifier's predicate says "not sent" ...
    assert submit_guard.classify_submit_outcome([], URL, False, "", True, "greenhouse")[0] == "provably_not_sent"
    # ... and the pipeline still refuses to believe it after a click
    kind, evidence = ap._observe_outcome(target, ctx, "j", [], 6)
    assert kind == "ambiguous" and evidence["observe"] == "quiet_but_unproven" and len(polls) == 6
    assert not hasattr(ap, "QUIET_POLLS_NOT_SENT")


def test_running_out_of_polls_without_a_decision_is_a_timeout(monkeypatch):
    polls = []
    monkeypatch.setattr(ap, "STOP", FakeStop())
    monkeypatch.setattr(ap, "assert_destination", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_snapshot", lambda t, c, s: polls.append(1) or ("ambiguous", {"requests": []}))
    kind, evidence = ap._observe_outcome(MagicMock(), _obs_ctx(SimpleNamespace(mutating=0)), "j", [], 12)
    assert kind == "ambiguous" and evidence["observe"] == "timeout" and len(polls) == 12


@pytest.mark.parametrize("kind", ["confirmed", "rejected"])
def test_observation_stops_early_on_a_decision(monkeypatch, kind):
    polls = []
    monkeypatch.setattr(ap, "STOP", FakeStop())
    monkeypatch.setattr(ap, "assert_destination", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_snapshot", lambda t, c, s: polls.append(1) or (kind, {"requests": []}))
    assert ap._observe_outcome(MagicMock(), _obs_ctx(SimpleNamespace(mutating=0)), "j", [], 45)[0] == kind
    assert len(polls) == 1


def test_leaving_the_allowlist_stops_reading_the_page(monkeypatch):
    def blocked(*a, **k):
        raise ap.JobEnd("blocked", "host_not_allowed")

    monkeypatch.setattr(ap, "STOP", FakeStop())
    monkeypatch.setattr(ap, "assert_destination", blocked)
    monkeypatch.setattr(ap, "_snapshot", lambda *a: pytest.fail("must not read a page off the allowlist"))
    target = MagicMock()
    target.url = "https://elsewhere.test/x"
    kind, evidence = ap._observe_outcome(target, _obs_ctx(SimpleNamespace(mutating=0)), "j", [], 45)
    assert kind == "ambiguous" and evidence["observe"] == "destination_left_allowlist"
    assert evidence["form_present"] is None  # unknown, not "gone"


def test_shutdown_during_observation_propagates(monkeypatch):
    monkeypatch.setattr(ap, "STOP", SimpleNamespace(wait=lambda s: True))
    with pytest.raises(ap.Shutdown):
        ap._observe_outcome(MagicMock(), _obs_ctx(SimpleNamespace(mutating=0)), "j", [], 45)


# ── OTP extraction and the mutating counter ────────────────────────────────────

def test_accept_otp_code_marks_only_through_the_given_mark(monkeypatch):
    loc = SimpleNamespace(input_value=lambda: "123456")
    for name, value in {
        "assert_destination": lambda *a, **k: None, "build_inventory": lambda t: [],
        "find_code_field": lambda inv: ({"selector": "#code"}, ""), "probe_acceptance": lambda t, s: {},
        "pace": lambda: None, "gate_field": lambda t, f, c: loc, "type_into": lambda l, c: None,
        "observe_acceptance": lambda *a, **k: "accepted",
    }.items():
        monkeypatch.setattr(ap, name, value)
    ctx = SimpleNamespace(assert_pinned=lambda t: None)
    marks, facts = [], {}
    ap._accept_otp_code("t", "p", ctx, "scope", "j", "u", facts, "123456", 60, marks.append)
    assert marks == ["otp_entered"] and facts == {"code_entered": 1, "verify_state": "accepted", "verify_confirmed": 1}
    noop_marks = []
    ap._accept_otp_code("t", "p", ctx, "scope", "j", "u", {}, "123456", 60, lambda *a, **k: None)
    assert noop_marks == []


def test_post_click_code_phase_would_be_out_of_order_without_the_noop_mark():
    phases = list(ap.PHASES)[: list(ap.PHASES).index("submit_clicked") + 1] + ["otp_entered"]
    state = {"j": {"status": "needs_reconciliation", "attempts": 1, "phases": phases}}
    assert ap.validate_state(state) == "bad_phases"


def test_route_handler_counts_mutating_requests_only(monkeypatch):
    scope = ap.JobScope(MagicMock(), "j")
    scope.owns = lambda page: True
    monkeypatch.setattr(ap, "net_decision", lambda *a, **k: (True, ""))
    handler = ap.make_route_handler(scope)
    assert scope.mutating == 0
    for method, expected in (("GET", 0), ("HEAD", 0), ("POST", 1), ("put", 2)):
        route = MagicMock()
        route.fetch.return_value = SimpleNamespace(status=200, headers={})
        request = MagicMock()
        request.method, request.resource_type, request.url = method, "fetch", "https://boards.example.com/x"
        handler(route, request)
        route.fulfill.assert_called_once()
        assert scope.mutating == expected


# ── Structure ──────────────────────────────────────────────────────────────────

def test_phases_include_submit_clicked_after_final_reached():
    assert ap.PHASES.index("submit_clicked") == ap.PHASES.index("final_reached") + 1
    state = {"j": {"status": "needs_reconciliation", "attempts": 1, "phases": list(ap.PHASES)}}
    assert ap.validate_state(state) is None


def test_gate_order_in_run_job_source():
    src = inspect.getsource(ap._run_job)
    dry = src.index("if DRY_RUN:\n        missing = dry_run_missing(facts)")
    pre = src.index("missing = live_preconditions(facts)")
    sub = src.index("return live_submit(target, page, ctx, facts, attempt_id)")
    assert dry < pre < sub


def test_live_submit_step_order_in_source():
    src = inspect.getsource(ap.live_submit)
    steps = ["if DRY_RUN:", "live_gate()", "live_preconditions(facts)", "reserve_intent(", "STOP.wait(wait_s)",
             "recheck_reservation(", "assert_destination(", "final_control(target)",
             'set_phase(jid, attempt_id, "submit_clicked")', 'page.on("response"', "_click_and_observe(",
             "_settle_code_gate(", "rearm_guard(page)"]
    positions = [src.index(s) for s in steps]
    assert positions == sorted(positions)
    assert "recheck_reservation(key, jid, posting, spacing_s)" in src  # spacing is re-checked from the dispatch
    assert "_click_and_observe(target, page, ctx, jid, seen, loc, ledger, key)" in src
    for gone in ("is_blocked_for_job", "is_blocked_for_posting", "rate_ok(", "append_intent(", "append_dispatch("):
        assert gone not in src  # the check-then-act sequence is replaced by the atomic reservation
    assert ".click(" not in src  # the only click lives in the seam
    seam = inspect.getsource(ap._click_and_observe)
    assert seam.count("loc.click(") == 1 and seam.index("lift_guard(page)") < seam.index("loc.click(")
    assert seam.index("if DRY_RUN:") < seam.index("live_gate()") < seam.index("STOP.is_set()") < seam.index("lift_guard(page)")
    # the final duplicate + gap check and the dispatch record are ONE ledger call (a single lock), made
    # after the last refusal point and immediately before the guard lifts; a standalone append_dispatch
    # would reopen the window between the check and the record
    assert "append_dispatch(" not in seam and "recheck_reservation(" not in seam
    assert (seam.index("STOP.is_set()") < seam.index("recheck_and_dispatch(") < seam.index("click_dispatched = True")
            < seam.index("lift_guard(page)"))


def test_confirmation_text_uses_both_redactors_in_source():
    src = inspect.getsource(ap._close_intent)
    assert "submit_guard.redact_text(redact(_page_text(target)))" in src


def test_armed_flag_is_never_assigned_in_the_new_code():
    assign = re.compile(r"""APPLY_AGENT_ARMED["']?\s*[:=]\s*["']?1\b""")
    for module in (ap, submit_guard, rs):
        assert not assign.search(open(module.__file__, encoding="utf-8").read())


# ── reconcile_submits ──────────────────────────────────────────────────────────

@pytest.fixture
def recon(tmp_path, monkeypatch):
    state_path, ledger_path = tmp_path / "state.json", tmp_path / "ledger.jsonl"
    monkeypatch.setenv("APPLY_STATE_PATH", str(state_path))
    monkeypatch.setenv("APPLY_SUBMIT_LEDGER_PATH", str(ledger_path))
    state_path.write_text(json.dumps({
        "job-1": {"status": "submitted_unconfirmed", "attempts": 1, "attempt_id": "att-1",
                  "phases": ["started", "submit_clicked"], "reconcile_reason": "submit_ambiguous"},
        "job-2": {"status": "done", "attempts": 1}}))
    led = SubmitLedger(str(ledger_path))
    led.append_intent("k1", "job-1", "att-1", "p1", ts=1_700_000_000.0)
    led.append_outcome("k1", "ambiguous", {})
    return SimpleNamespace(state=state_path, led=led, ledger_path=ledger_path)


def test_reconcile_list(recon, capsys):
    assert rs.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "job=job-1" in out and "state=submitted_unconfirmed" in out and "job-2" not in out


def test_reconcile_mark_submitted_keeps_the_job_blocked(recon):
    assert rs.main(["--mark", "submitted", "--job", "job-1", "--reason", "saw the receipt"]) == 0
    rec = json.loads(recon.state.read_text())["job-1"]
    assert rec["status"] == "done" and "reconcile_reason" not in rec
    assert recon.led.is_blocked_for_job("job-1")
    assert [r["action"] for r in recon.led.records() if r["type"] == "human_override"] == ["submitted_confirmed"]


def test_reconcile_marking_submitted_twice_records_one_override(recon):
    assert rs.main(["--mark", "submitted", "--job", "job-1", "--reason", "saw the receipt"]) == 0
    assert rs.main(["--mark", "submitted", "--job", "job-1", "--reason", "saw it again"]) == 0
    assert [r["action"] for r in recon.led.records() if r["type"] == "human_override"] == ["submitted_confirmed"]


def test_reconcile_not_sent_needs_the_hand_check_flag(recon):
    with pytest.raises(SystemExit) as exc:
        rs.main(["--mark", "not_sent", "--job", "job-1", "--reason", "nothing arrived"])
    assert exc.value.code != 0
    assert json.loads(recon.state.read_text())["job-1"]["status"] == "submitted_unconfirmed"
    assert recon.led.is_blocked_for_job("job-1")


def test_reconcile_not_sent_reopens_the_job(recon):
    assert rs.main(["--mark", "not_sent", "--job", "job-1", "--reason", "nothing arrived",
                    "--i-verified-by-hand"]) == 0
    rec = json.loads(recon.state.read_text())["job-1"]
    assert rec["status"] == "pending" and rec["phases"] == [] and "attempt_id" not in rec
    assert ap.validate_state({"job-1": rec}) is None
    assert not recon.led.is_blocked_for_job("job-1") and not recon.led.is_blocked_for_posting("p1")


def test_reconcile_submitted_after_not_sent_restores_blocking(recon):
    assert rs.main(["--mark", "not_sent", "--job", "job-1", "--reason", "looked empty", "--i-verified-by-hand"]) == 0
    assert not recon.led.is_blocked_for_job("job-1")
    recon.state.write_text(json.dumps({"job-1": {"status": "needs_review", "attempts": 2}}))
    assert rs.main(["--mark", "submitted", "--job", "job-1", "--reason", "the receipt showed up late"]) == 0
    assert recon.led.is_blocked_for_job("job-1") and recon.led.is_blocked_for_posting("p1")


def test_reconcile_submitted_then_not_sent_then_submitted_blocks_again(recon):
    assert rs.main(["--mark", "submitted", "--job", "job-1", "--reason", "saw the receipt"]) == 0
    assert rs.main(["--mark", "not_sent", "--job", "job-1", "--reason", "it was a different job",
                    "--i-verified-by-hand"]) == 0
    assert not recon.led.is_blocked_for_job("job-1") and not recon.led.is_blocked_for_posting("p1")
    entry = recon.led.entries()["k1"]
    assert entry["human_confirmed"] is True and entry["cleared"] is True  # the state that used to defeat re-marking
    assert rs.main(["--mark", "submitted", "--job", "job-1", "--reason", "it was sent after all"]) == 0
    assert recon.led.is_blocked_for_job("job-1") and recon.led.is_blocked_for_posting("p1")
    entry = recon.led.entries()["k1"]
    assert entry["outcome"] == "confirmed" and entry["cleared"] is False
    assert [r["action"] for r in recon.led.records() if r["type"] == "human_override"] == [
        "submitted_confirmed", "not_sent_cleared", "submitted_confirmed"]
    assert json.loads(recon.state.read_text())["job-1"]["status"] == "done"


@pytest.mark.parametrize("argv", [["--list", "--mark", "submitted"], [], ["--mark", "submitted", "--job", "job-1"],
                                  ["--mark", "maybe", "--job", "j", "--reason", "r"]])
def test_reconcile_misuse_exits_nonzero(recon, argv):
    with pytest.raises(SystemExit) as exc:
        rs.main(argv)
    assert exc.value.code != 0


def test_reconcile_unknown_job_and_corrupt_state(recon, capsys):
    assert rs.main(["--mark", "submitted", "--job", "nope", "--reason", "r"]) != 0
    recon.state.write_text("{broken")
    assert rs.main(["--list"]) != 0
    assert rs.main(["--mark", "submitted", "--job", "job-1", "--reason", "r"]) != 0
    recon.state.write_text(json.dumps(["not", "a", "dict"]))
    assert rs.main(["--list"]) != 0
    assert recon.led.is_blocked_for_job("job-1")


def test_reconcile_refuses_to_act_on_a_malformed_ledger(recon, capsys):
    with open(recon.ledger_path, "a") as fh:
        fh.write("{not json\n")
    before = recon.state.read_text()
    assert rs.main(["--list"]) == 3
    assert "ledger" in capsys.readouterr().err
    assert rs.main(["--mark", "submitted", "--job", "job-1", "--reason", "r"]) == 3
    assert rs.main(["--mark", "not_sent", "--job", "job-1", "--reason", "r", "--i-verified-by-hand"]) == 3
    assert recon.state.read_text() == before  # no state change when the ledger could not be read
