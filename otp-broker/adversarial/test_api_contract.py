"""Area 1: API contract. Live HTTP against 127.0.0.1:8765. Non-matching domains only (never consumes mail)."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from common import Rec, ensure_idle, health, http_call, now_iso, wait_for, wait_req

rec = Rec("api_contract")
if ensure_idle(timeout=120) is None:
    print("WARNING: live broker has active waiters from another client; counts may be off")

W = "/v1/wait"


def post(body=None, **kw):
    return http_call("POST", W, body, **kw)


def expect(cid, name, resp, ok_statuses, sev="P2", fix="", note=""):
    ok = resp["status"] in ok_statuses
    obs = f"status={resp['status']} t={resp['elapsed']}s body={(resp['text'] or resp['error'] or '')[:140]!r} {note}"
    rec.case(cid, name, ok, obs, sev, fix)


base = wait_req()

# ---- request-validation matrix ------------------------------------------------
b = dict(base); del b["sender_domains"]
expect("A01", "missing sender_domains -> 422", post(b), {422})
expect("A02", "empty sender_domains [] -> 422", post(dict(base, sender_domains=[])), {422})
expect("A03", "sender_domains as bare string -> 422", post(dict(base, sender_domains="x.com")), {422})
expect("A04", "sender_domains [123] -> 422", post(dict(base, sender_domains=[123])), {422})
expect("A05", "sender_domains [''] (empty-string domain) rejected", post(dict(base, sender_domains=[""])), {422},
       "P2", "Validate each domain: non-empty, hostname charset, no whitespace; reject '', '*', '.'")
expect("A05b", "sender_domains ['*'] / ['.'] rejected", post(dict(base, sender_domains=["*", "."])), {422},
       "P2", "Same domain validator; wildcard-only entries should be 422")
expect("A06", "timeout_s='abc' -> 422", post(dict(base, timeout_s="abc")), {422})
expect("A07", "timeout_s='1' (numeric string) -> 422 (strict typing)", post(dict(base, timeout_s="1")), {422},
       "P2", "Use pydantic StrictInt (or model_config strict=True) so string numerics are rejected")
expect("A08", "timeout_s=1.5 -> 422", post(dict(base, timeout_s=1.5)), {422})
expect("A09", "timeout_s=-5 -> 422", post(dict(base, timeout_s=-5)), {422})
expect("A10", "timeout_s=0 -> 422", post(dict(base, timeout_s=0)), {422})
b = dict(base); del b["timeout_s"]
expect("A11", "timeout_s missing -> 422", post(b), {422}, note="(spec: required)")
expect("A12", "timeout_s=null -> 422", post(dict(base, timeout_s=None)), {422})
expect("A13", "kind='sms' -> 422", post(dict(base, kind="sms")), {422})
b = dict(base); del b["kind"]
expect("A14", "kind missing -> 422", post(b), {422})
expect("A15", "after='not-a-date' -> 422", post(dict(base, after="not-a-date")), {422})
b = dict(base); del b["after"]
expect("A16", "after missing -> 422", post(b), {422})
expect("A17", "after naive timestamp (no tz) accepted as UTC", post(dict(base, after=datetime.utcnow().isoformat())), {408})
expect("A18", "after=0001-01-01T00:00:00Z does not 500", post(dict(base, after="0001-01-01T00:00:00Z")), {408, 422},
       "P1", "Clamp/validate `after` to a sane window (e.g. now-24h .. now+1h) in WaitRequest; OverflowError escapes Broker.scan() as HTTP 500")
expect("A19", "after=0001-01-02T00:00:00Z does not 500", post(dict(base, after="0001-01-02T00:00:00Z")), {408, 422},
       "P1", "Same: validate `after` range")
expect("A20", "after=9999-12-31T23:59:59Z does not 500", post(dict(base, after="9999-12-31T23:59:59Z")), {408, 422},
       "P1", "Same: validate `after` range")
expect("A21", "after as unix int 1700000000 not 500", post(dict(base, after=1700000000)), {408, 422}, "P2")
expect("A22", "unknown extra fields rejected (typo protection)", post(dict(base, bogus=1, sender_domain=["x.com"], allowed_link_host=["evil.com"])),
       {422}, "P2", "model_config = ConfigDict(extra='forbid'); typos like allowed_link_host silently fall back to defaults")
expect("A23", "non-JSON body -> 422", post(raw=b"this is not json", headers={"Content-Type": "application/json"}), {400, 422})
expect("A24", "JSON array body -> 422", post(raw=b"[1,2,3]", headers={"Content-Type": "application/json"}), {422})
expect("A25", "empty body -> 422", post(raw=b"", headers={"Content-Type": "application/json"}), {422})
expect("A26", "valid JSON with Content-Type: text/plain -> rejected", post(raw=json.dumps(base).encode(), headers={"Content-Type": "text/plain"}), {415, 422},
       "P2", "Upgrade FastAPI (strict_content_type) or check Content-Type; text/plain POSTs are CSRF-able from a browser on the same host")
expect("A27", "GET /v1/wait -> 405", http_call("GET", W), {405})
expect("A28", "POST /health -> 405", http_call("POST", "/health", {}), {405})
expect("A29", "unknown path -> 404", http_call("GET", "/nope"), {404})
expect("A30", "allowed_link_hosts wrong type -> 422", post(dict(base, allowed_link_hosts="x.com")), {422})
expect("A31", "1 MB hint string handled (no 500/hang)", post(dict(base, hint="A" * 1_000_000)), {408, 413, 422},
       "P2", "Cap field sizes: hint<=256, <=20 domains of <=253 chars; add body size limit at the proxy/uvicorn")
r = post(dict(base, sender_domains=[f"d{i}.example.invalid" for i in range(5000)]))
expect("A32", "5000 sender_domains handled (no 500/hang)", r, {408, 413, 422}, "P2", "Cap sender_domains length (e.g. <=20)")
expect("A33", "kind=link with allowed_link_hosts [] handled", post(dict(base, kind="link", allowed_link_hosts=[])), {408, 422}, "P2")
r = http_call("GET", "/docs"); r2 = http_call("GET", "/openapi.json")
rec.case("A34", "/docs and /openapi.json not exposed", r["status"] == 404 and r2["status"] == 404,
         f"/docs={r['status']} /openapi.json={r2['status']}", "P2",
         "FastAPI(docs_url=None, redoc_url=None, openapi_url=None) in production")

# ---- /health ------------------------------------------------------------------
h, hr = health()
want = {"imap_connected": bool, "last_successful_poll": (str, type(None)), "active_waiter_count": int,
        "degraded": bool, "uptime_seconds": (int, float), "last_auth_failure": (str, type(None))}
rec.case("H01", "/health has exactly the 6 spec keys", set(h) == set(want), f"keys={sorted(h)}", "P2",
         "Match BUILD_BRIEF schema exactly")
bad = [k for k, t in want.items() if k in h and not isinstance(h[k], t)]
rec.case("H02", "/health value types correct", not bad and hr["status"] == 200, f"bad_types={bad} status={hr['status']} body={h}", "P2")
try:
    ts = datetime.fromisoformat(h["last_successful_poll"]); parse_ok = True
except Exception:
    parse_ok = False
rec.case("H03", "last_successful_poll is parseable ISO-8601", parse_ok, f"value={h.get('last_successful_poll')}", "P2")
if parse_ok:
    age = (datetime.now(ts.tzinfo) - ts).total_seconds()
    rec.case("H04", "last_successful_poll fresh while imap_connected (usable as liveness signal)",
             age < 120 or not h["imap_connected"], f"age={age:.0f}s uptime={h['uptime_seconds']}s connected={h['imap_connected']} degraded={h['degraded']}",
             "P2", "In IDLE mode last_successful_poll only advances on EXISTS/fetch, so it goes arbitrarily stale on a healthy idle box. Stamp it on every IDLE keepalive/re-issue (or NOOP every few min) so staleness means 'broken'")
u1 = h["uptime_seconds"]; time.sleep(2); u2 = health()[0]["uptime_seconds"]
rec.case("H05", "uptime_seconds monotonic", 1.5 <= u2 - u1 <= 3.5, f"{u1} -> {u2}", "P2")

# ---- concurrency: 10 concurrent short waits ---------------------------------------
def one(i):
    return post(wait_req(domains=[f"nomatch{i}.invalid"], timeout_s=5), timeout=40)

t0 = time.monotonic()
with ThreadPoolExecutor(10) as ex:
    rs = list(ex.map(one, range(10)))
tot = time.monotonic() - t0
sts = [r["status"] for r in rs]
mx = max(r["elapsed"] for r in rs)
rec.case("C01", "10 concurrent waits (5s) all return 408, none 500/hang",
         all(s == 408 for s in sts) and mx < 15, f"statuses={sts} max_elapsed={mx}s wall={tot:.1f}s", "P1",
         "Investigate scan serialization: each wait triggers a full IMAP SEARCH under one scan lock")
rec.case("C02", "each 408 arrives close to requested timeout (5s +/- 3)", all(4.5 <= r["elapsed"] <= 8 for r in rs),
         f"elapsed={[r['elapsed'] for r in rs]}", "P2",
         "Enforce the deadline around the initial scan() too (it currently sits outside asyncio.wait_for)")
d = ensure_idle(timeout=10)
rec.case("C03", "active_waiter_count returns to 0 after timeouts", d is not None, f"settled_after={d}s count={health()[0].get('active_waiter_count')}", "P1",
         "Always deregister in finally (it does) and make count reflect only live waiters")

# ---- timeout clamp: 301 / 99999 (run last: these linger ~300s) ---------------------------
res = {}
def long_req(name, ts):
    res[name] = post(wait_req(timeout_s=ts), timeout=8)
t_start = time.monotonic()
th = [threading.Thread(target=long_req, args=(n, v)) for n, v in (("t301", 301), ("t99999", 99999))]
[t.start() for t in th]; [t.join() for t in th]
for n, cid in (("t301", "A35"), ("t99999", "A36")):
    r = res[n]
    accepted = r["status"] is None and r["error"] and "timed out" in r["error"]  # still waiting at 8s => not 422, not early 408
    rec.case(cid, f"timeout_s={n[1:]} accepted (silently clamped) rather than rejected",
             r["status"] == 422, f"status={r['status']} error={r['error']} -> server clamped silently and kept waiting" if accepted else f"status={r['status']} {r['text'][:100]}",
             "P2", "Spec says 'int<=300': reject >300 with 422 (le=300) instead of silently clamping, so clients learn their assumption is wrong")
h1 = health()[0]["active_waiter_count"]
rec.case("A37", "abandoned clients' waiters visible (2) right after disconnect", h1 == 2, f"active_waiter_count={h1}", "P1",
         "n/a (observation: server did not notice the client went away)", ) if False else None
drain = wait_for(lambda: health()[0]["active_waiter_count"] == 0, 330, 2.0)
total = round(time.monotonic() - t_start, 1)
rec.case("A38", "abandoned long waits (client gone after 8s) are released promptly", drain is not None and total < 30,
         f"count after disconnect={h1}; drained to 0 after {total}s since request start (=> timeout_s clamp {'= 300s confirmed' if drain and 290 < total < 320 else 'unclear'})",
         "P1", "Poll request.is_disconnected() (or use a Starlette background disconnect watcher) in the wait loop and cancel + deregister; otherwise an abandoned waiter keeps consuming OTP emails for ~5 min and the real retry gets nothing")
rec.done()
