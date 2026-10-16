"""Waiter registry and fan-out over the single Mailbox.

Flow per ``wait()``: register (persist) -> scan backlog -> await future.  New mail
triggers ``scan()``, which evaluates *every* live waiter against the messages.  A
message is claimed through ``Store.consume`` (atomic) only once a deliverable
result was extracted, so one message is never handed to two waiters.

Waiters reloaded after a restart have no HTTP request attached ("orphans").  They
are still evaluated; a result is stored with the row so that a retried identical
request can pick it up instead of finding the message already consumed.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, Sequence

from extract import (
    AmbiguousCode,
    address_domain,
    domain_matches,
    extract_code,
    extract_link,
    message_texts,
)
from imap_client import MailMessage, MailboxUnavailable
from store import Store, Waiter

log = logging.getLogger("otp_broker.broker")

# Gmail INTERNALDATE is stamped server-side, so only network/scan jitter needs covering.
CLOCK_SKEW_MARGIN = timedelta(seconds=15)
MAX_LOOKBACK = timedelta(hours=24)
SWEEP_INTERVAL_S = 30.0
MAX_TIMEOUT_S = 300
CONFLICT = {"error": "conflict"}
SHUTTING_DOWN = {"error": "shutting_down"}


class MailboxLike(Protocol):
    def fetch_since(self, after: datetime) -> Sequence[MailMessage]: ...
    def mark_seen(self, uidvalidity: int, uid: int) -> None: ...


class Broker:
    def __init__(self, store: Store, mailbox: MailboxLike) -> None:
        self._store = store
        self._mailbox = mailbox
        self._waiters: dict[str, Waiter] = {}
        self._scan_task: asyncio.Task[None] | None = None  # single-flight scanner
        self._rescan = False
        self._sweeper: asyncio.Task[None] | None = None
        self._stopping = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def active_waiter_count(self) -> int:
        now = datetime.now(timezone.utc)
        return sum(1 for w in self._waiters.values() if w.result is None and w.expires_at > now)

    # -- startup / triggers ------------------------------------------------
    async def start(self) -> None:
        """Reload unexpired waiters from SQLite and re-evaluate the backlog."""
        self._loop = asyncio.get_running_loop()
        now = datetime.now(timezone.utc)
        for w in self._store.load_waiters(now):
            self._waiters[w.request_id] = w
        self._sweeper = asyncio.ensure_future(self._sweep_forever())
        if self._waiters:
            log.info("reloaded %d waiter(s)", len(self._waiters))
            await self.scan()

    def begin_shutdown(self) -> None:
        """Fail every live waiter with 503-able ``shutting_down`` so the server can exit at once."""
        self._stopping = True
        for w in list(self._waiters.values()):
            if w.future is not None and not w.future.done():
                w.future.set_result(dict(SHUTTING_DOWN))

    async def shutdown(self) -> None:
        self.begin_shutdown()
        for t in [self._sweeper, self._scan_task, *self._tasks]:
            if t is not None and not t.done():
                t.cancel()

    async def _sweep_forever(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_S)
            self._sweep(datetime.now(timezone.utc))

    def _sweep(self, now: datetime) -> None:
        """Drop expired orphans (no HTTP request attached); live waiters clean up themselves."""
        for rid, w in list(self._waiters.items()):
            if w.future is None and w.expires_at <= now:
                del self._waiters[rid]
                self._store.delete_waiter(rid)

    def notify_threadsafe(self) -> None:
        """Called from the IMAP thread when new mail may have arrived."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._spawn_scan)

    def _spawn_scan(self) -> None:
        self._request_scan()

    # -- public API --------------------------------------------------------
    async def wait(
        self,
        *,
        sender_domains: list[str],
        after: datetime,
        kind: str,
        timeout_s: int,
        hint: str | None = None,
        allowed_link_hosts: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Returns ``(request_id, result)``; result has code|url or ``error``."""
        request_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        timeout_s = max(1, min(timeout_s, MAX_TIMEOUT_S))
        if self._stopping:
            return request_id, dict(SHUTTING_DOWN)
        w = Waiter(
            request_id=request_id,
            sender_domains=sender_domains,
            after=after,
            kind=kind,
            hint=hint,
            allowed_link_hosts=allowed_link_hosts or sender_domains,
            expires_at=now + timedelta(seconds=timeout_s),
        )
        stored = self._take_orphan_result(w)
        if stored is not None:
            log.info("wait %s answered from stored result of earlier waiter", request_id)
            return request_id, stored

        # (a) register
        w.future = asyncio.get_running_loop().create_future()
        self._waiters[request_id] = w
        self._store.add_waiter(w)
        log.info("wait %s registered kind=%s domains=%s timeout=%ds", request_id, kind, sender_domains, timeout_s)
        try:
            # The deadline covers everything below: a slow or stuck scan cannot outlive it.
            async with asyncio.timeout(timeout_s):
                # (b) search backlog (coalesced, runs in its own task), (c) wait loop
                self._request_scan()
                result = await w.future
        except TimeoutError:
            log.info("wait %s timed out", request_id)
            return request_id, {"error": "timeout"}
        finally:
            self._waiters.pop(request_id, None)
            self._store.delete_waiter(request_id)
        return request_id, result

    def _take_orphan_result(self, new: Waiter) -> dict[str, Any] | None:
        for rid, o in list(self._waiters.items()):
            if o.future is None and o.result is not None and o.fingerprint == new.fingerprint:
                del self._waiters[rid]
                self._store.delete_waiter(rid)
                return o.result
        return None

    # -- evaluation --------------------------------------------------------
    def _request_scan(self) -> None:
        """Ask for a scan without waiting for it; concurrent requests coalesce."""
        if self._scan_task is not None and not self._scan_task.done():
            self._rescan = True  # picked up by the running scanner's next pass
            return
        self._scan_task = asyncio.ensure_future(self._scan_loop())

    async def scan(self) -> None:
        """Run (or join) a scan and return once it, including any queued re-pass, finished."""
        self._request_scan()
        task = self._scan_task
        if task is not None:
            await asyncio.shield(task)  # a cancelled caller must not kill the shared scan

    async def _scan_loop(self) -> None:
        try:
            while True:
                self._rescan = False
                await self._scan_once()
                if not self._rescan:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never leave waiters without a scanner
            log.error("scan failed: %s", type(exc).__name__)
        finally:
            self._scan_task = None

    async def _scan_once(self) -> None:
        # Single-flight: only this task mutates waiter state, so no lock is held
        # while the (slow) IMAP fetch runs in a thread.
        now = datetime.now(timezone.utc)
        self._sweep(now)
        pending = [w for w in self._waiters.values() if w.result is None and not (w.future and w.future.done())]
        if not pending:
            return
        earliest = max(min(w.after for w in pending) - CLOCK_SKEW_MARGIN, now - MAX_LOOKBACK - CLOCK_SKEW_MARGIN)
        try:
            messages = await asyncio.to_thread(self._mailbox.fetch_since, earliest)
        except MailboxUnavailable as exc:
            log.warning("scan skipped: mailbox unavailable (%s)", exc)
            return
        except Exception as exc:
            log.error("scan failed: %s", type(exc).__name__)
            return
        messages = sorted(messages, key=lambda m: m.received_at, reverse=True)
        delivered: list[MailMessage] = []
        for w in pending:
            if w.future and w.future.done():
                continue
            m = self._evaluate(w, messages)
            if m is not None:
                delivered.append(m)
        for m in delivered:  # IMAP I/O only after every waiter has its answer
            try:
                await asyncio.to_thread(self._mailbox.mark_seen, m.uidvalidity, m.uid)
            except Exception as exc:
                log.warning("mark_seen failed: %s", type(exc).__name__)

    @staticmethod
    def _authenticated(w: Waiter, m: MailMessage) -> bool:
        """DKIM/DMARC gate from Gmail's own Authentication-Results header.

        Absent header -> warn and allow (Gmail normally adds it).  Present but without
        a dkim=pass (or dmarc=pass) aligned to the sender domain -> reject.
        """
        if str(m.msg.get("X-Gm-Spam", "")).strip() not in ("", "0"):
            return False
        headers = [str(h) for h in (m.msg.get_all("Authentication-Results") or [])]
        # Only the receiving server's header is trustworthy; the sender can add its own.
        trusted = [h for h in headers if h.lstrip().lower().startswith("mx.google.com")]
        if not trusted:
            log.warning("message-id=%s has no Authentication-Results from mx.google.com; allowing", m.message_id)
            return True
        sender_domain = address_domain(m.sender) or ""
        for h in trusted:
            if re.search(r"\bdmarc=pass\b", h, re.IGNORECASE):
                return True
            for d in re.finditer(r"\bdkim=pass\b[^;]*?header\.[di]=@?([A-Za-z0-9.\-]+)", h, re.IGNORECASE):
                signer = d.group(1).lower().rstrip(".")
                if sender_domain == signer or sender_domain.endswith("." + signer) or signer.endswith("." + sender_domain):
                    return True
        return False

    def _matches(self, w: Waiter, m: MailMessage) -> bool:
        if m.received_at < w.after - CLOCK_SKEW_MARGIN:
            return False
        if not domain_matches(address_domain(m.sender), w.sender_domains):
            return False
        if not self._authenticated(w, m):
            log.warning("skipping message-id=%s: sender authentication failed", m.message_id)
            return False
        if w.hint:
            hay = " ".join([m.subject, *message_texts(m.msg)]).lower()
            if w.hint.lower() not in hay:
                return False
        return True

    def _evaluate(self, w: Waiter, messages: Sequence[MailMessage]) -> MailMessage | None:
        """Newest matching message that yields a result wins; returns it if delivered."""
        for m in messages:
            if not self._matches(w, m):
                continue
            result: dict[str, Any]
            if w.kind == "otp":
                try:
                    code = extract_code(message_texts(m.msg), w.hint)
                except AmbiguousCode:
                    log.warning("wait %s conflict: ambiguous codes in message-id=%s", w.request_id, m.message_id)
                    # Reported exactly once: claim the message so later waiters don't re-409 on it.
                    if not self._store.consume(m.uidvalidity, m.uid):
                        continue
                    self._deliver(w, dict(CONFLICT))
                    return None
                if code is None:
                    continue
                result = {"code": code}
            else:
                url = extract_link(m.msg, w.allowed_link_hosts)
                if url is None:
                    continue
                result = {"url": url}
            if not self._store.consume(m.uidvalidity, m.uid):
                continue  # another waiter already took this message
            result.update(message_id=m.message_id, received_at=m.received_at.isoformat())
            # Metadata only — never the code/url itself.
            log.info(
                "wait %s delivered kind=%s sender=%s message-id=%s received=%s",
                w.request_id, w.kind, m.sender, m.message_id, m.received_at.isoformat(),
            )
            self._deliver(w, result)
            return m
        return None

    def _deliver(self, w: Waiter, result: dict[str, Any]) -> None:
        if w.future is not None:
            if not w.future.done():
                w.future.set_result(result)
        else:  # orphan: keep the result for a retried request
            w.result = result
            self._store.set_result(w.request_id, result)
