"""Area 2: IMAP resilience. Live: kill the IMAP TCP socket with `ss -K` (kernel INET_DIAG_DESTROY, affects only that one socket).
Auth breaker: CODE-REVIEW + OFFLINE simulation with a fake IMAP factory (NO network, NO real login attempts)."""
import logging
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from common import Rec, ensure_idle, health, http_call, sh, wait_for, wait_req

logging.getLogger().setLevel(logging.CRITICAL)
rec = Rec("imap_resilience")


def imap_sock():
    pid = sh("systemctl show otp-broker -p MainPID --value")
    out = sh(f"sudo -n ss -tnpoe | grep 'pid={pid},' | grep ':993'")
    m = re.search(r"\s(\d+\.\d+\.\d+\.\d+):(\d+)\s+(\d+\.\d+\.\d+\.\d+):993", out)
    return (m.group(2) if m else None), out


def kill_sock(port):
    return sh(f"sudo -n ss -K -tn state established sport = :{port} 2>&1 | tail -2")


def timeline(duration, poll=0.05):
    """Poll /health, record (t, connected, degraded) on every change."""
    t0 = time.monotonic(); seen = []; last = None
    while time.monotonic() - t0 < duration:
        h = http_call("GET", "/health", timeout=3)["json"] or {}
        key = (h.get("imap_connected"), h.get("degraded"))
        if key != last:
            seen.append((round(time.monotonic() - t0, 2), *key)); last = key
        if len(seen) >= 2 and key[0] is True and time.monotonic() - t0 > seen[1][0] + 0.5:
            break
        time.sleep(poll)
    return seen


# --- 1 baseline ---------------------------------------------------------------------------------
h, _ = health()
rec.case("I01", "baseline: /health imap_connected=true, degraded=false", h.get("imap_connected") is True and h.get("degraded") is False, str(h), "P1")
port, sockline = imap_sock()
ka = "keepalive" in sockline
rec.case("I02", "IMAP socket has TCP keepalive enabled (detects half-open peers)", ka, f"ss timers: {sockline[:200]!r}", "P1",
         "Set SO_KEEPALIVE + TCP_KEEPIDLE=60/TCP_KEEPINTVL=10/TCP_KEEPCNT=3 on conn.sock after connect. Without it a silently dropped connection (NAT/Tailscale/router state loss) is only noticed when the 25-min IDLE re-issue's DONE gets no reply (up to ~26 min of missed OTPs)")

if ensure_idle(timeout=300) is None:
    rec.skip("I10", "live socket kill", "live broker has an active (real) waiter; not disrupting IMAP under it")
