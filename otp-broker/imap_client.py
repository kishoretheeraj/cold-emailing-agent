"""Single-connection Gmail IMAP mailbox with IDLE, polling fallback and reconnect.

``Mailbox`` owns exactly one IMAP connection.  A background thread keeps it alive
(IDLE, or 10 s polling when degraded) and every other operation borrows it via a
lock; borrowing first ends any running IDLE.  The password is read from a file on
every connect and is never logged.
"""
from __future__ import annotations

import email
import imaplib
import logging
import random
import re
import select
import socket
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import Message
from email.utils import parseaddr
from pathlib import Path
from typing import Callable, Iterator

log = logging.getLogger("otp_broker.imap")

AUTH_FAILURE_LIMIT = 3
BACKOFF_CAP_S = 60.0
IDLE_REISSUE_S = 5 * 60  # short, so a silently dead connection is noticed within ~5 min
STABLE_CONNECTION_S = 30.0  # backoff only resets after a connection survived this long
AUTH_PROBE_INTERVAL_S = 30 * 60  # half-open breaker probe
KEEPALIVE_IDLE_S, KEEPALIVE_INTVL_S, KEEPALIVE_CNT = 60, 10, 3
IDLE_SLICE_S = 1.0
POLL_INTERVAL_S = 10.0
DEGRADED_RETRY_IDLE_S = 30 * 60
MAX_FETCH = 50
CACHE_SIZE = 300
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class AuthFailed(Exception):
    """Gmail rejected the credentials (AUTHENTICATIONFAILED)."""


class MailboxUnavailable(Exception):
    """No usable IMAP connection right now."""


class IdleStalled(Exception):
    """No server activity within twice the expected IDLE window."""


@dataclass(frozen=True)
class MailMessage:
    uidvalidity: int
    uid: int
    message_id: str
    sender: str  # bare address, lower-cased
    subject: str
    received_at: datetime  # tz-aware UTC (INTERNALDATE)
    msg: Message


def backoff_delay(attempt: int, rng: Callable[[], float] = random.random) -> float:
    """1s, 2s, 4s ... capped at 60s, with +/-20 % jitter (never above the cap)."""
    base = min(BACKOFF_CAP_S, 2.0**attempt)
    return min(BACKOFF_CAP_S, base * (0.8 + 0.4 * rng()))


def _imap_date(dt: datetime) -> str:
    return f"{dt.day:02d}-{_MONTHS[dt.month - 1]}-{dt.year}"


def _parse_internaldate(meta: bytes) -> datetime:
    m = re.search(rb'INTERNALDATE "(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d\d):(\d\d):(\d\d) ([+-]\d{4})"', meta)
    if not m:
        raise ValueError("no INTERNALDATE")
    day, mon, year, hh, mm, ss, tz = m.groups()
    sign = -1 if tz[:1] == b"-" else 1
    offset = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
    local = datetime(
        int(year), _MONTHS.index(mon.decode().title()) + 1, int(day), int(hh), int(mm), int(ss),
        tzinfo=timezone(offset),
    )
    return local.astimezone(timezone.utc)


