"""Outbox, sync drain and backfill tests. No network: _post is always a stub."""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import backfill_results
import outbox
import reply_repo
import supabase_sync

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def _conn(tmp_path, name="p.db"):
    conn = reply_repo.connect(str(tmp_path / name))
    outbox.ensure_schema(conn)
    return conn


def _rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM sync_outbox ORDER BY id")]


class Poster:
    def __init__(self, *statuses, fn=None):
        self.statuses = list(statuses)
        self.fn = fn
        self.calls = []

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, json.loads(body)))
        if self.fn is not None:
            return self.fn(url, headers, json.loads(body))
        return self.statuses.pop(0) if self.statuses else 200


# ── enqueue ────────────────────────────────────────────────────────────────────

def test_enqueue_returns_true_and_row_visible_in_same_transaction(tmp_path):
    conn = _conn(tmp_path)
    conn.execute("BEGIN")
    assert outbox.enqueue(conn, "job", "j1", "upsert", {"id": "j1"}) is True
    assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 1
    other = sqlite3.connect(str(tmp_path / "p.db"))
    assert other.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0
    other.close()
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0


def test_enqueue_commits_when_no_transaction_open(tmp_path):
    conn = _conn(tmp_path)
    assert outbox.enqueue(conn, "job", "j1", "upsert", {"id": "j1"}) is True
    other = sqlite3.connect(str(tmp_path / "p.db"))
    assert other.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 1
    other.close()


def test_enqueue_never_raises_on_unserializable_payload(tmp_path):
    conn = _conn(tmp_path)
    assert outbox.enqueue(conn, "job", "j1", "upsert", {"x": object()}) is False
    assert outbox.enqueue(conn, "job", "j1", "upsert", {"x": {1, 2}}) is False
    assert _rows(conn) == []


def test_enqueue_rejects_unknown_entity_and_op(tmp_path):
    conn = _conn(tmp_path)
    assert outbox.enqueue(conn, "credential", "x", "upsert", {}) is False
    assert outbox.enqueue(conn, "job", "x", "delete", {}) is False


def test_enqueue_creates_table_on_a_bare_connection(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "bare.db"))
    assert outbox.enqueue(conn, "reply", "m1", "upsert", {"message_id": "m1"}) is True
    assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 1


# ── drain ──────────────────────────────────────────────────────────────────────

def test_drain_2xx_marks_sent_and_keeps_row(tmp_path):
    conn = _conn(tmp_path)
    outbox.enqueue(conn, "job", "j1", "upsert", {"id": "j1"})
    post = Poster(200)
    counts = supabase_sync.drain(conn, post, NOW, "https://x.example", "k")
    assert counts["sent"] == 1
    row = _rows(conn)[0]
    assert row["sent_at"] is not None and row["last_error"] is None
    url, headers, body = post.calls[0]
    assert url.endswith("/rest/v1/rpc/pipeline_upsert")
    assert body["target"] == "pipeline_jobs" and body["rows"][0]["id"] == "j1"
    assert body["rows"][0]["version"] == 1


def test_drain_5xx_then_2xx_backs_off_then_sends(tmp_path):
    conn = _conn(tmp_path)
    outbox.enqueue(conn, "job", "j1", "upsert", {"id": "j1"})
    post = Poster(503, 200)
    first = supabase_sync.drain(conn, post, NOW)
    assert first["retried"] == 1 and first["halted"] is True
    row = _rows(conn)[0]
    assert row["attempts"] == 1 and row["sent_at"] is None and row["last_error"] == "HTTP503"
    assert row["next_attempt_at"] > NOW.replace(microsecond=0).isoformat()

    again = supabase_sync.drain(conn, post, NOW + timedelta(seconds=5))
    assert again["rows"] == 0 and len(post.calls) == 1

    later = supabase_sync.drain(conn, post, NOW + timedelta(seconds=120))
    assert later["sent"] == 1 and len(post.calls) == 2
    assert _rows(conn)[0]["sent_at"] is not None


def test_drain_429_is_retryable(tmp_path):
    conn = _conn(tmp_path)
    outbox.enqueue(conn, "job", "j1", "upsert", {"id": "j1"})
    supabase_sync.drain(conn, Poster(429), NOW)
    row = _rows(conn)[0]
    assert row["last_error"] == "HTTP429" and row["sent_at"] is None