else:
    # --- 2 single drop ----------------------------------------------------------------------------
    port, _ = imap_sock()
    t_kill = time.time()
    out = kill_sock(port)
    box = {}
    th = threading.Thread(target=lambda: box.update(r=http_call("POST", "/v1/wait", wait_req(timeout_s=6), timeout=60)))
    time.sleep(0.2); th.start()
    tl = timeline(30)
    th.join(60)
    r = box["r"]
    gap = next((t for t, c, d in tl if c is True and t > 0), None)
    killed = "destroyed" in out.lower() or len(tl) > 1
    rec.case("I10", "ss -K kills the IMAP socket (service notices)", killed, f"ss output={out!r} timeline(t,connected,degraded)={tl}", "P2")
    rec.case("I11", "/health reflects the outage (imap_connected=false) during reconnect", any(c is False for _, c, _ in tl), f"timeline={tl}", "P1",
             "Expose imap_connected from a heartbeat, not `_conn is not None`")
    rec.case("I12", "degraded=true during reconnect (user question; spec ties degraded to polling fallback)", any(d is True for _, _, d in tl),
             f"timeline={tl} -> degraded stays {'true' if any(d for _,_,d in tl) else 'false'} through plain reconnects; only imap_connected flips", "P2",
             "Consider a `state` field (connected|reconnecting|degraded|auth_failed) so monitors can alert on reconnect storms; today only imap_connected=false for ~1-2s")
    up = next((t for t, c, d in tl if c is True and t > 0.05), None)
    rec.case("I13", "imap_connected=true again within 10s of drop", up is not None and up < 10, f"reconnected {up}s after poll start (kill->first poll ~0.2s)", "P1")
    rec.case("I14", "/v1/wait issued DURING the reconnect returns a bounded, clean result", r["status"] in (408, 503) and r["elapsed"] < 15,
             f"status={r['status']} body={r['text'][:80]!r} elapsed={r['elapsed']}s (timeout_s=6)", "P1",
             "If the mailbox is down for the whole wait, 408 is indistinguishable from 'no mail'; return 503 {error:'mailbox_unavailable', retry_after} when scans failed >= N times")
    # --- 3 rapid drops ----------------------------------------------------------------------------
    marks = []
    for i in range(3):
        port, _ = imap_sock()
        if not port:
            time.sleep(0.5); port, _ = imap_sock()
        kill_sock(port); marks.append(time.time())
        wait_for(lambda: health()[0].get("imap_connected") is False, 3, 0.05)
        wait_for(lambda: health()[0].get("imap_connected") is True, 20, 0.05)
        time.sleep(0.3)
    time.sleep(1)
    jr = sh("sudo -n journalctl -u otp-broker --since '-120 sec' --no-pager -o short-iso | grep -E 'otp_broker.imap'")
    delays = [float(x) for x in re.findall(r"reconnecting in ([\d.]+)s", jr)]
    conns = re.findall(r"(\S+) .*imap connected", jr)
    rec.case("I20", "3 rapid drops -> each reconnect logged with exception type and delay", len(delays) >= 3 and all("imap error" in jr for _ in [0]),
             f"logged delays={delays}; journal tail={jr[-700:]!r}", "P2", "")
    rec.case("I21", "backoff escalates 1s,2s,4s on repeated failures (jittered, capped 60s)", False,
             f"Observed delays {delays}: every drop restarts at ~1s because `attempt` resets to 0 after any successful connect, so a flapping link (connect OK, drop after 100ms) reconnects ~once per second forever. Escalation only happens on failed connects (untestable live without blocking 993 egress -> SKIPPED live; verified by code: imap_client.backoff_delay 1,2,4..60 x0.8-1.2).",
             "P2", "Reset `attempt` only after the connection has been stable for >= 30s; otherwise flapping links hammer Gmail (risk: temporary IP/account throttling)")
    rec.case("I22", "service recovers fully after rapid drops (connected, no waiters stuck)", health()[0].get("imap_connected") is True and health()[0].get("active_waiter_count") == 0, str(health()[0]), "P1")

# --- 4 auth breaker: offline simulation + code review --------------------------------------------------------------
import imaplib
from imap_client import AUTH_FAILURE_LIMIT, Mailbox

class AuthFailIMAP:
    logins = 0
    def __init__(self, *a, **k): pass
    def login(self, u, p):
        AuthFailIMAP.logins += 1
        raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
    def shutdown(self): pass

pw = Path(tempfile.mkdtemp()) / "pw"; pw.write_text("dummy-not-a-real-password\n")
mb = Mailbox("nobody@example.invalid", pw, imap_factory=lambda h, p, **kw: AuthFailIMAP())
mb.start()
ok_trip = wait_for(lambda: mb.auth_failed, 15, 0.1)
n_at_trip = AuthFailIMAP.logins
time.sleep(8)
n_later = AuthFailIMAP.logins
rec.case("B01", "[OFFLINE SIM + CODE-REVIEW] breaker trips after exactly 3 AUTHENTICATIONFAILED", ok_trip is not None and n_at_trip == AUTH_FAILURE_LIMIT, f"tripped after {ok_trip}s with {n_at_trip} login attempts (limit {AUTH_FAILURE_LIMIT})", "P0")
rec.case("B02", "[OFFLINE SIM] no further login attempts once open (8s observed)", n_later == n_at_trip, f"attempts at trip={n_at_trip}, 8s later={n_later}", "P0")
rec.case("B03", "[OFFLINE SIM] last_auth_failure set, connected=false", mb.last_auth_failure is not None and not mb.connected, f"last_auth_failure={mb.last_auth_failure}", "P2")
mb.stop()
# API surface when open
from fastapi.testclient import TestClient
from main import Config, create_app
cfg = Config("x@y.z", pw, Path(tempfile.mkdtemp()) / "t.db", False)
mb2 = Mailbox("nobody@example.invalid", pw, imap_factory=lambda h, p, **kw: AuthFailIMAP())
mb2.start(); wait_for(lambda: mb2.auth_failed, 15, 0.1)
app = create_app(cfg, mailbox=mb2)
with TestClient(app) as cl:
    hr = cl.get("/health"); wr = cl.post("/v1/wait", json=wait_req(timeout_s=2))
