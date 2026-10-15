import sys
import types

import pytest

from ops import browser_pool
from ops.browser_pool import BrowserPool, human_delay
from ops.checkpoint import CheckpointManager
from ops.state_machine import (
    VALID_TRANSITIONS,
    ApplicationState as S,
    InvalidTransition,
    StateMachine,
)


# ── State machine ──────────────────────────────────────────────────────────────

def _advance(sm, app_id, path):
    for state in path:
        sm.transition(app_id, state)


HAPPY_PATH = [
    S.DISCOVERED, S.NORMALIZED, S.SCREENED, S.ELIGIBLE, S.TAILOR_PENDING,
    S.RESUME_READY, S.APPLY_READY, S.APPLYING,
]


def test_every_state_has_a_transition_entry():
    assert set(VALID_TRANSITIONS) == set(S)


def test_happy_path_and_history():
    sm = StateMachine()
    _advance(sm, "a1", HAPPY_PATH + [S.SUBMITTED])
    assert sm.get_state("a1") == S.SUBMITTED
    history = sm.get_history("a1")
    assert [h["to_state"] for h in history] == [s.value for s in HAPPY_PATH + [S.SUBMITTED]]
    assert history[0]["from_state"] is None
    assert history[1]["from_state"] == "discovered"
    assert all(h["timestamp"] for h in history)


def test_reason_recorded():
    sm = StateMachine()
    sm.transition("a1", S.DISCOVERED, "from feed")
    assert sm.get_history("a1")[0]["reason"] == "from feed"


@pytest.mark.parametrize("to_state", [s for s in S if s != S.DISCOVERED])
def test_new_application_must_start_at_discovered(to_state):
    with pytest.raises(InvalidTransition):
        StateMachine().transition("a1", to_state)


@pytest.mark.parametrize("bad", [S.APPLYING, S.SUBMITTED, S.ELIGIBLE])
def test_invalid_transition_raises_and_leaves_state(bad):
    sm = StateMachine()
    _advance(sm, "a1", [S.DISCOVERED, S.NORMALIZED])
    with pytest.raises(InvalidTransition):
        sm.transition("a1", bad)
    assert sm.get_state("a1") == S.NORMALIZED
    assert len(sm.get_history("a1")) == 2


@pytest.mark.parametrize("terminal", [S.SUBMITTED, S.REJECTED])
def test_terminal_states_have_no_exits(terminal):
    assert VALID_TRANSITIONS[terminal] == set()


def test_unknown_submission_never_returns_to_applying(caplog):
    sm = StateMachine()
    _advance(sm, "a1", HAPPY_PATH)
    with caplog.at_level("CRITICAL"):
        sm.transition("a1", S.UNKNOWN_SUBMISSION)
    assert "never auto-retry, reconcile first" in caplog.text
    for forbidden in (S.APPLYING, S.APPLY_READY, S.AWAITING_VERIFICATION):
        with pytest.raises(InvalidTransition):
            sm.transition("a1", forbidden)
    assert VALID_TRANSITIONS[S.UNKNOWN_SUBMISSION] == {S.SUBMITTED, S.BLOCKED}


@pytest.mark.parametrize("outcome", [S.SUBMITTED, S.BLOCKED])
def test_unknown_submission_can_be_reconciled(outcome):
    sm = StateMachine()
    _advance(sm, "a1", HAPPY_PATH + [S.UNKNOWN_SUBMISSION])
    sm.transition("a1", outcome, "reconciled")
    assert sm.get_state("a1") == outcome


def test_blocked_cannot_reenter_apply_loop():
    assert S.APPLYING not in VALID_TRANSITIONS[S.BLOCKED]
    assert S.APPLY_READY not in VALID_TRANSITIONS[S.BLOCKED]


def test_get_state_unknown_application():
    with pytest.raises(KeyError):
        StateMachine().get_state("nope")


def test_state_persists_across_instances(tmp_path):
    path = str(tmp_path / "ops.db")
    sm = StateMachine(path)
    sm.transition("a1", S.DISCOVERED)
    sm.close()
    assert StateMachine(path).get_state("a1") == S.DISCOVERED


# ── Checkpoints ────────────────────────────────────────────────────────────────

def test_checkpoint_round_trip_and_overwrite():
    cm = CheckpointManager()
    cm.save("a1", "tailor", {"step": 1, "items": [1, 2]})
    assert cm.load("a1", "tailor") == {"step": 1, "items": [1, 2]}
    cm.save("a1", "tailor", {"step": 2})
    assert cm.load("a1", "tailor") == {"step": 2}
    assert cm.list_stages("a1") == ["tailor"]