class Mailbox:
    def __init__(
        self,
        user: str,
        password_file: str | Path,
        *,
        host: str = "imap.gmail.com",
        port: int = 993,
        mark_seen: bool = False,
        imap_factory: Callable[..., imaplib.IMAP4] | None = None,
        on_new_mail: Callable[[], None] | None = None,
        poll_interval: float = POLL_INTERVAL_S,
        idle_reissue: float = IDLE_REISSUE_S,
        clock: Callable[[], float] = time.monotonic,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._user = user
        self._password_file = Path(password_file)
        self._host, self._port = host, port
        self._mark_seen = mark_seen
        self._factory = imap_factory or (lambda h, p, **kw: imaplib.IMAP4_SSL(h, p, **kw))
        self._on_new_mail = on_new_mail
        self._poll_interval = poll_interval
        self._idle_reissue = idle_reissue
        self._clock = clock
        self._rng = rng

        self._lock = threading.Lock()  # guards the connection and everything below it
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._conn: imaplib.IMAP4 | None = None
        self._uidvalidity = 0
        self._idling = False
        self._idle_tag = b""
        self._idle_started = 0.0
        self._last_activity = 0.0
        self._tag_counter = 0
        self._cache: OrderedDict[tuple[int, int], MailMessage] = OrderedDict()

        self._degraded = False
        self._degraded_since = 0.0
        self._auth_failures = 0
        self._auth_tripped = False
        self._last_auth_failure: datetime | None = None
        self._last_poll: datetime | None = None
        self._last_error: str | None = None
        self._tripped_at = 0.0
        self._connected_at = 0.0

    # -- status -----------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._conn is not None

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def auth_failed(self) -> bool:
        return self._auth_tripped

    @property
    def last_successful_poll(self) -> datetime | None:
        return self._last_poll

    @property
    def last_auth_failure(self) -> datetime | None:
        return self._last_auth_failure

    @property
    def last_error(self) -> str | None:
        """Class name / short reason of the latest failure (never the password)."""
        return self._last_error

    @property
    def state(self) -> str:
        if self._auth_tripped:
            return "auth_failed"
        if self._conn is None:
            return "reconnecting"
        return "degraded" if self._degraded else "connected"

    def _mark_poll(self) -> None:
        self._last_poll = datetime.now(timezone.utc)

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name="imap-mailbox", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        self._drop_conn()

    def _sleep(self, seconds: float) -> None:
        self._stop.wait(seconds)

    def _notify(self) -> None:
        if self._on_new_mail:
            try:
                self._on_new_mail()
            except Exception:  # never let a callback kill the IMAP thread
                log.exception("on_new_mail callback failed")

    # -- connection -------------------------------------------------------
    def _read_password(self) -> str:
        try:
            return self._password_file.read_text().strip()
        except OSError as exc:  # missing/unreadable credential file is an auth-class failure
            raise AuthFailed(f"password file: {type(exc).__name__}") from None

    @staticmethod
    def _enable_keepalive(conn: imaplib.IMAP4) -> None:
        """TCP keepalive so half-open peers are detected by the kernel."""
        sock = getattr(conn, "sock", None)
        if sock is None:
            return
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            for name, val in (("TCP_KEEPIDLE", KEEPALIVE_IDLE_S), ("TCP_KEEPINTVL", KEEPALIVE_INTVL_S),
                              ("TCP_KEEPCNT", KEEPALIVE_CNT)):
                if hasattr(socket, name):
                    sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), val)
        except OSError as exc:
            log.warning("could not enable TCP keepalive: %s", type(exc).__name__)

    @staticmethod
    def _is_auth_error(exc: Exception) -> bool:
        text = str(exc).upper()
        return any(k in text for k in ("AUTHENTICATIONFAILED", "[ALERT]", "INVALID CREDENTIALS", "AUTHENTICATION FAILED"))

    def _connect(self) -> None:
        conn = self._factory(self._host, self._port, timeout=60)
        try:
            self._enable_keepalive(conn)
            try:
                conn.login(self._user, self._read_password())
            except imaplib.IMAP4.error as exc:
                if self._is_auth_error(exc):
                    raise AuthFailed from None
                raise
            typ, _ = conn.select("INBOX", readonly=not self._mark_seen)
            if typ != "OK":
                raise imaplib.IMAP4.error("SELECT INBOX failed")
            uv = conn.response("UIDVALIDITY")[1]
            uidvalidity = int(uv[0]) if uv and uv[0] else 0
        except BaseException:
            self._close(conn)
            raise
        with self._lock:
            if uidvalidity != self._uidvalidity:
                self._cache.clear()
            self._conn, self._uidvalidity = conn, uidvalidity
            self._idling = False
        self._auth_failures = 0
        self._connected_at = self._clock()
        self._last_error = None
        log.info("imap connected, uidvalidity=%s", uidvalidity)

    @staticmethod
    def _close(conn: imaplib.IMAP4) -> None:
        try:
            conn.shutdown()
        except Exception:
            pass

    def _drop_conn(self) -> None:
        with self._lock:
            conn, self._conn, self._idling = self._conn, None, False
        if conn is not None:
            self._close(conn)

    # -- main loop --------------------------------------------------------
    def run(self) -> None:
        """Keep the connection alive until ``stop()``.  Runs in its own thread."""
        attempt = 0
        while not self._stop.is_set():
            if self._auth_tripped:
                # Half-open breaker: one login probe every AUTH_PROBE_INTERVAL_S.
                if self._clock() - self._tripped_at >= AUTH_PROBE_INTERVAL_S:
                    self._probe_auth()
                else:
                    self._sleep(5)
                continue
            try:
                if self._conn is None:
                    self._connect()  # backoff is NOT reset here: only after the link proves stable
                elif attempt and self._clock() - self._connected_at >= STABLE_CONNECTION_S:
                    attempt = 0
                if self._degraded and self._clock() - self._degraded_since > DEGRADED_RETRY_IDLE_S:
                    log.info("leaving degraded mode, retrying IDLE")
                    self._degraded = False
                if self._degraded:
                    self._poll_once()
                    self._sleep(self._poll_interval)
                else:
                    self._idle_step()
            except AuthFailed as exc:
                self._auth_failures += 1
                self._last_auth_failure = datetime.now(timezone.utc)
                self._last_error = str(exc) or "AuthFailed"
                log.error("imap login rejected (%s) (%d/%d)", self._last_error, self._auth_failures, AUTH_FAILURE_LIMIT)
                if self._auth_failures >= AUTH_FAILURE_LIMIT:
                    self._trip_breaker()
                else:
                    self._sleep(backoff_delay(attempt, self._rng))
                    attempt += 1
            except IdleStalled:
                self._last_error = "IdleStalled"
                log.warning("IDLE stalled; tearing down, falling back to polling")
                self._enter_degraded()
                self._drop_conn()
            except Exception as exc:
                self._last_error = type(exc).__name__
                delay = backoff_delay(attempt, self._rng)
                log.warning("imap error %s; reconnecting in %.1fs", type(exc).__name__, delay)
                self._drop_conn()
                self._sleep(delay)
                attempt += 1

    def _trip_breaker(self) -> None:
        self._auth_tripped = True
        self._tripped_at = self._clock()
        log.critical("auth circuit open: pausing IMAP logins (reason=auth_failed); probing again in %d min",
                     AUTH_PROBE_INTERVAL_S // 60)

    def _probe_auth(self) -> None:
        """Half-open state: attempt exactly one login; success closes the breaker."""
        log.warning("auth circuit half-open: probing IMAP login")
        try:
            self._connect()
        except AuthFailed as exc:
            self._last_auth_failure = datetime.now(timezone.utc)
            self._last_error = str(exc) or "AuthFailed"
            self._tripped_at = self._clock()
            log.error("auth probe failed (%s); breaker stays open", self._last_error)
        except Exception as exc:
            self._last_error = type(exc).__name__
            self._tripped_at = self._clock()
            log.warning("auth probe inconclusive (%s); will retry later", type(exc).__name__)
        else:
            self._auth_tripped = False
            self._auth_failures = 0
            log.warning("auth probe succeeded: circuit closed, IMAP resumed")

    def _enter_degraded(self) -> None:
        self._degraded = True
        self._degraded_since = self._clock()

    def _poll_once(self) -> None:
        with self._exclusive() as conn:
            conn.noop()
            self._mark_poll()
        self._notify()

    # -- IDLE -------------------------------------------------------------
    def _wait_readable(self, conn: imaplib.IMAP4, timeout: float) -> bool:
        sock = conn.sock
        if getattr(sock, "pending", lambda: 0)() > 0:
            return True
        return bool(select.select([sock], [], [], timeout)[0])

    def _start_idle(self, conn: imaplib.IMAP4) -> None:
        self._tag_counter += 1
        self._idle_tag = b"OTPI%d" % self._tag_counter
        conn.send(self._idle_tag + b" IDLE\r\n")
        line = conn.readline()
        if not line.startswith(b"+"):
            raise imaplib.IMAP4.error(f"IDLE rejected: {line[:60]!r}")
        self._idling = True
        self._idle_started = self._last_activity = self._clock()

    def _end_idle(self, conn: imaplib.IMAP4) -> bool:
        """Send DONE and consume lines up to the tagged OK.  True if EXISTS was seen."""
        conn.send(b"DONE\r\n")
        exists = False
        while True:
            line = conn.readline()
            if not line:
                raise ConnectionError("connection closed while ending IDLE")
            exists |= bool(re.match(rb"\* \d+ EXISTS", line))
            if line.startswith(self._idle_tag):
                if b" OK" not in line[:len(self._idle_tag) + 4]:
                    raise imaplib.IMAP4.error("IDLE termination failed")
                break
        self._idling = False
        self._last_activity = self._clock()
        return exists

    def _idle_step(self) -> None:
        """One ≤1 s slice of IDLE; the lock is released between slices."""
        exists = False
        with self._lock:
            conn = self._conn
            if conn is None:
                return
            now = self._clock()
            if not self._idling:
                try:
                    self._start_idle(conn)
                except imaplib.IMAP4.error as exc:
                    log.warning("IDLE unavailable (%s); polling", exc)
                    self._enter_degraded()
                    return
                exists = True  # anything that arrived before IDLE began
            elif now - self._idle_started >= self._idle_reissue:
                exists = self._end_idle(conn)  # tagged OK counts as activity
                self._mark_poll()  # a completed IDLE round trip proves the link is alive
            elif now - self._last_activity > 2 * self._idle_reissue:
                raise IdleStalled
            elif self._wait_readable(conn, IDLE_SLICE_S):
                while True:
                    line = conn.readline()
                    if not line or line.startswith(b"* BYE"):
                        raise ConnectionError("server closed connection during IDLE")
                    self._last_activity = self._clock()
                    exists |= bool(re.match(rb"\* \d+ EXISTS", line))
                    if getattr(conn.sock, "pending", lambda: 0)() <= 0:
                        break
        if exists:
            self._mark_poll()
            self._notify()
        self._stop.wait(0.05)  # let borrowers grab the lock between slices

    # -- borrowing the connection ----------------------------------------
    @contextmanager
    def _exclusive(self) -> Iterator[imaplib.IMAP4]:
        with self._lock:
            conn = self._conn
            if conn is None:
                raise MailboxUnavailable("not connected")
            try:
                if self._idling:
                    self._end_idle(conn)
                yield conn
            except (OSError, imaplib.IMAP4.abort, ConnectionError) as exc:
                self._conn, self._idling = None, False
                self._close(conn)
                raise MailboxUnavailable(type(exc).__name__) from exc

    # -- search / fetch ---------------------------------------------------
    def fetch_since(self, after: datetime) -> list[MailMessage]:
        """Messages received at or after ``after`` (callers add any clock-skew margin).

        Search is coarse (IMAP SINCE has date granularity, one day of slack is added);
        the exact INTERNALDATE filter happens here.  Parsed messages are cached so many
        waiters share one fetch.  Does not set \\Seen (BODY.PEEK).
        """
        with self._exclusive() as conn:
            uv = self._uidvalidity
            typ, data = conn.uid("SEARCH", None, "SINCE", _imap_date(after - timedelta(days=1)))
            if typ != "OK":
                raise MailboxUnavailable("SEARCH failed")
            uids = [int(x) for x in (data[0] or b"").split()][-MAX_FETCH:]
            for uid in uids:
                if (uv, uid) not in self._cache:
                    self._fetch_one(conn, uv, uid)
            self._mark_poll()
            found = [self._cache[(uv, u)] for u in uids if (uv, u) in self._cache]
        return [m for m in found if m.received_at >= after]

    def _fetch_one(self, conn: imaplib.IMAP4, uv: int, uid: int) -> None:
        typ, data = conn.uid("FETCH", str(uid), "(INTERNALDATE BODY.PEEK[])")
        if typ != "OK":
            return
        for item in data:
            if isinstance(item, tuple):
                meta, raw = item
                try:
                    received = _parse_internaldate(meta)
                except ValueError:
                    return
                msg = email.message_from_bytes(raw, policy=policy.default)
                self._cache[(uv, uid)] = MailMessage(
                    uidvalidity=uv,
                    uid=uid,
                    message_id=str(msg.get("Message-ID", "")).strip(),
                    sender=parseaddr(str(msg.get("From", "")))[1].lower(),
                    subject=str(msg.get("Subject", "")),
                    received_at=received,
                    msg=msg,
                )
                while len(self._cache) > CACHE_SIZE:
                    self._cache.popitem(last=False)
                return

    def mark_seen(self, uidvalidity: int, uid: int) -> None:
        """Set \\Seen — only when configured with mark_seen=True."""
        if not self._mark_seen:
            return
        with self._exclusive() as conn:
            if uidvalidity == self._uidvalidity:
                conn.uid("STORE", str(uid), "+FLAGS", r"(\Seen)")
