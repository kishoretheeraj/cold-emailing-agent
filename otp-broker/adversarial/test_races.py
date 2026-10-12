"""Area 4: races / consume atomicity / restart.
Parts 1-4: in-process Broker + FakeMailbox + temp SQLite (simulated mail; the live mailbox is never touched, no creds read).
Part 5 (live): restart the real service with a waiter in flight (gated on no other active waiters)."""
import asyncio
import json
import os
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from fastapi.testclient import TestClient

from broker import Broker
from common import Rec, ensure_idle, health, http_call, now_iso, sh, wait_for, wait_req
from imap_client import MailMessage
from main import Config, create_app
from store import Store

import logging
logging.getLogger().setLevel(logging.CRITICAL)
rec = Rec("races")
UTC = timezone.utc


def msg(uid, body, *, sender="noreply@acme.com", age_s=0, uv=1, html=None):
    m = EmailMessage(); m["From"], m["Subject"], m["Message-ID"] = sender, "s", f"<m{uid}@t>"
    m.set_content(body)
    if html:
        m.add_alternative(html, subtype="html")
    return MailMessage(uv, uid, f"<m{uid}@t>", sender, "s", datetime.now(UTC) - timedelta(seconds=age_s), m)


class FakeMbox:
    connected, degraded, auth_failed = True, False, False
    last_successful_poll = last_auth_failure = None

    def __init__(self, delay=0.0):
        self.messages, self.delay, self.calls, self.raise_exc = [], delay, 0, None

    def fetch_since(self, after):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.raise_exc:
            raise self.raise_exc
        return [m for m in self.messages if m.received_at >= after]

    def mark_seen(self, *a): pass
    def start(self): pass
    def stop(self): pass


def newdb():
    return os.path.join(tempfile.mkdtemp(prefix="otpadv"), "t.db")


async def run_waiters(broker, n, **kw):
    kw.setdefault("sender_domains", ["acme.com"]); kw.setdefault("kind", "otp"); kw.setdefault("timeout_s", 3)
    kw.setdefault("after", datetime.now(UTC) - timedelta(seconds=1))
    return await asyncio.gather(*[broker.wait(**kw) for _ in range(n)])


def codes(results):
    return [r[1].get("code") or r[1].get("error") for r in results]


# ---- R01: N waiters, 1 mail injected mid-wait -> exactly one code ---------------------------
async def r01():
    mb = FakeMbox(); st = Store(newdb()); b = Broker(st, mb); await b.start()
    tasks = [asyncio.create_task(b.wait(sender_domains=["acme.com"], after=datetime.now(UTC) - timedelta(seconds=1), kind="otp", timeout_s=3)) for _ in range(10)]
    await asyncio.sleep(0.5)
    mb.messages.append(msg(1, "Your verification code is 424242"))
    b.notify_threadsafe() if False else await b.scan()
    res = await asyncio.gather(*tasks)
    return codes(res)

c = asyncio.run(r01())
rec.case("R01", "10 concurrent waiters, 1 matching mail -> exactly one gets the code", c.count("424242") == 1 and c.count("timeout") == 9, f"results={c}", "P0",
         "Atomic consume must be the only delivery path")

# ---- R02: 10 waiters, 10 mails -> each gets a distinct code -----------------------------------
async def r02():
    mb = FakeMbox(); st = Store(newdb()); b = Broker(st, mb); await b.start()
    tasks = [asyncio.create_task(b.wait(sender_domains=["acme.com"], after=datetime.now(UTC) - timedelta(seconds=1), kind="otp", timeout_s=3)) for _ in range(10)]
    await asyncio.sleep(0.5)
    mb.messages.extend(msg(i, f"Your verification code is {100000 + i}") for i in range(1, 11))
    await b.scan()
    return codes(await asyncio.gather(*tasks))

c = asyncio.run(r02())
rec.case("R02", "10 waiters, 10 mails -> 10 distinct codes, none duplicated", len(set(c)) == 10 and all(x.isdigit() for x in c), f"results={sorted(c)}", "P0")

# ---- R03: concurrent scans from IMAP thread notifications + registrations -----------------------
async def r03():
    mb = FakeMbox(); st = Store(newdb()); b = Broker(st, mb); await b.start()
    mb.messages.append(msg(1, "Your verification code is 313131"))
    ths = [threading.Thread(target=b.notify_threadsafe) for _ in range(50)]
    tasks = [asyncio.create_task(b.wait(sender_domains=["acme.com"], after=datetime.now(UTC) - timedelta(seconds=1), kind="otp", timeout_s=2)) for _ in range(20)]
    [t.start() for t in ths]
    res = codes(await asyncio.gather(*tasks))
    consumed = st._db.execute("select count(*) from consumed").fetchone()[0]
    return res, consumed

