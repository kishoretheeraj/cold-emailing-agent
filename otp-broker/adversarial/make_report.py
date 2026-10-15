"""Builds ../TEST_REPORT.md from results/*.json (+ reviewer overrides below)."""
import glob, json, os
from collections import Counter

AREAS = [("api_contract", "1. API contract (test_api_contract.py)"), ("imap_resilience", "2. IMAP resilience (test_imap_resilience.py)"),
         ("extraction", "3. Extraction adversarial (test_extraction_adversarial.py)"), ("races", "4. Races / atomic consume / restart (test_races.py)"),
         ("security", "5. Security, read-only (test_security.py)"), ("load", "6. Load / leak (test_load.py)")]
# reviewer overrides after reading raw output: (area,id) -> dict(status,sev,observed_suffix,fix)
OV = {
 ("races", "R20a5"): dict(status="INFO", observed_suffix=" [TEST ARTIFACT: probed 0.2s after the new process started, before IMAP login finished. The long-waiter restart (R20b5) shows connected; script now polls up to 30s.]"),
 ("extraction", "E06"): dict(sev="P1"), ("extraction", "E27"): dict(sev="P1"),
 ("extraction", "E36"): dict(status="INFO", sev="P2", observed_suffix=" [Returned 222222 = the NEWEST code here (top-posted reply), so correct for this ordering; a bottom-posted/quoted-first message would return the stale quoted code. Expectation CONFLICT was too strict.]",
                              fix="Strip quoted text ('>' lines, 'On ... wrote:') before extraction."),
 ("extraction", "S20"): dict(sev="P2"),
 ("load", "L02"): dict(fix="Same root cause as L14: each wait does an IMAP round-trip (END IDLE/SEARCH/IDLE) under one lock; occasional 1-2s overshoot is Gmail latency + lock contention."),
 ("imap_resilience", "I12"): dict(sev="P2"),
 ("imap_resilience", "I21"): dict(observed_prefix="[CODE-REVIEW + live observation] "),
}
TOP5 = """\
1. **Stale/wrong OTP delivery paths (P0).** `R07`: `wait(after=now)` instantly returns an *unconsumed 4-minute-old* code because the 5-minute `CLOCK_SKEW_MARGIN` is applied to both the IMAP fetch and `_matches` (Gmail INTERNALDATE is server-side, so no skew margin is needed). Any retry/resend flow submits an expired code. Compounding it, `A38`/`L05`: a client that disconnects is never noticed — its waiter lives for the full `timeout_s` (up to 300 s, measured 300.3 s) and can **consume the OTP meant for the retry**. Fix: margin ≤15 s, cancel on disconnect, and consume only if the response is actually written.
2. **Extraction is wrong on both sides (P0/P1).** False positives: `E10` "zip code 90210" → returns `90210`; `E11` "promo code 2024"; `E30` "verification 10-15 minutes" → `1015`; `E21` hidden `display:none` decoy wins. False negatives that make the broker useless for common senders: code-before-keyword (`S10` Google, `S11` Microsoft, `E12`, `E13`), "one-time password" (`E14`), alphanumeric Greenhouse codes (`S03`/`E16`), `&nbsp;`/zero-width in codes (`E07d/e`, `E19`), `**482913**` / `[482913]` (`E33/E34`), digit-per-`<td>` codes (`E15b`). Non-ASCII digits are accepted (`E17/E18`).
3. **Deadlines are not enforced; concurrency collapses (P1).** `Broker.wait` awaits `scan()` *outside* `asyncio.wait_for`, and every wait runs its own serialized IMAP round-trip under one lock. Measured: 30 concurrent `timeout_s=5` waits all returned 408 only after **30–32 s** (`L09/L10/L14`), 10 concurrent after 14.6 s on the second run; simulated hung IMAP (6 s) turned `timeout_s=2` into 6 s and a `timeout_s=1` waiter into 10 s (`R13/R14`). A real IMAP stall means unbounded caller hangs. Also one hostile `after` (`0001-01-01` → HTTP 500 `A18`; `0001-01-02` makes `scan()` raise for *every* pending waiter, `R12`).
4. **Restart/deploy behaviour (P1).** uvicorn drains in-flight `/v1/wait` calls on SIGTERM, so `systemctl restart` blocked **38 s** with a 40 s waiter and **90 s then SIGKILL** with a 150 s waiter (`R20a2/b2`); the SIGKILLed client got `RemoteDisconnected` after 92 s (`R20b1`). The orphan-reload path itself works (`R10`, `R20b3`: row recovered, count drained after expiry) but only helps a client that retries with identical parameters. Add `--timeout-graceful-shutdown`, `TimeoutStopSec`, and cancel waiters with an explicit 503 on shutdown.
5. **Trust boundaries (P0/P1).** `L05` (extraction): `https://evil.com\\@verify.example.com/verify?token=1` passes the host allowlist (Python sees host `verify.example.com`; browsers treat `\\` as `/` and go to `evil.com`) — the returned URL is meant to be opened by a browser agent. `S15`: sender is trusted from the `From:` header alone (no DKIM/DMARC `Authentication-Results` check). `S13`: the API has no authentication — any local user/container can request OTPs and magic links for any domain. Plus `S12/S11`: no `MemoryMax`, full capability bounding set, systemd exposure score 8.3 EXPOSED.
"""

