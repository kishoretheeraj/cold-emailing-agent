"""Area 6: load / leak. Live service, non-matching domains only."""
import socket
import statistics
import threading
import time
import json
from concurrent.futures import ThreadPoolExecutor

from common import BASE_HOST, BASE_PORT, Rec, ensure_idle, health, http_call, now_iso, sh, wait_for, wait_req

rec = Rec("load")
pid = sh("systemctl show otp-broker -p MainPID --value")


def mem():
    return int(sh("systemctl show otp-broker -p MemoryCurrent --value") or 0)


def fds():
    return int(sh(f"sudo -n ls /proc/{pid}/fd | wc -l") or 0)


def threads():
    return int(sh(f"sudo -n sh -c 'ls /proc/{pid}/task | wc -l'") or 0)


if ensure_idle(timeout=330) is None:
    print("live broker busy with a real client; aborting load run"); rec.skip("L00", "load tests", "live broker busy"); rec.done(); raise SystemExit
h0, _ = health(); m0, f0, t0_ = mem(), fds(), threads()

# L01 sequential 50 x 2s
lat, sts = [], []
t_start = time.monotonic()
for i in range(50):
    r = http_call("POST", "/v1/wait", wait_req(domains=[f"seq{i}.invalid"], timeout_s=2), timeout=30)
    lat.append(r["elapsed"]); sts.append(r["status"])
total = round(time.monotonic() - t_start, 1)
h1, _ = health(); m1, f1, th1 = mem(), fds(), threads()
rec.case("L01", "50 sequential waits (2s) all 408", all(s == 408 for s in sts), f"statuses={dict((s, sts.count(s)) for s in set(sts))} total={total}s (ideal 100s) lat min/med/max={min(lat)}/{statistics.median(lat)}/{max(lat)}", "P1")
rec.case("L02", "per-request overhead small (every wait within 2.0-3.0s)", all(1.9 <= x <= 3.0 for x in lat), f"max overhead={max(lat)-2:.2f}s", "P2")
rec.case("L03", "/health sane after sequential run (uptime grew ~ elapsed, connected, 0 waiters)", h1.get("imap_connected") is True and h1.get("active_waiter_count") == 0 and 0 < h1["uptime_seconds"] - h0["uptime_seconds"] < total + 15,
         f"uptime {h0['uptime_seconds']} -> {h1['uptime_seconds']}, waiters={h1['active_waiter_count']}, connected={h1['imap_connected']}", "P1")
rec.case("L04", "no memory / fd / thread growth after 50 sequential requests", (m1 - m0) < 8 * 1024 * 1024 and f1 - f0 <= 2 and th1 - t0_ <= 2,
         f"MemoryCurrent {m0/1e6:.1f}MB -> {m1/1e6:.1f}MB, fds {f0}->{f1}, threads {t0_}->{th1}", "P1", "Investigate leak (cache bounded at 300 msgs, MAX_FETCH=50)")

# L05 20 rapid register/abandon
def abandon(i):
    s = socket.create_connection((BASE_HOST, BASE_PORT), timeout=5)
    body = json.dumps(wait_req(domains=[f"abandon{i}.invalid"], timeout_s=30)).encode()
    s.sendall(b"POST /v1/wait HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    time.sleep(0.15)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")  # RST on close
    s.close()
t_ab = time.monotonic()
with ThreadPoolExecutor(20) as ex:
    list(ex.map(abandon, range(20)))
time.sleep(1)
cnt_after = health()[0].get("active_waiter_count")
drain = wait_for(lambda: health()[0].get("active_waiter_count") == 0, 60, 0.5)
took = round(time.monotonic() - t_ab, 1)
rec.case("L05", "20 register+abandon cycles: waiters released promptly after client RST", cnt_after == 0 or (drain is not None and took < 8),
         f"active_waiter_count 1s after abandon={cnt_after}; fully drained {took}s after start (timeout_s was 30)", "P1",
         "Detect client disconnect (await request.is_disconnected() in a watcher task, or anyio cancel scope) and deregister immediately. Leaked waiters keep scanning, can CONSUME OTP emails meant for the retry, and inflate IMAP SEARCH load")
