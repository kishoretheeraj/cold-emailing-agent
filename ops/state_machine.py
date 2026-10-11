"""Application lifecycle state machine with a durable SQLite audit log.

Every transition is validated against VALID_TRANSITIONS and recorded with a
timestamp. UNKNOWN_SUBMISSION is a dead end for retries: the only ways out are
SUBMITTED or BLOCKED (after a human or reconciler confirms what happened), never
back to APPLYING.
"""
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from enum import Enum

log = logging.getLogger(__name__)


# ── States ─────────────────────────────────────────────────────────────────────

class ApplicationState(Enum):
    """Lifecycle states of a job application."""

    DISCOVERED = "discovered"
    NORMALIZED = "normalized"
    SCREENED = "screened"
    ELIGIBLE = "eligible"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
    TAILOR_PENDING = "tailor_pending"
    RESUME_READY = "resume_ready"
    APPLY_READY = "apply_ready"
    APPLYING = "applying"
    SUBMITTED = "submitted"
    BLOCKED = "blocked"
    AWAITING_VERIFICATION = "awaiting_verification"
    AWAITING_APPROVAL = "awaiting_approval"
    UNKNOWN_SUBMISSION = "unknown_submission"


S = ApplicationState

VALID_TRANSITIONS = {
    S.DISCOVERED: {S.NORMALIZED},
    S.NORMALIZED: {S.SCREENED},
    S.SCREENED: {S.ELIGIBLE, S.REJECTED, S.NEEDS_REVIEW},
    S.NEEDS_REVIEW: {S.ELIGIBLE, S.REJECTED},
    S.ELIGIBLE: {S.TAILOR_PENDING},
    S.TAILOR_PENDING: {S.RESUME_READY},
    S.RESUME_READY: {S.APPLY_READY},
    S.APPLY_READY: {S.APPLYING},
    S.APPLYING: {
        S.SUBMITTED,
        S.BLOCKED,
        S.AWAITING_VERIFICATION,
        S.AWAITING_APPROVAL,
        S.UNKNOWN_SUBMISSION,
    },
    S.AWAITING_VERIFICATION: {S.APPLYING, S.BLOCKED},
    S.AWAITING_APPROVAL: {S.APPLYING, S.BLOCKED},
    # Never back to APPLYING: reconcile first, then record what actually happened.
    S.UNKNOWN_SUBMISSION: {S.SUBMITTED, S.BLOCKED},
    # A blocked application goes to a human, never straight back into the apply loop.
    S.BLOCKED: {S.NEEDS_REVIEW, S.REJECTED},
    S.SUBMITTED: set(),
    S.REJECTED: set(),
}


class InvalidTransition(Exception):
    """Raised when a requested state change is not in VALID_TRANSITIONS."""


# ── State machine ──────────────────────────────────────────────────────────────

class StateMachine:
    """Persists application states and their full transition history in SQLite."""

    def __init__(self, db_path: str = ":memory:") -> None:
        """Open (and create if needed) the state and audit tables at db_path."""
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS application_state ("
                "application_id TEXT PRIMARY KEY, state TEXT NOT NULL)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS state_audit ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT NOT NULL, "
                "ts TEXT NOT NULL, from_state TEXT, to_state TEXT NOT NULL, reason TEXT)"
            )

    def transition(self, application_id: str, to_state: ApplicationState, reason: str = "") -> bool:
        """Move an application to to_state, or raise InvalidTransition.

        An unseen application may only enter at DISCOVERED.
        """
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT state FROM application_state WHERE application_id = ?",
                (application_id,),
            ).fetchone()
            from_state = ApplicationState(row[0]) if row else None
            allowed = {S.DISCOVERED} if from_state is None else VALID_TRANSITIONS[from_state]
            if to_state not in allowed:
                raise InvalidTransition(
                    f"{application_id}: {from_state.value if from_state else 'none'} "
                    f"-> {to_state.value} is not allowed"
                )
            self._conn.execute(
                "INSERT INTO application_state (application_id, state) VALUES (?, ?) "
                "ON CONFLICT(application_id) DO UPDATE SET state = excluded.state",
                (application_id, to_state.value),
            )
            self._conn.execute(
                "INSERT INTO state_audit (application_id, ts, from_state, to_state, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    application_id,
                    datetime.now(timezone.utc).isoformat(),
                    from_state.value if from_state else None,
                    to_state.value,
                    reason,
                ),
            )
        if to_state is S.UNKNOWN_SUBMISSION:
            log.critical(
                f"[OPS] {application_id} | UNKNOWN_SUBMISSION | never auto-retry, reconcile first"
            )
        else:
            log.info(
                f"[OPS] {application_id} | {from_state.value if from_state else 'none'} "
                f"-> {to_state.value}"
            )
        return True

    def get_state(self, application_id: str) -> ApplicationState:
        """Return the current state; KeyError if the application was never recorded."""
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM application_state WHERE application_id = ?",
                (application_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown application: {application_id}")
        return ApplicationState(row[0])

    def get_history(self, application_id: str) -> list[dict]:
        """Return every transition for the application, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, from_state, to_state, reason FROM state_audit "
                "WHERE application_id = ? ORDER BY id",
                (application_id,),
            ).fetchall()
        return [
            {"timestamp": ts, "from_state": frm, "to_state": to, "reason": reason}
            for ts, frm, to, reason in rows
        ]

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()