def load():
    out = {}
    for key, _ in AREAS:
        p = f"results/{key}.json"
        out[key] = json.load(open(p)) if os.path.exists(p) else []
    return out

def eff(area, c):
    o = OV.get((area, c["id"]), {})
    st = o.get("status", c["status"]); sev = o.get("sev", c["fail_severity"])
    obs = o.get("observed_prefix", "") + c["observed"] + o.get("observed_suffix", "")
    fix = o.get("fix", c["fix"])
    return st, sev, obs, fix

def esc(t, n=420):
    t = " ".join(str(t).split())
    t = t if len(t) <= n else t[: n - 1] + "…"
    return t.replace("|", "\\|")

data = load()
L = []
L.append("# OTP Broker — Adversarial Test Report\n")
L.append("**Date:** 2026-10-12  |  **Target:** `otp-broker` on the Beelink (`127.0.0.1:8765`, deployed files byte-identical to `~/workspace/otp-broker/*.py` by SHA-256)  |  **Scope:** find gaps, no source changes.\n")
L.append("## How this was run\n")
L.append("""- Scripts live in `~/workspace/otp-broker/adversarial/` (one per area, plus `common.py`, `run_remote.sh`, `bx.sh`, `make_report.py`); `run_remote.sh <script>` tars the scripts + source to the Beelink and runs them there (`~/otp-adv/`, scratch venv with fastapi/bs4/httpx; nothing installed system-wide, no repo or `/opt/otp-broker` file modified). Raw per-case JSON is in `adversarial/results/`.
- **Live** = real HTTP against the running service (non-matching `*.invalid` sender domains only, so no real mail could be consumed or delivered). **In-process** = the repo's `Broker`/`Store`/`extract`/`Mailbox` imported with a fake mailbox/fake IMAP and a temp SQLite file — used wherever a real email would have to be injected, because that needs the Gmail app password (not read, not used).
- **Disruptive actions actually performed on the live service:** 2× `systemctl restart otp-broker` (R20; gated on zero active waiters; the second one hit systemd's 90 s SIGKILL), 4× `sudo ss -K` on the single IMAP socket (I10/I20). A real client (`jobicy.com`, 300 s waiter) was seen in the journal earlier in the evening; tests waited for `active_waiter_count==0` before anything disruptive. No bad-password attempt was made against Gmail.
- Severity: **P0** security / data loss / wrong code; **P1** reliability / crash / hang / silent failure; **P2** minor / spec deviation. Severity is shown for FAIL (and INFO) rows. `CODE-REVIEW` = conclusion drawn from reading `imap_client.py`, not from an executed failure.
- Test-methodology notes: `S06` first produced false positives (PIDs in `Started server process [184584]`, `Killing process 186153`); the scan now strips PIDs/timestamps/UUIDs/Message-IDs/ip:port and reports 0 hits. `R20a5` is a test timing artifact (see row).
""")
# summary
L.append("## Summary\n")
L.append("| Area | PASS | FAIL | SKIP | INFO | FAIL P0 | FAIL P1 | FAIL P2 |\n|---|---|---|---|---|---|---|---|")
tot = Counter()
for key, title in AREAS:
    c = Counter(); sv = Counter()
    for x in data[key]:
        st, sev, _, _ = eff(key, x); c[st] += 1
        if st == "FAIL": sv[sev] += 1
    tot.update({"PASS": c["PASS"], "FAIL": c["FAIL"], "SKIP": c["SKIP"], "INFO": c["INFO"], "P0": sv["P0"], "P1": sv["P1"], "P2": sv["P2"]})
    L.append(f"| {title} | {c['PASS']} | {c['FAIL']} | {c['SKIP']} | {c['INFO']} | {sv['P0']} | {sv['P1']} | {sv['P2']} |")
