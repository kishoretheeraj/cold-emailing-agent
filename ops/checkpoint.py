"""Durable per-stage checkpoints (SQLite) so a failed run can resume where it stopped."""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional


class CheckpointManager:
    """Stores one JSON state blob per (application_id, stage)."""

    def __init__(self, db_path: str = ":memory:") -> None:
        """Open (and create if needed) the checkpoint table at db_path."""
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS checkpoints ("
                "application_id TEXT NOT NULL, stage TEXT NOT NULL, "
                "state_json TEXT NOT NULL, saved_at TEXT NOT NULL, "
                "seq INTEGER NOT NULL, PRIMARY KEY (application_id, stage))"
            )

    def save(self, application_id: str, stage: str, state: dict) -> None:
        """Write state for (application_id, stage), replacing any earlier checkpoint."""
        payload = json.dumps(state)
        with self._lock, self._conn:
            seq = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM checkpoints"
            ).fetchone()[0]
            self._conn.execute(
                "INSERT OR REPLACE INTO checkpoints "
                "(application_id, stage, state_json, saved_at, seq) VALUES (?, ?, ?, ?, ?)",
                (application_id, stage, payload, datetime.now(timezone.utc).isoformat(), seq),
            )

    def load(self, application_id: str, stage: Optional[str] = None) -> Optional[dict]:
        """Return the checkpoint for a stage, or the most recently saved one if stage is None."""
        with self._lock:
            if stage is None:
                row = self._conn.execute(
                    "SELECT state_json FROM checkpoints WHERE application_id = ? "
                    "ORDER BY seq DESC LIMIT 1",
                    (application_id,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT state_json FROM checkpoints WHERE application_id = ? AND stage = ?",
                    (application_id, stage),
                ).fetchone()
        return json.loads(row[0]) if row else None

    def list_stages(self, application_id: str) -> list[str]:
        """Return checkpointed stages in the order they were last saved."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT stage FROM checkpoints WHERE application_id = ? ORDER BY seq",
                (application_id,),
            ).fetchall()
        return [r[0] for r in rows]

    def clear(self, application_id: str) -> None:
        """Delete all checkpoints for the application (call on successful completion)."""
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM checkpoints WHERE application_id = ?", (application_id,)
            )

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()
