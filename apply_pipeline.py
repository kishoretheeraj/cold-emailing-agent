#!/usr/bin/env python3
"""Job-application pipeline orchestrator (DRY RUN by default).

queue -> new tab in the already-running Chrome (CDP) -> existing fillers ->
email verification via OTP broker -> final step -> result log.

live_submit() is wired but fails closed: DRY_RUN is the default, and a live run additionally needs
/etc/jobagent24/pipeline_live_ok plus the armed environment flag set to exactly "1" (see live_gate).
Exactly-once: every click is preceded by a durable ledger intent (submit_guard.SubmitLedger); an
unresolved intent blocks its job and posting until a human resolves it (reconcile_submits.py).
"""
import argparse
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import logging
import math
import os
import random
import re
import signal
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

import ats_fillers
import config
import email_verification
import submit_guard
import universal_filler
import workday_adapter
from answers.workauth import (  # noqa: F401 - SKIP/pick_option stay importable from here
    SKIP, WORK_AUTH_FACTS, classify_question, pick_option, pick_option_ex)

# ---------------------------------------------------------------- settings
DRY_RUN = os.environ.get("APPLY_DRY_RUN", "1") != "0"  # only exact "0" disables
LIVE_OK_FILE = "/etc/jobagent24/pipeline_live_ok"
SUBMIT_LEDGER_PATH = os.environ.get("APPLY_SUBMIT_LEDGER_PATH", "/var/lib/jobagent24/submit_ledger.jsonl")
SUBMIT_ARTIFACTS_DIR = os.environ.get("APPLY_SUBMIT_ARTIFACTS_DIR", "/var/lib/jobagent24/submit_artifacts")
CDP_URL = os.environ.get("APPLY_CDP_URL", "http://127.0.0.1:9222")
QUEUE_PATH = os.environ.get("APPLY_QUEUE_PATH", "/opt/jobagent24/job_queue.json")
STATE_PATH = os.environ.get("APPLY_STATE_PATH", "/var/lib/jobagent24/pipeline_state.json")
RESULTS_PATH = os.environ.get("APPLY_RESULTS_PATH", "/opt/jobagent24/application_results.jsonl")
RESUME_PATH = os.environ.get("APPLY_RESUME_PATH", "/opt/jobagent24/resume_master.pdf")
LOG_PATH = os.environ.get("APPLY_LOG_PATH", "")  # optional log file (stdout is always on)
PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
FORM_PATH = os.path.join(PIPELINE_DIR, "test_form.html")
IDLE_S, CDP_BACKOFF_S, CHALLENGE_WAIT_S, MAX_ATTEMPTS = 30, 60, 30, 2
LEASE_S = 900  # a claim is valid for 15 min and renewed at every phase marker

STANDARD_FIELD_VALUES = {
    "name": "Kishore Theeraj Vasudevan Jaya",
    "first_name": "Kishore Theeraj",
    "last_name": "Vasudevan Jaya",
    "email": "kishoretheerajvj@gmail.com",
    "phone": "+1 603-322-0535",
    "location": "Hanover, NH",
    "linkedin": "linkedin.com/in/kishoretheeraj",
}
KEY_ALIASES = {"full_name": "name", "phone_number": "phone", "linkedin_url": "linkedin"}
ATS_FILLERS = {
    "greenhouse": ats_fillers.fill_greenhouse,
    "lever": ats_fillers.fill_lever,
    "ashby": ats_fillers.fill_ashby,
}
KNOWN_PLATFORMS = ("generic", "greenhouse", "lever", "ashby", "workday", "smartrecruiters",
                   "workable", "oracle", "icims", "test_local")

# Confirmed facts the work-authorization answers are derived from (never guessed).
FACTS = dict(WORK_AUTH_FACTS)  # single source of truth: answers/workauth.py

STOP = threading.Event()
log = logging.getLogger("apply")

# Per-process identity used on claims; a claim is only ever touched by its owner.
OWNER_ID = f"{uuid.uuid4().hex}:{os.getpid()}"

# Attempt phases, in order. "navigating" is persisted BEFORE the first navigation: from that point
# the remote site has seen us (cookies, tracking, server-side drafts), so nothing at or after it
# ever auto-requeues. Only a crash while still at "started" (nothing sent anywhere) is retryable.
PHASES = ("started", "navigating", "navigated", "filled", "uploaded", "otp_requested",
          "magic_link_open", "otp_entered", "final_reached", "submit_clicked")
REQUEUE_OK_PHASES = ("started",)
BAD_OUTCOMES = ("failed", "blocked", "unsupported", "needs_reconciliation", "dry_run_incomplete")
STATE_FOR_STATUS = {"dry_run_ok": "done", "submitted": "done"}  # all others map to themselves
STATE_STATUSES = frozenset(("pending", "in_progress", "done", "failed", "blocked", "unsupported",
                            "needs_reconciliation", "dry_run_incomplete", "submitted_unconfirmed",
                            "needs_review", "skipped"))


class Shutdown(BaseException):
    """Raised at the next pacing point after SIGTERM/SIGINT."""


class StateCorrupt(BaseException):
    """The state file is unreadable or has the wrong shape. BaseException so the generic
    'except Exception' job handlers can never swallow it into a normal job failure."""


class StorageError(BaseException):
    """A private-storage guarantee (0600 on lock/state/results/log) could not be established.
    BaseException so no generic handler turns it into a normal job failure."""


class QueueError(Exception):
    """The queue could not be loaded or has invalid entries."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class JobEnd(Exception):
    """Controlled early end of a job with an explicit status."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


# ---------------------------------------------------------------- logging / redaction
def ref(value):
    """Stable short opaque reference for an identifier/free text that must not reach a log."""
    return "h-" + hashlib.sha256(str(value).encode()).hexdigest()[:8]