L.append(f"| **Total** | **{tot['PASS']}** | **{tot['FAIL']}** | **{tot['SKIP']}** | **{tot['INFO']}** | **{tot['P0']}** | **{tot['P1']}** | **{tot['P2']}** |\n")
L.append("What held up well: all validation of `timeout_s`≤0 / wrong types / bad `kind` / missing fields returns 422; `/health` schema and types are exact; atomic consume is solid (R01–R05, R16: one winner under 10–32 concurrent claimants, 50 notify threads, FastAPI-level race); register-then-search finds mail that arrived before registering (R06); newest-first ordering (R08); orphan reload from SQLite (R10); IMAP drop recovery in ~1.4 s with correct re-SELECT (I10–I22); the 3-strike auth breaker trips at exactly 3 and stops (B01–B04, offline sim); bound to loopback only, password file and DB are 600, service runs as `jobagent` with no effective caps, no OTP-like values in the journal (S01–S10).\n")
L.append("## Top 5 gaps\n")
L.append(TOP5)
L.append("## Other notable findings (not in the top 5)\n")
L.append("""- `A35/A36`: `timeout_s` 301/99999 is silently clamped to 300 (confirmed: abandoned 99999 s wait drained at 300.3 s) instead of rejected.
- `A22`, `A05/A05b`, `A07`: lenient input (unknown fields ignored — typos such as `allowed_link_host` silently fall back to defaults; `""`/`"*"` domains and `"1"` as timeout accepted). `A34/S14`: `/docs` and `/openapi.json` exposed.
- `I02/B09` (CODE-REVIEW): no TCP keepalive; IDLE re-issued only every 25 min, so a silently dead connection can go unnoticed for up to ~26 min; `last_successful_poll` is not refreshed by IDLE cycles (I observed it fresh only because tests had just triggered fetches). `B05/B06`: tripped breaker needs manual restart; non-`AUTHENTICATIONFAILED` login errors (missing/unreadable password file, `[ALERT]`) never surface in `/health`.
- `R09`, `R11`, `R15`: conflict message is never consumed (re-409s later waiters); `active_waiter_count` includes expired orphans until a scan runs (live, the count did drain 58 s after expiry, so something triggers scans); a failing mailbox is reported as 408 "timeout".
- `S05d`: orphan OTP results are stored in plaintext in `waiters.result`; `consumed` is never purged. `S05e`: `.venv` is 775.
""")
L.append("## Detailed results\n")
for key, title in AREAS:
    L.append(f"### {title}\n")
    L.append("| ID | Case | Result | Sev | Observed | Fix recommendation |\n|---|---|---|---|---|---|")
    for x in data[key]:
        st, sev, obs, fix = eff(key, x)
        sevcol = sev if st in ("FAIL", "INFO") else "—"
        L.append(f"| {key}:{x['id']} | {esc(x['name'], 160)} | **{st}** | {sevcol} | {esc(obs)} | {esc(fix, 380) if st in ('FAIL','INFO') else '—'} |")
    L.append("")
L.append("## SKIPPED / not performed\n")
L.append("""- **R30** inject a real email and race two *live* waiters — needs the Gmail app password (to send) or an external allowed sender; credential use not authorised. Covered by simulated R01–R03/R16 on the same `Store.consume` path.
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
""")
open("../TEST_REPORT.md", "w").write("\n".join(L))
print("\n".join(L[:30])[:3000])
