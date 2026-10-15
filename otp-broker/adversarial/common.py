"""Shared helpers for the adversarial suite (stdlib only; runs on the Beelink)."""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

BASE_HOST, BASE_PORT = "127.0.0.1", 8765
RESULTS_DIR = os.path.expanduser("~/otp-adv/results")
SRC_DIR = os.path.expanduser("~/otp-adv/src")


def now_iso(delta_s: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


def http_call(method: str, path: str, body=None, *, raw: bytes | None = None,
              headers: dict | None = None, timeout: float = 30.0, host=BASE_HOST, port=BASE_PORT):
    """Returns dict(status, json, text, elapsed, headers); status None on client-side timeout/error."""
    t0 = time.monotonic()
    hdrs = dict(headers or {})
    data = raw
    if body is not None and raw is None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    out = {"status": None, "json": None, "text": "", "elapsed": 0.0, "headers": {}, "error": None}
    try:
        c = http.client.HTTPConnection(host, port, timeout=timeout)
        c.request(method, path, body=data, headers=hdrs)
        r = c.getresponse()
        txt = r.read().decode("utf-8", "replace")
        out.update(status=r.status, text=txt, headers=dict(r.getheaders()))
        try:
            out["json"] = json.loads(txt)
        except ValueError:
            pass
        c.close()
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    out["elapsed"] = round(time.monotonic() - t0, 2)
    return out


def wait_req(domains=("nomatch.invalid",), timeout_s=1, kind="otp", after=None, **extra):
    body = {"sender_domains": list(domains), "after": after or now_iso(), "kind": kind, "timeout_s": timeout_s}
    body.update(extra)
    return body


def health():
    r = http_call("GET", "/health", timeout=10)
    return r["json"] or {}, r


def wait_for(pred, timeout: float, step: float = 0.25):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = pred()
        if v:
            return round(time.monotonic() - t0, 2)
        time.sleep(step)
    return None


def sh(cmd: str, timeout: float = 60) -> str:
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return (p.stdout + p.stderr).strip()


class Rec:
    """Collects per-case results, prints PASS/FAIL lines, dumps JSON for the report."""

    def __init__(self, area: str):
        self.area, self.cases = area, []
        print(f"=== {area} ===", flush=True)

    def case(self, cid: str, name: str, ok: bool | None, observed: str, sev: str = "P2",
             fix: str = "", status: str | None = None):
        st = status or ("PASS" if ok else "FAIL")
        self.cases.append(dict(id=cid, name=name, status=st, observed=observed,
                               severity=(sev if st in ("FAIL",) else "-"), fix=(fix if st == "FAIL" else fix),
                               fail_severity=sev))
        print(f"{st:5} [{cid}] {name} :: {observed}", flush=True)

    def skip(self, cid, name, reason):
        self.case(cid, name, None, reason, status="SKIP")

    def info(self, cid, name, observed, sev="P2", fix=""):
        self.case(cid, name, None, observed, sev=sev, fix=fix, status="INFO")

    def done(self):
        os.makedirs(RESULTS_DIR, exist_ok=True)
        with open(os.path.join(RESULTS_DIR, f"{self.area}.json"), "w") as f:
            json.dump(self.cases, f, indent=1)
        c = {}
        for x in self.cases:
            c[x["status"]] = c.get(x["status"], 0) + 1
        print(f"--- {self.area}: {c}", flush=True)


def ensure_idle(rec_note=None, timeout=330):
    """Block until the live broker has no active waiters (so counts are attributable to us)."""
    def idle():
        h, _ = health()
        return h.get("active_waiter_count", 1) == 0
    return wait_for(idle, timeout, 1.0)