c, consumed = asyncio.run(r03())
rec.case("R03", "20 waiters + 50 thread-notified scans, 1 mail -> 1 delivery, 1 consumed row", c.count("313131") == 1 and consumed == 1, f"deliveries={c.count('313131')} consumed_rows={consumed}", "P0")

# ---- R04: raw store.consume hammered from 32 threads -----------------------------------------
st = Store(newdb()); wins = []
def hammer():
    wins.append(st.consume(1, 99))
ths = [threading.Thread(target=hammer) for _ in range(32)]; [t.start() for t in ths]; [t.join() for t in ths]
rec.case("R04", "Store.consume(uv,uid) from 32 threads -> exactly one True", wins.count(True) == 1, f"True={wins.count(True)}", "P0")

# ---- R05: UIDVALIDITY rollover: same uid, new validity => treated as new message ---------------
st = Store(newdb())
a, b2 = st.consume(1, 7), st.consume(2, 7)
rec.case("R05", "consume keys on (uidvalidity,uid): rollover re-delivers uid 7 once", a and b2 and not st.consume(2, 7), f"{a},{b2}", "P2")

# ---- R06: code-arrives-before-register (backlog search) ------------------------------------------
async def r06():
    mb = FakeMbox(); st = Store(newdb()); b = Broker(st, mb); await b.start()
    mb.messages.append(msg(1, "Your verification code is 565656", age_s=30))
    t0 = time.monotonic()
    r = await b.wait(sender_domains=["acme.com"], after=datetime.now(UTC) - timedelta(seconds=60), kind="otp", timeout_s=5)
    return r[1], time.monotonic() - t0
r, dt = asyncio.run(r06())
rec.case("R06", "mail that arrived BEFORE register is found immediately (register-then-search)", r.get("code") == "565656" and dt < 1.5, f"result={r} in {dt:.2f}s", "P0")

# ---- R07: stale-code hazard: old unconsumed mail (4 min old) + after=now -> stale code delivered ------
async def r07():
    mb = FakeMbox(); st = Store(newdb()); b = Broker(st, mb); await b.start()
    mb.messages.append(msg(1, "Your verification code is 111111", age_s=240))
    r = await b.wait(sender_domains=["acme.com"], after=datetime.now(UTC), kind="otp", timeout_s=2)
    return r[1]
r = asyncio.run(r07())
rec.case("R07", "wait(after=now) must NOT return a 4-minute-old OTP (client just requested a fresh one)", r.get("code") != "111111", f"result={r}", "P0",
         "The 5-min CLOCK_SKEW_MARGIN is applied to BOTH the IMAP fetch and _matches, so any unconsumed OTP from the last 5 min is returned instantly -> job agent submits a stale/expired code. Gmail INTERNALDATE is server-assigned (no client skew), so use margin<=10-15s; or let callers pass `after` = their own request time and drop the margin; or track last-delivered per sender")

# ---- R08: newest-first: two mails -> newest wins ---------------------------------------------------------
async def r08():
    mb = FakeMbox(); st = Store(newdb()); b = Broker(st, mb); await b.start()
    mb.messages += [msg(1, "Your verification code is 111111", age_s=50), msg(2, "Your verification code is 222222", age_s=5)]
    return (await b.wait(sender_domains=["acme.com"], after=datetime.now(UTC) - timedelta(seconds=120), kind="otp", timeout_s=2))[1]
r = asyncio.run(r08())
rec.case("R08", "two valid mails -> NEWEST delivered", r.get("code") == "222222", f"result={r}", "P0")

# ---- R09: a conflict message is never consumed -> poisons every later waiter -------------------------------
async def r09():
    mb = FakeMbox(); st = Store(newdb()); b = Broker(st, mb); await b.start()
    mb.messages.append(msg(1, "code 111111 code 222222 code 333333", age_s=5))
    r1 = (await b.wait(sender_domains=["acme.com"], after=datetime.now(UTC) - timedelta(seconds=60), kind="otp", timeout_s=2))[1]
    mb.messages.append(msg(2, "Your verification code is 444444", age_s=1))
    r2 = (await b.wait(sender_domains=["acme.com"], after=datetime.now(UTC) - timedelta(seconds=60), kind="otp", timeout_s=2))[1]
    return r1, r2
