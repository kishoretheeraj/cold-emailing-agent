import asyncio
import threading
from datetime import datetime, timedelta, timezone

import pytest

from broker import Broker
from store import Store, Waiter
from fakes import FakeMailbox, make_msg

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def test_db_is_wal_and_mode_600(tmp_path):
    s = Store(tmp_path / "x.db")
    assert s._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert (tmp_path / "x.db").stat().st_mode & 0o777 == 0o600
    s.close()


def test_consume_is_atomic_across_threads(store):
    wins = []
    def go():
        wins.append(store.consume(1, 7))
    ts = [threading.Thread(target=go) for _ in range(16)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert wins.count(True) == 1
    assert store.consume(2, 7) is True  # different uidvalidity is a different message


def test_waiter_persistence_and_expiry(store):
    live = Waiter("a", ["example.com"], NOW(), "otp", None, ["example.com"], NOW() + timedelta(seconds=60))
    dead = Waiter("b", ["example.com"], NOW(), "otp", None, ["example.com"], NOW() - timedelta(seconds=1))
    store.add_waiter(live); store.add_waiter(dead)
    loaded = store.load_waiters(NOW())
    assert [w.request_id for w in loaded] == ["a"]


def wait(b, **kw):
    args = dict(sender_domains=["example.com"], after=NOW() - timedelta(minutes=1), kind="otp", timeout_s=2)
    args.update(kw)
    return b.wait(**args)


async def test_backlog_delivery_and_no_duplicate(store):
    mb = FakeMailbox([make_msg(1, "Your code is 111111")])
    b = Broker(store, mb)
    rid, res = await wait(b)
    assert res["code"] == "111111" and res["message_id"] == "<m1@x>"
    # same message must not be delivered again
    _, res2 = await wait(b, timeout_s=1)
    assert res2 == {"error": "timeout"}


async def test_concurrent_waiters_get_distinct_messages(store):
    now = NOW()
    mb = FakeMailbox([make_msg(1, "code 111111", received=now), make_msg(2, "code 222222", received=now)])
    b = Broker(store, mb)
    r = await asyncio.gather(wait(b), wait(b), wait(b, timeout_s=1))
    codes = sorted(x[1].get("code", "none") for x in r)
    assert codes == ["111111", "222222", "none"]


async def test_fan_out_by_sender_and_kind(store):
    mb = FakeMailbox([
        make_msg(1, "code 111111", sender="a@alpha.com"),
        make_msg(2, "code 222222", sender="b@beta.com"),
        make_msg(3, "Go https://gamma.com/verify/x now", sender="c@gamma.com"),
    ])
    b = Broker(store, mb)
    ra, rb, rc = await asyncio.gather(
        wait(b, sender_domains=["alpha.com"]),
        wait(b, sender_domains=["beta.com"]),
        wait(b, sender_domains=["gamma.com"], kind="link"),
    )
    assert ra[1]["code"] == "111111" and rb[1]["code"] == "222222"
    assert rc[1]["url"] == "https://gamma.com/verify/x"


async def test_mail_arriving_after_registration(store):
    mb = FakeMailbox()
    b = Broker(store, mb)
    await b.start()
    task = asyncio.create_task(wait(b, timeout_s=5))
    await asyncio.sleep(0.2)
    assert b.active_waiter_count == 1
    mb.messages.append(make_msg(9, "verification code 909090"))
    await b.scan()
    _, res = await task
    assert res["code"] == "909090"
    assert b.active_waiter_count == 0


async def test_newest_matching_message_wins(store):
    now = NOW()
    mb = FakeMailbox([
        make_msg(1, "code 111111", received=now - timedelta(seconds=30)),
        make_msg(2, "code 222222", received=now),
    ])
    _, res = await wait(Broker(store, mb))
    assert res["code"] == "222222"


async def test_conflict_is_reported_once(store):
    mb = FakeMailbox([make_msg(1, "code is 111111 code is 222222")])
    b = Broker(store, mb)
    _, res = await wait(b)
    assert res == {"error": "conflict"}
    assert store.consume(1, 1) is False  # claimed: later waiters don't re-409 on it
    mb.messages.append(make_msg(2, "Your code is 444444"))
    _, res2 = await wait(b)
    assert res2["code"] == "444444"


async def test_old_messages_and_hint_filter(store):
    old = make_msg(1, "code 111111", received=NOW() - timedelta(hours=2))
    other = make_msg(2, "code 222222", subject="Newsletter")
    mb = FakeMailbox([old, other])
    _, res = await wait(Broker(store, mb), hint="login", timeout_s=1)
    assert res == {"error": "timeout"}


async def test_timeout_deregisters(store):
    b = Broker(store, FakeMailbox())
    _, res = await wait(b, timeout_s=1)
    assert res == {"error": "timeout"}
    assert b.active_waiter_count == 0
    assert store.load_waiters(NOW()) == []


async def test_restart_reloads_waiter_and_keeps_result_for_retry(store):
    w = Waiter("old", ["example.com"], NOW() - timedelta(minutes=1), "otp", None, ["example.com"],
               NOW() + timedelta(seconds=60))
    store.add_waiter(w)
    b = Broker(store, FakeMailbox([make_msg(1, "code 424242")]))
    await b.start()  # orphan evaluated against backlog
    assert b.active_waiter_count == 0
    _, res = await wait(b, timeout_s=1)  # identical retry picks up stored result
    assert res["code"] == "424242"
    assert store.load_waiters(NOW()) == []


async def test_mark_seen_only_when_delivered(store):
    mb = FakeMailbox([make_msg(1, "code 111111")])
    await wait(Broker(store, mb))
    assert mb.seen == [(1, 1)]


def pytest_collection_modifyitems(items):  # pragma: no cover
    pass


# ---- hardening ----------------------------------------------------------------
class SlowMailbox(FakeMailbox):
    def __init__(self, delay, messages=None):
        super().__init__(messages)
        self.delay, self.calls = delay, 0

    def fetch_since(self, after):
        import time
        self.calls += 1
        time.sleep(self.delay)
        return super().fetch_since(after)


async def test_timeout_enforced_even_if_scan_is_slow(store):
    b = Broker(store, SlowMailbox(3.0))
    t0 = asyncio.get_running_loop().time()
    _, res = await wait(b, timeout_s=1)
    assert res == {"error": "timeout"} and asyncio.get_running_loop().time() - t0 < 2
    await asyncio.sleep(3.2)  # let the shielded scan thread finish


async def test_concurrent_waits_coalesce_scans(store):
    mb = SlowMailbox(0.3)
    b = Broker(store, mb)
    r = await asyncio.gather(*[wait(b, timeout_s=1) for _ in range(20)])
    assert all(x[1] == {"error": "timeout"} for x in r)
    assert mb.calls <= 4  # not one fetch per waiter


async def test_cancelled_waiter_deregisters(store):
    b = Broker(store, FakeMailbox())
    t = asyncio.create_task(wait(b, timeout_s=30))
    await asyncio.sleep(0.1)
    assert b.active_waiter_count == 1
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert b.active_waiter_count == 0 and store.load_waiters(NOW()) == []


async def test_begin_shutdown_fails_waiters_fast(store):
    b = Broker(store, FakeMailbox())
    t = asyncio.create_task(wait(b, timeout_s=30))
    await asyncio.sleep(0.1)
    b.begin_shutdown()
    _, res = await asyncio.wait_for(t, 1)
    assert res == {"error": "shutting_down"}


async def test_expired_orphans_not_counted_and_swept(store):
    b = Broker(store, FakeMailbox())
    old = Waiter("o", ["example.com"], NOW(), "otp", None, ["example.com"], NOW() - timedelta(seconds=1))
    b._waiters["o"] = old
    assert b.active_waiter_count == 0
    b._sweep(NOW())
    assert "o" not in b._waiters


def _authed(uid, results):
    m = make_msg(uid, "Your code is 123123")
    m.msg["Authentication-Results"] = results
    return m


async def test_dkim_gate(store):
    good = _authed(1, "mx.google.com; dkim=pass header.i=@mail.example.com header.s=x header.b=y; spf=pass")
    _, res = await wait(Broker(store, FakeMailbox([good])))
    assert res["code"] == "123123"
    bad = _authed(2, "mx.google.com; dkim=fail header.i=@example.com; spf=fail")
    _, res = await wait(Broker(store, FakeMailbox([bad])), timeout_s=1)
    assert res == {"error": "timeout"}
    other = _authed(3, "mx.google.com; dkim=pass header.i=@evil.io; spf=pass")
    _, res = await wait(Broker(store, FakeMailbox([other])), timeout_s=1)
    assert res == {"error": "timeout"}
    spam = make_msg(4, "Your code is 123123")
    spam.msg["X-Gm-Spam"] = "1"
    _, res = await wait(Broker(store, FakeMailbox([spam])), timeout_s=1)
    assert res == {"error": "timeout"}


def test_purge_consumed(store):
    store.consume(1, 1)
    assert store.purge_consumed(timedelta(days=7)) == 0
    assert store.purge_consumed(timedelta(seconds=-1)) == 1