rec.case("L06", "count returns to 0 eventually (no permanent leak)", drain is not None and health()[0].get("active_waiter_count") == 0, f"drained={drain}s", "P0")
m2, f2, th2 = mem(), fds(), threads()
rec.case("L07", "no fd/thread leak after abandoned connections", f2 - f0 <= 2 and th2 - t0_ <= 2, f"fds {f0}->{f2}, threads {t0_}->{th2}, mem {m2/1e6:.1f}MB", "P1")
rows = sh("sudo -n python3 -c \"import sqlite3;print(sqlite3.connect('file:/opt/otp-broker/otp-broker.db?mode=ro',uri=True).execute('select count(*) from waiters').fetchone())\"")
rec.case("L08", "no stale rows left in SQLite waiters table", rows.strip() in ("(0,)",), f"waiters rows={rows}", "P1")

# L09 30 concurrent
def one(i):
    return http_call("POST", "/v1/wait", wait_req(domains=[f"conc{i}.invalid"], timeout_s=5), timeout=60)
t_c = time.monotonic()
with ThreadPoolExecutor(30) as ex:
    rs = list(ex.map(one, range(30)))
wall = round(time.monotonic() - t_c, 1)
sts = [r["status"] for r in rs]
rec.case("L09", "30 concurrent waits: all 408, no 500, none dropped", all(s == 408 for s in sts), f"statuses={dict((s, sts.count(s)) for s in set(sts))} wall={wall}s max_elapsed={max(r['elapsed'] for r in rs)}s errors={[r['error'] for r in rs if r['error']][:3]}", "P1")
rec.case("L10", "30 concurrent finish near their 5s deadline (<=10s)", wall <= 10, f"wall={wall}s", "P2", "Deadline not enforced around initial scan(); scans serialize on _scan_lock with one IMAP SEARCH each")
h3, _ = health()
m3, f3, th3 = mem(), fds(), threads()
settle = wait_for(lambda: health()[0].get("active_waiter_count") == 0, 15, 0.5)
rec.case("L11", "after burst: waiters 0, IMAP still connected, no growth", settle is not None and h3.get("imap_connected") is True and (m3 - m0) < 16 * 1024 * 1024 and f3 - f0 <= 2,
         f"waiters settled={settle}s connected={h3.get('imap_connected')} mem {m0/1e6:.1f}->{m3/1e6:.1f}MB fds {f0}->{f3} threads {t0_}->{th3}", "P1")
# L14 scaling: where does deadline overshoot start?
scal = {}
for n in (5, 10, 20, 30):
    t = time.monotonic()
    with ThreadPoolExecutor(n) as ex:
        rr = list(ex.map(lambda i: http_call("POST", "/v1/wait", wait_req(domains=[f"sc{n}x{i}.invalid"], timeout_s=5), timeout=90), range(n)))
    scal[n] = (round(time.monotonic() - t, 1), round(max(r["elapsed"] for r in rr), 1), sum(1 for r in rr if r["status"] != 408))
    wait_for(lambda: health()[0].get("active_waiter_count") == 0, 60, 0.5)
rec.case("L14", "deadline honoured as concurrency grows (timeout_s=5): wall/max-elapsed/non-408 per N", all(v[1] <= 8 and v[2] == 0 for v in scal.values()),
         f"N -> (wall_s, max_elapsed_s, non408): {scal}", "P1",
         "Each wait() runs its own scan() under one asyncio lock holding a blocking IMAP round-trip (END IDLE + SEARCH + re-IDLE ~0.9s). Coalesce: one scanner task + per-waiter futures; run waiters' deadlines independently of scan; skip SEARCH when UIDNEXT unchanged")
# L12 amplification note
j = sh("sudo -n journalctl -u otp-broker --since '-20 min' --no-pager | grep -cE 'imap error|scan failed|IDLE'")
rec.info("L12", "journal noise during load (imap error / scan failed / IDLE lines in last 20 min)", f"{j} lines", "P2")
rec.info("L13", "Gmail-side amplification: every /v1/wait registration triggers a full IMAP SEARCH (+ DONE/IDLE cycle) with no rate limit", f"{50+20+30} waits issued above => ~{50+20+30}+ SEARCH commands to Gmail in a few minutes", "P2", "Coalesce scans (min interval 1-2s), serve cache hits without touching IMAP when nothing new (use EXISTS/UIDNEXT from IDLE), rate-limit /v1/wait per client")
rec.done()