r1, r2 = asyncio.run(r09())
rec.case("R09", "conflict is returned once; later waiter still gets the next good mail", r1 == {"error": "conflict"} and r2.get("code") == "444444", f"r1={r1} r2={r2}", "P1",
         "Conflict message is not consumed, so it is re-evaluated (and re-409s) for every future waiter in the window while newer-first ordering saves only the case where a newer good mail exists; consume or blacklist a conflicting message after first report")

# ---- R10: orphan / restart persistence in-process ------------------------------------------------------------
async def r10():
    path = newdb(); mb = FakeMbox(); st = Store(path); b = Broker(st, mb); await b.start()
    t = asyncio.create_task(b.wait(sender_domains=["acme.com"], after=datetime.now(UTC) - timedelta(seconds=1), kind="otp", timeout_s=30))
    await asyncio.sleep(0.3)
    rows = st._db.execute("select count(*) from waiters").fetchone()[0]
    # simulate crash: new Store/Broker on same db (old task abandoned)
    t.cancel()
    st2 = Store(path); st2._db.execute("delete from waiters")  # cancel() ran finally => row already gone; re-add via add_waiter to emulate crash-time row
    from store import Waiter
    w = Waiter("orph-1", ["acme.com"], datetime.now(UTC) - timedelta(seconds=1), "otp", None, ["acme.com"], datetime.now(UTC) + timedelta(seconds=30))
    st2.add_waiter(w)
    mb2 = FakeMbox(); b2 = Broker(st2, mb2); await b2.start()
    cnt_before = b2.active_waiter_count
    mb2.messages.append(msg(1, "Your verification code is 727272"))
    await b2.scan()
    stored = st2._db.execute("select result from waiters where request_id='orph-1'").fetchone()
    # retried identical request picks the stored result up
    retried = await b2.wait(sender_domains=["acme.com"], after=datetime.now(UTC) - timedelta(seconds=1), kind="otp", timeout_s=2)
    return rows, cnt_before, stored, retried[1]
rows, cnt, stored, retried = asyncio.run(r10())
rec.case("R10", "reloaded orphan waiter is evaluated, result persisted, identical retry receives it", rows == 1 and cnt == 1 and stored and retried.get("code") == "727272",
         f"rows_while_waiting={rows} active_after_reload={cnt} stored={bool(stored and stored[0])} retry={retried}", "P1")

# ---- R11: expired orphan inflates active_waiter_count until some scan happens -----------------------------------------
async def r11():
    path = newdb(); st = Store(path); from store import Waiter
    st.add_waiter(Waiter("o", ["acme.com"], datetime.now(UTC), "otp", None, ["acme.com"], datetime.now(UTC) + timedelta(seconds=1)))
    b = Broker(st, FakeMbox()); await b.start()
    c0 = b.active_waiter_count
    await asyncio.sleep(1.5)
    c1 = b.active_waiter_count   # no new mail, no new wait => no scan => never pruned
    return c0, c1
c0, c1 = asyncio.run(r11())
rec.case("R11", "expired orphan waiter disappears from active_waiter_count on its own", c1 == 0, f"count at reload={c0}, 0.5s after expiry={c1}", "P2",
         "Prune expired orphans in active_waiter_count (filter by expires_at) and/or run a periodic sweeper task")

# ---- R12: poison `after` (year 1) pending waiter breaks scans for ALL other waiters ---------------------------------
async def r12():
    mb = FakeMbox(); st = Store(newdb()); b = Broker(st, mb); await b.start()
    mb.messages.append(msg(1, "Your verification code is 919191", age_s=3))
    # emulate main.py's real path: fetch_since(after - 5min) is what the Mailbox does with `after - 1 day` subtraction; reproduce with real helper
    import imap_client
    orig = mb.fetch_since
    def real_like(after):
        imap_client._imap_date(after - timedelta(days=1))   # what Mailbox.fetch_since does first
        return orig(after)
    mb.fetch_since = real_like
    poison = asyncio.create_task(b.wait(sender_domains=["x.invalid"], after=datetime(1, 1, 2, tzinfo=UTC), kind="otp", timeout_s=3))
    await asyncio.sleep(0.3)
    good = await b.wait(sender_domains=["acme.com"], after=datetime.now(UTC) - timedelta(seconds=30), kind="otp", timeout_s=2)
    p = await poison
    return good[1], p[1]
