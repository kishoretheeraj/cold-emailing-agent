"""
Persistent pacing and backoff for auth actions, per scope ('platform:tenant').

A JSON file (0600, flocked, atomic writes) so the state survives restarts. Rules:
- at least 60 s between auth actions in one scope (check_and_mark);
- a 429 or an account lockout stops the scope for 24 h (record_429 / record_lockout);
- backoff_for gives the wait before retry n: 30 s, 2 min, 10 min (capped at 30 min), then
  BackoffExhausted after 3 attempts.
The clock is injectable (now_fn returns epoch seconds) for tests.
"""

import logging
import time
from datetime import datetime, timezone

from credential_vault import file_lock

from ._fileio import atomic_write_json, read_json

log = logging.getLogger(__name__)

DEFAULT_PATH = "/var/lib/jobagent24/ats_backoff.json"
MIN_GAP_SECONDS = 60
STOP_SECONDS = 24 * 3600
BACKOFF_STEPS = (30, 120, 600)
BACKOFF_CAP_SECONDS = 30 * 60


class BackoffExhausted(Exception):
    pass


class BackoffStore:
    def __init__(self, path=DEFAULT_PATH, now_fn=None):
        self.path = path
        self._now = now_fn or time.time

    def _update(self, mutate):
        with file_lock(self.path):
            data = read_json(self.path, {})
            result = mutate(data)
            atomic_write_json(self.path, data)
            return result

    def check_and_mark(self, scope):
        """Seconds to wait before the next auth action in `scope`; 0 (and the action is marked)
        when it is clear to go. While the scope is stopped, the remaining stop time."""
        def mutate(data):
            now = self._now()
            rec = data.setdefault(scope, {})
            stopped_until = float(rec.get("stopped_until", 0))
            if stopped_until > now:
                return stopped_until - now
            if "last_action" in rec:
                wait = float(rec["last_action"]) + MIN_GAP_SECONDS - now
                if wait > 0:
                    return wait
            rec["last_action"] = now
            return 0.0
        return self._update(mutate)

    def _stop(self, scope, reason):
        def mutate(data):
            now = self._now()
            rec = data.setdefault(scope, {})
            rec["stopped_until"] = now + STOP_SECONDS
            rec["stop_reason"] = reason
        self._update(mutate)
        log.warning(f"[ATS] | backoff | {scope} | stopped 24h | reason={reason}")

    def record_429(self, scope):
        self._stop(scope, "http_429")

    def record_lockout(self, scope):
        self._stop(scope, "lockout")

    def is_stopped(self, scope):
        """False, or the ISO time the stop lifts (truthy)."""
        rec = read_json(self.path, {}).get(scope, {})
        until = float(rec.get("stopped_until", 0))
        if until > self._now():
            return datetime.fromtimestamp(until, timezone.utc).isoformat()
        return False

    def backoff_for(self, scope, attempt):
        """Seconds to wait before retry number `attempt` (1-based)."""
        if attempt < 1:
            raise ValueError("attempt is 1-based")
        if attempt > len(BACKOFF_STEPS):
            raise BackoffExhausted(f"{scope}: {len(BACKOFF_STEPS)} attempts used")
        return min(BACKOFF_STEPS[attempt - 1], BACKOFF_CAP_SECONDS)
