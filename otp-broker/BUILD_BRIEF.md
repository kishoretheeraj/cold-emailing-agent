# OTP Broker Service - Build Brief

Build a production-ready IMAP-based OTP broker microservice in `/home/hatch/workspace/otp-broker/`.

## Purpose
Local microservice that retrieves email verification codes (OTPs, magic links) from Gmail via IMAP. A job application agent calls it via HTTP instead of reading email directly.

## Architecture
- FastAPI service, binds 127.0.0.1:8765 only
- Single IMAP connection to imap.gmail.com:993 (SSL) via Python imaplib
- SQLite database (WAL mode) at `./otp-broker.db` for waiter persistence + consumed message tracking
- Credentials: read app password from file path given by env var `OTP_BROKER_PASSWORD_FILE` (default `/etc/jobagent24/gmail_app_password`). NEVER hardcode. NEVER log the password value.

## Critical requirements

1. **Single IMAP connection**: Exactly one connection per Gmail account. A `Mailbox` class owns the connection. All waiters multiplex on it. Never open per-request connections. Use a threading lock around IMAP operations.

2. **IDLE primary, polling fallback**: Implement IMAP IDLE (raw `IDLE` command via `conn.send()`, since imaplib lacks it) with 25-minute re-issue. If IDLE thread fails (no EXISTS/heartbeat within 2x expected window), tear down and fall back to 10s polling. Polling is the degraded mode.

3. **Reconnect**: Exponential backoff 1s, 2s, 4s ... cap 60s with jitter. Re-SELECT INBOX after reconnect. Log every reconnect with exception type.

4. **Auth circuit breaker**: After 3 consecutive AUTHENTICATIONFAILED, stop retrying IMAP, mark health unhealthy with reason `auth_failed`. Do not hammer auth.

5. **Waiter persistence**: Persist active waiters in SQLite. On startup, reload unexpired waiters and re-evaluate backlog.

6. **Code extraction** (strict):
   - Never bare `\d{4,8}`. Require context: `(?:code|verification|otp|passcode|pin)[\s:.\-]*(\d{4,8})` case-insensitive.
   - Parse text/plain AND text/html (multipart/alternative). Strip tags and rejoin (codes split like `4<b>8</b>3920`).
   - Normalize: strip spaces/hyphens inside digit runs (`483 920` -> `483920`).
   - Reject currency/phone context (strip `$12.34`, phone-like sequences first).
   - Multiple distinct candidates: if >2, return 409 conflict, do not guess.
   - Return FIRST match in the NEWEST matching message.

7. **Link extraction**: Parse all hrefs. Exact registrable-domain match against allowlist (use urllib.parse; never substring match). Prefer shortest path containing verify|confirm|activate|token. Return URL only; NEVER auto-GET.

8. **Atomic consume**: `INSERT OR IGNORE` into `consumed(uidvalidity, uid)` with UNIQUE(uidvalidity, uid), inside a transaction, checked before delivering a code to any waiter.

9. **Register-then-search**: For each wait: (a) register waiter, (b) search backlog with `after` timestamp (with 5-min overlap margin for clock skew), (c) enter wait loop. On 408 timeout, deregister.

10. **/health schema**: `{imap_connected: bool, last_successful_poll: str|null, active_waiter_count: int, degraded: bool, uptime_seconds: float, last_auth_failure: str|null}`.

11. **Timeouts**: Server-side max 300s for timeout_s. Generate request_id (uuid) per wait, log end-to-end.

12. **Security**: bind 127.0.0.1 only; password file mode 600 (document); never log OTP values (log metadata only: sender, message-id, timestamp); SQLite file 600.

13. **Systemd unit**: Write `otp-broker.service` unit file in this directory (for later install): Restart=always, RestartSec=5, runs uvicorn on 127.0.0.1:8765, User=jobagent.

14. **Leave messages unread** by default (do not set \Seen). Configurable via env `OTP_BROKER_MARK_SEEN=false`.

## API
- `POST /v1/wait` body: `{sender_domains: [str], after: ISO8601 str, kind: "otp"|"link", timeout_s: int<=300, hint: str optional, allowed_link_hosts: [str] optional (for kind=link)}` -> 200 `{request_id, code|url, message_id, received_at}` or 408 `{error: "timeout"}` or 409 `{error: "conflict"}`.
- `GET /health` -> schema above.

## Files to create in /home/hatch/workspace/otp-broker/
- `main.py` - FastAPI app
- `imap_client.py` - Mailbox class (connect, IDLE, poll, reconnect, search)
- `extract.py` - code/link extraction
- `store.py` - SQLite (waiters, consumed), WAL mode, aiosqlite or single-writer lock
- `otp-broker.service` - systemd unit (for review, not installed)
- `requirements.txt` - fastapi, uvicorn, aiosqlite (or just sqlite3), beautifulsoup4
- `tests/` - pytest: reconnect logic (mock IMAP), duplicate-delivery prevention, multi-waiter fan-out, regex corpus with 20+ OTP email samples

## Constraints
- Python 3.11+. No Gmail API, no OAuth. IMAP only.
- Do NOT read /etc/jobagent24/gmail_app_password at build time; read at runtime via env var path.
- Keep it a single-purpose service. No extra features.

Write clean, typed, documented code. Then run the tests and make them pass.