good, p = asyncio.run(r12())
rec.case("R12", "waiter with after=0001-01-02 does not starve other waiters while pending", good.get("code") == "919191", f"legit waiter got {good}; poison={p}", "P1",
         "scan() uses min(after) across ALL pending waiters; one OverflowError/odd date makes fetch_since raise for everyone (swallowed as 'scan failed'). Validate `after` on input (clamp to [now-1h, now+5m]) and evaluate waiters with independent fetch windows")

# ---- R13: hung IMAP fetch -> deadline not enforced for the HTTP caller ---------------------------------------------------------
async def r13():
    mb = FakeMbox(delay=6); st = Store(newdb()); b = Broker(st, mb); await b.start()
    t0 = time.monotonic()
    r = await b.wait(sender_domains=["acme.com"], after=datetime.now(UTC), kind="otp", timeout_s=2)
    return r[1], time.monotonic() - t0
r, dt = asyncio.run(r13())
rec.case("R13", "timeout_s=2 honoured even if IMAP fetch hangs 6s", dt < 3.5, f"returned {r} after {dt:.1f}s (asked 2s)", "P1",
         "Broker.wait awaits scan() OUTSIDE asyncio.wait_for. Wrap the whole register+scan+wait body in asyncio.timeout(timeout_s), and run initial scan as a task so the deadline can fire; also hold _scan_lock only around state changes, not IMAP I/O")

# ---- R14: second waiter blocked behind hung scan beyond ITS timeout -----------------------------------------------------------
async def r14():
    mb = FakeMbox(delay=5); st = Store(newdb()); b = Broker(st, mb); await b.start()
    t0 = time.monotonic()
    a = asyncio.create_task(b.wait(sender_domains=["acme.com"], after=datetime.now(UTC), kind="otp", timeout_s=2))
    await asyncio.sleep(0.2)
    r = await b.wait(sender_domains=["other.com"], after=datetime.now(UTC), kind="otp", timeout_s=1)
    await a
    return time.monotonic() - t0
dt = asyncio.run(r14())
rec.case("R14", "short-timeout waiter is not stuck behind another waiter's slow scan", dt < 3, f"timeout_s=1 waiter finished after {dt:.1f}s", "P1", "Same fix as R13")

# ---- R15: mailbox exception -> clean 408, not 500/hang -----------------------------------------------------------------------------
async def r15():
    mb = FakeMbox(); mb.raise_exc = RuntimeError("boom"); st = Store(newdb()); b = Broker(st, mb); await b.start()
    return (await b.wait(sender_domains=["acme.com"], after=datetime.now(UTC), kind="otp", timeout_s=1))[1]
r = asyncio.run(r15())
rec.case("R15", "fetch_since raising -> waiter times out cleanly", r == {"error": "timeout"}, f"result={r}", "P2",
         "A broken mailbox is reported as 'timeout' (408) which a client cannot tell from 'no mail': return 503 {error: mailbox_unavailable} when scans fail for the entire wait")

# ---- R16: API-level concurrent POSTs via TestClient (full FastAPI stack, fake mailbox) ---------------------------------------------------
mb = FakeMbox()
cfg = Config(user="x@y.z", password_file=__import__("pathlib").Path("/dev/null"), db_path=__import__("pathlib").Path(newdb()), mark_seen=False)
app = create_app(cfg, mailbox=mb)
out = []
with TestClient(app) as cl:
    def post():
        out.append(cl.post("/v1/wait", json=wait_req(domains=["acme.com"], timeout_s=4, after=now_iso(-1))))
    ths = [threading.Thread(target=post) for _ in range(2)]; [t.start() for t in ths]
    time.sleep(1.0)
    mb.messages.append(msg(1, "Your verification code is 606060"))
    app.state.broker.notify_threadsafe()
    [t.join() for t in ths]
sts = sorted(r.status_code for r in out)
rec.case("R16", "FastAPI-level: 2 concurrent POSTs, 1 mail -> one 200 (code) + one 408", sts == [200, 408] and sum(1 for r in out if r.status_code == 200 and r.json().get("code") == "606060") == 1,
         f"statuses={sts} bodies={[r.json() for r in out]}", "P0")
async def r17():
    b = Broker(Store(newdb()), FakeMbox()); await b.start()
    t = asyncio.create_task(b.wait(sender_domains=["q.invalid"], after=datetime.now(UTC), kind="otp", timeout_s=99999))
    await asyncio.sleep(0.2)
    w = list(b._waiters.values())[0]; exp = round((w.expires_at - datetime.now(UTC)).total_seconds())
    t.cancel()
    return exp
