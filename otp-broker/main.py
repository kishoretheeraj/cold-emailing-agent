"""FastAPI front end: POST /v1/wait and GET /health.  Bind 127.0.0.1 only.

Run with exactly one worker (a second process would open a second IMAP connection):
    uvicorn main:app --host 127.0.0.1 --port 8765
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import secrets
import signal
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from broker import MAX_TIMEOUT_S, Broker
from imap_client import Mailbox
from store import Store, default_db_path

logging.basicConfig(level=os.environ.get("OTP_BROKER_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("otp_broker")


@dataclass(frozen=True)
class Config:
    user: str
    password_file: Path
    db_path: Path
    mark_seen: bool
    # Bearer-token file for /v1/wait.  None disables auth (unit tests only; from_env always sets it).
    token_file: Path | None = None

    @classmethod
    def from_env(cls) -> "Config":
        user = os.environ.get("OTP_BROKER_USER")
        if not user:
            raise RuntimeError("OTP_BROKER_USER (Gmail address) is required")
        return cls(
            user=user,
            password_file=Path(os.environ.get("OTP_BROKER_PASSWORD_FILE", "/etc/jobagent24/gmail_app_password")),
            db_path=default_db_path(),
            mark_seen=os.environ.get("OTP_BROKER_MARK_SEEN", "false").strip().lower() in ("1", "true", "yes"),
            token_file=Path(os.environ.get("OTP_BROKER_TOKEN_FILE", "/etc/jobagent24/otp_broker_token")),
        )


def load_or_create_token(path: Path) -> str:
    """Read the API token; create it (mode 600) if missing.  Fails closed on any problem."""
    try:
        token = path.read_text().strip()
    except FileNotFoundError:
        token = secrets.token_urlsafe(32)
        old = os.umask(0o077)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(token + "\n")
        finally:
            os.umask(old)
        log.warning("generated new API token at %s", path)
    if len(token) < 16:
        raise RuntimeError(f"API token in {path} is empty or too short")
    return token


_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.\-]{0,251}[a-z0-9])?$")


def _check_hosts(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    out = []
    for v in values:
        v = v.strip().lower() if isinstance(v, str) else v
        if not isinstance(v, str) or not _HOST_RE.match(v) or ".." in v:
            raise ValueError("invalid domain entry")
        out.append(v)
    return out


AFTER_MAX_AGE = timedelta(hours=24)
AFTER_MAX_FUTURE = timedelta(minutes=5)


class WaitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_domains: list[str] = Field(min_length=1, max_length=50)
    after: datetime
    kind: Literal["otp", "link"]
    timeout_s: StrictInt = Field(ge=1, le=MAX_TIMEOUT_S)
    hint: str | None = Field(default=None, max_length=200)
    allowed_link_hosts: list[str] | None = Field(default=None, max_length=50)

    @field_validator("sender_domains", "allowed_link_hosts")
    @classmethod
    def _hosts(cls, v: list[str] | None) -> list[str] | None:
        return _check_hosts(v)

    @field_validator("after")
    @classmethod
    def _after_in_range(cls, v: datetime) -> datetime:
        v = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if not now - AFTER_MAX_AGE <= v <= now + AFTER_MAX_FUTURE:
            raise ValueError("after must be within [now-24h, now+5min]")
        return v


DISCONNECT_POLL_S = 0.5


def _fail_waiters_on_sigterm(broker: Broker) -> None:
    """Answer in-flight waits with 503 as soon as SIGTERM arrives, so uvicorn's drain is instant.

    Chains the handler uvicorn installed.  Only possible on the main thread (not under TestClient).
    """
    loop = asyncio.get_running_loop()
    try:
        previous = signal.getsignal(signal.SIGTERM)

        def handler(signum, frame):  # noqa: ANN001
            loop.call_soon_threadsafe(broker.begin_shutdown)
            if callable(previous):
                previous(signum, frame)

        signal.signal(signal.SIGTERM, handler)
    except ValueError:
        pass


def create_app(config: Config | None = None, mailbox: Mailbox | None = None) -> FastAPI:
    started = time.monotonic()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg = config or Config.from_env()
        store = Store(cfg.db_path)
        holder: dict[str, Broker] = {}
        mbox = mailbox or Mailbox(
            cfg.user, cfg.password_file, mark_seen=cfg.mark_seen,
            on_new_mail=lambda: holder["b"].notify_threadsafe(),
        )
        broker = Broker(store, mbox)
        holder["b"] = broker
        app.state.broker, app.state.mailbox = broker, mbox
        app.state.token = load_or_create_token(cfg.token_file) if cfg.token_file else None
        await broker.start()
        if mailbox is None:
            mbox.start()
        _fail_waiters_on_sigterm(broker)
        try:
            yield
        finally:
            await broker.shutdown()
            mbox.stop()
            store.close()

    # No /docs, /redoc or /openapi.json.
    app = FastAPI(title="otp-broker", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def require_token(authorization: str | None = Header(default=None)) -> None:
        token: str | None = app.state.token
        if token is None:
            return
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.strip().encode(), token.encode()):
            raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})

    @app.post("/v1/wait", dependencies=[Depends(require_token)])
    async def wait(req: WaitRequest, request: Request) -> Response:
        mbox: Mailbox = app.state.mailbox
        if mbox.auth_failed:
            return JSONResponse({"error": "auth_failed"}, status_code=503)
        task = asyncio.ensure_future(app.state.broker.wait(
            sender_domains=req.sender_domains,
            after=req.after,
            kind=req.kind,
            timeout_s=req.timeout_s,
            hint=req.hint,
            allowed_link_hosts=req.allowed_link_hosts,
        ))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=DISCONNECT_POLL_S)
                if done:
                    break
                if await request.is_disconnected():
                    # Abandoned waiter: free it now so it cannot swallow the retry's OTP.
                    log.info("client disconnected; cancelling waiter")
                    return Response(status_code=499)
            request_id, result = task.result()
        finally:
            if not task.done():
                task.cancel()
        error = result.get("error")
        if error == "timeout":
            return JSONResponse({"error": "timeout"}, status_code=408)
        if error == "conflict":
            return JSONResponse({"error": "conflict"}, status_code=409)
        if error == "shutting_down":
            return JSONResponse({"error": "shutting_down"}, status_code=503)
        return JSONResponse({"request_id": request_id, **result})

    @app.get("/health")
    async def health() -> Response:
        mbox: Mailbox = app.state.mailbox
        fmt = lambda d: d.isoformat() if d else None  # noqa: E731
        body = {
            "imap_connected": mbox.connected,
            "last_successful_poll": fmt(mbox.last_successful_poll),
            "active_waiter_count": app.state.broker.active_waiter_count,
            "degraded": mbox.degraded,
            "uptime_seconds": round(time.monotonic() - started, 1),
            "last_auth_failure": fmt(mbox.last_auth_failure),
            "state": mbox.state,
            "last_error": mbox.last_error,
        }
        # Schema is fixed; the unhealthy reason travels in the status code + header.
        if mbox.auth_failed:
            return JSONResponse(body, status_code=503, headers={"X-Unhealthy-Reason": "auth_failed"})
        return JSONResponse(body)

    return app


app = create_app()