def _ev_value(key, value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    s = str(value)
    if key in ("company", "question"):
        return ref(s) if s else ""
    if key == "ids":
        return ",".join(ref(i) for i in s.split(",") if i)
    if key == "platform":
        return s if s.lower() in KNOWN_PLATFORMS or not s else ref(s)
    return redact(s, strict=False)


def ev(name, job_id="-", **kv):
    """Structured log line. Every argument is redacted: job ids/company/question text become opaque
    refs, anything else passes through the secret/PII/URL scrubber."""
    parts = [f"event={name}", f"job_id={job_id if job_id == '-' else ref(job_id)}"]
    for k, v in kv.items():
        s = _ev_value(k, v)
        if not s or re.search(r"[\s=]", s):
            s = json.dumps(s)
        parts.append(f"{k}={s}")
    log.info(" ".join(parts))


_TOKEN_SEG = re.compile(r"[A-Za-z0-9_-]{21,}")

def clean_url(url):
    """scheme://hostname[:port]/path only: no userinfo, query or fragment ever reach a log.
    Path segments that look like tokens (>20 chars of [A-Za-z0-9_-]) are redacted."""
    try:
        p = urlsplit(str(url or ""))
        host = p.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = f":{p.port}" if p.port else ""
        path = "/".join("[redacted]" if _TOKEN_SEG.fullmatch(seg) else seg
                        for seg in p.path.split("/"))
        return f"{p.scheme}://{host}{port}{path}"
    except ValueError:
        return "[unparseable-url]"


_SECRETS = set()  # runtime secrets (e.g. the OTP in flight) to scrub from error text


def register_secret(value):
    if value and len(str(value)) >= 4:
        _SECRETS.add(str(value))


def forget_secrets():
    _SECRETS.clear()


def _secret_variants():
    out = set(_SECRETS)
    for v in STANDARD_FIELD_VALUES.values():
        out.add(v)
        digits = re.sub(r"\D", "", v)
        if len(digits) >= 7:
            out.add(digits)
            if len(digits) >= 10:
                d = digits[-10:]
                out.update((d, f"{d[:3]}-{d[3:6]}-{d[6:]}", f"({d[:3]}) {d[3:6]}-{d[6:]}", f"{d[:3]} {d[3:6]} {d[6:]}"))
    return sorted((s for s in out if s), key=len, reverse=True)


def redact(text, strict=True):
    """Scrub a free-text message: userinfo, query/fragment, known PII, OTP-like runs, long tokens.
    strict=False (structured log values) keeps plain numbers/ids but still removes secrets/PII."""
    s = str(text)
    s = re.sub(r"[^\s/@]+:[^\s/@]*@", "[redacted]@", s)                       # user:pass@
    s = re.sub(r"[#?]\S*", "", s)                                             # anything after # or ?
    for v in _secret_variants():
        s = re.sub(re.escape(v), "[redacted]", s, flags=re.I)
    s = re.sub(r"(?i)\b(password|passwd|pwd|passcode|otp|token|secret|code)\b\s*[=:]\s*\S+", r"\1=[redacted]", s)
    if not strict:
        return s
    s = re.sub(r"\b\d{3}[ -]\d{3,5}\b", "[code]", s)
    s = re.sub(r"\b\d{4,8}\b", "[code]", s)
    s = re.sub(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,8}\b", "[code]", s)
    s = re.sub(r"[A-Za-z0-9_+=\-]{24,}", "[token]", s)
    return s


def describe(exc):
    """Class name plus a short, redacted first-line message."""
    msg = (str(exc).strip().splitlines() or [""])[0]
    return f"{type(exc).__name__}: {redact(msg)[:120]}"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- URL policy (destination allowlist)
EXTRA_ATS_SUFFIXES = ("smartrecruiters.com", "workable.com", "icims.com", "oraclecloud.com")
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
LOCAL_ORIGINS = set()  # {(host, port)} of the local fixture server, registered at startup


def parse_hosts_env(value):
    """APPLY_APPROVED_HOSTS: comma-separated bare hostnames (suffix match). Junk entries are dropped."""
    out = []
    for item in (value or "").split(","):
        h = item.strip().lower().rstrip(".")
        h = h[2:] if h.startswith("*.") else h.lstrip(".")
        if h and re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", h):
            out.append(h)
    return out


def build_approved_origins():
    ats = tuple(getattr(universal_filler, "ATS_HOST_SUFFIXES", ()))
    return tuple(sorted(set(parse_hosts_env(os.environ.get("APPLY_APPROVED_HOSTS", ""))) | set(ats) | set(EXTRA_ATS_SUFFIXES)))


APPROVED_ORIGINS = build_approved_origins()  # host suffixes; the local fixture origin is added at runtime
ACTIVE_APPROVED = None  # explicit approved suffixes for the job being run (set by run_job); None -> global


def approved_for_job(platform):
    """Explicit approved-host list for ONE job: a known ATS platform gets only its own suffixes (when
    any match) plus APPLY_APPROVED_HOSTS; 'generic' gets every ATS suffix plus APPLY_APPROVED_HOSTS.
    The company's own careers domain is never implied: it must be listed in APPLY_APPROVED_HOSTS.
    Nothing from the queue entry itself can widen this list."""
    env = tuple(parse_hosts_env(os.environ.get("APPLY_APPROVED_HOSTS", "")))
    plat = str(platform or "generic").lower()
    own = tuple(s for s in APPROVED_ORIGINS if plat != "generic" and plat in s)
    base = own if own else APPROVED_ORIGINS
    return tuple(sorted(set(base) | set(env)))


def register_local_origin(base_url):
    p = urlsplit(base_url)
    LOCAL_ORIGINS.add(((p.hostname or "").lower(), p.port or (443 if p.scheme == "https" else 80)))


def _is_ip(host):
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def analyze_url(url):
    """(problem, scheme, host, port). problem is None for a well-formed URL with an acceptable scheme."""
    if not isinstance(url, str) or not url or re.search(r"[\s\x00-\x1f]", url):
        return "url_invalid", "", "", None
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        return "url_invalid", "", "", None
    scheme, host = p.scheme.lower(), (p.hostname or "").lower().rstrip(".")
    if "@" in p.netloc:
        return "url_userinfo_rejected", scheme, host, port
    if not host:
        return "url_invalid", scheme, host, port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    if scheme == "https" or (scheme == "http" and host in LOCAL_HOSTS):
        return None, scheme, host, port
    return "url_scheme_not_allowed", scheme, host, port


def origin_allowed(url):
    """True only for an https ATS/approved host (default port) or the registered local fixture origin."""
    problem, scheme, host, port = analyze_url(url)
    if problem:
        return False
    if host in LOCAL_HOSTS:
        return (host, port) in LOCAL_ORIGINS
    if scheme != "https" or port != 443 or _is_ip(host):
        return False
    approved = APPROVED_ORIGINS if ACTIVE_APPROVED is None else ACTIVE_APPROVED
    return any(host == s or host.endswith("." + s) for s in approved)


def resolve_job_url(url, test_base):
    return test_base + url[len("{TEST_BASE}"):] if url.startswith("{TEST_BASE}") else url


def validate_job_url(url):
    """Queue URL check BEFORE the first navigation; the URL is never its own trust reference."""
    problem = analyze_url(url)[0]
    if problem:
        raise JobEnd("blocked", problem)
    if not origin_allowed(url):
        raise JobEnd("blocked", "host_not_allowed")


def _target_urls(target):
    urls = [str(getattr(target, "url", "") or "")]
    page = getattr(target, "page", None)
    if page is not None and not callable(page):
        urls.append(str(getattr(page, "url", "") or ""))
    parent = getattr(target, "parent_frame", None)
    for _ in range(10):
        if parent is None or callable(parent):
            break
        urls.append(str(getattr(parent, "url", "") or ""))
        parent = getattr(parent, "parent_frame", None)
    return urls


def assert_destination(target, job_url=None, scope=None, jid="-"):
    """Re-validate where a page/frame (and its page and ancestors) currently is. `job_url` is NOT a
    trust reference; only the approved-origin allowlist is. Also surfaces any popup violation."""
    if scope is not None:
        scope.check()
    for u in _target_urls(target):
        if not origin_allowed(u):
            ev("destination_blocked", jid, url=clean_url(u))
            raise JobEnd("blocked", "host_not_allowed")


# ---------------------------------------------------------------- network policy (route enforcement)
STATIC_BLOCKED_TYPES = ("websocket", "eventsource", "ping")


def net_decision(method, resource_type, url, dry_run=None, main_frame=True):
    """(allow, reason) for one request. Dry run: default deny, only GET/HEAD to approved origins;
    nothing can be sent with POST/PUT/PATCH/DELETE; websockets, event streams and beacons never.
    Live: only top-level navigations are held to the approved origins (redirect hops included)."""
    dry = DRY_RUN if dry_run is None else dry_run
    if (urlsplit(url).scheme or "").lower() in ("data", "blob", "about"):
        return True, ""
    if not dry:
        if resource_type == "document" and main_frame and not origin_allowed(url):
            return False, "host_not_allowed"
        return True, ""
    if not origin_allowed(url):
        return False, "host_not_allowed"
    if str(method).upper() not in ("GET", "HEAD"):
        return False, "method_not_allowed"
    if resource_type in STATIC_BLOCKED_TYPES:
        return False, "resource_type_not_allowed"
    return True, ""


def make_route_handler(scope):
    """Context-level handler, the continuous enforcement point: every request of a page this job
    owns (all resource types, every redirect hop, frames, popups, service-worker traffic) is
    decided here; other tabs are left alone."""
    def handler(route, request):
        try:
            try:
                frame = request.frame
                page = frame.page
            except Exception:  # service-worker / detached request: owner unknown -> policy still applies
                frame = page = None
            if page is not None and not scope.owns(page):
                # Pre-existing tabs (existed before job) are allowed through;
                # only truly unattributed newcomers are blocked.
                # This branch runs BEFORE the SW fail-closed abort below so a
                # violated job never disturbs foreign tabs' page traffic.
                # (Frameless requests, e.g. a foreign worker's own fetch, have no
                # attributable page and are still fail-closed once violated.)
                if page in scope.before:
                    pass  # pre-existing tab, allow through normal policy
                else:
                    # Fail closed: unattributed newcomers get blocked, not bypassed.
                    scope.note_violation("unattributed_tab_blocked")
                    route.abort("blockedbyclient")
                    return
            elif scope.sw_violations:
                # Fail closed once a service worker was flagged for this job: abort
                # this job's owned pages and any frameless (owner-unknown) requests.
                # Pre-existing tabs' page traffic above is unaffected.
                route.abort("blockedbyclient")
                return
            top = frame is not None and getattr(frame, "parent_frame", None) is None
            allow, reason = net_decision(request.method, request.resource_type, request.url,
                                         main_frame=top or frame is None)
            if allow:
                if request.method.upper() not in ("GET", "HEAD"):
                    scope.mutating += 1
                from urllib.parse import urljoin as _urljoin
                _cur = request.url
                _resp = route.fetch(max_redirects=0)
                _hops = 0
                _bad = None
                while 300 <= _resp.status < 400 and _resp.headers.get("location"):
                    _hops += 1
                    _cur = _urljoin(_cur, _resp.headers["location"])
                    _ok, _why = net_decision("GET", request.resource_type, _cur,
                                             main_frame=top or frame is None)
                    if _hops > 10 or not _ok:
                        _bad = _why if not _ok else "too_many_redirects"
                        break
                    _meth = "GET" if _resp.status in (301, 302, 303) else request.method
                    # Block cross-origin redirects: they can leak credentials via
                    # inherited headers and defeat origin pinning via fulfill().
                    try:
                        _orig_parts = urlsplit(request.url)
                        _cur_parts = urlsplit(_cur)
                        _orig_origin = (_orig_parts.scheme, (_orig_parts.hostname or "").lower(),
                                        _orig_parts.port or {"http": 80, "https": 443}.get(_orig_parts.scheme))
                        _cur_origin = (_cur_parts.scheme, (_cur_parts.hostname or "").lower(),
                                       _cur_parts.port or {"http": 80, "https": 443}.get(_cur_parts.scheme))
                        if _orig_origin != _cur_origin:
                            _bad = "cross_origin_redirect"
                            break
                    except ValueError:
                        _bad = "redirect_url_error"
                        break
                    # Strip sensitive headers before following same-origin redirect
                    _safe_headers = {k: v for k, v in request.headers.items()
                                     if k.lower() not in ("authorization", "cookie", "proxy-authorization")}
                    if _meth == "GET":
                        _safe_headers = {k: v for k, v in _safe_headers.items()
                                         if k.lower() not in ("content-type", "content-length")}
                    _resp = route.fetch(url=_cur, method=_meth, headers=_safe_headers, max_redirects=0)
                if _bad:
                    route.abort("blockedbyclient")
                else:
                    route.fulfill(response=_resp)
                return
            scope.blocked += 1
            ev("net_blocked", scope.jid, method=request.method, rtype=request.resource_type,
               host=urlsplit(request.url).hostname or "", reason=reason)
            if request.resource_type == "document" and page is not None and top:
                if reason == "host_not_allowed":
                    scope.note_violation("host_not_allowed" if page is scope.main else "popup_host_not_allowed")
                elif reason == "method_not_allowed":
                    scope.note_violation("submit_attempt_blocked")
            route.abort("blockedbyclient")
        except Exception:
            try:
                route.abort("blockedbyclient")
            except Exception:
                pass
    return handler


DRY_RUN_JS = r"""
(() => {
  const deny = () => { throw new Error('blocked by dry-run policy'); };
  try { window.WebSocket = function () { deny(); }; } catch (e) {}
  try { window.EventSource = function () { deny(); }; } catch (e) {}
  try { if (navigator.sendBeacon) navigator.sendBeacon = () => false; } catch (e) {}
  try { if (navigator.serviceWorker) navigator.serviceWorker.register = () => Promise.reject(new Error('blocked')); } catch (e) {}
  try { window.addEventListener('submit', (e) => { e.preventDefault(); e.stopImmediatePropagation(); }, true); } catch (e) {}
})();
"""


# Live-mode page guard: the pipeline never needs WebSockets or service workers, so both are
# blocked at page level in live mode as well as dry run. Unlike DRY_RUN_JS this does NOT block
# form submits (live mode must submit).
LIVE_NET_GUARD_JS = r"""
(() => {
  const deny = () => { throw new Error('blocked by network policy'); };
  try { window.WebSocket = function () { deny(); }; } catch (e) {}
  try { if (navigator.serviceWorker) navigator.serviceWorker.register = () => Promise.reject(new Error('blocked')); } catch (e) {}
})();
"""


class JobScope:
    """Everything one job opened in the shared context: pages (diff vs. 'before' + 'page' events),
    the route and the listeners. Only pages THIS job created (its tab and its popups) are ever
    closed; pre-existing tabs and unattributed newcomers are policed but never closed. Cleanup
    closes pages first, while the route still enforces, and only then removes the hooks."""

    def __init__(self, context, jid="-"):
        self.context, self.jid = context, jid
        self.before, self.owned, self.closable, self.main = [], [], [], None
        self._explicit_adopt = set()  # pages explicitly created by open_page()
        self.violation, self.blocked = None, 0
        self.mutating = 0  # mutating (non-GET/HEAD) requests the route let through
        self.sw_violations = []  # service-worker URLs flagged for THIS job only (never global)
        self._route = self._listener = self._sw_listener = None
        self._hooks = []  # (page, event, callback)

    def attach(self):
        self.before = list(self.context.pages)  # tabs that existed before this job: never touched
        self._listener = self.adopt
        self.context.on("page", self._listener)
        self._sw_listener = self._service_worker
        self.context.on("serviceworker", self._sw_listener)
        self._route = make_route_handler(self)  # dry run AND live: documents/redirects are always held
        self.context.route("**/*", self._route)

    def _service_worker(self, worker):
        url = str(getattr(worker, "url", "") or "")
        if not self._job_has_page_on_origin(url):
            # The worker's origin hosts none of this job's pages: it belongs to a
            # foreign tab. Service workers are origin-scoped and cannot observe this
            # job's pages, so flagging it would block the job over unrelated tabs.
            # (A worker that activates after the job opens a page on its origin
            # fires this event then and is flagged at that point.)
            return
        if not origin_allowed(url):
            # The job has a page on a non-approved origin AND a worker for it:
            # the dangerous case (exfiltration outside the trust boundary).
            self.note_violation("service_worker_unapproved_origin")
        else:
            self.note_violation("service_worker")
        self.sw_violations.append(url)

    def _job_has_page_on_origin(self, url):
        """True if any page this job owns is on the worker's origin."""
        def _origin(u):
            from urllib.parse import urlsplit as _us
            p = _us(str(u or ""))
            return (p.scheme, p.netloc)
        w_origin = _origin(url)
        for pg in list(self.owned):
            with contextlib.suppress(Exception):
                if _origin(pg.url) == w_origin:
                    return True
        return False

    def owns(self, page):
        if page in self.owned:
            return True
        if page in self.before:
            return False
        _op = None
        with contextlib.suppress(Exception):
            _op = page.opener()
        # Adoption requires a real opener chain into owned. The job's first tab
        # must be registered explicitly via adopt(), never implicitly here.
        # (Old guard `if self.owned and ...` was skipped when owned was empty,
        # letting any openerless tab fall through to adopt().)
        if _op is None or _op not in self.owned:
            return False
        self.adopt(page)
        return True

    def note_violation(self, why):
        self.violation = self.violation or why

    def _opened_by_job(self, page):
        try:
            op = page.opener() if callable(getattr(page, "opener", None)) else None
        except Exception:
            op = None
        return op is not None and op in self.owned

    def adopt(self, page):
        # Returns True if adopted (or already owned), False if rejected.
        # Setup (closable, scripts, listeners, policing) runs for all adopted pages.
        adopted = False
        if page in self.owned:
            adopted = True
        elif page in self._explicit_adopt:
            self.owned.append(page)
            adopted = True
        else:
            try:
                _op = page.opener() if callable(page.opener) else page.opener
            except Exception:
                _op = "error"
            # Not explicitly created and no known parent: never adopt.
            # (Old guard `if self.owned and ...` was skipped when owned was empty,
            # letting any openerless tab fall through to adopt().)
            if _op is not None and _op != "error" and _op in self.owned:
                self.owned.append(page)
                adopted = True
        if not adopted:
            return False
        # Setup runs for all adopted pages (fixes unreachable-code issue)
        if self._opened_by_job(page) and page not in self.closable:
            self.closable.append(page)
        if DRY_RUN:
            with contextlib.suppress(Exception):
                page.add_init_script(DRY_RUN_JS)
        else:
            with contextlib.suppress(Exception):
                page.add_init_script(LIVE_NET_GUARD_JS)
        # WebSockets bypass context.route - must block at browser level in BOTH modes.
        # Fail closed: if route_web_socket is unavailable or fails, abort the job.
        _ws_blocked = False
        if callable(getattr(page, "route_web_socket", None)):
            try:
                page.route_web_socket("**/*", lambda ws: ws.close())
                _ws_blocked = True
            except Exception:
                pass
        if not _ws_blocked:
            # Cannot enforce WebSocket policy - fail closed rather than
            # allowing potential PII exfiltration via WebSocket
            raise JobEnd("blocked", "websocket_interception_unavailable")
        if callable(getattr(page, "on", None)):
            cb = lambda frame, p=page: self._navigated(p, frame)  # noqa: E731
            with contextlib.suppress(Exception):
                page.on("framenavigated", cb)
                self._hooks.append((page, "framenavigated", cb))
        self._police(page)
        return True

    def _navigated(self, page, frame):
        if getattr(frame, "parent_frame", None) is not None:
            return
        self._police(page, str(getattr(frame, "url", "") or ""))

    def _police(self, page, url=None):
        try:
            if page.is_closed():
                return
            url = page.url or "" if url is None else url
        except Exception:
            return
        if url in ("", "about:blank") or origin_allowed(url):
            return
        if page is self.main:
            self.note_violation("host_not_allowed")
        else:
            self.note_violation("popup_host_not_allowed")
            if page in self.closable:
                with contextlib.suppress(Exception):
                    page.close()

    def open_page(self):
        page = self.context.new_page()
        self._explicit_adopt.add(page)
        self.adopt(page)
        if page not in self.closable:
            self.closable.append(page)
        self.main = page
        return page

    def refresh(self):
        for p in list(self.context.pages):
            if p not in self.before and p not in self.owned:
                self.adopt(p)

    def check(self):
        self.refresh()
        for p in list(self.owned):
            self._police(p)
        if self.violation:
            ev("scope_violation", self.jid, reason=self.violation)
            raise JobEnd("blocked", self.violation)

    def detach(self):
        if self._listener is not None:  # never adopt anything for a scope that was not attached
            with contextlib.suppress(Exception):
                self.refresh()
        # 1) close what this job opened FIRST, while the route is still enforcing the policy
        for p in list(self.closable):
            with contextlib.suppress(Exception):
                if not p.is_closed():
                    p.close()
        # 2) only then remove the hooks and the route
        for page, event, cb in self._hooks:
            with contextlib.suppress(Exception):
                page.remove_listener(event, cb)
        self._hooks = []
        for event, cb in (("page", self._listener), ("serviceworker", self._sw_listener)):
            if cb is not None:
                with contextlib.suppress(Exception):
                    self.context.remove_listener(event, cb)
        if self._route is not None:
            _still = []
            for _p in list(self.closable):
                try:
                    if not _p.is_closed():
                        _still.append(_p)
                except Exception:
                    _still.append(_p)
            if _still:
                raise RuntimeError('cleanup: owned pages still open; enforcement kept')
            with contextlib.suppress(Exception):
                self.context.unroute("**/*", self._route)


# ---------------------------------------------------------------- state / results
def fsync_dir(path):
    fd = os.open(os.path.dirname(os.path.abspath(path)) or ".", os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_private(path):
    """chmod 0600 or abort: a lock/state/results/log file that cannot be made private is a hard error."""
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        ev("storage_private_failed", file=os.path.basename(path), reason=type(exc).__name__)
        raise StorageError(f"cannot make {os.path.basename(path)} owner-only ({type(exc).__name__})")
    return True


def _finite_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and (abs(v) < 1e15 and math.isfinite(v))


def validate_state(state):
    """None when the state structure is sound, else a short reason (checked before any processing)."""
    if not isinstance(state, dict):
        return "not_a_dict"
    for jid, rec in state.items():
        if not isinstance(jid, str) or not isinstance(rec, dict):
            return "bad_record"
        if not isinstance(rec.get("status"), str) or rec.get("status") not in STATE_STATUSES:
            return "bad_status"
        a = rec.get("attempts")
        if not isinstance(a, int) or isinstance(a, bool) or a < 0:
            return "bad_attempts"
        for key in ("attempt_id", "owner", "updated_at", "reconcile_reason"):
            if key in rec and not isinstance(rec[key], str):
                return f"bad_{key}"
        if "phases" in rec:
            ph = rec["phases"]
            if not isinstance(ph, list) or not all(isinstance(x, str) and x in PHASES for x in ph) or (hasattr(PHASES, 'index') and (lambda _ix: _ix != sorted(_ix))([PHASES.index(x) for x in ph])):
                return "bad_phases"
        if "lease_expires" in rec and not _finite_number(rec["lease_expires"]):
            return "bad_lease_expires"
    return None


def _reject_constant(name):
    raise ValueError(f"non-finite JSON constant {name}")


def read_state_file():
    try:
        with open(STATE_PATH) as fh:
            state = json.load(fh, parse_constant=_reject_constant)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        ev("state_corrupt", reason="unreadable")
        raise StateCorrupt("unreadable")
    why = validate_state(state)
    if why:
        ev("state_corrupt", reason=why)
        raise StateCorrupt(why)
    return state


@contextlib.contextmanager
def locked_state():
    """Exclusive flock; yields the state dict; writes it back atomically on clean exit."""
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    lock_fd = os.open(STATE_PATH + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(lock_fd, "a+") as lock:
        ensure_private(STATE_PATH + ".lock")
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_state_file()
        yield state
        tmp = STATE_PATH + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            ensure_private(tmp)
            json.dump(state, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, STATE_PATH)
        fsync_dir(STATE_PATH)  # make the rename itself durable


_SINGLETON_FD = None


def acquire_singleton():
    """Process-lifetime lock: a second pipeline process must not run against the same state."""
    global _SINGLETON_FD
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    path = STATE_PATH + ".singleton"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    try:
        ensure_private(path)
    except StorageError:
        os.close(fd)
        raise
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _SINGLETON_FD = fd  # held until the process exits
    return True


def preflight_storage():
    """None when results/state storage is usable, else a reason. Runs before any browser action."""
    for label, path in (("results", RESULTS_PATH), ("state", STATE_PATH)):
        d = os.path.dirname(os.path.abspath(path))
        probe = os.path.join(d, f".probe.{os.getpid()}.{uuid.uuid4().hex[:8]}")
        try:
            os.makedirs(d, exist_ok=True)
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, b"x")
                os.fsync(fd)
            finally:
                os.close(fd)
                os.unlink(probe)
        except OSError:
            return f"{label}_dir_unwritable"
    try:
        existed = os.path.exists(RESULTS_PATH)
        os.close(os.open(RESULTS_PATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600))
        if not existed:
            fsync_dir(RESULTS_PATH)
        ensure_private(RESULTS_PATH)
    except StorageError:
        return "results_chmod_failed"
    except OSError:
        return "results_unwritable"
    try:
        read_state_file()
    except StateCorrupt:
        return "state_corrupt"
    return None


def set_state(job_id, status, attempts_delta=0):
    with locked_state() as st:
        rec = st.setdefault(job_id, {"status": "pending", "attempts": 0})
        rec["status"] = status
        rec["attempts"] = max(0, rec.get("attempts", 0) + attempts_delta)
        rec["updated_at"] = now_iso()


def claim_job(job_id):
    """Atomic claim: pending -> in_progress with owner + lease in ONE critical section.
    Returns the new attempt id, or None when someone else already holds / finished the job."""
    attempt_id = uuid.uuid4().hex
    with locked_state() as st:
        rec = st.setdefault(job_id, {"status": "pending", "attempts": 0})
        if rec.get("status") != "pending":
            return None
        rec.update(status="in_progress", attempts=rec.get("attempts", 0) + 1, owner=OWNER_ID,
                   attempt_id=attempt_id, lease_expires=time.time() + LEASE_S, phases=["started"],
                   updated_at=now_iso())
    return attempt_id


def renew_lease(job_id, attempt_id):
    """Bump lease_expires without adding a phase. Raises JobEnd('failed','claim_lost')."""
    with locked_state() as st:
        rec = st.get(job_id)
        if not _owned(rec, attempt_id):
            raise JobEnd("failed", "claim_lost")
        rec["lease_expires"] = time.time() + LEASE_S
        rec["updated_at"] = now_iso()


def _owned(rec, attempt_id):
    return bool(rec) and rec.get("status") == "in_progress" and rec.get("owner") == OWNER_ID \
        and rec.get("attempt_id") == attempt_id


_OUTBOX_CONN = None


def _outbox_conn():
    global _OUTBOX_CONN
    if _OUTBOX_CONN is not None:
        return _OUTBOX_CONN
    try:
        import outbox
        import reply_repo
        conn = reply_repo.connect()
        outbox.ensure_schema(conn)
        _OUTBOX_CONN = conn
        return conn
    except Exception as exc:
        log.info(f"outbox_unavailable reason={type(exc).__name__}")
        return None


def set_phase(job_id, attempt_id, phase):
    """Persist a phase marker BEFORE the side effect it names, and renew the lease."""
    with locked_state() as st:
        rec = st.get(job_id)
        if not _owned(rec, attempt_id):
            raise JobEnd("failed", "claim_lost")
        phases = list(rec.get("phases") or [])
        if not phases or phases[-1] != phase:
            phases.append(phase)
        rec["phases"] = phases
        rec["lease_expires"] = time.time() + LEASE_S
        rec["updated_at"] = now_iso()
        n_phases = len(phases)

    # The local write above is a JSON state file under a lock, not SQLite, so this enqueue cannot
    # share its transaction; it runs immediately after, best-effort (a failure only skips the sync).
    # Version 11+ per phase: pipeline_upsert only applies strictly-higher versions, so each new
    # phase supersedes the last and the terminal write_result row (version 100) supersedes them all.
    try:
        import outbox
        conn = _outbox_conn()
        if conn is not None:
            aid = outbox.normalize_uuid(attempt_id)
            ver = 10 + n_phases
            outbox.enqueue(conn, "attempt", aid, "upsert",
                           {"attempt_id": aid, "job_id": job_id, "phase": phase,
                            "status": "in_progress", "state": "in_progress",
                            "version": ver}, version=ver)
    except Exception as exc:
        log.info(f"outbox_skipped entity=attempt reason={type(exc).__name__}")


def finish_job(job_id, attempt_id, state_status):
    with locked_state() as st:
        rec = st.get(job_id)
        if not _owned(rec, attempt_id):
            ev("finish_skipped_not_owner", job_id)
            return False
        rec["status"] = state_status
        for k in ("owner", "lease_expires"):
            rec.pop(k, None)
        rec["updated_at"] = now_iso()
    return True


def recovery_action(rec, terminal_status):
    """('terminal', status) | ('requeue', '') | ('needs_reconciliation', why) for an expired claim."""
    if terminal_status:
        return "terminal", terminal_status
    phases = rec.get("phases")
    if not rec.get("attempt_id") or not isinstance(phases, list) or not phases:
        return "needs_reconciliation", "no_phase_record"  # legacy/unknown: could have done anything
    if phases == ["started"]:
        return "requeue", ""
    return "needs_reconciliation", f"phase_{phases[-1]}"


def release_after_shutdown(job_id, attempt_id):
    """Shutdown mid-job: requeue only if nothing side-effecting happened, else reconcile."""
    with locked_state() as st:
        rec = st.get(job_id)
        if not _owned(rec, attempt_id):
            return "not_owner"
        action, why = recovery_action(rec, None)
        if action == "requeue":
            rec["status"], rec["phases"] = "pending", []
            rec["attempts"] = max(0, rec.get("attempts", 0) - 1)
        else:
            rec["status"], rec["reconcile_reason"] = "needs_reconciliation", why
        for k in ("owner", "lease_expires"):
            rec.pop(k, None)
        rec["updated_at"] = now_iso()
        return rec["status"]


def read_results():
    """{(job_id, attempt_id): status} for result lines that carry an attempt id."""
    out = {}
    try:
        with open(RESULTS_PATH) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("attempt_id") and r.get("job_id"):
                    out[(r["job_id"], r["attempt_id"])] = r.get("status")
    except OSError:
        pass
    return out


def parse_facts(detail):
    return dict(re.findall(r"(\w+)=(\S*)", detail or ""))


def _fact_int(facts, key, default=-1):
    try:
        return int(facts.get(key, default))
    except (TypeError, ValueError):
        return default


def dry_run_missing(facts):
    """Conditions a dry run must meet to count as dry_run_ok; returns the list that failed."""
    miss = []
    if _fact_int(facts, "fields_filled") <= 0:
        miss.append("fields_filled")
    if _fact_int(facts, "required_unfilled") != 0 or _fact_int(facts, "required_total") < 0:
        miss.append("required_unfilled")  # ALL required fields must be filled (unknown counts as not)
    if _fact_int(facts, "resume_uploaded") != 1:
        miss.append("resume_uploaded")  # set only after the upload was observed
    if _fact_int(facts, "unanswered_questions") != 0:
        miss.append("unanswered_questions")
    if _fact_int(facts, "for_review") != 0:
        miss.append("for_review")
    if _fact_int(facts, "verify_required") not in (0, 1) or (
            _fact_int(facts, "verify_required") == 1 and _fact_int(facts, "verify_confirmed") != 1):
        miss.append("verification_unconfirmed")
    if _fact_int(facts, "submit_candidates") != 1:
        miss.append("final_control_not_unique")
    return miss


def live_preconditions(facts):
    """Live submit gate: the same bar as the dry run. Returns the missing list (empty = OK)."""
    return dry_run_missing(facts)


def write_result(job_id, started_at, finished_at, status, detail, latency_s, attempt_id=None):
    """The ONLY writer of result lines; makes 'submitted' impossible while DRY_RUN is on and
    'dry_run_ok' impossible unless every dry-run condition is met."""
    if DRY_RUN and status == "submitted":
        status, detail = "failed", "invariant_violation_submitted_in_dry_run"
    if status in ("dry_run_ok", "dry_run_incomplete") and not DRY_RUN:
        status, detail = "failed", "invariant_violation_dry_run_status_in_live"
    if status == "dry_run_ok":
        missing = dry_run_missing(parse_facts(detail))
        if missing:
            status, detail = "dry_run_incomplete", f"missing={','.join(missing)} {detail}"
        elif not detail.startswith("dry_run_submit_skipped"):
            detail = "dry_run_submit_skipped " + detail
    rec = {
        "job_id": job_id, "attempt_id": attempt_id, "started_at": started_at, "finished_at": finished_at,
        "status": status, "detail": detail, "latency_s": round(latency_s, 2),
    }
    if status in ("dry_run_ok", "dry_run_incomplete"):
        rec["dry_run"] = True
    existed = os.path.exists(RESULTS_PATH)
    fd = os.open(RESULTS_PATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as fh:
        ensure_private(RESULTS_PATH)
        fh.write(json.dumps(rec) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    if not existed:
        fsync_dir(RESULTS_PATH)

    # version 100 on the final row: pipeline_upsert applies a row only when its version is
    # strictly higher than the stored one, so the terminal attempt state supersedes the
    # version-11+ in_progress phase rows (attempts realistically have far fewer than 89 phases).
    try:
        import outbox
        conn = _outbox_conn()
        if conn is not None:
            aid = outbox.normalize_uuid(attempt_id or outbox.derive_attempt_id(job_id, started_at))
            outbox.enqueue(conn, "attempt", aid, "upsert",
                           {"attempt_id": aid, "job_id": job_id, "started_at": started_at,
                            "finished_at": finished_at, "status": status, "detail": detail,
                            "latency_s": rec["latency_s"], "dry_run": bool(rec.get("dry_run")),
                            "version": 100}, version=100)
            if status in ("submitted", "submitted_unconfirmed"):
                sid = outbox.derive_uuid("submission", aid)
                outbox.enqueue(conn, "submission", sid, "upsert",
                               {"id": sid, "job_id": job_id, "attempt_id": aid,
                                "submitted_at": finished_at, "confirmed": status == "submitted",
                                "version": 1}, version=1)
    except Exception as exc:
        log.info(f"outbox_skipped entity=result reason={type(exc).__name__}")

    return status


def validate_queue(data):
    """(valid_jobs, problems). problems is a list of (index, id_or_None, reason); no field values."""
    problems, jobs, seen = [], [], set()
    if not isinstance(data, list):
        return [], [(-1, None, "queue_not_a_list")]
    for i, j in enumerate(data):
        jid = j.get("id") if isinstance(j, dict) else None
        ident = jid[:40] if isinstance(jid, str) else None
        if not isinstance(j, dict):
            problems.append((i, None, "entry_not_a_mapping"))
            continue
        why = []
        if not isinstance(jid, str) or not jid.strip():
            why.append("id_invalid")
        elif jid in seen:
            why.append("id_duplicate")
        else:
            seen.add(jid)
        url = j.get("url")
        if not isinstance(url, str):
            why.append("url_not_string")
        elif not url.startswith("{TEST_BASE}/"):
            p = analyze_url(url)[0]
            if p:
                why.append(p)
        plat = j.get("platform")
        if plat is not None and (not isinstance(plat, str) or plat.lower() not in KNOWN_PLATFORMS):
            why.append("platform_unknown")
        if "verification" in j and j["verification"] is not None:
            v = j["verification"]
            sd = v.get("sender_domains") if isinstance(v, dict) else None
            if (not isinstance(v, dict) or not isinstance(sd, list)
                    or not all(isinstance(d, str) and re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", d.lower()) for d in sd)
                    or v.get("type") not in (None, "code", "magic_link")):
                why.append("verification_invalid")
        if why:
            problems.append((i, ident, ",".join(why)))
        else:
            jobs.append(j)
    return jobs, problems


def load_queue():
    """Valid jobs ([] for an empty queue). Raises QueueError on a failed load or any invalid entry."""
    try:
        with open(QUEUE_PATH) as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        ev("queue_error", reason=type(exc).__name__)
        raise QueueError(type(exc).__name__)
    jobs, problems = validate_queue(data)
    for i, ident, reason in problems:
        ev("queue_invalid_entry", ident or "-", index=i, reason=reason)
    if problems:
        ev("queue_error", reason="invalid_entries", count=len(problems))
        raise QueueError("invalid_entries")
    return jobs


def pending_jobs(queue):
    with locked_state() as st:
        return [j for j in queue if st.get(j["id"], {}).get("status", "pending") == "pending"]


def lease_expired(rec, now=None):
    now = time.time() if now is None else now
    le = rec.get("lease_expires")
    if isinstance(le, (int, float)) and not isinstance(le, bool):
        return le < now
    try:  # no lease recorded: fall back to the age of the record
        return datetime.fromisoformat(rec.get("updated_at")).timestamp() + LEASE_S < now
    except (TypeError, ValueError):
        return True


def recover_in_progress_ex():
    """Expired claims only (a live owner's lease is never touched). Reconciles with the result log:
    terminal result -> that state; only a crash still at "started" requeues; anything at or after
    "navigating" -> needs_reconciliation. After MAX_ATTEMPTS a requeue becomes failed crash_loop_guard.
    Returns {kind: [job ids]} for kind in terminal / requeued / reconcile / crash_loop / skipped."""
    results = read_results()
    out = {"terminal": [], "requeued": [], "reconcile": [], "crash_loop": [], "skipped": []}
    tripped = []
    with locked_state() as st:
        for jid, rec in st.items():
            if rec.get("status") != "in_progress":
                continue
            if not lease_expired(rec):
                out["skipped"].append(jid)
                continue
            term = results.get((jid, rec.get("attempt_id"))) if rec.get("attempt_id") else None
            action, why = recovery_action(rec, term)
            if action == "terminal":
                rec["status"] = STATE_FOR_STATUS.get(why, why)
                out["terminal"].append(jid)
            elif action == "requeue":
                if rec.get("attempts", 0) >= MAX_ATTEMPTS:
                    rec["status"] = "failed"
                    tripped.append((jid, rec.get("attempt_id")))
                    out["crash_loop"].append(jid)
                else:
                    rec["status"], rec["phases"] = "pending", []
                    out["requeued"].append(jid)
            else:
                rec["status"], rec["reconcile_reason"] = "needs_reconciliation", why
                out["reconcile"].append(jid)
            for k in ("owner", "lease_expires"):
                rec.pop(k, None)
            rec["updated_at"] = now_iso()
    for jid in out["skipped"]:
        ev("recover_skipped_live_lease", jid)
    for jid, attempt_id in tripped:
        t = now_iso()
        final = write_result(jid, t, t, "failed", "crash_loop_guard", 0.0, attempt_id)
        ev("job_result", jid, status=final, detail="crash_loop_guard", latency_s=0.0)
    for kind in ("terminal", "requeued", "reconcile", "crash_loop"):
        if out[kind]:
            ev("recovery_outcome", kind=kind, count=len(out[kind]), ids=",".join(out[kind]))
    return out


def recover_in_progress():
    """Compatibility wrapper: the ids that now need reconciliation."""
    return recover_in_progress_ex()["reconcile"]


def unexpired_claims():
    """Job ids still in_progress with a live lease (an --once run must not leave any behind)."""
    with locked_state() as st:
        return sorted(j for j, r in st.items() if r.get("status") == "in_progress" and not lease_expired(r))


def announce_reconciliation():
    with locked_state() as st:
        ids = sorted(j for j, r in st.items() if r.get("status") == "needs_reconciliation")
    if ids:
        ev("needs_reconciliation_jobs", count=len(ids), ids=",".join(ids))
    return ids


# ---------------------------------------------------------------- local test server
POST_HITS = []  # every POST the local fixture server actually received (the self-test asserts none)
PROBE_HTML = (b"<html><body><form id=f method=post action=/__probe_post><input name=a value=1></form>"
              b"</body></html>")


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        POST_HITS.append(urlsplit(self.path).path)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path not in ("/test_form.html", "/__probe"):
            self.send_error(404)
            return
        if path == "/__probe":
            body = PROBE_HTML
        else:
            with open(FORM_PATH, "rb") as fh:
                body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_test_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


# ---------------------------------------------------------------- browser helpers
def pace():
    """Human-pace pause between browser actions; interruptible by shutdown."""
    if STOP.wait(random.uniform(3, 5)):
        raise Shutdown()


def settle(page):
    try:
        page.wait_for_load_state("load", timeout=15000)
    except PWTimeout:
        pass


# Tri-state: 'active' when ANY visible unresolved challenge exists (visible captcha frame/widget
# with size whose own response field is still empty, or challenge text). The invisible reCAPTCHA
# badge and solved widgets do not count, and one solved widget never clears the rest of the document.
CHALLENGE_JS = r"""
() => {
  const vis = (el) => {
    if (!el || el.closest('.grecaptcha-badge')) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 20) return false;
    if (r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight || r.left >= innerWidth) return false;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return false;
    }
    return true;
  };
  const text = ((document.title || '') + ' ' +
    ((document.body && document.body.innerText) || '')).toLowerCase().slice(0, 5000);
  const phrases = ['just a moment', 'verify you are human', "verify you're human",
                   'checking your browser', 'are you a robot'];
  if (phrases.some((p) => text.includes(p))) return 'active';
  const providers = [
    { frame: /recaptcha/, widget: '.g-recaptcha', resp: 'textarea[name="g-recaptcha-response"]' },
    { frame: /hcaptcha/, widget: '.h-captcha', resp: 'textarea[name="h-captcha-response"]' },
    { frame: /challenges\.cloudflare|turnstile/, widget: '.cf-turnstile', resp: 'input[name="cf-turnstile-response"]' },
  ];
  // Per-widget verification: each visible widget must have its OWN response
  // field populated. A token from a different widget does not count.
  const seen = new Set();
  let total = 0, unsolved = 0;
  for (const p of providers) {
    const widgets = new Set();
    for (const w of document.querySelectorAll(p.widget)) {
      if ((w.getAttribute('data-size') || '') !== 'invisible' && vis(w)) widgets.add(w);
    }
    for (const f of document.querySelectorAll('iframe')) {
      if (p.frame.test((f.src || '').toLowerCase()) && vis(f)) {
        // Use widget ancestor if found; otherwise use the iframe itself.
        // Never fall back to parentElement (could be the form, collapsing
        // multiple frames into one container).
        const container = f.closest(p.widget) || f;
        if (container && ![...widgets].some((b) => b !== container && b.contains(f))) widgets.add(container);
      }
    }
    for (const w of widgets) {
      if (!w || seen.has(w)) continue;
      seen.add(w);
      total++;
      // Response field must be INSIDE this widget AND its nearest widget
      // ancestor must be this widget (not a nested widget's field)
      const fields = w.querySelectorAll(p.resp);
      let ok = false;
      for (const fld of fields) {
        // Find nearest ancestor matching any widget selector
        const nearest = fld.closest('.g-recaptcha, .h-captcha, .cf-turnstile');
        // Field belongs to this widget only if nearest ancestor is this widget
        // (or no ancestor, meaning field is directly in w)
        if (nearest && nearest !== w) continue;  // belongs to nested widget, skip
        if ((fld.value || '').trim().length > 0) { ok = true; break; }
      }
      if (!ok) unsolved++;
    }
  }
  // Unknown-provider containers: a visible captcha-looking element that is not
  // (part of) a recognised provider widget is a challenge this detector cannot
  // read. Report 'unknown' (-> 'error' Python-side, fail closed) instead of
  // letting the known-provider count above report 'clear' for it.
  // Exclusions, in order: structural tags; no captcha-ish name; inside or
  // wrapping a known widget; a known provider frame (itself or a descendant);
  // related to a widget already counted above; wrapping the invisible-reCAPTCHA
  // badge; no widget-like structure (not an iframe and no frame/form control
  // inside, e.g. help text); not visible.
  const knownSel = '.g-recaptcha, .h-captcha, .cf-turnstile';
  const knownFrame = /recaptcha|hcaptcha|challenges\.cloudflare|turnstile/;
  const unknownName = /captcha|turnstile|arkose|geetest|datadome|perimeterx|awswaf/;
  const skipTags = new Set(['HTML', 'HEAD', 'BODY', 'SCRIPT', 'STYLE', 'LINK', 'META', 'NOSCRIPT', 'TEMPLATE']);
  const isKnownFrame = (f) => knownFrame.test((f.src || '').toLowerCase());
  const nameOf = (el) => [
    el.id || '',
    typeof el.className === 'string' ? el.className : '',
    el.getAttribute('name') || '',
    el.getAttribute('title') || '',
    el.getAttribute('aria-label') || '',
    el.tagName === 'IFRAME' ? (el.src || '') : '',
  ].join(' ').toLowerCase();
  const counted = [...seen];
  for (const el of document.querySelectorAll('*')) {
    if (skipTags.has(el.tagName) || !unknownName.test(nameOf(el))) continue;
    if (el.closest(knownSel) || el.querySelector(knownSel)) continue;
    const isFrame = el.tagName === 'IFRAME';
    if (isFrame ? isKnownFrame(el) : [...el.querySelectorAll('iframe')].some(isKnownFrame)) continue;
    if (counted.some((w) => w === el || w.contains(el) || el.contains(w))) continue;
    if (el.querySelector('.grecaptcha-badge')) continue;
    if (!isFrame && !el.querySelector('iframe, input, textarea, select, button')) continue;
    if (!vis(el)) continue;
    return 'unknown';
  }
  return (total === 0 || unsolved === 0) ? 'clear' : 'active';
}
"""


def parse_challenge_result(value):
    # 'unknown' = visible captcha container of an unrecognized provider (CHALLENGE_JS sentinel):
    # fail closed as 'error', never 'clear'. Anything else unrecognized is also 'error'.
    return value if value in ("clear", "active") else "error"


def challenge_state(page, *others):
    """'clear' | 'active' | 'error' over the page AND any further target (the selected application
    frame). Any inspection failure is 'error' (fail closed)."""
    states = []
    for t in (page,) + others:
        try:
            states.append(parse_challenge_result(t.evaluate(CHALLENGE_JS)))
        except Exception:
            states.append("error")
    if "error" in states:
        return "error"
    return "active" if "active" in states else "clear"


def check_challenge(page, job_id, target=None):
    """Wait once (30s) if a challenge shows on the page or the application frame; still there ->
    blocked, never retried. Fails closed when anything cannot be inspected."""
    others = (target,) if target is not None and target is not page else ()
    state = challenge_state(page, *others)
    if state == "error":
        raise JobEnd("blocked", "challenge_inspection_error")
    if state == "clear":
        return
    ev("challenge_blocked", job_id, phase="first_detect", action="wait_30s")
    if STOP.wait(CHALLENGE_WAIT_S):
        raise Shutdown()
    state = challenge_state(page, *others)
    if state == "error":
        raise JobEnd("blocked", "challenge_inspection_error")
    if state == "active":
        ev("challenge_blocked", job_id, phase="still_present", action="give_up")
        raise JobEnd("blocked", "challenge_loop")


def safe_count(fn, page):
    try:
        return int(fn(page))
    except Exception:
        return -1


def safe_title(page):
    try:
        return page.title()
    except PWError:
        return None


# Shared by the inventory and the per-element probe so label/kind/question logic can never diverge.
_JS_COMMON = r"""
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim().slice(0, 200);
  const labelOf = (el) => {
    let t = '';
    if (el.labels && el.labels.length) t = Array.from(el.labels).map((l) => l.innerText).join(' ');
    if (!t) t = el.getAttribute('aria-label') || '';
    if (!t && el.getAttribute('aria-labelledby')) {
      t = el.getAttribute('aria-labelledby').split(/\s+/).map((id) => {
        const n = document.getElementById(id); return n ? n.innerText : '';
      }).join(' ');
    }
    if (!t) t = el.getAttribute('placeholder') || '';
    return clean(t);
  };
  const typeOf = (el) => (el.getAttribute('type') || '').toLowerCase();
  const kindOf = (el) => {
    const type = typeOf(el), tag = el.tagName.toLowerCase();
    return type === 'file' ? 'file' : tag === 'select' ? 'select' : type === 'radio' ? 'radio'
         : type === 'checkbox' ? 'checkbox' : tag === 'textarea' ? 'textarea' : 'input';
  };
  const questionOf = (el) => {
    const fs = el.closest('fieldset');
    const lg = fs ? fs.querySelector('legend') : null;
    if (lg) return clean(lg.innerText);
    const grp = el.closest('[role=radiogroup],[role=group]');
    if (grp && grp.getAttribute('aria-label')) return clean(grp.getAttribute('aria-label'));
    const box = el.parentElement && el.parentElement.parentElement;
    if (!box) return '';
    const names = new Set(Array.from(box.querySelectorAll('input[type=radio]')).map((r) => r.name));
    return names.size > 1 ? '' : clean(box.innerText);   // ambiguous container: never guess
  };
"""

INVENTORY_JS = "() => {" + _JS_COMMON + r"""
  // stale markers from an earlier numbering must never survive a rebuild
  document.querySelectorAll('[data-jp-i]').forEach((e) => e.removeAttribute('data-jp-i'));
  const forms = Array.from(document.forms);
  const out = [];
  let i = 0;
  document.querySelectorAll('input, textarea, select').forEach((el) => {
    const type = typeOf(el);
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type) || el.disabled) return;
    const r = el.getBoundingClientRect();
    if (type !== 'file' && (r.width === 0 || r.height === 0)) return;
    el.setAttribute('data-jp-i', String(i));
    const tag = el.tagName.toLowerCase();
    const kind = kindOf(el);
    const label = labelOf(el);
    const group = kind === 'radio' || kind === 'checkbox'
      ? [forms.indexOf(el.form), el.getAttribute('name') || '', questionOf(el)].join('|') : '';
    let filled = false, selected = '', fileName = '';
    if (kind === 'file') { filled = !!(el.files && el.files.length); fileName = filled ? el.files[0].name : ''; }
    else if (kind === 'select') {
      const o = el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
      selected = o ? clean(o.text) : '';
      filled = !!o && el.value !== '' && !(el.selectedIndex === 0 && /^\s*(select|choose|please|--)/i.test(selected));
    }
    else if (kind === 'radio' || kind === 'checkbox') filled = !!el.checked;
    else filled = (el.value || '').trim().length > 0;
    out.push({
      kind: kind, selector: '[data-jp-i="' + i + '"]', label: label,
      name: el.getAttribute('name') || '', input_type: type,
      autocomplete: el.getAttribute('autocomplete') || '',
      question: kind === 'radio' || kind === 'checkbox' ? questionOf(el) : '',
      group: group, required: !!(el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label)),
      filled: filled, checked: (kind === 'radio' || kind === 'checkbox') && !!el.checked,
      selected: selected, file_name: fileName,
      options: tag === 'select' ? Array.from(el.options).map((o) => clean(o.text)) : [],
    });
    i += 1;
  });
  return out;
}
"""

FIELD_PROBE_JS = "(el) => {" + _JS_COMMON + r"""
  const kind = kindOf(el);
  return { kind: kind, input_type: typeOf(el), label: labelOf(el),
           autocomplete: el.getAttribute('autocomplete') || '', name: el.getAttribute('name') || '',
           question: kind === 'radio' || kind === 'checkbox' ? questionOf(el) : '' };
}
"""

UPLOAD_PROBE_JS = "(el) => (el.files && el.files.length) ? [el.files[0].name, el.files[0].size] : null"


_MAGIC_LINK_NEGATIVE_RE = re.compile(
    r"(?<!last\s)sign\s*in|(?<!last\s)log\s*in|"
    r"link[^.]{0,40}expir|expir[^.]{0,40}link|"
    r"invalid\s+(link|token)|"
    r"request\s+(another|a\s+new)\s+link|"
    r"continue\s+with\s+email|"
    r"no\s+longer\s+valid|already\s+(been\s+)?used",
    re.IGNORECASE)


def build_inventory(target):
    """Fresh inventory; INVENTORY_JS clears old data-jp-i markers first. Rebuild after EVERY
    structural change (each typed field, answer, upload): selectors from an older build are not trusted."""
    return target.evaluate(INVENTORY_JS)


def fields_match(expected, actual):
    """Immediately-before-entry re-check: the element still is what the inventory said it was
    (kind/type/label always; name and radio/checkbox question whenever the inventory recorded them)."""
    if not isinstance(actual, dict):
        return False
    keys = ["kind", "input_type", "label"] + [k for k in ("name", "question") if k in expected]
    return all((expected.get(k) or "") == (actual.get(k) or "") for k in keys)


def field_for(inventory, selector):
    return next((f for f in inventory if f.get("selector") == selector), None)


def unique_locator(target, selector):
    loc = target.locator(selector)
    if loc.count() != 1:
        raise JobEnd("failed", "locator_not_unique")
    return loc


def gate_field(target, field, ctx):
    """Everything required right before touching a field: destination still approved AND still the
    pinned application origin, locator unique, element's kind/type/label/name unchanged."""
    assert_destination(target, ctx.url, ctx.scope, ctx.jid)
    pinned = getattr(ctx, "assert_pinned", None)
    if pinned is not None:
        pinned(target)
    loc = unique_locator(target, field["selector"])
    if not fields_match(field, loc.evaluate(FIELD_PROBE_JS)):
        raise JobEnd("failed", "field_changed")
    return loc


def type_into(loc, text):
    _pg = loc.page
    _u0 = _pg.url
    try:
        _f0 = loc.evaluate("() => location.href", timeout=3000)
    except Exception:
        _f0 = None
    def _same(*_a, **_k):
        try:
            _fnow = loc.evaluate("() => location.href", timeout=3000)
        except Exception:
            raise RuntimeError('sensitive entry aborted: frame evaluation failed')
        if _f0 is None or _fnow != _f0:
            raise RuntimeError('sensitive entry aborted: frame navigated')
        return _same_base(*_a, **_k)
    _h = loc.element_handle(timeout=10000)
    def _same_base():
        if _pg.url != _u0 or not _h.evaluate('e => e.isConnected'):
            raise RuntimeError('sensitive entry aborted: target navigated or was replaced')
    loc.click(timeout=10000)
    _same()
    loc.fill("", timeout=10000)
    _same()
    loc.press_sequentially(text, delay=random.randint(60, 140))


def required_accounting(inventory):
    """(required_total, required_unfilled) over the current inventory: radio groups count once and
    are filled when any member is checked; everything else is filled per its own state."""
    total = unfilled = 0
    groups = {}
    for f in inventory:
        kind = f.get("kind")
        if kind == "radio":
            g = groups.setdefault(f.get("group") or f.get("name") or f["selector"], [False, False])
            g[0] = g[0] or bool(f.get("required"))
            g[1] = g[1] or bool(f.get("checked"))
        elif f.get("required"):
            total += 1
            unfilled += 0 if f.get("filled") else 1
    for req, chk in groups.values():
        if req:
            total += 1
            unfilled += 0 if chk else 1
    return total, unfilled


def blank_textareas(inventory):
    return sum(1 for f in inventory if f.get("kind") == "textarea" and not f.get("filled"))


# ---------------------------------------------------------------- work-authorization answers
# classify_question / pick_option_ex / pick_option live in answers/workauth.py (imported at the top).


def plan_workauth(inventory):
    """Pure plan over one inventory: {'actions': [(kind, field, option|None, key)], 'unanswered': n,
    'review': [(reason, kind, selector, question[:40])], 'satisfied': [keys already correct]}."""
    actions, review, satisfied, unanswered = [], [], [], 0
    radios = {}
    for f in inventory:
        kind = f.get("kind")
        if kind == "radio":
            radios.setdefault(f.get("group") or f.get("name") or f["selector"], []).append(f)
            continue
        if kind == "textarea":
            unanswered += 0 if f.get("filled") else 1  # a blank essay is an unanswered question
            continue
        if kind not in ("select", "checkbox"):
            continue
        q = f.get("label") or ""
        act, key, ans, why = classify_question(q, kind)
        if act == "skip":
            if kind == "select" and not f.get("filled"):
                unanswered += 1
            continue
        if act == "review":
            review.append((why, kind, f["selector"], q[:40]))
            continue
        if kind == "select":
            opt, status = pick_option_ex(f.get("options", []), ans, key)
            if status == "assertion":
                review.append(("option_assertion", kind, f["selector"], q[:40]))
            elif opt is None:
                unanswered += 1
            elif f.get("selected") == opt:
                satisfied.append(key)
            else:
                actions.append(("select", f, opt, key))
        elif f.get("checked"):
            satisfied.append(key)
        else:
            actions.append(("check", f, None, key))
    for members in radios.values():
        q = members[0].get("question") or ""
        act, key, ans, why = classify_question(q, "radio")
        if act == "skip":
            unanswered += 0 if any(m.get("checked") or m.get("filled") for m in members) else 1
            continue
        if act == "review":
            review.append((why, "radio", members[0]["selector"], q[:40]))
            continue
        picks = [(m, pick_option_ex([m.get("label", "")], ans, key)) for m in members]
        if any(st == "assertion" for _, (_, st) in picks):
            review.append(("option_assertion", "radio", members[0]["selector"], q[:40]))
            continue
        hits = [m for m, (o, st) in picks if o is not None]
        if len(hits) != 1:
            unanswered += 1
        elif hits[0].get("checked"):
            satisfied.append(key)
        else:
            actions.append(("check", hits[0], None, key))
    return {"actions": actions, "unanswered": unanswered, "review": review, "satisfied": satisfied}


def answer_workauth(target, inventory, gate=None, rebuild=None, max_steps=40):
    """Answer work-auth/sponsorship questions from FACTS. Returns (answered_keys, unanswered_count,
    for_review) where for_review is [(reason, kind, selector, question[:40])] and NOTHING in it is
    touched. With `rebuild`, exactly ONE action is taken per inventory and the inventory is rebuilt
    (and everything re-planned) after each, so no selector from an older build is ever used and the
    returned counts describe the FINAL page state. Without it, one plan is executed as-is."""
    if gate is None:
        gate = lambda f: target.locator(f["selector"]).first  # noqa: E731
    done, failed = [], set()
    plan = plan_workauth(inventory)
    steps = 0
    while True:
        todo = [a for a in plan["actions"] if a[1]["selector"] not in failed]
        if not todo or steps >= max_steps:
            break
        for kind, f, opt, key in (todo[:1] if rebuild else todo):
            steps += 1
            try:
                pace()
                if kind == "select":
                    gate(f).select_option(label=opt, timeout=5000)
                else:
                    gate(f).check(timeout=5000)
                done.append(key)
            except PWError:
                failed.add(f["selector"])
        if not rebuild:
            break
        plan = plan_workauth(rebuild())
    answered = list(dict.fromkeys(plan["satisfied"]))
    unanswered = plan["unanswered"] + len(failed) + len([a for a in plan["actions"] if a[1]["selector"] not in failed])
    return answered, unanswered, plan["review"]


# ---------------------------------------------------------------- verification code field
_CODE_LABEL_RE = re.compile(
    r"^\s*(?:(?:enter|your|email)\s+)*(?:(?:verification|security|confirmation|one[- ]time|authentication)\s+)?"
    r"(?:code|passcode)\s*\*?\s*:?\s*$", re.I)
_CODE_EXCLUDE_RE = re.compile(r"zip|postal|promo|referral|coupon", re.I)
_CODE_INPUT_TYPES = ("", "text", "tel", "number")


def find_code_field(inventory):
    """(field, reason). Exactly one text-like input that is autocomplete=one-time-code or has a
    strict code label; reason is 'missing' / 'ambiguous' when there is not exactly one."""
    cands = []
    for f in inventory:
        if f.get("kind") != "input" or f.get("input_type", "") not in _CODE_INPUT_TYPES:
            continue
        label = f.get("label") or ""
        if f.get("autocomplete") == "one-time-code" or (
                _CODE_LABEL_RE.match(label) and not _CODE_EXCLUDE_RE.search(label)):
            cands.append(f)
    if not cands:
        return None, "missing"
    if len(cands) > 1:
        return None, "ambiguous"
    return cands[0], ""


def live_gate():
    """Fail closed before touching anything when DRY_RUN is off but arming is incomplete."""
    if DRY_RUN:
        return
    if os.environ.get("APPLY_AGENT_ARMED") != "1":
        raise JobEnd("failed", "live_not_armed")
    if not os.path.exists(LIVE_OK_FILE):
        raise JobEnd("failed", "live_flag_file_missing")


# ---------------------------------------------------------------- live submit (exactly once)

def _submit_gap_wait_s(last_ts, spacing_s, now=None):
    if last_ts is None:
        return 0.0
    now = time.time() if now is None else now
    return max(0.0, last_ts + spacing_s - now)


def _artifact_dir(jid):
    return os.path.join(SUBMIT_ARTIFACTS_DIR, re.sub(r"[^A-Za-z0-9_-]", "_", str(jid))[:64] or "job")


def _write_private(path, text):
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        ensure_private(path)
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    fsync_dir(path)


def _page_text(target):
    try:
        text = target.evaluate("() => document.body ? document.body.innerText.slice(0, 20000) : ''")
    except Exception:
        return ""
    return text if isinstance(text, str) else ""


# Port of universal_filler._STRICT_SUBMIT (built from its pattern so the two cannot drift). Counts every
# visible submit-class control, enabled or not: a disabled Submit still means the form is on the page.
_SUBMIT_PROBE_JS = r"""() => {
  const re = new RegExp(%s, 'i');
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    const st = getComputedStyle(el);
    return st.display !== 'none' && st.visibility !== 'hidden';
  };
  let n = 0;
  for (const el of document.querySelectorAll('button, input[type=submit], input[type=button]')) {
    if (el.closest('[role=search], form[role=search]') || !shown(el)) continue;
    const label = (el.innerText || el.value || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (re.test(label)) n += 1;
  }
  return n;
}""" % json.dumps(universal_filler._STRICT_SUBMIT.pattern)


def _form_present(target):
    # True: a submit control is on the page. False: affirmatively none. None: the probe failed, which
    # proves neither state (final_control() cannot tell zero controls from several, or a read error).
    try:
        count = target.evaluate(_SUBMIT_PROBE_JS)
    except Exception:
        return None
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    return count >= 1


def _compact_evidence(evidence):
    e = evidence if isinstance(evidence, dict) else {}
    parts = []
    try:
        reqs = ",".join(f"{redact(str(m))}:{redact(str(s))}" for m, _p, s in (e.get("requests") or [])[:6])
        if reqs:
            parts.append("reqs=" + reqs)
        for key, label in (("final_url_path", "path"), ("url_changed", "changed"),
                           ("form_present", "form"), ("text_hit", "text")):
            if key in e:
                value = e[key]
                value = int(value) if isinstance(value, bool) else value
                parts.append(f"{label}={redact(str(value))}")
        for key in ("click_error", "code_step", "observe", "error"):
            if e.get(key):
                parts.append(f"{key}={redact(str(e[key]))}")
    except Exception:
        pass
    return submit_guard.redact_text(" ".join(parts), 300)


def _mark_reconcile(jid, attempt_id, reason):
    with locked_state() as st:
        rec = st.get(jid)
        if _owned(rec, attempt_id):
            rec["reconcile_reason"] = reason
            rec["updated_at"] = now_iso()


def _scrub_evidence(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _scrub_evidence(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_evidence(v) for v in value]
    return value


def _close_intent(ledger, key, result, evidence, jid, page=None, target=None, attempt_id=None, reconcile=None):
    """Record the outcome that closes an intent, then the (redacted) confirmation artifacts."""
    try:
        ledger.append_outcome(key, result, _scrub_evidence(evidence))
    except OSError as exc:  # the intent stays unresolved, which keeps the job blocked: the safe side
        ev("submit_outcome_unrecorded", jid, reason=type(exc).__name__)
    directory = _artifact_dir(jid)
    try:
        text = submit_guard.redact_text(redact(_page_text(target))) if target is not None else ""
        _write_private(os.path.join(directory, "confirmation.txt"), text)
    except OSError:
        pass
    try:
        png = os.path.join(directory, "confirmation.png")
        # Kept for human reconciliation. The confirmation page necessarily shows the user's own
        # application, so it is contained, not scrubbed: a 0600 file in a 0700 per-job directory.
        page.screenshot(path=png, full_page=True)
        os.chmod(png, 0o600)
    except Exception:
        pass
    ev("submit_outcome", jid, outcome=result)
    if reconcile and attempt_id:
        _mark_reconcile(jid, attempt_id, reconcile)


def _snapshot(target, ctx, seen):
    final_url = str(getattr(target, "url", "") or "")
    changed = submit_guard.canonical_apply_url(final_url) != ctx.pre_click_url
    return submit_guard.classify_submit_outcome(
        list(seen), final_url, changed, _page_text(target), _form_present(target), ctx.platform)


def _observe_outcome(target, ctx, jid, seen, polls):
    """Poll once a second until the page proves 'confirmed' or 'rejected'. Silence never proves that
    nothing was sent, so a quiet page (or the polls running out) is 'ambiguous', never 'not sent'."""
    kind, evidence = None, {}
    for _ in range(polls):
        if STOP.wait(1.0):
            raise Shutdown()
        try:
            assert_destination(target, ctx.url, ctx.scope, jid)
        except JobEnd:  # left the allowlist: stop reading the page, judge by what was seen
            kind, evidence = submit_guard.classify_submit_outcome(
                list(seen), str(getattr(target, "url", "") or ""), True, "", None, ctx.platform)
            evidence["observe"] = "destination_left_allowlist"
            return (kind if kind == "confirmed" else "ambiguous"), evidence
        kind, evidence = _snapshot(target, ctx, seen)
        if kind in ("confirmed", "rejected"):
            return kind, evidence
    evidence["observe"] = "quiet_but_unproven" if kind == "provably_not_sent" else "timeout"
    return "ambiguous", evidence


def _click_and_observe(target, page, ctx, jid, seen, loc, ledger, key):
    """The one place the approved Submit is clicked: re-check arming, lift the page guard, click
    once (never retried), then watch the outcome. Returns (kind, evidence)."""
    if DRY_RUN:
        raise JobEnd("failed", "invariant_live_submit_in_dry_run")
    live_gate()  # the gap wait can be long: arming is re-read right before the click
    ctx.pre_click_url = submit_guard.canonical_apply_url(str(getattr(target, "url", "") or ""))
    ctx.submit_mut0 = ctx.scope.mutating
    ctx.click_since = datetime.now(timezone.utc)
    if STOP.is_set():
        raise Shutdown()
    try:  # final duplicate + gap check and the dispatch record under one lock; on any failure nothing has been clicked yet
        ledger.recheck_and_dispatch(key, ctx.jid, submit_guard.posting_key(ctx.url),
                                    submit_guard.submit_pacing_from_env()[1])
    except submit_guard.SubmitConflict as exc:
        raise JobEnd("failed", exc.reason)
    except (OSError, submit_guard.LedgerError):
        raise JobEnd("failed", "ledger_unwritable")
    ctx.click_dispatched = True  # from here a request may leave the machine: failures stay ambiguous
    try:
        universal_filler.lift_guard(page)
        loc.click(timeout=15000)
    except Exception as exc:
        return "click_failed", {"click_error": type(exc).__name__}
    ev("submit_clicked", jid)
    return _observe_outcome(target, ctx, jid, seen, 45)


def _post_click_code_needed(target, ctx):
    """True: a code field is on the page. False: the page was read and has none. None: it could not be
    read, or the field is ambiguous, which proves nothing either way."""
    try:
        field, why = find_code_field(build_inventory(target))
    except Exception:
        return None
    if field is not None:
        return True
    return None if why == "ambiguous" else False


def _post_click_code_step(target, page, ctx, jid, seen, facts, evidence):
    """A site holding the submission for an emailed code: one attempt, then 20s more of watching.
    Returns (kind, evidence, accepted); accepted is True only when this code was observed accepted."""
    evidence = dict(evidence)
    if STOP.is_set():
        raise Shutdown()
    code_facts = {}  # fresh: a pre-click verify_confirmed must not vouch for this code
    try:
        evidence["code_step"] = "attempted"
        # wait_for_verification is bounded by verify_timeout (default 60s) and cannot be interrupted
        # mid-poll: a stop request is honoured before it starts and again while observing afterwards.
        res = email_verification.wait_for_verification(
            ctx.verif_domains, ctx.click_since, timeout_seconds=ctx.verify_timeout, allowed_link_hosts=())
        if not res or "code" not in res:
            evidence["code_step"] = "no_code"
            return "ambiguous", evidence, False
        code = str(res["code"])
        register_secret(code)
        del res
        try:
            # a no-op mark: appending otp_entered after submit_clicked would read as out-of-order phases
            _accept_otp_code(target, page, ctx, ctx.scope, jid, ctx.url, code_facts, code,
                             ctx.verify_timeout, lambda *a, **k: None)
        finally:
            del code
    except Exception as exc:
        evidence["code_step"] = "failed_" + type(exc).__name__
        return "ambiguous", evidence, False
    accepted = code_facts.get("verify_confirmed") == 1
    kind, observed = _observe_outcome(target, ctx, jid, seen, 20)
    observed["code_step"] = evidence["code_step"]
    return kind, observed, accepted


def _settle_code_gate(target, page, ctx, jid, seen, facts, kind, evidence):
    """A site may hold the submission for an emailed code. Only a page that was read and has no code
    field keeps `kind`. An unreadable page, a gate we cannot serve, a code that was not accepted, or a
    code field still on the page afterwards is ambiguous, never confirmed."""
    need = _post_click_code_needed(target, ctx)
    if need is False:
        return kind, evidence
    evidence = dict(evidence)
    if need is None:
        if kind == "confirmed":
            evidence["code_step"] = "inspection_failed"
            return "ambiguous", evidence
        return kind, evidence
    if not ctx.verif_domains:
        evidence["code_step"] = "unserviceable"
        return "ambiguous", evidence
    kind, evidence, accepted = _post_click_code_step(target, page, ctx, jid, seen, facts, evidence)
    if not accepted:
        return "ambiguous", evidence
    if kind == "confirmed" and _post_click_code_needed(target, ctx) is not False:
        return "ambiguous", dict(evidence, code_step="code_field_remains")
    return kind, evidence


def _conflict_end(exc):
    return JobEnd("needs_review" if exc.reason.startswith("duplicate") else "failed", exc.reason)


def _close_unclicked(ledger, key):
    with contextlib.suppress(Exception):  # an unrecorded close leaves the intent live: the safe side
        ledger.append_outcome(key, "not_sent", {"stage": "pre_click"})


def live_submit(target, page, ctx, facts, attempt_id):
    """Click the approved Submit exactly once and return (status, detail). Fails closed: refuses in
    dry-run or unarmed, writes a durable ledger intent BEFORE the click, never retries, and reports
    anything it cannot prove as ambiguous (submitted_unconfirmed), never as not sent."""
    if DRY_RUN:
        raise JobEnd("failed", "invariant_live_submit_in_dry_run")
    live_gate()
    missing = live_preconditions(facts)
    if missing:
        raise JobEnd("needs_review", "live_precondition_missing=" + ",".join(missing))
    jid = ctx.jid
    if not attempt_id:
        raise JobEnd("failed", "attempt_id_missing")
    posting = submit_guard.posting_key(ctx.url)
    if posting is None:
        raise JobEnd("failed", "posting_key_invalid")
    ledger = submit_guard.SubmitLedger(SUBMIT_LEDGER_PATH)
    max_per_day, spacing_s = submit_guard.submit_pacing_from_env()
    key = submit_guard.idempotency_key(jid, attempt_id, posting)
    try:  # duplicates, the daily cap and the intent are ONE atomic step: no check-then-act window
        ts, wait_s = ledger.reserve_intent(key, jid, attempt_id, posting, max_per_day, spacing_s)
    except submit_guard.SubmitConflict as exc:
        raise _conflict_end(exc)
    except submit_guard.LedgerError:
        raise JobEnd("failed", "ledger_unreadable")
    ev("submit_intent", jid, platform=ctx.platform, key=key[:8])
    seen = []

    def on_response(resp):
        try:
            req = resp.request
            method = str(req.method).upper()
            if method not in ("GET", "HEAD") and origin_allowed(req.url):
                seen.append((method, req.url, int(resp.status)))
        except Exception:
            pass  # a missed response can only keep the outcome ambiguous; scope.mutating backs it up

    try:
        if wait_s > 0:
            ev("submit_gap_wait", jid, wait_s=f"{wait_s:.0f}")
            if STOP.wait(wait_s):
                raise Shutdown()
        try:
            ledger.recheck_reservation(key, jid, posting, spacing_s)
        except submit_guard.SubmitConflict as exc:
            raise _conflict_end(exc)
        except submit_guard.LedgerError:
            raise JobEnd("failed", "ledger_unreadable")
        assert_destination(target, ctx.url, ctx.scope, jid)
        ctx.assert_pinned(target)
        check_challenge(page, jid, target)
        try:
            n, _label, _form = universal_filler.final_control(target)
        except universal_filler.Stop as exc:
            raise JobEnd("needs_review", "submit_control_" + str(getattr(exc, "kind", None) or "unknown"))
        loc = universal_filler.control(target, n)
        try:
            usable = bool(loc.is_visible() and loc.is_enabled())
        except Exception:
            usable = False
        if not usable:
            raise JobEnd("needs_review", "submit_control_changed")
        _write_private(os.path.join(_artifact_dir(jid), "submit_attempted.json"), json.dumps(
            {"key": key, "job_id": jid, "attempt_id": attempt_id, "posting_key": posting, "ts": ts,
             "platform": ctx.platform, "url": submit_guard.canonical_apply_url(ctx.url)}, sort_keys=True))
        set_phase(jid, attempt_id, "submit_clicked")
        page.on("response", on_response)
    except BaseException:  # nothing was clicked yet: close the intent so the job is not blocked for nothing
        _close_unclicked(ledger, key)
        raise

    kind, evidence = "ambiguous", {}
    ctx.click_dispatched = False
    try:
        try:
            kind, evidence = _click_and_observe(target, page, ctx, jid, seen, loc, ledger, key)
            if kind in ("confirmed", "ambiguous"):
                # the site may be holding the submission for an emailed code: confirmed only once accepted
                kind, evidence = _settle_code_gate(target, page, ctx, jid, seen, facts, kind, evidence)
        except Shutdown:
            if ctx.click_dispatched:  # a request may still be in flight: ambiguous, never not_sent
                _close_intent(ledger, key, "ambiguous", {"shutdown": True}, jid, page, target)
            else:
                _close_unclicked(ledger, key)
            raise
        except JobEnd:
            if not ctx.click_dispatched:  # refused before anything could leave the machine
                _close_unclicked(ledger, key)
                raise
            kind, evidence = "ambiguous", {"error": "JobEnd"}
        except Exception as exc:
            kind, evidence = "ambiguous", {"error": type(exc).__name__}
    finally:
        with contextlib.suppress(Exception):
            universal_filler.rearm_guard(page)
        with contextlib.suppress(Exception):
            page.remove_listener("response", on_response)

    status, text, result, reconcile = {
        "confirmed": ("submitted", "live_submit_confirmed", "confirmed", None),
        "rejected": ("needs_review", "submit_rejected", "rejected", None),
        "click_failed": ("submitted_unconfirmed", "submit_click_failed", "ambiguous", "submit_click_failed"),
    }.get(kind, ("submitted_unconfirmed", "submit_ambiguous", "ambiguous", "submit_ambiguous"))
    _close_intent(ledger, key, result, evidence, jid, page, target, attempt_id, reconcile)
    return status, f"{text} {_compact_evidence(evidence)}".strip()


def _accept_otp_code(target, page, ctx, scope, jid, url, facts, code, verify_timeout, mark):
    assert_destination(target, url, scope, jid)  # still on the validated application page/frame
    ctx.assert_pinned(target)                    # ... and on the pinned application origin
    box, why = find_code_field(build_inventory(target))
    if box is None:
        raise JobEnd("failed", f"verify_code_field_{why}")
    before = probe_acceptance(target, box["selector"])
    mark("otp_entered")
    pace()
    loc = gate_field(target, box, ctx)  # destination + pin + uniqueness + label/name re-check
    type_into(loc, code)
    facts["code_entered"] = 1
    if loc.input_value() != code:
        raise JobEnd("failed", "verify_incomplete")
    # The typed value is NOT evidence. Confirmed only on an observed acceptance signal.
    state = observe_acceptance(target, box["selector"], before, ctx)
    facts["verify_state"] = state
    if state == "rejected":
        raise JobEnd("failed", "verify_rejected")
    facts["verify_confirmed"] = int(state == "accepted")

# ---------------------------------------------------------------- verification acceptance (observed, not assumed)
ACCEPT_PROBE_JS = r"""(sel) => {
  const el = document.querySelector(sel);
  const text = ((document.body && document.body.innerText) || '').toLowerCase().slice(0, 8000);
  const ok = /(verification|code)\s+(was\s+|is\s+)?(successful|accepted|verified|confirmed)|email\s+(address\s+)?(has\s+been\s+|is\s+)?(verified|confirmed)|successfully\s+verified|code\s+accepted/.test(text);
  const bad = /(invalid|incorrect|expired|wrong)[^.\n]{0,40}code|code[^.\n]{0,40}(invalid|incorrect|expired|wrong|did not match|try again)/.test(text);
  let visible = false;
  if (el && el.isConnected) {
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    visible = r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
  }
  return { ok: ok, bad: bad, visible: visible, locked: !!(el && (el.disabled || el.readOnly)) };
}"""


def acceptance_from(before, after):
    """'accepted' | 'rejected' | 'unknown' from two probes. Accepted needs a NEW success signal
    (success text that was not there before, or the code box going away/locked); an error text that
    is new is 'rejected'. Merely holding the typed value is never evidence."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return "unknown"
    if after.get("bad") and not before.get("bad"):
        return "rejected"
    if after.get("ok") and not before.get("ok"):
        return "accepted"
    if False:
        return "accepted"
    return "unknown"


def probe_acceptance(target, selector):
    try:
        return target.evaluate(ACCEPT_PROBE_JS, selector)
    except Exception:
        return None


def observe_acceptance(target, selector, before, ctx, polls=6, interval=1.0):
    """Watch (destination re-checked every poll) for an acceptance signal after the code was typed."""
    for _ in range(polls):
        if STOP.wait(interval):
            raise Shutdown()
        assert_destination(target, ctx.url, ctx.scope, ctx.jid)
        state = acceptance_from(before, probe_acceptance(target, selector))
        if state != "unknown":
            return state
    return "unknown"


def upload_confirmed(probe, inventory, page_text="", *, expected_name=None):
    """The resume counts as uploaded only when the input itself reported the file (name + size) and
    the page still shows it after the rebuild (file input with that name, or the name in the page)."""
    name = expected_name or os.path.basename(RESUME_PATH)
    if not probe or probe[0] != name or not probe[1]:
        return False
    if any(f.get("kind") == "file" and f.get("file_name") == name for f in inventory):
        return True
    return name.lower() in (page_text or "").lower()


def origin_of(url):
    problem, scheme, host, port = analyze_url(url)
    return None if problem else (scheme, host, port)


# ---------------------------------------------------------------- job execution
class RunCtx:
    """What the per-field gates need: job id, the (untrusted) queue URL, the job's scope, and the
    pinned origin of the application target that PII/upload/OTP may be entered on."""

    def __init__(self, jid, url, scope, platform="generic"):
        self.jid, self.url, self.scope = jid, url, scope
        self.pinned = None
        self.platform = platform
        self.verif_domains, self.verify_timeout = (), 60
        self.pre_click_url = self.submit_mut0 = self.click_since = None

    def pin(self, target):
        self.pinned = origin_of(str(getattr(target, "url", "") or ""))
        if self.pinned is None or not origin_allowed(str(getattr(target, "url", "") or "")):
            raise JobEnd("blocked", "host_not_allowed")

    def assert_pinned(self, target):
        if self.pinned is not None and origin_of(str(getattr(target, "url", "") or "")) != self.pinned:
            ev("destination_blocked", self.jid, reason="pinned_origin_changed")
            raise JobEnd("blocked", "destination_changed")


def run_job(context, job, test_base, attempt_id=None, assets=None):
    """Runs one job. Every exception that is not an explicit job outcome is converted here, while
    the in-flight secrets are still registered, into a redacted JobEnd: nothing downstream formats
    a raw exception that could contain the verification code."""
    global ACTIVE_APPROVED
    jid, platform = job["id"], str(job.get("platform") or "generic").lower()
    live_gate()
    if not DRY_RUN and not config.APPLY_RESUME_V2 and os.environ.get("APPLY_ALLOW_STATIC_RESUME") != "1":
        raise JobEnd("failed", "static_resume_not_allowed_live")
    if config.APPLY_RESUME_V2 and not DRY_RUN and not (assets and assets.resume_pdf):
        raise JobEnd("failed", "resume_not_tailored")
    resume_path = (assets.resume_pdf if (assets and config.APPLY_RESUME_V2 and assets.resume_pdf)
                   else RESUME_PATH)
    if not resume_path or not os.path.isfile(resume_path):
        raise JobEnd("failed", "resume_missing")
    url = resolve_job_url(job["url"], test_base)
    ACTIVE_APPROVED = approved_for_job(platform)  # explicit per-job origin list
    scope = JobScope(context, jid)
    failure = None
    try:
        validate_job_url(url)  # BEFORE any browser action or network request; the queue URL is never its own trust reference
        ev("approved_origins", jid, count=len(ACTIVE_APPROVED))
        return _run_job(scope, job, jid, platform, url, attempt_id,
                        assets=assets, resume_path=resume_path)
    except (JobEnd, Shutdown):
        raise
    except Exception as exc:
        failure = describe(exc)  # redaction first (secrets still registered) ...
        forget_secrets()         # ... then the secrets go
    finally:
        forget_secrets()
        try:
            scope.detach()  # pages first (route still enforcing), then hooks/route; never the browser or other tabs
        finally:
            ACTIVE_APPROVED = None
    raise JobEnd("failed", failure)  # outside the except block: no raw exception rides along as context


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _upload_and_observe(loc, target, path):
    """set_input_files, then (probe, rebuilt inventory, page text); the upload may add/replace controls."""
    loc.set_input_files(path)
    try:
        probe = loc.evaluate(UPLOAD_PROBE_JS)
    except Exception:
        probe = None
    inventory = build_inventory(target)
    try:
        text = target.evaluate("() => (document.body && document.body.innerText) || ''")
    except Exception:
        text = ""
    return probe, inventory, text


def _run_job(scope, job, jid, platform, url, attempt_id, assets=None, resume_path=None):
    notes = str(job.get("notes") or "")
    m = re.search(r"verify_timeout_s=(\d+)", notes)
    verify_timeout = min(max(int(m.group(1)), 1), 600) if m else 60
    verif = job.get("verification") or {}
    domains = [d for d in (verif.get("sender_domains") or []) if d]
    verif_type = str(verif.get("type") or "code").lower()
    facts = {}

    def mark(phase):  # persisted BEFORE the side effect it names
        if attempt_id:
            set_phase(jid, attempt_id, phase)

    ctx = RunCtx(jid, url, scope, platform)
    ctx.verif_domains, ctx.verify_timeout = domains, verify_timeout
    scope.attach()  # page listener + route, installed before any navigation
    pace()
    page = scope.open_page()  # one new tab per job; existing tabs are never touched
    page.add_init_script(SUBMIT_GUARD_JS)  # per page, installed before any navigation
    if DRY_RUN:
        page.add_init_script(DRY_RUN_JS)
    ev("page_open", jid, platform=platform, url=clean_url(url))
    mark("navigating")  # BEFORE the first request: a crash from here on is never auto-requeued
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except PWError:
        scope.check()  # a blocked redirect/popup is a 'blocked' job, not a generic failure
        raise
    settle(page)
    assert_destination(page, url, scope, jid)  # after redirects, before anything reads the page
    check_challenge(page, jid)
    assert_destination(page, url, scope, jid)
    if safe_count(universal_filler.guard_count, page) < 0:
        raise JobEnd("failed", "guard_not_installed")
    g0, n0 = safe_count(universal_filler.guard_count, page), safe_count(universal_filler.network_count, page)
    title0 = safe_title(page)
    since = datetime.now(timezone.utc)  # BEFORE any action that may trigger the email
    mark("navigated")

    if platform == "workday":
        try:
            pace()
            assert_destination(page, url, scope, jid)
            workday_adapter.enter_apply_flow(page)
            assert_destination(page, url, scope, jid)
            pace()
            mark("filled")
            workday_adapter.fill_known_fields(page, STANDARD_FIELD_VALUES)
        except workday_adapter.WorkdayStop:
            pass
        raise JobEnd("blocked", "workday_needs_session")  # anything beyond is out of scope

    target, filled = page, []
    if platform in ATS_FILLERS:
        pace()
        assert_destination(page, url, scope, jid)
        mark("filled")
        res = ATS_FILLERS[platform](page, STANDARD_FIELD_VALUES)
        filled = sorted(k for k, ok in res.items() if ok)
        assert_destination(page, url, scope, jid)
    else:
        pace()
        target = universal_filler.application_target(page, url)
        assert_destination(target, url, scope, jid)
    ctx.pin(target)  # PII / upload / OTP are only ever entered on this approved origin
    if platform not in ATS_FILLERS:
        tried, marked = set(), False
        for _ in range(len(STANDARD_FIELD_VALUES) + 4):
            inventory = build_inventory(target)  # rebuilt after EACH typed field
            plan = universal_filler.contact_plan(inventory)
            nxt = None
            for sel, key in plan.items():
                field = field_for(inventory, sel)
                ident = (key, (field or {}).get("label"), (field or {}).get("name"))
                if field is not None and ident not in tried:
                    nxt = (field, key, ident)
                    break
            if nxt is None:
                break
            field, key, ident = nxt
            tried.add(ident)
            val = STANDARD_FIELD_VALUES.get(key) or STANDARD_FIELD_VALUES.get(KEY_ALIASES.get(key, ""))
            if not val:
                continue
            if not marked:
                mark("filled")
                marked = True
            pace()
            type_into(gate_field(target, field, ctx), val)
            filled.append(key)
    facts["fields_filled"] = len(filled)
    ev("fill_done", jid, fields=",".join(filled) or "none")
    check_challenge(page, jid, target)
    assert_destination(target, url, scope, jid)

    pace()
    inventory = build_inventory(target)
    file_sel = universal_filler.file_targets(inventory).get("resume")
    field = field_for(inventory, file_sel) if file_sel else None
    facts["resume_uploaded"] = 0
    resume_path = resume_path or RESUME_PATH
    if assets is not None:
        facts["cover_letter_uploaded"] = 0
    if field is not None:
        if assets is not None:
            actual = _sha256_file(resume_path)
            if actual != (assets.resume_manifest or {}).get("pdf_sha256"):
                raise JobEnd("failed", "resume_tampered")
            if assets.resume_pdf and os.path.isfile(RESUME_PATH) and actual == _sha256_file(RESUME_PATH):
                raise JobEnd("failed", "resume_not_tailored")
        mark("uploaded")
        loc = gate_field(target, field, ctx)
        probe, inventory, text = _upload_and_observe(loc, target, resume_path)
        facts["resume_uploaded"] = int(upload_confirmed(  # counted only once observed
            probe, inventory, text, expected_name=os.path.basename(resume_path)))
    if assets is not None:
        cover_sel = universal_filler.file_targets(inventory).get("cover_letter")
        cover_field = field_for(inventory, cover_sel) if cover_sel else None
        letter = getattr(assets, "letter", None)
        letter_pdf = getattr(letter, "pdf_path", None)
        usable = bool(letter_pdf and config.APPLY_COVER_LETTER)
        if cover_field is not None and cover_field.get("required") and not usable:
            raise JobEnd("needs_review", "cover_letter_required_ungrounded")
        if cover_field is not None and usable:
            expected = (getattr(letter, "manifest", None) or {}).get("pdf_sha256")
            if expected and _sha256_file(letter_pdf) != expected:
                raise JobEnd("failed", "cover_letter_tampered")
            mark("uploaded")
            cprobe, inventory, ctext = _upload_and_observe(
                gate_field(target, cover_field, ctx), target, letter_pdf)
            facts["cover_letter_uploaded"] = int(upload_confirmed(
                cprobe, inventory, ctext, expected_name=os.path.basename(letter_pdf)))
    ev("upload_done", jid, uploaded=facts["resume_uploaded"])
    check_challenge(page, jid, target)

    def audit():
        """(Re)answer work-auth questions with one action per rebuilt inventory, then derive every
        count from the FINAL page state; also reports a verification field the page itself shows."""
        answered, unanswered, review = answer_workauth(
            target, build_inventory(target), gate=lambda f: gate_field(target, f, ctx),
            rebuild=lambda: build_inventory(target))
        inv = build_inventory(target)
        total, unfilled = required_accounting(inv)
        code_field, code_why = find_code_field(inv)
        facts.update(workauth_answered=",".join(answered) or "none", unanswered_questions=unanswered,
                     for_review=len(review), required_total=total, required_unfilled=unfilled,
                     blank_textareas=blank_textareas(inv))
        facts.pop("review_reasons", None)
        if review:
            facts["review_reasons"] = ",".join(sorted({r[0] for r in review}))
        facts["verify_required"] = int(bool(domains) or code_field is not None or code_why == "ambiguous")
        return inv, review

    inventory, review = audit()
    if facts["verify_required"] and "verify_confirmed" not in facts:
        facts.update(verify_received=0, code_entered=0, verify_confirmed=0)
    if domains:
        assert_destination(target, url, scope, jid)
        ctx.assert_pinned(target)
        link_hosts = (tuple(sorted({h for h in [urlsplit(url).hostname] + [d.lower() for d in domains] if h}))
                      if verif_type == "magic_link" else ())
        pace()
        mark("otp_requested")
        res = email_verification.wait_for_verification(
            domains, since,
            timeout_seconds=(max(verify_timeout, 120) if verif_type == "magic_link" else verify_timeout),
            allowed_link_hosts=link_hosts)
        outcome = "code_received" if res and "code" in res else "link_received" if res and "link" in res else "none"
        ev("verify_result", jid, outcome=outcome)
        facts["verify"] = outcome
        if outcome == "link_received":
            global ACTIVE_APPROVED
            link = str(res["link"]); register_secret(link); del res
            try:
                # Full origin validation BEFORE navigation or approval widening:
                # same policy as origin_allowed (https, port 443, non-IP, suffix match).
                # Redirect hops during goto are additionally held to approved origins
                # by the route layer (net_decision).
                problem, _scheme, lh, _port = analyze_url(link)
                if problem or _scheme != "https" or _port != 443 or _is_ip(lh) or not any(
                        lh == h or lh.endswith("." + h) for h in link_hosts):
                    raise JobEnd("blocked", "magic_link_host_rejected")
                ev("magic_link_navigating", jid)
                mark("magic_link_open")
                pace()
                if DRY_RUN:
                    # Validated but not redeemed: the token is single-use; dry runs
                    # must not consume it.
                    ev("magic_link_dryrun", jid)
                else:
                    # Narrow, explicit widening consistent with the module's suffix
                    # model; run_job's finally resets ACTIVE_APPROVED=None per job.
                    ACTIVE_APPROVED = tuple(sorted(set(ACTIVE_APPROVED or ()) | {lh}))
                    page.goto(link, wait_until="domcontentloaded", timeout=45000)
                    assert_destination(target, link, scope, jid)
                    ctx.assert_pinned(target)
                    check_challenge(page, jid, target)
                    # Positive authenticated signal: must hold on 2 consecutive
                    # polls (defeats transient intermediate pages). Requires:
                    #  1. interactive content present (not a blank/error shell)
                    #  2. verification UI gone (no code field remains)
                    #  3. non-empty page text with no login/expired-link language
                    # Downstream gates (audit -> form fill -> final_control) remain
                    # the arbiters of submittability; this gates verify_confirmed.
                    stable_hits = 0
                    for _ in range(12):
                        inv = build_inventory(target)
                        box2, _ = find_code_field(inv)
                        body_text = target.evaluate(
                            "() => document.body ? document.body.innerText.slice(0, 10000) : ''") or ""
                        if (inv and box2 is None and body_text.strip()
                                and not _MAGIC_LINK_NEGATIVE_RE.search(body_text)):
                            stable_hits += 1
                            if stable_hits >= 2:
                                break
                        else:
                            stable_hits = 0
                        page.wait_for_timeout(1000)
                    else:
                        raise JobEnd("blocked", "magic_link_no_session")
                    facts["verify_received"] = 1
                    facts["verify_confirmed"] = 1
                    ev("verify_confirmed", jid, via="magic_link")
            finally:
                del link
        if outcome == "code_received":
            facts["verify_received"] = 1
            code = str(res["code"])
            register_secret(code)
            del res
            try:
                _accept_otp_code(target, page, ctx, scope, jid, url, facts, code, verify_timeout, mark)
            finally:
                del code
        elif outcome == "none" and verif_type == "magic_link" and not DRY_RUN:
            raise JobEnd("blocked", "magic_link_timeout")
        elif outcome == "none" and not DRY_RUN:
            raise JobEnd("failed", "verify_timeout")
        check_challenge(page, jid, target)
    inventory, review = audit()  # typing the code / answering may have revealed or changed controls
    for why, kind, sel, text in review:
        ev("for_review", jid, reason=why, kind=kind, selector=sel, question=text)

    pace()  # before the final step
    assert_destination(target, url, scope, jid)
    ctx.assert_pinned(target)
    try:
        universal_filler.final_control(target)  # returns the control's INDEX, not a count
    except universal_filler.Stop as exc:
        kind = str(getattr(exc, "kind", None) or (exc.args[0] if exc.args else ""))
        raise JobEnd("blocked", "multi_step_unsupported" if "multi" in kind else f"final_step_stop_{kind or 'unknown'}")
    mark("final_reached")
    facts["submit_candidates"] = 1  # final_control raises Stop unless exactly one strict submit control exists
    g1, n1 = safe_count(universal_filler.guard_count, page), safe_count(universal_filler.network_count, page)
    facts["guard_blocked"] = g1 - g0 if min(g0, g1) >= 0 else -1
    facts["net_blocked"] = n1 - n0 if min(n0, n1) >= 0 else -1
    facts["route_blocked"] = scope.blocked
    facts["title_unchanged"] = int(title0 == safe_title(page))
    scope.check()  # any popup that appeared meanwhile ends the job as blocked

    if DRY_RUN:
        missing = dry_run_missing(facts)
        if missing:
            return "dry_run_incomplete", f"missing={','.join(missing)} " + fmt_facts(facts)
        ev("dry_run_submit_skipped", jid, submit_candidates=1)
        return "dry_run_ok", "dry_run_submit_skipped " + fmt_facts(facts)
    # Live preconditions: the same bar as the dry run. Any gap ends the job as
    # needs_review WITHOUT clicking submit.
    missing = live_preconditions(facts)
    if missing:
        return "needs_review", "live_precondition_missing=" + ",".join(missing) + " " + fmt_facts(facts)
    return live_submit(target, page, ctx, facts, attempt_id)


def fmt_facts(facts):
    return " ".join(f"{k}={v}" for k, v in facts.items())


def prepare_assets(job, attempt_id):
    """Pre-browser asset stage. Returns AssetBundle|None. Every failure raises JobEnd(needs_review,...)."""
    if not config.APPLY_RESUME_V2:
        return None
    # An empty tuple in an except clause matches nothing, so the AssetBuildError clause below is inert
    # (and cannot raise NameError) if the imports fail with anything other than ImportError.
    _v2build_error = ()
    try:
        from resume.v2 import build as _v2build
        from resume.v2 import tailor_v2
        _v2build_error = _v2build.AssetBuildError
        jd_text = ((job.get("posting_snapshot") or {}).get("description")
                   or job.get("description") or "")
        if len(str(jd_text).strip()) < 200:
            raise JobEnd("needs_review", "resume_build_failed:no_jd")
        profile = tailor_v2.load_candidate_profile()
        bank_facts = {
            "full_name": STANDARD_FIELD_VALUES.get("name", ""),
            "email": STANDARD_FIELD_VALUES.get("email", ""),
            "phone": STANDARD_FIELD_VALUES.get("phone", ""),
            "linkedin": STANDARD_FIELD_VALUES.get("linkedin", ""),
            "location": STANDARD_FIELD_VALUES.get("location", ""),
        }
        master_sha = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
        artifacts_root = os.environ.get("APPLY_ARTIFACTS_DIR") or "/opt/jobagent24/artifacts"
        return _v2build.build_assets(
            job, jd_text, profile, bank_facts, artifacts_root, master_md_sha=master_sha,
            renew_lease=lambda: renew_lease(job["id"], attempt_id))
    except JobEnd:
        raise
    except Shutdown:
        raise
    except ImportError:
        ev("resume_build_failed", job["id"], stage="unavailable")
        raise JobEnd("needs_review", "resume_build_failed:unavailable") from None
    except _v2build_error as exc:
        ev("resume_build_failed", job["id"], stage=exc.stage)
        raise JobEnd("needs_review", f"resume_build_failed:{exc.stage}") from None
    except Exception as exc:
        ev("resume_build_failed", job["id"], stage="unexpected", error=type(exc).__name__)
        raise JobEnd("needs_review", "resume_build_failed:unexpected") from None


def process_job(context, job, test_base):
    """Returns the final result status, or None when the job was not claimable."""
    jid = job["id"]
    attempt_id = claim_job(jid)
    if attempt_id is None:
        ev("job_claim_skipped", jid, reason="not_pending")
        return None
    # version 3: supersedes the version-2 backfilled "queued" rows (and version-1 stubs).
    try:
        import outbox
        conn = _outbox_conn()
        if conn is not None:
            outbox.enqueue(conn, "job", jid, "upsert",
                           {"id": jid, "company": job.get("company", ""), "role": job.get("role", ""),
                            "url": job.get("url", ""), "platform": job.get("platform", ""),
                            "state": "claimed", "version": 3}, version=3)
    except Exception as exc:
        log.info(f"outbox_skipped entity=job reason={type(exc).__name__}")

    started, t0 = now_iso(), time.monotonic()
    ev("job_start", jid, company=job.get("company", ""), platform=job.get("platform", ""),
       dry_run=int(DRY_RUN), attempt_id=attempt_id)
    try:
        assets = prepare_assets(job, attempt_id)  # pre-browser; a JobEnd lands in the shared tail below
        status, detail = run_job(context, job, test_base, attempt_id, assets=assets)
    except Shutdown:
        new = release_after_shutdown(jid, attempt_id)
        ev("job_result", jid, status=new, detail="shutdown_requeued" if new == "pending" else "shutdown_needs_reconciliation")
        raise
    except JobEnd as end:
        status, detail = end.status, end.detail
    except Exception as exc:  # incl. universal_filler.Stop, WorkdayStop, playwright errors
        status, detail = "failed", describe(exc)
    latency = time.monotonic() - t0
    final = write_result(jid, started, now_iso(), status, detail, latency, attempt_id)
    finish_job(jid, attempt_id, STATE_FOR_STATUS.get(final, final))
    ev("job_result", jid, status=final, detail=detail if final == status else "invariant_violation",
       latency_s=f"{latency:.2f}", attempt_id=attempt_id)
    return final


# ---------------------------------------------------------------- main loop
def connect(pw):
    try:
        browser = pw.chromium.connect_over_cdp(CDP_URL, timeout=10000)
        if browser.contexts:
            return browser
        ev("cdp_unreachable", reason="no_context")
    except Exception as exc:
        ev("cdp_unreachable", reason=type(exc).__name__)
    return None


def setup_logging():
    handlers = [logging.StreamHandler(sys.stdout)]
    if LOG_PATH:
        fd = os.open(LOG_PATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.close(fd)
        ensure_private(LOG_PATH)
        handlers.append(logging.FileHandler(LOG_PATH))
    logging.basicConfig(handlers=handlers, level=logging.INFO, format="%(levelname)s %(message)s", force=True)


def network_self_test(context, test_base):
    """Dry-run only. Tries to SEND three things from a real page under the job route (a native form
    POST, a fetch POST to the local server, a fetch POST to a foreign host) and passes only if all
    were blocked at the route and the local server never received a POST. Live mode: not applicable."""
    if not DRY_RUN:
        return True
    scope = JobScope(context, "selftest")
    hits0 = len(POST_HITS)
    try:
        scope.attach()
        page = scope.open_page()
        page.goto(test_base + "/__probe", wait_until="domcontentloaded", timeout=15000)
        blocked0 = scope.blocked
        for js in (f"fetch({json.dumps(test_base + '/__probe_post')}, {{method: 'POST', body: 'x'}}).then(() => 'sent', () => 'failed')",
                   "fetch('https://selftest.invalid/p', {method: 'POST', body: 'x'}).then(() => 'sent', () => 'failed')",
                   "document.getElementById('f').submit()"):
            with contextlib.suppress(PWError):  # the form submit tears the document down mid-evaluate
                page.evaluate(js)
        # The sync API only delivers route events while it is pumped: time.sleep/STOP.wait would
        # leave the aborted POST unobserved, so wait through the page.
        pump = getattr(page, "wait_for_timeout", None)
        if callable(pump):
            pump(2000)
        else:
            STOP.wait(1.5)
        blocked = scope.blocked - blocked0
        ok = len(POST_HITS) == hits0 and blocked >= 3
        ev("network_self_test", ok=int(ok), blocked=blocked, server_posts=len(POST_HITS) - hits0)
        return ok
    except Exception as exc:
        ev("network_self_test", ok=0, reason=describe(exc))
        return False
    finally:
        scope.detach()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="process all pending jobs then exit")
    ap.add_argument("--self-test", action="store_true", help="run the dry-run network-isolation self-test and exit")
    args = ap.parse_args()
    once = args.once
    os.umask(0o077)  # every file we create (state, lock, tmp, results, log) is owner-only
    try:
        setup_logging()
    except StorageError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    try:
        if not acquire_singleton():
            ev("singleton_held", owner=OWNER_ID)
            return 3
    except StorageError as exc:
        ev("storage_error", reason=str(exc))
        return 2
    except OSError as exc:
        ev("storage_preflight_failed", reason=type(exc).__name__)
        return 2
    try:
        return _main(once, args.self_test)
    except StorageError as exc:  # a privacy guarantee failed mid-run: stop, nothing continues unprotected
        ev("storage_error", reason=str(exc))
        return 2


def _main(once, self_test_only):
    try:
        config.require_secrets()
    except Exception as exc:
        ev("config_invalid", reason=type(exc).__name__)
        return 2
    ev("pipeline_start", dry_run=int(DRY_RUN), once=int(once), cdp=clean_url(CDP_URL), owner=OWNER_ID)
    why = preflight_storage()
    if why:
        ev("storage_preflight_failed", reason=why)
        return 2
    if not self_test_only:
        try:  # refuse to run on an invalid queue, before any browser action
            load_queue()
        except QueueError:
            return 2
    test_base = start_test_server()
    register_local_origin(test_base)
    rc, outcomes, skipped = 0, [], set()
    recovery = {"terminal": [], "requeued": [], "reconcile": [], "crash_loop": []}
    selftest_done = not DRY_RUN

    def absorb(out):
        for kind in recovery:
            recovery[kind].extend(out[kind])
        return out["reconcile"] or out["crash_loop"]

    try:
        if not self_test_only:
            absorb(recover_in_progress_ex())
            announce_reconciliation()
        with sync_playwright() as pw:
            browser = None
            while not STOP.is_set():
                try:
                    if not self_test_only and absorb(recover_in_progress_ex()):
                        announce_reconciliation()
                    queue = []
                    if not self_test_only:
                        try:
                            queue = load_queue()
                        except QueueError:
                            if once:
                                rc = max(rc, 2)
                                break
                            STOP.wait(IDLE_S)
                            continue
                    jobs = [j for j in pending_jobs(queue) if j["id"] not in skipped]
                    if not jobs and not self_test_only:
                        if once:
                            break
                        ev("idle", wait_s=IDLE_S)
                        STOP.wait(IDLE_S)
                        continue
                    if browser is None or not browser.is_connected():
                        browser = connect(pw)
                    if browser is None:
                        if once or self_test_only:
                            rc = max(rc, 1)
                            break
                        STOP.wait(CDP_BACKOFF_S)  # jobs stay pending
                        continue
                    if not selftest_done or self_test_only:
                        if not network_self_test(browser.contexts[0], test_base):
                            ev("network_self_test_failed")  # no job may run on an unproven isolation
                            rc = 2
                            break
                        selftest_done = True
                        if self_test_only:
                            break
                    from urllib.parse import urlparse as _up
                    _ctx0 = browser.contexts[0]
                    _allowed = set()
                    if isinstance(test_base, str) and test_base:
                        _p = _up(test_base)
                        _allowed.add(f"{_p.scheme}://{_p.netloc}")
                    _bad = []
                    for _sw in list(_ctx0.service_workers):
                        _p = _up(_sw.url)
                        _o = f"{_p.scheme}://{_p.netloc}"
                        if _o not in _allowed:
                            _bad.append(_sw.url)
                    for _pg0 in list(_ctx0.pages):
                        try:
                            if not _pg0.url.startswith("http"):
                                continue
                            _regs = _pg0.evaluate(
                                "() => navigator.serviceWorker.getRegistrations().then("
                                "r => r.map(x => x.scope))"
                            )
                        except Exception:
                            continue
                        for _sc in _regs or []:
                            _p = _up(_sc)
                            if f"{_p.scheme}://{_p.netloc}" not in _allowed:
                                _bad.append(_sc)
                    if _bad:
                        raise RuntimeError(f"service worker registration for non-allowed origin: {_bad}")
                    final = process_job(browser.contexts[0], jobs[0], test_base)
                    if final is None:
                        skipped.add(jobs[0]["id"])
                    else:
                        outcomes.append(final)
                except Shutdown:
                    break
                except StateCorrupt:
                    rc = 2
                    break
                except Exception as exc:  # main loop must never crash
                    ev("loop_error", reason=describe(exc))
                    if once:
                        rc = max(rc, 1)
                        break
                    STOP.wait(IDLE_S)
    except Shutdown:
        pass
    except StateCorrupt:
        rc = 2
    if once and not self_test_only:
        bad = [o for o in outcomes if o in BAD_OUTCOMES]
        try:
            stuck, live = announce_reconciliation(), unexpired_claims()
        except StateCorrupt:
            stuck, live, rc = [], [], 2
        recovered = sum(len(v) for v in recovery.values())
        if live:
            ev("unexpired_claims_remain", count=len(live), ids=",".join(live))
        if bad or stuck or live or recovered:
            rc = max(rc, 1)
    ev("pipeline_stop", rc=rc, outcomes=",".join(outcomes) or "none")
    return rc


SUBMIT_GUARD_JS = universal_filler.SUBMIT_GUARD

if __name__ == "__main__":
    sys.exit(main())
