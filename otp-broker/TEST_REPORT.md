# OTP Broker — Adversarial Test Report

**Date:** 2026-10-12  |  **Target:** `otp-broker` on the Beelink (`127.0.0.1:8765`, deployed files byte-identical to `~/workspace/otp-broker/*.py` by SHA-256)  |  **Scope:** find gaps, no source changes.

## How this was run

- Scripts live in `~/workspace/otp-broker/adversarial/` (one per area, plus `common.py`, `run_remote.sh`, `bx.sh`, `make_report.py`); `run_remote.sh <script>` tars the scripts + source to the Beelink and runs them there (`~/otp-adv/`, scratch venv with fastapi/bs4/httpx; nothing installed system-wide, no repo or `/opt/otp-broker` file modified). Raw per-case JSON is in `adversarial/results/`.
- **Live** = real HTTP against the running service (non-matching `*.invalid` sender domains only, so no real mail could be consumed or delivered). **In-process** = the repo's `Broker`/`Store`/`extract`/`Mailbox` imported with a fake mailbox/fake IMAP and a temp SQLite file — used wherever a real email would have to be injected, because that needs the Gmail app password (not read, not used).
- **Disruptive actions actually performed on the live service:** 2× `systemctl restart otp-broker` (R20; gated on zero active waiters; the second one hit systemd's 90 s SIGKILL), 4× `sudo ss -K` on the single IMAP socket (I10/I20). A real client (`jobicy.com`, 300 s waiter) was seen in the journal earlier in the evening; tests waited for `active_waiter_count==0` before anything disruptive. No bad-password attempt was made against Gmail.
- Severity: **P0** security / data loss / wrong code; **P1** reliability / crash / hang / silent failure; **P2** minor / spec deviation. Severity is shown for FAIL (and INFO) rows. `CODE-REVIEW` = conclusion drawn from reading `imap_client.py`, not from an executed failure.
- Test-methodology notes: `S06` first produced false positives (PIDs in `Started server process [184584]`, `Killing process 186153`); the scan now strips PIDs/timestamps/UUIDs/Message-IDs/ip:port and reports 0 hits. `R20a5` is a test timing artifact (see row).

## Summary

| Area | PASS | FAIL | SKIP | INFO | FAIL P0 | FAIL P1 | FAIL P2 |
|---|---|---|---|---|---|---|---|
| 1. API contract (test_api_contract.py) | 37 | 9 | 0 | 0 | 0 | 2 | 7 |
| 2. IMAP resilience (test_imap_resilience.py) | 12 | 6 | 1 | 0 | 0 | 4 | 2 |
| 3. Extraction adversarial (test_extraction_adversarial.py) | 67 | 25 | 0 | 1 | 5 | 16 | 4 |
| 4. Races / atomic consume / restart (test_races.py) | 18 | 7 | 2 | 1 | 1 | 5 | 1 |
| 5. Security, read-only (test_security.py) | 15 | 4 | 0 | 4 | 0 | 2 | 2 |
| 6. Load / leak (test_load.py) | 8 | 4 | 0 | 2 | 0 | 2 | 2 |
| **Total** | **157** | **55** | **3** | **8** | **6** | **31** | **18** |

What held up well: all validation of `timeout_s`≤0 / wrong types / bad `kind` / missing fields returns 422; `/health` schema and types are exact; atomic consume is solid (R01–R05, R16: one winner under 10–32 concurrent claimants, 50 notify threads, FastAPI-level race); register-then-search finds mail that arrived before registering (R06); newest-first ordering (R08); orphan reload from SQLite (R10); IMAP drop recovery in ~1.4 s with correct re-SELECT (I10–I22); the 3-strike auth breaker trips at exactly 3 and stops (B01–B04, offline sim); bound to loopback only, password file and DB are 600, service runs as `jobagent` with no effective caps, no OTP-like values in the journal (S01–S10).

## Top 5 gaps

1. **Stale/wrong OTP delivery paths (P0).** `R07`: `wait(after=now)` instantly returns an *unconsumed 4-minute-old* code because the 5-minute `CLOCK_SKEW_MARGIN` is applied to both the IMAP fetch and `_matches` (Gmail INTERNALDATE is server-side, so no skew margin is needed). Any retry/resend flow submits an expired code. Compounding it, `A38`/`L05`: a client that disconnects is never noticed — its waiter lives for the full `timeout_s` (up to 300 s, measured 300.3 s) and can **consume the OTP meant for the retry**. Fix: margin ≤15 s, cancel on disconnect, and consume only if the response is actually written.
2. **Extraction is wrong on both sides (P0/P1).** False positives: `E10` "zip code 90210" → returns `90210`; `E11` "promo code 2024"; `E30` "verification 10-15 minutes" → `1015`; `E21` hidden `display:none` decoy wins. False negatives that make the broker useless for common senders: code-before-keyword (`S10` Google, `S11` Microsoft, `E12`, `E13`), "one-time password" (`E14`), alphanumeric Greenhouse codes (`S03`/`E16`), `&nbsp;`/zero-width in codes (`E07d/e`, `E19`), `**482913**` / `[482913]` (`E33/E34`), digit-per-`<td>` codes (`E15b`). Non-ASCII digits are accepted (`E17/E18`).
3. **Deadlines are not enforced; concurrency collapses (P1).** `Broker.wait` awaits `scan()` *outside* `asyncio.wait_for`, and every wait runs its own serialized IMAP round-trip under one lock. Measured: 30 concurrent `timeout_s=5` waits all returned 408 only after **30–32 s** (`L09/L10/L14`), 10 concurrent after 14.6 s on the second run; simulated hung IMAP (6 s) turned `timeout_s=2` into 6 s and a `timeout_s=1` waiter into 10 s (`R13/R14`). A real IMAP stall means unbounded caller hangs. Also one hostile `after` (`0001-01-01` → HTTP 500 `A18`; `0001-01-02` makes `scan()` raise for *every* pending waiter, `R12`).
4. **Restart/deploy behaviour (P1).** uvicorn drains in-flight `/v1/wait` calls on SIGTERM, so `systemctl restart` blocked **38 s** with a 40 s waiter and **90 s then SIGKILL** with a 150 s waiter (`R20a2/b2`); the SIGKILLed client got `RemoteDisconnected` after 92 s (`R20b1`). The orphan-reload path itself works (`R10`, `R20b3`: row recovered, count drained after expiry) but only helps a client that retries with identical parameters. Add `--timeout-graceful-shutdown`, `TimeoutStopSec`, and cancel waiters with an explicit 503 on shutdown.
5. **Trust boundaries (P0/P1).** `L05` (extraction): `https://evil.com\@verify.example.com/verify?token=1` passes the host allowlist (Python sees host `verify.example.com`; browsers treat `\` as `/` and go to `evil.com`) — the returned URL is meant to be opened by a browser agent. `S15`: sender is trusted from the `From:` header alone (no DKIM/DMARC `Authentication-Results` check). `S13`: the API has no authentication — any local user/container can request OTPs and magic links for any domain. Plus `S12/S11`: no `MemoryMax`, full capability bounding set, systemd exposure score 8.3 EXPOSED.

## Other notable findings (not in the top 5)

- `A35/A36`: `timeout_s` 301/99999 is silently clamped to 300 (confirmed: abandoned 99999 s wait drained at 300.3 s) instead of rejected.
- `A22`, `A05/A05b`, `A07`: lenient input (unknown fields ignored — typos such as `allowed_link_host` silently fall back to defaults; `""`/`"*"` domains and `"1"` as timeout accepted). `A34/S14`: `/docs` and `/openapi.json` exposed.
- `I02/B09` (CODE-REVIEW): no TCP keepalive; IDLE re-issued only every 25 min, so a silently dead connection can go unnoticed for up to ~26 min; `last_successful_poll` is not refreshed by IDLE cycles (I observed it fresh only because tests had just triggered fetches). `B05/B06`: tripped breaker needs manual restart; non-`AUTHENTICATIONFAILED` login errors (missing/unreadable password file, `[ALERT]`) never surface in `/health`.
- `R09`, `R11`, `R15`: conflict message is never consumed (re-409s later waiters); `active_waiter_count` includes expired orphans until a scan runs (live, the count did drain 58 s after expiry, so something triggers scans); a failing mailbox is reported as 408 "timeout".
- `S05d`: orphan OTP results are stored in plaintext in `waiters.result`; `consumed` is never purged. `S05e`: `.venv` is 775.

## Detailed results

### 1. API contract (test_api_contract.py)

| ID | Case | Result | Sev | Observed | Fix recommendation |
|---|---|---|---|---|---|
| api_contract:A01 | missing sender_domains -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"missing","loc":["body","sender_domains"],"msg":"Field required","input":{"after":"2026-10-12T06:00:18.761829+00:00","kin' | — |
| api_contract:A02 | empty sender_domains [] -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"too_short","loc":["body","sender_domains"],"msg":"List should have at least 1 item after validation, not 0","input":[],"' | — |
| api_contract:A03 | sender_domains as bare string -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"list_type","loc":["body","sender_domains"],"msg":"Input should be a valid list","input":"x.com"}]}' | — |
| api_contract:A04 | sender_domains [123] -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"string_type","loc":["body","sender_domains",0],"msg":"Input should be a valid string","input":123}]}' | — |
| api_contract:A05 | sender_domains [''] (empty-string domain) rejected | **FAIL** | P2 | status=408 t=1.21s body='{"error":"timeout"}' | Validate each domain: non-empty, hostname charset, no whitespace; reject '', '*', '.' |
| api_contract:A05b | sender_domains ['*'] / ['.'] rejected | **FAIL** | P2 | status=408 t=1.0s body='{"error":"timeout"}' | Same domain validator; wildcard-only entries should be 422 |
| api_contract:A06 | timeout_s='abc' -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"int_parsing","loc":["body","timeout_s"],"msg":"Input should be a valid integer, unable to parse string as an integer","i' | — |
| api_contract:A07 | timeout_s='1' (numeric string) -> 422 (strict typing) | **FAIL** | P2 | status=408 t=1.0s body='{"error":"timeout"}' | Use pydantic StrictInt (or model_config strict=True) so string numerics are rejected |
| api_contract:A08 | timeout_s=1.5 -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"int_from_float","loc":["body","timeout_s"],"msg":"Input should be a valid integer, got a number with a fractional part",' | — |
| api_contract:A09 | timeout_s=-5 -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"greater_than_equal","loc":["body","timeout_s"],"msg":"Input should be greater than or equal to 1","input":-5,"ctx":{"ge"' | — |
| api_contract:A10 | timeout_s=0 -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"greater_than_equal","loc":["body","timeout_s"],"msg":"Input should be greater than or equal to 1","input":0,"ctx":{"ge":' | — |
| api_contract:A11 | timeout_s missing -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"missing","loc":["body","timeout_s"],"msg":"Field required","input":{"sender_domains":["nomatch.invalid"],"after":"2026-1' (spec: required) | — |
| api_contract:A12 | timeout_s=null -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"int_type","loc":["body","timeout_s"],"msg":"Input should be a valid integer","input":null}]}' | — |
| api_contract:A13 | kind='sms' -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"literal_error","loc":["body","kind"],"msg":"Input should be \'otp\' or \'link\'","input":"sms","ctx":{"expected":"\'otp\' or \'' | — |
| api_contract:A14 | kind missing -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"missing","loc":["body","kind"],"msg":"Field required","input":{"sender_domains":["nomatch.invalid"],"after":"2026-10-12T' | — |
| api_contract:A15 | after='not-a-date' -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"datetime_from_date_parsing","loc":["body","after"],"msg":"Input should be a valid datetime or date, invalid character in' | — |
| api_contract:A16 | after missing -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"missing","loc":["body","after"],"msg":"Field required","input":{"sender_domains":["nomatch.invalid"],"kind":"otp","timeo' | — |
| api_contract:A17 | after naive timestamp (no tz) accepted as UTC | **PASS** | — | status=408 t=1.0s body='{"error":"timeout"}' | — |
| api_contract:A18 | after=0001-01-01T00:00:00Z does not 500 | **FAIL** | P1 | status=500 t=0.13s body='Internal Server Error' | Clamp/validate `after` to a sane window (e.g. now-24h .. now+1h) in WaitRequest; OverflowError escapes Broker.scan() as HTTP 500 |
| api_contract:A19 | after=0001-01-02T00:00:00Z does not 500 | **PASS** | — | status=408 t=1.01s body='{"error":"timeout"}' | — |
| api_contract:A20 | after=9999-12-31T23:59:59Z does not 500 | **PASS** | — | status=408 t=1.0s body='{"error":"timeout"}' | — |
| api_contract:A21 | after as unix int 1700000000 not 500 | **PASS** | — | status=408 t=4.03s body='{"error":"timeout"}' | — |
| api_contract:A22 | unknown extra fields rejected (typo protection) | **FAIL** | P2 | status=408 t=1.0s body='{"error":"timeout"}' | model_config = ConfigDict(extra='forbid'); typos like allowed_link_host silently fall back to defaults |
| api_contract:A23 | non-JSON body -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"json_invalid","loc":["body",0],"msg":"JSON decode error","input":{},"ctx":{"error":"Expecting value"}}]}' | — |
| api_contract:A24 | JSON array body -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"model_attributes_type","loc":["body"],"msg":"Input should be a valid dictionary or object to extract fields from","input' | — |
| api_contract:A25 | empty body -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]}' | — |
| api_contract:A26 | valid JSON with Content-Type: text/plain -> rejected | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"model_attributes_type","loc":["body"],"msg":"Input should be a valid dictionary or object to extract fields from","input' | — |
| api_contract:A27 | GET /v1/wait -> 405 | **PASS** | — | status=405 t=0.0s body='{"detail":"Method Not Allowed"}' | — |
| api_contract:A28 | POST /health -> 405 | **PASS** | — | status=405 t=0.0s body='{"detail":"Method Not Allowed"}' | — |
| api_contract:A29 | unknown path -> 404 | **PASS** | — | status=404 t=0.0s body='{"detail":"Not Found"}' | — |
| api_contract:A30 | allowed_link_hosts wrong type -> 422 | **PASS** | — | status=422 t=0.0s body='{"detail":[{"type":"list_type","loc":["body","allowed_link_hosts"],"msg":"Input should be a valid list","input":"x.com"}]}' | — |
| api_contract:A31 | 1 MB hint string handled (no 500/hang) | **PASS** | — | status=408 t=1.02s body='{"error":"timeout"}' | — |
| api_contract:A32 | 5000 sender_domains handled (no 500/hang) | **PASS** | — | status=408 t=1.0s body='{"error":"timeout"}' | — |
| api_contract:A33 | kind=link with allowed_link_hosts [] handled | **PASS** | — | status=408 t=1.0s body='{"error":"timeout"}' | — |
| api_contract:A34 | /docs and /openapi.json not exposed | **FAIL** | P2 | /docs=200 /openapi.json=200 | FastAPI(docs_url=None, redoc_url=None, openapi_url=None) in production |
| api_contract:H01 | /health has exactly the 6 spec keys | **PASS** | — | keys=['active_waiter_count', 'degraded', 'imap_connected', 'last_auth_failure', 'last_successful_poll', 'uptime_seconds'] | — |
| api_contract:H02 | /health value types correct | **PASS** | — | bad_types=[] status=200 body={'imap_connected': True, 'last_successful_poll': '2026-10-12T06:00:33.190134+00:00', 'active_waiter_count': 0, 'degraded': False, 'uptime_seconds': 443.9, 'last_auth_failure': None} | — |
| api_contract:H03 | last_successful_poll is parseable ISO-8601 | **PASS** | — | value=2026-10-12T06:00:33.190134+00:00 | — |
| api_contract:H04 | last_successful_poll fresh while imap_connected (usable as liveness signal) | **PASS** | — | age=0s uptime=443.9s connected=True degraded=False | — |
| api_contract:H05 | uptime_seconds monotonic | **PASS** | — | 443.9 -> 445.9 | — |
| api_contract:C01 | 10 concurrent waits (5s) all return 408, none 500/hang | **PASS** | — | statuses=[408, 408, 408, 408, 408, 408, 408, 408, 408, 408] max_elapsed=5.0s wall=5.0s | — |
| api_contract:C02 | each 408 arrives close to requested timeout (5s +/- 3) | **PASS** | — | elapsed=[5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0] | — |
| api_contract:C03 | active_waiter_count returns to 0 after timeouts | **PASS** | — | settled_after=0.0s count=0 | — |
| api_contract:A35 | timeout_s=301 accepted (silently clamped) rather than rejected | **FAIL** | P2 | status=None error=TimeoutError: timed out -> server clamped silently and kept waiting | Spec says 'int<=300': reject >300 with 422 (le=300) instead of silently clamping, so clients learn their assumption is wrong |
| api_contract:A36 | timeout_s=99999 accepted (silently clamped) rather than rejected | **FAIL** | P2 | status=None error=TimeoutError: timed out -> server clamped silently and kept waiting | Spec says 'int<=300': reject >300 with 422 (le=300) instead of silently clamping, so clients learn their assumption is wrong |
| api_contract:A38 | abandoned long waits (client gone after 8s) are released promptly | **FAIL** | P1 | count after disconnect=2; drained to 0 after 300.3s since request start (=> timeout_s clamp = 300s confirmed) | Poll request.is_disconnected() (or use a Starlette background disconnect watcher) in the wait loop and cancel + deregister; otherwise an abandoned waiter keeps consuming OTP emails for ~5 min and the real retry gets nothing |

### 2. IMAP resilience (test_imap_resilience.py)

| ID | Case | Result | Sev | Observed | Fix recommendation |
|---|---|---|---|---|---|
| imap_resilience:I01 | baseline: /health imap_connected=true, degraded=false | **PASS** | — | {'imap_connected': True, 'last_successful_poll': '2026-10-12T06:18:35.891052+00:00', 'active_waiter_count': 0, 'degraded': False, 'uptime_seconds': 118.1, 'last_auth_failure': None} | — |
| imap_resilience:I02 | IMAP socket has TCP keepalive enabled (detects half-open peers) | **FAIL** | P1 | ss timers: 'ESTAB 0 0 10.20.48.31:49016 172.253.63.108:993 users:(("uvicorn",pid=186175,fd=10)) uid:999 ino:1980838 sk:5008 cgroup:/system.slice/otp-broker' | Set SO_KEEPALIVE + TCP_KEEPIDLE=60/TCP_KEEPINTVL=10/TCP_KEEPCNT=3 on conn.sock after connect. Without it a silently dropped connection (NAT/Tailscale/router state loss) is only noticed when the 25-min IDLE re-issue's DONE gets no reply (up to ~26 min of missed OTPs) |
| imap_resilience:I10 | ss -K kills the IMAP socket (service notices) | **PASS** | — | ss output='Recv-Q Send-Q Local Address:Port Peer Address:Port\n0 0 10.20.48.31:49016 172.253.63.108:993' timeline(t,connected,degraded)=[(0.0, False, False), (1.41, True, False)] | — |
| imap_resilience:I11 | /health reflects the outage (imap_connected=false) during reconnect | **PASS** | — | timeline=[(0.0, False, False), (1.41, True, False)] | — |
| imap_resilience:I12 | degraded=true during reconnect (user question; spec ties degraded to polling fallback) | **FAIL** | P2 | timeline=[(0.0, False, False), (1.41, True, False)] -> degraded stays false through plain reconnects; only imap_connected flips | Consider a `state` field (connected\|reconnecting\|degraded\|auth_failed) so monitors can alert on reconnect storms; today only imap_connected=false for ~1-2s |
| imap_resilience:I13 | imap_connected=true again within 10s of drop | **PASS** | — | reconnected 1.41s after poll start (kill->first poll ~0.2s) | — |
| imap_resilience:I14 | /v1/wait issued DURING the reconnect returns a bounded, clean result | **PASS** | — | status=408 body='{"error":"timeout"}' elapsed=6.0s (timeout_s=6) | — |
| imap_resilience:I20 | 3 rapid drops -> each reconnect logged with exception type and delay | **PASS** | — | logged delays=[1.2, 1.0, 0.9, 2.3]; journal tail=':42-04:00 beelink uvicorn[186175]: 2026-10-12 02:19:42,398 WARNING otp_broker.imap imap error ConnectionAbortedError; reconnecting in 1.0s\n2026-10-12T02:19:43-04:00 beelink uvicorn[186175]: 2026-10-12 02:19:43,907 INFO otp_broker.imap imap connected, uidvalidity=1\n2026-10-12T02:19:44-04:00 beelink uvicorn[186175]: 2026-10-12 02:19:44,271 WARNING otp_broker.imap ima… | — |
| imap_resilience:I21 | backoff escalates 1s,2s,4s on repeated failures (jittered, capped 60s) | **FAIL** | P2 | [CODE-REVIEW + live observation] Observed delays [1.2, 1.0, 0.9, 2.3]: every drop restarts at ~1s because `attempt` resets to 0 after any successful connect, so a flapping link (connect OK, drop after 100ms) reconnects ~once per second forever. Escalation only happens on failed connects (untestable live without blocking 993 egress -> SKIPPED live; verified by code: imap_client.backoff_delay 1,2,4..60 x0.8-1.2). | Reset `attempt` only after the connection has been stable for >= 30s; otherwise flapping links hammer Gmail (risk: temporary IP/account throttling) |
| imap_resilience:I22 | service recovers fully after rapid drops (connected, no waiters stuck) | **PASS** | — | {'imap_connected': True, 'last_successful_poll': '2026-10-12T06:20:07.330008+00:00', 'active_waiter_count': 0, 'degraded': False, 'uptime_seconds': 150.7, 'last_auth_failure': None} | — |
| imap_resilience:B01 | [OFFLINE SIM + CODE-REVIEW] breaker trips after exactly 3 AUTHENTICATIONFAILED | **PASS** | — | tripped after 2.71s with 3 login attempts (limit 3) | — |
| imap_resilience:B02 | [OFFLINE SIM] no further login attempts once open (8s observed) | **PASS** | — | attempts at trip=3, 8s later=3 | — |
| imap_resilience:B03 | [OFFLINE SIM] last_auth_failure set, connected=false | **PASS** | — | last_auth_failure=2026-10-12 06:20:11.289656+00:00 | — |
| imap_resilience:B04 | [OFFLINE SIM] breaker open => /health 503 + X-Unhealthy-Reason, /v1/wait 503 fast | **PASS** | — | /health=503 hdr=auth_failed /v1/wait=503 {"error":"auth_failed"} | — |
| imap_resilience:B05 | [CODE-REVIEW] recovery requires manual restart (no auto-reset, no half-open probe) | **FAIL** | P1 | run() sleeps 5s forever once `_auth_tripped`; nothing clears it. A transient Gmail AUTHENTICATIONFAILED burst (rotated app password restored, Google hiccup) permanently kills OTP retrieval until `systemctl restart`, and Restart=always does not help because the process stays alive and 'healthy' to systemd. | Add a half-open probe (one login attempt every 30-60 min) or a systemd watchdog / health-based restart (ExecStartPost + WatchdogSec with sd_notify), and alert on 503 auth_failed. Document the manual recovery procedure |
| imap_resilience:B06 | [CODE-REVIEW] non-AUTHENTICATIONFAILED login errors never trip a breaker | **FAIL** | P1 | Any other login failure (e.g. '[ALERT] Application-specific password required', 'Too many simultaneous connections', LOGIN-disabled, missing/unreadable password file -> FileNotFoundError) goes down the generic `except Exception` path: retries forever with backoff capped at 60s and never marks health unhealthy (degraded=false, last_auth_failure=null). | Classify [ALERT]/[AUTHENTICATIONFAILED]/'Invalid credentials' and OSError(password file) as auth-class failures that count toward the breaker; expose last_error in /health |
| imap_resilience:B07 | [CODE-REVIEW] auth counter is 'consecutive' | **PASS** | — | `_auth_failures` resets only on successful connect (`_connect` end) and increments only on AuthFailed; interleaved network errors do not reset it (conservative). Retry delay between attempts 1s/2s so the breaker trips ~3s after start: with a typo'd/rotated password a restart-loop (systemd RestartSec=5 only matters on crash) cannot hammer Gmail beyond 3 logins per process start | — |
| imap_resilience:B08 | live bad-password test against Gmail | **SKIP** | — | Per instructions: skipped to avoid Gmail lockout; B01-B04 use an in-process fake IMAP only | — |
| imap_resilience:B09 | [CODE-REVIEW] half-open TCP detection window | **FAIL** | P1 | IDLE is re-issued every 25 min (IDLE_REISSUE_S). If the TCP path dies silently, `_end_idle` sends DONE into the void and `readline()` blocks until the 60s socket timeout -> reconnect. Worst-case blind window = ~26 min during which no push arrives; waiters only rescue themselves because every new wait() does an explicit SEARCH (which needs the lock and would also hang 60s) so latency spikes, and `IdleStalled` (2x win… | Re-issue IDLE every 5-10 min (Gmail allows <=29), enable TCP keepalive, and use a socket timeout << 60s for DONE/readline; update last_successful_poll on each IDLE cycle |

### 3. Extraction adversarial (test_extraction_adversarial.py)

| ID | Case | Result | Sev | Observed | Fix recommendation |
|---|---|---|---|---|---|
| extraction:E01 | multiple 6-digit numbers -> verification code, not order/zip | **PASS** | — | got='789012' want='789012' | — |
| extraction:E02 | code split across HTML tags 4<b>8</b>3<i>9</i>20 | **PASS** | — | got='483920' want='483920' | — |
| extraction:E03 | phone number does not pollute | **PASS** | — | got='998877' want='998877' | — |
| extraction:E03b | phone number AFTER keyword is ignored | **PASS** | — | got='998877' want='998877' | — |
| extraction:E04 | currency $48.39 near code 112233 | **PASS** | — | got='112233' want='112233' | — |
| extraction:E04b | currency directly after keyword ('code $483920 refund') | **PASS** | — | got='112233' want='112233' | — |
| extraction:E05 | no code at all -> None (graceful) | **PASS** | — | got=None want=None | — |
| extraction:E06 | two distinct codes -> 409 (CONFLICT) or documented behaviour | **FAIL** | P1 | got='111111' want='CONFLICT' | BUILD_BRIEF says '>2 candidates' => 409, so exactly 2 distinct codes silently returns the first. Make MAX_DISTINCT_CODES=1 (any 2 distinct codes => 409) or prefer the candidate nearest an 'is/:' cue |
| extraction:E07a | spaces: '48 39 20' | **PASS** | — | got='483920' want='483920' | — |
| extraction:E07b | hyphens: '48-39-20' | **PASS** | — | got='483920' want='483920' | — |
| extraction:E07c | grouped 3-3: '483 920' | **PASS** | — | got='483920' want='483920' | — |
| extraction:E07d | non-breaking space '483\u00a0920' | **FAIL** | P1 | got=None want='483920' | Normalise \u00a0/\u2009/\u202f to ' ' before matching (HTML &nbsp; commonly renders as \u00a0) |
| extraction:E07e | html &nbsp; group '483&nbsp;920' | **FAIL** | P1 | got=None want='483920' | Same: normalise Unicode spaces |
| extraction:E10 | zip CODE then real code ('zip code 90210 ... verification code is 789012') | **FAIL** | P0 | got='90210' want='789012' | Require keyword set to be code-ish (verification\|security\|login\|one-time\|otp\|passcode) and drop bare 'code'/'pin' unless adjacent to is/: |
| extraction:E11 | promo code with digits then OTP | **FAIL** | P0 | got='2024' want='654321' | Same as E10 |
| extraction:E12 | OTP before keyword ('123456 is your verification code') | **FAIL** | P1 | got=None want='123456' | Add reverse pattern: (\d{4,8})\s+(?:is\|as)\s+your\s+(?:\w+\s+){0,2}(?:code\|otp\|passcode) (Google, Microsoft, Apple style) |
| extraction:E13 | 'Use 482913 as your code' | **FAIL** | P1 | got=None want='482913' | Same reverse pattern |
| extraction:E14 | 'one-time password is 482913' (keyword 'password') | **FAIL** | P1 | got=None want='482913' | Add 'password' and 'one-time'/'security code' variants to the keyword set |
| extraction:E15 | digit-per-table-cell OTP | **PASS** | — | got=None want=None | — |
| extraction:E15b | 'Your code:' then one digit per <td> | **FAIL** | P1 | got=None want='483920' | html_to_text turns <td> into newlines, fragmenting digit-per-cell codes. Fallback: if no match, collapse whitespace between single digits |
| extraction:E16 | alphanumeric code (Greenhouse-style 'security code: Ab3dE9xZ') | **FAIL** | P1 | got=None want='Ab3dE9xZ' | numeric-only: Greenhouse/Workable-style alphanumeric codes can never be extracted. Add optional alphanumeric mode (hint-gated) with length 6-10 + mixed charset |
| extraction:E17 | Unicode (Arabic-Indic) digits not accepted as a code | **FAIL** | P2 | got='٤٨٣٩٢٠' want=None | Use [0-9] instead of \d in _CODE_RE (str \d matches all Unicode Nd digits; int() normalises them silently) |
| extraction:E18 | fullwidth digits not accepted | **FAIL** | P2 | got='４８３９２０' want=None | Use [0-9] |
| extraction:E19 | zero-width space inside code '48\u200b3920' | **FAIL** | P1 | got=None want='483920' | Strip \u200b\u200c\u200d\ufeff before matching |
| extraction:E20 | HTML comment decoy not extracted | **PASS** | — | got='123456' want='123456' | — |
| extraction:E21 | display:none decoy ignored | **FAIL** | P0 | got='000000' want='123456' | Attacker-controlled sender can inject hidden candidates; since first match wins hidden text placed first wins. Drop elements with display:none/visibility:hidden/font-size:0/opacity:0 and preheader spans |
| extraction:E22 | <style>/<script> content ignored | **PASS** | — | got='123456' want='123456' | — |
| extraction:E23 | 11-digit run is not truncated to 8 | **PASS** | — | got=None want=None | — |
| extraction:E24 | 9-digit run not truncated | **PASS** | — | got=None want=None | — |
| extraction:E25 | 3-digit run rejected | **PASS** | — | got=None want=None | — |
| extraction:E26 | plain+html alternatives with same code (spaced vs not) -> single code | **PASS** | — | got='483920' want='483920' | — |
| extraction:E27 | plain and html disagree (plain 111111 / html 222222) | **FAIL** | P1 | got='111111' want='CONFLICT' | Disagreeing alternative bodies are suspicious; treat as conflict. Currently first (plain) wins silently |
| extraction:E28 | time-like text '10:30' / years / 'code expires in 10 minutes' ignored | **PASS** | — | got='482913' want='482913' | — |
| extraction:E29 | 'PIN' inside word ('pinterest 123456') not a keyword | **PASS** | — | got='482913' want='482913' | — |
| extraction:E30 | 'verification 10-15 minutes' not parsed as 1015 | **FAIL** | P0 | got='1015' want='482913' | Grouped-number branch accepts any 2-4 digit groups; require groups to be equal-sized (3-3, 2-2-2, 4-4) and not followed by unit words |
| extraction:E31 | case-insensitive 'CODE' / 'Otp' | **PASS** | — | got='482913' want='482913' | — |
| extraction:E32 | code on next line after keyword | **PASS** | — | got='482913' want='482913' | — |
| extraction:E33 | code in bold markdown-ish '**482913**' | **FAIL** | P1 | got=None want='482913' | Allow [*_`"'<>()\[\]] between keyword/is and digits |
| extraction:E34 | code in quotes or brackets 'code is [482913]' | **FAIL** | P1 | got=None want='482913' | Same as E33 |
| extraction:E35 | three distinct codes -> CONFLICT | **PASS** | — | got='CONFLICT' want='CONFLICT' | — |
| extraction:E36 | message-quoted/forwarded old code after new one (reply chain) | **INFO** | P2 | got='222222' want='CONFLICT' [Returned 222222 = the NEWEST code here (top-posted reply), so correct for this ordering; a bottom-posted/quoted-first message would return the stale quoted code. Expectation CONFLICT was too strict.] | Strip quoted text ('>' lines, 'On ... wrote:') before extraction. |
| extraction:S01 | realistic sample: Workday | **PASS** | — | got='482913' want='482913' | — |
| extraction:S02 | realistic sample: Workday-html | **PASS** | — | got='482913' want='482913' | — |
| extraction:S03 | realistic sample: Greenhouse | **FAIL** | P1 | got=None want='A7bX92Qd' | see E10-E36 recommendations |
| extraction:S04 | realistic sample: Lever | **PASS** | — | got='837261' want='837261' | — |
| extraction:S05 | realistic sample: iCIMS | **PASS** | — | got='556677' want='556677' | — |
| extraction:S06 | realistic sample: Chase | **PASS** | — | got='774411' want='774411' | — |
| extraction:S07 | realistic sample: BofA | **PASS** | — | got='208154' want='208154' | — |
| extraction:S08 | realistic sample: Wells Fargo | **PASS** | — | got='913377' want='913377' | — |
| extraction:S09 | realistic sample: Citi | **PASS** | — | got='650021' want='650021' | — |
| extraction:S10 | realistic sample: Google | **FAIL** | P1 | got=None want='123456' | see E10-E36 recommendations |
| extraction:S11 | realistic sample: Microsoft | **FAIL** | P1 | got=None want='4711' | see E10-E36 recommendations |
| extraction:S12 | realistic sample: Apple | **PASS** | — | got='872305' want='872305' | — |
| extraction:S13 | realistic sample: LinkedIn | **PASS** | — | got='193846' want='193846' | — |
| extraction:S14 | realistic sample: Indeed | **PASS** | — | got='308122' want='308122' | — |
| extraction:S15 | realistic sample: Ashby | **PASS** | — | got='904412' want='904412' | — |
| extraction:S16 | realistic sample: Okta | **PASS** | — | got='663301' want='663301' | — |
| extraction:S17 | realistic sample: Dartmouth Duo-ish | **PASS** | — | got='40291847' want='40291847' | — |
| extraction:S18 | realistic sample: Amazon | **PASS** | — | got='385920' want='385920' | — |
| extraction:S19 | realistic sample: PayPal | **PASS** | — | got='782345' want='782345' | — |
| extraction:S20 | realistic sample: Uber html | **FAIL** | P2 | got=None want='3841' | see E10-E36 recommendations |
| extraction:S21 | realistic sample: Zoom | **PASS** | — | got='249018' want='249018' | — |
| extraction:S22 | realistic sample: Stripe | **PASS** | — | got='482913' want='482913' | — |
| extraction:S23 | realistic sample: Robinhood | **PASS** | — | got='129478' want='129478' | — |
| extraction:S24 | realistic sample: Substack magic | **PASS** | — | got='551209' want='551209' | — |
| extraction:S25 | realistic sample: Handshake | **PASS** | — | got='772291' want='772291' | — |
| extraction:S26 | realistic sample: Coinbase html-split | **PASS** | — | got='482913' want='482913' | — |
| extraction:S27 | realistic sample: Marketing noise | **PASS** | — | got=None want=None | — |
| extraction:S28 | realistic sample: Receipt no OTP | **PASS** | — | got=None want=None | — |
| extraction:L01 | only exact allowlisted host picked among evil lookalikes | **PASS** | — | got='https://verify.example.com/verify?token=abc' want='https://verify.example.com/verify?token=abc' | — |
| extraction:L02 | evil-verify.example.com alone rejected | **PASS** | — | got=None want=None | — |
| extraction:L03 | verify.example.com.evil.com alone rejected | **PASS** | — | got=None want=None | — |
| extraction:L04 | userinfo trick https://verify.example.com@evil.com/ rejected | **PASS** | — | got=None want=None | — |
| extraction:L05 | backslash differential https://evil.com\@verify.example.com/verify rejected (browsers treat \ as /) | **FAIL** | P0 | got='https://evil.com\\@verify.example.com/verify?token=1' want=None | Reject any URL containing backslash, control chars, whitespace or '@' in the authority before parsing; re-parse with a WHATWG-compliant parser or compare urlsplit(url.replace('\\','/')).hostname too |
| extraction:L06 | http:// (non-TLS) rejected | **PASS** | — | got=None want=None | — |
| extraction:L07 | javascript:/data: schemes rejected | **PASS** | — | got=None want=None | — |
| extraction:L08 | uppercase scheme/host accepted & returned | **PASS** | — | got='HTTPS://VERIFY.EXAMPLE.COM/Verify?x=1' want='HTTPS://VERIFY.EXAMPLE.COM/Verify?x=1' | — |
| extraction:L09 | shortest keyword path wins | **PASS** | — | got='https://verify.example.com/verify' want='https://verify.example.com/verify' | — |
| extraction:L10 | &amp; in href decoded | **PASS** | — | got='https://verify.example.com/confirm?a=1&token=2' want='https://verify.example.com/confirm?a=1&token=2' | — |
| extraction:L11 | no-keyword magic link (opaque path /l/Xk29s) ignored => None | **FAIL** | P1 | got=None want='https://verify.example.com/l/Xk29s' | Many ATS magic links have no verify\|confirm\|activate\|token keyword (e.g. /l/<opaque>, /auth/<jwt>). Fall back to the single allowlisted https link that isn't unsubscribe/privacy/help |
| extraction:L12 | unsubscribe link with keyword-less path never returned | **PASS** | — | got=None want=None | — |
| extraction:L13 | plain-text link with trailing punctuation | **PASS** | — | got='https://verify.example.com/verify?token=abc' want='https://verify.example.com/verify?token=abc' | — |
| extraction:L14 | subdomain of allowlisted host accepted (a.verify.example.com) | **PASS** | — | got='https://a.verify.example.com/verify?t=1' want='https://a.verify.example.com/verify?t=1' | — |
| extraction:L15 | percent-encoded dot host https://verify%2eexample%2ecom/ rejected | **PASS** | — | got=None want=None | — |
| extraction:L16 | IDN homograph host rejected | **PASS** | — | got=None want=None | — |
| extraction:L17 | trailing-dot FQDN accepted | **PASS** | — | got='https://verify.example.com./verify?t=1' want='https://verify.example.com./verify?t=1' | — |
| extraction:L18 | port on allowed host accepted (https://verify.example.com:8443/verify) | **FAIL** | P2 | got='https://verify.example.com:8443/verify?t=1' want=None | Non-default ports on a verification link are unusual; consider rejecting |
| extraction:L19 | empty href / whitespace / newline-split href | **PASS** | — | got='https://verify.example.com/verify?t=1' want='https://verify.example.com/verify?t=1' | — |
| extraction:L20 | empty allowlist -> nothing returned | **PASS** | — | got=None want=None | — |
| extraction:L21 | allowlist entry '*'/'.' never matches everything | **PASS** | — | got=None want=None | — |
| extraction:L22 | open redirect param inside allowlisted link flagged | **PASS** | — | got='https://verify.example.com/verify?next=https://evil.com' want='https://verify.example.com/verify?next=https://evil.com' | — |
| extraction:D01 | domain_matches: substring/suffix confusion (notexample.com vs example.com) | **PASS** | — |  | — |
| extraction:D02 | domain_matches: wildcard-only allowlist entries match nothing | **PASS** | — |  | — |

### 4. Races / atomic consume / restart (test_races.py)

| ID | Case | Result | Sev | Observed | Fix recommendation |
|---|---|---|---|---|---|
| races:R01 | 10 concurrent waiters, 1 matching mail -> exactly one gets the code | **PASS** | — | results=['424242', 'timeout', 'timeout', 'timeout', 'timeout', 'timeout', 'timeout', 'timeout', 'timeout', 'timeout'] | — |
| races:R02 | 10 waiters, 10 mails -> 10 distinct codes, none duplicated | **PASS** | — | results=['100001', '100002', '100003', '100004', '100005', '100006', '100007', '100008', '100009', '100010'] | — |
| races:R03 | 20 waiters + 50 thread-notified scans, 1 mail -> 1 delivery, 1 consumed row | **PASS** | — | deliveries=1 consumed_rows=1 | — |
| races:R04 | Store.consume(uv,uid) from 32 threads -> exactly one True | **PASS** | — | True=1 | — |
| races:R05 | consume keys on (uidvalidity,uid): rollover re-delivers uid 7 once | **PASS** | — | True,True | — |
| races:R06 | mail that arrived BEFORE register is found immediately (register-then-search) | **PASS** | — | result={'code': '565656', 'message_id': '<m1@t>', 'received_at': '2026-10-12T06:14:28.419637+00:00'} in 0.00s | — |
| races:R07 | wait(after=now) must NOT return a 4-minute-old OTP (client just requested a fresh one) | **FAIL** | P0 | result={'code': '111111', 'message_id': '<m1@t>', 'received_at': '2026-10-12T06:10:58.421134+00:00'} | The 5-min CLOCK_SKEW_MARGIN is applied to BOTH the IMAP fetch and _matches, so any unconsumed OTP from the last 5 min is returned instantly -> job agent submits a stale/expired code. Gmail INTERNALDATE is server-assigned (no client skew), so use margin<=10-15s; or let callers pass `after` = their own request time and drop the margin; or track last-delivered per sender |
| races:R08 | two valid mails -> NEWEST delivered | **PASS** | — | result={'code': '222222', 'message_id': '<m2@t>', 'received_at': '2026-10-12T06:14:53.422850+00:00'} | — |
| races:R09 | conflict is returned once; later waiter still gets the next good mail | **PASS** | — | r1={'error': 'conflict'} r2={'code': '444444', 'message_id': '<m2@t>', 'received_at': '2026-10-12T06:14:57.424916+00:00'} | — |
| races:R10 | reloaded orphan waiter is evaluated, result persisted, identical retry receives it | **PASS** | — | rows_while_waiting=1 active_after_reload=1 stored=True retry={'code': '727272', 'message_id': '<m1@t>', 'received_at': '2026-10-12T06:14:58.728779+00:00'} | — |
| races:R11 | expired orphan waiter disappears from active_waiter_count on its own | **FAIL** | P2 | count at reload=1, 0.5s after expiry=1 | Prune expired orphans in active_waiter_count (filter by expires_at) and/or run a periodic sweeper task |
| races:R12 | waiter with after=0001-01-02 does not starve other waiters while pending | **FAIL** | P1 | legit waiter got {'error': 'timeout'}; poison={'error': 'timeout'} | scan() uses min(after) across ALL pending waiters; one OverflowError/odd date makes fetch_since raise for everyone (swallowed as 'scan failed'). Validate `after` on input (clamp to [now-1h, now+5m]) and evaluate waiters with independent fetch windows |
| races:R13 | timeout_s=2 honoured even if IMAP fetch hangs 6s | **FAIL** | P1 | returned {'error': 'timeout'} after 6.0s (asked 2s) | Broker.wait awaits scan() OUTSIDE asyncio.wait_for. Wrap the whole register+scan+wait body in asyncio.timeout(timeout_s), and run initial scan as a task so the deadline can fire; also hold _scan_lock only around state changes, not IMAP I/O |
| races:R14 | short-timeout waiter is not stuck behind another waiter's slow scan | **FAIL** | P1 | timeout_s=1 waiter finished after 10.0s | Same fix as R13 |
| races:R15 | fetch_since raising -> waiter times out cleanly | **PASS** | — | result={'error': 'timeout'} | — |
| races:R16 | FastAPI-level: 2 concurrent POSTs, 1 mail -> one 200 (code) + one 408 | **PASS** | — | statuses=[200, 408] bodies=[{'request_id': 'f03801a7-1b5f-481f-814f-3affbca50776', 'code': '606060', 'message_id': '<m1@t>', 'received_at': '2026-10-12T06:15:21.265216+00:00'}, {'error': 'timeout'}] | — |
| races:R17 | timeout_s=99999 clamps server-side to <=300s | **PASS** | — | expires_in=300s | — |
| races:R20a1 | restart with short waiter (40s): client gets a clean, bounded outcome | **PASS** | — | client status=408 err=None after 40.0s; journal='Oct 12 02:15:26 beelink systemd[1]: Stopping otp-broker.service - OTP broker (IMAP verification-code service)...\nOct 12 02:16:04 beelink uvicorn[185833]: 2026-10-12 02:16:04,479 INFO otp_broker.broker wait 6911ebf0-0316-45c8-bf4e-ba9bbc7864b5 timed out\nOct 12 02:16:04 beelink systemd[1]: Stopped otp' | — |
| races:R20a2 | restart with short waiter (40s): `systemctl restart` completes promptly (<15s) | **FAIL** | P1 | restart blocked 38.4s; new process answered 0.5s later | uvicorn drains in-flight /v1/wait on SIGTERM, so every deploy/restart blocks until the longest waiter finishes or systemd SIGKILLs at 90s. Add --timeout-graceful-shutdown 5 and cancel waiters in lifespan shutdown |
| races:R20a3 | waiter recovered from SQLite after restart (short waiter (40s)) | **PASS** | — | active_waiter_count before=1 right after=0 (waiter finished during graceful drain; nothing to recover) | — |
| races:R20a5 | IMAP reconnected after restart | **INFO** | P1 | {'imap_connected': False, 'last_successful_poll': None, 'active_waiter_count': 0, 'degraded': False, 'uptime_seconds': 0.2, 'last_auth_failure': None} [TEST ARTIFACT: probed 0.2s after the new process started, before IMAP login finished. The long-waiter restart (R20b5) shows connected; script now polls up to 30s.] |  |
| races:R20b1 | restart with long waiter (150s > systemd default TimeoutStopSec=90s): client gets a clean, bounded outcome | **PASS** | — | client status=None err=RemoteDisconnected: Remote end closed connection without response after 92.24s; journal="Oct 12 02:16:07 beelink systemd[1]: Stopping otp-broker.service - OTP broker (IMAP verification-code service)...\nOct 12 02:17:37 beelink systemd[1]: otp-broker.service: State 'stop-sigterm' timed out. Killing.\nOct 12 02:17:37 beelink systemd[1]: otp-broker.service: Killing process 186153 (uvicorn) w" | — |
| races:R20b2 | restart with long waiter (150s > systemd default TimeoutStopSec=90s): `systemctl restart` completes promptly (<15s) | **FAIL** | P1 | restart blocked 90.2s; new process answered 0.5s later | uvicorn drains in-flight /v1/wait on SIGTERM, so every deploy/restart blocks until the longest waiter finishes or systemd SIGKILLs at 90s. Add --timeout-graceful-shutdown 5 and cancel waiters in lifespan shutdown |
| races:R20b3 | waiter recovered from SQLite after restart (long waiter (150s > systemd default TimeoutStopSec=90s)) | **PASS** | — | active_waiter_count before=1 right after=1 | — |
| races:R20b4 | orphan waiter count drains to 0 after its expiry (no new traffic) | **PASS** | — | drained after 58.13s | — |
| races:R20b5 | IMAP reconnected after restart | **PASS** | — | {'imap_connected': True, 'last_successful_poll': '2026-10-12T06:18:35.891052+00:00', 'active_waiter_count': 0, 'degraded': False, 'uptime_seconds': 58.4, 'last_auth_failure': None} | — |
| races:R30 | inject REAL email and race two live waiters | **SKIP** | — | Would require reading the Gmail app password (jobagent-owned, 600) to send mail, or sending from an external sender; out of scope without explicit credential-use approval. Covered by simulated R01-R03/R16 which exercise the same Store.consume path. | — |
| races:R31 | live 'mail before register' using a real mailbox message | **SKIP** | — | Needs a real recent OTP email; the only path to read the mailbox is the broker itself, and a successful match would mark a real message consumed in the production DB. Covered by simulated R06. | — |

### 5. Security, read-only (test_security.py)

| ID | Case | Result | Sev | Observed | Fix recommendation |
|---|---|---|---|---|---|
| security:S01 | GET http://100.77.193.40:8765/health (tailscale address) must fail/refuse | **PASS** | — | curl -> 'curl: (7) Failed to connect to 100.77.193.40 port 8765 after 0 ms: Could not connect to server\n000' | — |
| security:S02 | GET http://10.20.48.31:8765/health (LAN address) must fail/refuse | **PASS** | — | curl -> 'curl: (7) Failed to connect to 10.20.48.31 port 8765 after 0 ms: Could not connect to server\n000' | — |
| security:S03 | only 127.0.0.1:8765 is listening (no 0.0.0.0 / [::]) | **PASS** | — | listeners on :8765 = '127.0.0.1:8765' | — |
| security:S03b | IPv6 loopback [::1]:8765 not listening | **PASS** | — | curl -> 'curl: (7) Failed to connect to ::1 port 8765 after 0 ms: Could not connect to server\n000' | — |
| security:S04 | gmail_app_password mode 600 | **PASS** | — | 600 jobagent:jobagent /etc/jobagent24/gmail_app_password | — |
| security:S04b | gmail_app_password owned by the service user (jobagent), not root-readable by others | **PASS** | — | 600 jobagent:jobagent /etc/jobagent24/gmail_app_password | — |
| security:S04c | /etc/jobagent24 directory not world-writable | **PASS** | — | 750 root:jobagent /etc/jobagent24 | — |
| security:S05 | SQLite db / -wal / -shm are mode 600 | **PASS** | — | 600 jobagent:jobagent /opt/otp-broker/otp-broker.db \| 600 jobagent:jobagent /opt/otp-broker/otp-broker.db-shm \| 600 jobagent:jobagent /opt/otp-broker/otp-broker.db-wal | — |
| security:S05b | /opt/otp-broker itself not world-accessible (750) | **PASS** | — | 750 jobagent:jobagent /opt/otp-broker \| 775 jobagent:jobagent /opt/otp-broker/.venv | — |
| security:S05e | /opt/otp-broker/.venv is 775 (group-writable by jobagent group) | **INFO** | P2 | 750 jobagent:jobagent /opt/otp-broker \| 775 jobagent:jobagent /opt/otp-broker/.venv | chmod -R g-w,o-rwx .venv; the venv is executable code run by the service |
| security:S05d | SQLite stores orphan OTP results in plaintext (waiters.result); consumed table grows unbounded | **INFO** | P2 | waiters columns / stored-results / consumed rows: "['request_id', 'fingerprint', 'sender_domains', 'after_ts', 'kind', 'hint', 'allowed_link_hosts', 'expires_at', 'result']\n(0,) (0,)". Code: Store.set_result persists {'code': ...} until the waiter row is deleted (expiry or retry pickup); `consumed` is never purged. | Delete orphan result immediately on pickup, purge consumed rows > 7 days, keep db 600 (already) |
| security:S06 | journal (last 1h): no 6-digit OTP-looking values after stripping timestamps/uuids/message-ids/ip:port | **PASS** | — | 2024 lines scanned; 6-digit candidates=0 []; keyword+digits lines=0 []. METHOD: strip journald prefix, embedded timestamps, UUIDs, <message-ids>, ip:port; regex (?<![\w.:/-])\d{6}(?![\w.:/-]); plus keyword-adjacent 4-8 digit scan. Note: live traffic in this window was only non-matching test waiters, so a real delivery path (which logs sender/message-id/timestamp only, per broker.py) was NOT exercised against a real … | — |
| security:S06b | journal (last 24h) same scan | **PASS** | — | 2024 lines; candidates=0 []; keyword lines=0 | — |
| security:S07 | journal contains no app-password-shaped token (16 lowercase letters / 4x4 groups) | **PASS** | — | 0 suspicious lines (values withheld) | — |
| security:S08 | Log hygiene: wait registration logs sender_domains + timeout (job-site names), delivery logs sender + message-id | **INFO** | P2 | 294 registrations/h; sample: "2:00:18,766 INFO otp_broker.broker wait a4bf8e7c-b1d5-47d7-8d91-4593f41c9a55 registered kind=otp domains=[''] timeout=1s\nOct 12 02:00:19 beelink uvicorn[184584]: 2026-10-12 02:00:19,973 INFO otp_broker.broker wait d96a8104-7888-4f77-9e96-23f0f407e09d registered kind=otp domains=['*', '.'] timeout=1s". Reveals which employers the agent is applying to to anyone with journal access; no co… | Acceptable for a local box; hash or truncate domains if journal is shipped off-host |
| security:S09 | service runs as non-root User=jobagent (unit AND live process) | **PASS** | — | Restart=always TimeoutStopUSec=1min 30s WatchdogUSec=0 MemoryMax=infinity CapabilityBoundingSet=cap_chown cap_dac_override cap_dac_read_search cap_fowner cap_fsetid cap_kill cap_setgid cap_setuid cap_setpcap cap_linux_immutable cap_net_bind_service cap_net_broadcast cap_net_admin cap_net_raw cap_ipc_lock cap_ipc_owner cap_sys_module cap_sys_rawio cap_sys_chroot cap_sys_ptrace cap_sys_pacct cap_sys_admin cap_sys_boot… | — |
| security:S10 | no effective capabilities | **PASS** | — | Uid: 999 999 999 999 CapEff: 0000000000000000 NoNewPrivs: 1 | — |
| security:S11 | systemd hardening exposure score | **INFO** | P2 | → Overall exposure level for otp-broker.service: 8.3 EXPOSED :-( | Add: MemoryMax=256M, RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX, CapabilityBoundingSet=, SystemCallFilter=@system-service, ProtectKernelTunables/Modules, RestrictNamespaces, LockPersonality, TimeoutStopSec=15, IPAddressDeny/Allow to Gmail only if feasible |
| security:S12 | resource limits set (MemoryMax / TasksMax) | **FAIL** | P2 | Restart=always TimeoutStopUSec=1min 30s WatchdogUSec=0 MemoryMax=infinity CapabilityBoundingSet=cap_chown cap_dac_override cap_dac_read_search cap_fowner cap_fsetid cap_kill cap_setgid cap_setuid cap_setpcap cap_linux_immutable cap_net_bind_service cap_net_broadcast cap_net_admin cap_net_raw cap_ipc_lock cap_ipc_owner cap_sys_module cap_sys_rawio cap_sys_chroot cap_sys_ptrace cap_sys_pacct cap_sys_admin cap_sys_boot… | MemoryMax=256M TasksMax=64 |
| security:S13 | API requires local authentication (any local user can call /health, /v1/wait) | **FAIL** | P1 | as user `nobody`: '{"imap_connected":true,"last_successful_poll":"2026-10-12T06:28:43.529023+00:00","active_waiter_count":0,"degraded":false,"uptime_seconds":677.0,"last_auth_failure":null}'. /v1/wait is unauthenticated: any local account, container with host networking, or SSRF-able local service can request OTPs/magic links for any sender domain (allowed_link_hosts is also caller-supplied). | Bind a unix socket with mode 660 owned by jobagent:jobagent-clients, or require a bearer token from a 600 file; restrict sender_domains to a server-side allowlist |
| security:S14 | API schema endpoints disabled | **FAIL** | P2 | /openapi.json -> 200 | FastAPI(docs_url=None, redoc_url=None, openapi_url=None) |
| security:S15 | [CODE-REVIEW] sender authenticity: From header trusted, no SPF/DKIM/DMARC check | **FAIL** | P1 | broker._matches/domain_matches use only the parsed From address. Gmail normally spam-folders spoofed mail, but anything delivered to INBOX with a forged From: noreply@<allowed-domain> (or a legit-but-compromised/forwarding sender, or a lookalike 'Reply-To' trick) is accepted as the OTP. Also kind=link returns attacker-chosen URLs on any allowlisted host. | Require Authentication-Results: dkim=pass/dmarc=pass (Gmail adds it) with header.d aligned to sender_domains, and Return-Path/envelope domain consistent; skip messages in SPAM/with X-Gm-Spam |
| security:S16 | [CODE-REVIEW] hint is caller-controlled substring match over full message body | **PASS** | — | Not exploitable beyond own mailbox; noted only. | — |

### 6. Load / leak (test_load.py)

| ID | Case | Result | Sev | Observed | Fix recommendation |
|---|---|---|---|---|---|
| load:L01 | 50 sequential waits (2s) all 408 | **PASS** | — | statuses={408: 50} total=108.4s (ideal 100s) lat min/med/max=2.0/2.0/3.81 | — |
| load:L02 | per-request overhead small (every wait within 2.0-3.0s) | **FAIL** | P2 | max overhead=1.81s | Same root cause as L14: each wait does an IMAP round-trip (END IDLE/SEARCH/IDLE) under one lock; occasional 1-2s overshoot is Gmail latency + lock contention. |
| load:L03 | /health sane after sequential run (uptime grew ~ elapsed, connected, 0 waiters) | **PASS** | — | uptime 425.2 -> 533.7, waiters=0, connected=True | — |
| load:L04 | no memory / fd / thread growth after 50 sequential requests | **PASS** | — | MemoryCurrent 39.7MB -> 40.6MB, fds 11->11, threads 4->4 | — |
| load:L05 | 20 register+abandon cycles: waiters released promptly after client RST | **FAIL** | P1 | active_waiter_count 1s after abandon=20; fully drained 30.3s after start (timeout_s was 30) | Detect client disconnect (await request.is_disconnected() in a watcher task, or anyio cancel scope) and deregister immediately. Leaked waiters keep scanning, can CONSUME OTP emails meant for the retry, and inflate IMAP SEARCH load |
| load:L06 | count returns to 0 eventually (no permanent leak) | **PASS** | — | drained=29.13s | — |
| load:L07 | no fd/thread leak after abandoned connections | **PASS** | — | fds 11->11, threads 4->4, mem 41.2MB | — |
| load:L08 | no stale rows left in SQLite waiters table | **PASS** | — | waiters rows=(0,) | — |
| load:L09 | 30 concurrent waits: all 408, no 500, none dropped | **PASS** | — | statuses={408: 30} wall=30.3s max_elapsed=30.26s errors=[] | — |
| load:L10 | 30 concurrent finish near their 5s deadline (<=10s) | **FAIL** | P2 | wall=30.3s | Deadline not enforced around initial scan(); scans serialize on _scan_lock with one IMAP SEARCH each |
| load:L11 | after burst: waiters 0, IMAP still connected, no growth | **PASS** | — | waiters settled=0.0s connected=True mem 39.7->41.6MB fds 11->11 threads 4->4 | — |
| load:L14 | deadline honoured as concurrency grows (timeout_s=5): wall/max-elapsed/non-408 per N | **FAIL** | P1 | N -> (wall_s, max_elapsed_s, non408): {5: (5.0, 5.0, 0), 10: (14.6, 14.6, 0), 20: (19.6, 19.6, 0), 30: (31.7, 31.7, 0)} | Each wait() runs its own scan() under one asyncio lock holding a blocking IMAP round-trip (END IDLE + SEARCH + re-IDLE ~0.9s). Coalesce: one scanner task + per-waiter futures; run waiters' deadlines independently of scan; skip SEARCH when UIDNEXT unchanged |
| load:L12 | journal noise during load (imap error / scan failed / IDLE lines in last 20 min) | **INFO** | P2 | 4 lines |  |
| load:L13 | Gmail-side amplification: every /v1/wait registration triggers a full IMAP SEARCH (+ DONE/IDLE cycle) with no rate limit | **INFO** | P2 | 100 waits issued above => ~100+ SEARCH commands to Gmail in a few minutes | Coalesce scans (min interval 1-2s), serve cache hits without touching IMAP when nothing new (use EXISTS/UIDNEXT from IDLE), rate-limit /v1/wait per client |

## SKIPPED / not performed

- **R30** inject a real email and race two *live* waiters — needs the Gmail app password (to send) or an external allowed sender; credential use not authorised. Covered by simulated R01–R03/R16 on the same `Store.consume` path.
- **R31** live "mail before register" with a real mailbox message — only the broker can read the mailbox, and a match would mark a real message consumed in the production DB. Covered by R06.
- **B08** live bad-password test — skipped per instructions (Gmail lockout risk); breaker verified by code reading + in-process fake IMAP (B01–B07).
- **Live backoff escalation (1,2,4…60 s)** and **half-open TCP / IDLE-stall detection** — need blocking/dropping egress to port 993 (firewall change on the Beelink); verified by reading `imap_client.py` only (`I21`, `B09`).
- Gmail rate-limit behaviour under sustained load (L13) — not provoked deliberately; only noted.

## Reproduce

```bash
cd ~/workspace/otp-broker/adversarial
./run_remote.sh test_api_contract.py        # ~5.5 min (ends with a 300 s drain)
./run_remote.sh test_extraction_adversarial.py
./run_remote.sh test_races.py               # restarts the live service twice (~4 min); waits for 0 active waiters first
./run_remote.sh test_imap_resilience.py     # kills the IMAP socket 4x
./run_remote.sh test_security.py
./run_remote.sh test_load.py                # ~4 min
./bx.sh 'tar -C ~/otp-adv/results -cf - .' | tar -C results -xf - --no-same-owner && python3 make_report.py
```