def test_drain_4xx_poisons_without_blocking_later_rows(tmp_path):
    conn = _conn(tmp_path)
    outbox.enqueue(conn, "job", "bad", "upsert", {"id": "bad"})
    outbox.enqueue(conn, "job", "good", "upsert", {"id": "good"})
    outbox.enqueue(conn, "attempt", "a1", "upsert", {"attempt_id": "a1", "job_id": "good"})

    def fn(url, headers, body):
        return 400 if any(r.get("id") == "bad" for r in body["rows"]) else 200

    counts = supabase_sync.drain(conn, Poster(fn=fn), NOW)
    rows = {r["entity_id"]: r for r in _rows(conn)}
    assert counts["poisoned"] == 1 and counts["sent"] == 2
    assert rows["bad"]["last_error"] == "HTTP400" and rows["bad"]["sent_at"] is not None
    assert rows["good"]["last_error"] is None and rows["good"]["sent_at"] is not None
    assert rows["a1"]["sent_at"] is not None


def test_retryable_failure_defers_dependent_entities(tmp_path):
    conn = _conn(tmp_path)
    outbox.enqueue(conn, "job", "j1", "upsert", {"id": "j1"})
    outbox.enqueue(conn, "attempt", "a1", "upsert", {"attempt_id": "a1", "job_id": "j1"})
    post = Poster(500)
    supabase_sync.drain(conn, post, NOW)
    rows = {r["entity"]: r for r in _rows(conn)}
    assert len(post.calls) == 1
    assert rows["attempt"]["attempts"] == 0 and rows["attempt"]["sent_at"] is None


def test_conflict_splits_so_one_orphan_does_not_doom_neighbours(tmp_path):
    conn = _conn(tmp_path)
    outbox.enqueue(conn, "job", "orphan", "upsert", {"id": "orphan"})
    outbox.enqueue(conn, "job", "good", "upsert", {"id": "good"})

    def fn(url, headers, body):
        return 409 if any(r.get("id") == "orphan" for r in body["rows"]) else 200

    post = Poster(fn=fn)
    counts = supabase_sync.drain(conn, post, NOW)
    rows = {r["entity_id"]: r for r in _rows(conn)}
    # The orphan retried alone; the healthy row was sent on the first pass.
    assert rows["good"]["sent_at"] is not None
    assert rows["orphan"]["sent_at"] is None and rows["orphan"]["attempts"] == 1
    assert counts["sent"] == 1 and counts["retried"] == 1


def test_replay_same_version_twice_second_drain_is_noop(tmp_path):
    conn = _conn(tmp_path)
    outbox.enqueue(conn, "job", "j1", "upsert", {"id": "j1"}, version=1)
    outbox.enqueue(conn, "job", "j1", "upsert", {"id": "j1"}, version=1)
    post = Poster()
    supabase_sync.drain(conn, post, NOW)
    assert len(post.calls) == 1
    assert all(r["sent_at"] for r in _rows(conn))
    again = supabase_sync.drain(conn, post, NOW + timedelta(minutes=5))
    assert again["rows"] == 0 and len(post.calls) == 1


# ── run ────────────────────────────────────────────────────────────────────────

def test_run_once_returns_counts(tmp_path):
    db = str(tmp_path / "run.db")
    conn = reply_repo.connect(db)
    for i in range(3):
        outbox.enqueue(conn, "job", f"j{i}", "upsert", {"id": f"j{i}"})
    conn.close()
    counts = supabase_sync.run(once=True, _post=Poster(), _now=lambda: NOW, db_path=db)
    assert counts["sent"] == 3 and counts["rows"] == 3 and counts["batches"] == 1


def test_run_without_credentials_does_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    counts = supabase_sync.run(once=True, db_path=str(tmp_path / "x.db"))
    assert counts["rows"] == 0 and counts["batches"] == 0


def test_stop_flag_finishes_current_batch_then_exits(tmp_path):
    db = str(tmp_path / "stop.db")
    conn = reply_repo.connect(db)
    for i in range(120):
        outbox.enqueue(conn, "job", f"j{i}", "upsert", {"id": f"j{i}"})
    conn.close()

    def fn(url, headers, body):
        supabase_sync.STOP.set()
        return 200

    counts = supabase_sync.run(poll_seconds=0, _post=Poster(fn=fn), _now=lambda: NOW, db_path=db)
    assert counts["batches"] == 1 and counts["sent"] == supabase_sync.BATCH_SIZE
    check = sqlite3.connect(db)
    unsent = check.execute("SELECT COUNT(*) FROM sync_outbox WHERE sent_at IS NULL").fetchone()[0]
    check.close()
    assert unsent == 120 - supabase_sync.BATCH_SIZE