def test_checkpoint_load_latest_and_missing():
    cm = CheckpointManager()
    assert cm.load("a1") is None
    assert cm.load("a1", "x") is None
    cm.save("a1", "tailor", {"n": 1})
    cm.save("a1", "fill", {"n": 2})
    assert cm.load("a1") == {"n": 2}
    assert cm.load("a1", "tailor") == {"n": 1}
    cm.save("a1", "tailor", {"n": 3})
    assert cm.load("a1") == {"n": 3}
    assert cm.list_stages("a1") == ["fill", "tailor"]


def test_checkpoint_clear_is_scoped_to_application():
    cm = CheckpointManager()
    cm.save("a1", "s", {"x": 1})
    cm.save("a2", "s", {"x": 2})
    cm.clear("a1")
    assert cm.load("a1") is None
    assert cm.list_stages("a1") == []
    assert cm.load("a2") == {"x": 2}


def test_checkpoint_persists_across_instances(tmp_path):
    path = str(tmp_path / "cp.db")
    cm = CheckpointManager(path)
    cm.save("a1", "s", {"x": 1})
    cm.close()
    assert CheckpointManager(path).load("a1", "s") == {"x": 1}


# ── Browser pool ───────────────────────────────────────────────────────────────

class _FakeBrowser:
    def __init__(self):
        self.connected = True
        self.closed = False

    def is_connected(self):
        return self.connected

    def close(self):
        self.closed = True
        self.connected = False


@pytest.fixture
def fake_playwright(monkeypatch):
    launched = []

    class _Chromium:
        def launch(self, headless=True):
            b = _FakeBrowser()
            launched.append(b)
            return b

    class _PW:
        chromium = _Chromium()
        stopped = False

        def stop(self):
            self.stopped = True

    pw = _PW()
    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: types.SimpleNamespace(start=lambda: pw)
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    return launched, pw


def test_pool_does_not_import_playwright_at_module_level():
    assert "playwright" not in vars(browser_pool)


def test_pool_reuses_healthy_browser_and_stats(fake_playwright):
    launched, _ = fake_playwright
    pool = BrowserPool(max_uses=5)
    assert pool.stats() == {"active_browsers": 0, "total_uses": 0, "recycles": 0}
    b1 = pool.get_browser()
    pool.release_browser(b1)
    assert pool.get_browser() is b1
    assert len(launched) == 1
    assert pool.stats() == {"active_browsers": 1, "total_uses": 1, "recycles": 0}


def test_pool_replaces_disconnected_browser(fake_playwright):
    launched, _ = fake_playwright
    pool = BrowserPool()
    b1 = pool.get_browser()
    b1.connected = False
    b2 = pool.get_browser()
    assert b2 is not b1
    assert len(launched) == 2


def test_pool_recycles_at_max_uses(fake_playwright):
    launched, pw = fake_playwright
    pool = BrowserPool(max_uses=2)
    b1 = pool.get_browser()
    pool.release_browser(b1)
    assert not b1.closed
    pool.release_browser(b1)
    assert b1.closed and pw.stopped
    assert pool.stats() == {"active_browsers": 0, "total_uses": 2, "recycles": 1}
    assert pool.get_browser() is not b1


def test_manual_recycle(fake_playwright):
    pool = BrowserPool()
    b = pool.get_browser()
    pool.recycle()
    assert b.closed
    assert pool.stats()["recycles"] == 1


def test_recycle_survives_close_errors(fake_playwright):
    pool = BrowserPool()
    b = pool.get_browser()
    b.close = lambda: (_ for _ in ()).throw(RuntimeError("dead"))
    pool.recycle()
    assert pool.stats()["active_browsers"] == 0


# ── human_delay ────────────────────────────────────────────────────────────────

def test_human_delay_defaults_within_range(mocker):
    sleep = mocker.patch("ops.browser_pool.time.sleep")
    for _ in range(50):
        human_delay()
    assert sleep.call_count == 50
    assert all(2.0 <= c.args[0] <= 5.0 for c in sleep.call_args_list)


def test_human_delay_custom_range_uses_uniform(mocker):
    sleep = mocker.patch("ops.browser_pool.time.sleep")
    uniform = mocker.patch("ops.browser_pool.random.uniform", return_value=1.5)
    human_delay(1.0, 2.0)
    uniform.assert_called_once_with(1.0, 2.0)
    sleep.assert_called_once_with(1.5)