clamp = asyncio.run(r17())
rec.case("R17", "timeout_s=99999 clamps server-side to <=300s", 295 <= clamp <= 300, f"expires_in={clamp}s", "P2")

# ---- R20: LIVE restart mid-wait (short waiter => graceful drain; long waiter => SIGKILL after TimeoutStopSec) ----------------
def live_restart(wait_s):
    box = {}
    def client():
        box["r"] = http_call("POST", "/v1/wait", wait_req(domains=["restart-probe.invalid"], timeout_s=wait_s), timeout=wait_s + 60)
    th = threading.Thread(target=client); th.start(); time.sleep(2)
    h_before = health()[0]
    t0 = time.monotonic()
    out = sh("sudo -n systemctl restart otp-broker; echo rc=$?", timeout=wait_s + 120)
    took = round(time.monotonic() - t0, 1)
    up = wait_for(lambda: health()[0].get("uptime_seconds") is not None, 60, 0.25)
    th.join(wait_s + 60)
    log = sh("sudo -n journalctl -u otp-broker --since '-%d sec' --no-pager | grep -iE 'sigkill|timed out|Stopping|Stopped|stop-sigterm|reloaded' | tail -6" % int(took + 60))
    return dict(r=box["r"], took=took, up=up, hb=h_before, ha=health()[0], log=log)

idle = ensure_idle(timeout=400)
if idle is None:
    rec.skip("R20", "live restart mid-wait", "live broker never became idle (real client waiting); not restarting under it")
else:
    for tag, wait_s in (("a", 40), ("b", 150)):
        x = live_restart(wait_s); r = x["r"]
        shape = "short waiter (40s)" if wait_s == 40 else "long waiter (150s > systemd default TimeoutStopSec=90s)"
        rec.case(f"R20{tag}1", f"restart with {shape}: client gets a clean, bounded outcome",
                 r["status"] in (408, 502, 503) or (r["status"] is None and r["elapsed"] < wait_s + 30),
                 f"client status={r['status']} err={r['error']} after {r['elapsed']}s; journal={x['log'][:300]!r}", "P1",
                 "On shutdown resolve in-flight waiters with 503 {error:'restarting'} so clients get an explicit retryable error instead of a reset")
        rec.case(f"R20{tag}2", f"restart with {shape}: `systemctl restart` completes promptly (<15s)", x["took"] < 15,
                 f"restart blocked {x['took']}s; new process answered {x['up']}s later", "P1",
                 "uvicorn drains in-flight /v1/wait on SIGTERM, so every deploy/restart blocks until the longest waiter finishes or systemd SIGKILLs at 90s. Add --timeout-graceful-shutdown 5 and cancel waiters in lifespan shutdown")
        rec.case(f"R20{tag}3", f"waiter recovered from SQLite after restart ({shape})",
                 x["ha"].get("active_waiter_count") == 1 if wait_s == 150 else True,
                 f"active_waiter_count before={x['hb'].get('active_waiter_count')} right after={x['ha'].get('active_waiter_count')}" + ("" if wait_s == 150 else " (waiter finished during graceful drain; nothing to recover)"),
                 "P1", "Orphan reload only works if the old process is killed; clients must retry with identical parameters to pick up a stored result")
        if wait_s == 150 and x["ha"].get("active_waiter_count") == 1:
            exp = wait_for(lambda: health()[0].get("active_waiter_count") == 0, 200, 1.0)
            rec.case("R20b4", "orphan waiter count drains to 0 after its expiry (no new traffic)", exp is not None,
                     f"drained after {exp}s" if exp is not None else "still counted 200s after (no scan triggered)", "P2",
                     "Same as R11: count only unexpired waiters; add a periodic sweeper")
        conn_t = wait_for(lambda: health()[0].get("imap_connected") is True, 30, 0.1)
        rec.case(f"R20{tag}5", "IMAP reconnected after restart", conn_t is not None, f"imap_connected=true {conn_t}s after first poll; {health()[0]}", "P1")
rec.skip("R30", "inject REAL email and race two live waiters", "Would require reading the Gmail app password (jobagent-owned, 600) to send mail, or sending from an external sender; out of scope without explicit credential-use approval. Covered by simulated R01-R03/R16 which exercise the same Store.consume path.")
rec.skip("R31", "live 'mail before register' using a real mailbox message", "Needs a real recent OTP email; the only path to read the mailbox is the broker itself, and a successful match would mark a real message consumed in the production DB. Covered by simulated R06.")
rec.done()