def test_purge_sent_removes_only_old_sent_rows(tmp_path):
    conn = _conn(tmp_path)
    outbox.enqueue(conn, "job", "old", "upsert", {"id": "old"})
    outbox.enqueue(conn, "job", "new", "upsert", {"id": "new"})
    outbox.enqueue(conn, "job", "pending", "upsert", {"id": "pending"})
    conn.execute("UPDATE sync_outbox SET sent_at = ? WHERE entity_id = 'old'",
                 ((NOW - timedelta(days=40)).isoformat(),))
    conn.execute("UPDATE sync_outbox SET sent_at = ? WHERE entity_id = 'new'", (NOW.isoformat(),))
    conn.commit()
    assert supabase_sync.purge_sent(conn, 30, now=NOW) == 1
    assert {r["entity_id"] for r in _rows(conn)} == {"new", "pending"}


# ── backfill ───────────────────────────────────────────────────────────────────

def _backfill_files(tmp_path):
    queue = tmp_path / "job_queue.json"
    queue.write_text(json.dumps([
        {"id": "j1", "company": "Acme", "role": "PM", "url": "https://x.example/1", "platform": "greenhouse", "notes": ""},
        {"id": "j2", "company": "Beta", "role": "APM", "url": "https://x.example/2", "platform": "lever", "notes": ""},
    ]))
    results = tmp_path / "application_results.jsonl"
    lines = [
        {"job_id": "j1", "attempt_id": "3f2b8c1e-0000-4000-8000-000000000001", "started_at": "2026-10-01T10:00:00+00:00",
         "finished_at": "2026-10-01T10:01:00+00:00", "status": "submitted", "detail": "ok", "latency_s": 60.0, "dry_run": False},
        {"job_id": "j2", "started_at": "2026-10-02T10:00:00+00:00", "finished_at": "2026-10-02T10:01:00+00:00",
         "status": "dry_run_ok", "detail": "x", "latency_s": 61.0, "dry_run": True},
        {"job_id": "j3", "started_at": "2026-10-03T10:00:00+00:00", "finished_at": "2026-10-03T10:01:00+00:00",
         "status": "failed", "detail": "y", "latency_s": 5.0, "dry_run": False},
    ]
    results.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    return str(queue), str(results)


def test_backfill_dry_writes_nothing(tmp_path):
    queue, results = _backfill_files(tmp_path)
    db = str(tmp_path / "dry.db")
    code = backfill_results.main(["--dry", "--queue", queue, "--results", results, "--db", db])
    assert code == 1  # the malformed line is counted as an error
    assert not os.path.exists(db)
    counts = backfill_results.run(queue_path=queue, results_path=results, db_path=db, dry=True)
    assert counts["jobs"] == 3 and counts["attempts"] == 3
    assert not os.path.exists(db)


def test_backfill_twice_leaves_identical_row_counts(tmp_path):
    queue, results = _backfill_files(tmp_path)
    db = str(tmp_path / "bf.db")
    first = backfill_results.run(queue_path=queue, results_path=results, db_path=db)
    conn = reply_repo.connect(db)
    n1 = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]
    conn.close()
    second = backfill_results.run(queue_path=queue, results_path=results, db_path=db)
    conn = reply_repo.connect(db)
    n2 = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]
    entities = {r[0] for r in conn.execute("SELECT DISTINCT entity FROM sync_outbox")}
    conn.close()
    assert n1 == n2 == 6  # 2 queued jobs + 1 stub job + 3 attempts
    assert first["jobs"] == 3 and first["attempts"] == 3
    assert second["jobs"] == 0 and second["attempts"] == 0 and second["skipped"] == 6
    assert entities == {"job", "attempt"}


def test_backfill_derives_attempt_id_when_missing(tmp_path):
    queue, results = _backfill_files(tmp_path)
    db = str(tmp_path / "ids.db")
    backfill_results.run(queue_path=queue, results_path=results, db_path=db)
    conn = reply_repo.connect(db)
    ids = {r[0] for r in conn.execute("SELECT entity_id FROM sync_outbox WHERE entity = 'attempt'")}
    conn.close()
    assert outbox.derive_attempt_id("j2", "2026-10-02T10:00:00+00:00") in ids
    assert "3f2b8c1e-0000-4000-8000-000000000001" in ids