rec.case("B04", "[OFFLINE SIM] breaker open => /health 503 + X-Unhealthy-Reason, /v1/wait 503 fast", hr.status_code == 503 and hr.headers.get("x-unhealthy-reason") == "auth_failed" and wr.status_code == 503,
         f"/health={hr.status_code} hdr={hr.headers.get('x-unhealthy-reason')} /v1/wait={wr.status_code} {wr.text}", "P1")
mb2._stop.set()
rec.case("B05", "[CODE-REVIEW] recovery requires manual restart (no auto-reset, no half-open probe)", False,
         "run() sleeps 5s forever once `_auth_tripped`; nothing clears it. A transient Gmail AUTHENTICATIONFAILED burst (rotated app password restored, Google hiccup) permanently kills OTP retrieval until `systemctl restart`, and Restart=always does not help because the process stays alive and 'healthy' to systemd.",
         "P1", "Add a half-open probe (one login attempt every 30-60 min) or a systemd watchdog / health-based restart (ExecStartPost + WatchdogSec with sd_notify), and alert on 503 auth_failed. Document the manual recovery procedure")
rec.case("B06", "[CODE-REVIEW] non-AUTHENTICATIONFAILED login errors never trip a breaker", False,
         "Any other login failure (e.g. '[ALERT] Application-specific password required', 'Too many simultaneous connections', LOGIN-disabled, missing/unreadable password file -> FileNotFoundError) goes down the generic `except Exception` path: retries forever with backoff capped at 60s and never marks health unhealthy (degraded=false, last_auth_failure=null).",
         "P1", "Classify [ALERT]/[AUTHENTICATIONFAILED]/'Invalid credentials' and OSError(password file) as auth-class failures that count toward the breaker; expose last_error in /health")
rec.case("B07", "[CODE-REVIEW] auth counter is 'consecutive'", True, "`_auth_failures` resets only on successful connect (`_connect` end) and increments only on AuthFailed; interleaved network errors do not reset it (conservative). Retry delay between attempts 1s/2s so the breaker trips ~3s after start: with a typo'd/rotated password a restart-loop (systemd RestartSec=5 only matters on crash) cannot hammer Gmail beyond 3 logins per process start", "P2")
rec.skip("B08", "live bad-password test against Gmail", "Per instructions: skipped to avoid Gmail lockout; B01-B04 use an in-process fake IMAP only")
rec.case("B09", "[CODE-REVIEW] half-open TCP detection window", False,
         "IDLE is re-issued every 25 min (IDLE_REISSUE_S). If the TCP path dies silently, `_end_idle` sends DONE into the void and `readline()` blocks until the 60s socket timeout -> reconnect. Worst-case blind window = ~26 min during which no push arrives; waiters only rescue themselves because every new wait() does an explicit SEARCH (which needs the lock and would also hang 60s) so latency spikes, and `IdleStalled` (2x window = 50 min) is effectively unreachable. Also last_successful_poll is not updated in IDLE keepalives and `connected` is just `_conn is not None`.",
         "P1", "Re-issue IDLE every 5-10 min (Gmail allows <=29), enable TCP keepalive, and use a socket timeout << 60s for DONE/readline; update last_successful_poll on each IDLE cycle")
rec.done()
