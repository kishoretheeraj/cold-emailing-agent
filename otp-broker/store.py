"""SQLite persistence: active waiters and consumed message ids.

One connection guarded by a lock (single writer), WAL mode, file mode 600.
Calls are short and synchronous; they are safe to make from the event loop.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS waiters (
    request_id        TEXT PRIMARY KEY,
    fingerprint       TEXT NOT NULL,
    sender_domains    TEXT NOT NULL,
    after_ts          TEXT NOT NULL,
    kind              TEXT NOT NULL,
    hint              TEXT,
    allowed_link_hosts TEXT NOT NULL,
    expires_at        TEXT NOT NULL,
    result            TEXT
);
CREATE TABLE IF NOT EXISTS consumed (
    uidvalidity INTEGER NOT NULL,
    uid         INTEGER NOT NULL,
    consumed_at TEXT NOT NULL,
    UNIQUE (uidvalidity, uid)
);
"""


@dataclass
class Waiter:
    """A pending wait.  ``future`` is set only for waiters with a live HTTP request."""

    request_id: str
    sender_domains: list[str]
    after: datetime
    kind: str
    hint: str | None
    allowed_link_hosts: list[str]
    expires_at: datetime
    result: dict[str, Any] | None = None
    future: Any = field(default=None, repr=False, compare=False)

    @property
    def fingerprint(self) -> str:
        """Identity of the *request parameters*, used to re-attach a stored result."""
        return json.dumps(
            [
                sorted(d.lower() for d in self.sender_domains),
                self.kind,
                self.hint,
                sorted(h.lower() for h in self.allowed_link_hosts),
            ]
        )


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._lock = threading.Lock()
        old_umask = os.umask(0o077)  # db, -wal and -shm are created 600
        try:
            self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        finally:
            os.umask(old_umask)
        if str(path) != ":memory:":
            os.chmod(path, 0o600)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(_SCHEMA)
        self.purge_consumed()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- consumed ---------------------------------------------------------
    def consume(self, uidvalidity: int, uid: int) -> bool:
        """Atomically claim a message.  True for exactly one caller per (uidvalidity, uid)."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                cur = self._db.execute(
                    "INSERT OR IGNORE INTO consumed (uidvalidity, uid, consumed_at) VALUES (?, ?, ?)",
                    (uidvalidity, uid, _iso(datetime.now(timezone.utc))),
                )
                claimed = cur.rowcount == 1
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            return claimed

    # -- waiters ----------------------------------------------------------
    def add_waiter(self, w: Waiter) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO waiters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    w.request_id,
                    w.fingerprint,
                    json.dumps(w.sender_domains),
                    _iso(w.after),
                    w.kind,
                    w.hint,
                    json.dumps(w.allowed_link_hosts),
                    _iso(w.expires_at),
                    json.dumps(w.result) if w.result else None,
                ),
            )

    def set_result(self, request_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE waiters SET result = ? WHERE request_id = ?", (json.dumps(result), request_id)
            )

    def delete_waiter(self, request_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM waiters WHERE request_id = ?", (request_id,))

    def purge_expired(self, now: datetime) -> None:
        with self._lock:
            self._db.execute("DELETE FROM waiters WHERE expires_at <= ?", (_iso(now),))

    def purge_consumed(self, older_than: timedelta = timedelta(days=7)) -> int:
        """Delete consumed-message rows older than ``older_than``; returns the row count."""
        cutoff = _iso(datetime.now(timezone.utc) - older_than)
        with self._lock:
            return self._db.execute("DELETE FROM consumed WHERE consumed_at < ?", (cutoff,)).rowcount

    def load_waiters(self, now: datetime) -> list[Waiter]:
        """Unexpired waiters (expired ones are deleted)."""
        self.purge_expired(now)
        with self._lock:
            rows = self._db.execute(
                "SELECT request_id, sender_domains, after_ts, kind, hint, allowed_link_hosts,"
                " expires_at, result FROM waiters"
            ).fetchall()
        return [
            Waiter(
                request_id=r[0],
                sender_domains=json.loads(r[1]),
                after=datetime.fromisoformat(r[2]),
                kind=r[3],
                hint=r[4],
                allowed_link_hosts=json.loads(r[5]),
                expires_at=datetime.fromisoformat(r[6]),
                result=json.loads(r[7]) if r[7] else None,
            )
            for r in rows
        ]


def default_db_path() -> Path:
    return Path(os.environ.get("OTP_BROKER_DB", "./otp-broker.db"))
