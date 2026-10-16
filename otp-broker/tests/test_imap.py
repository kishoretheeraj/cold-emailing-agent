"""Mailbox with a mocked IMAP: reconnect/backoff, auth breaker, fetch, IDLE."""
import imaplib
from datetime import datetime, timedelta, timezone

import pytest

from imap_client import (
    AUTH_FAILURE_LIMIT, IdleStalled, Mailbox, MailboxUnavailable, backoff_delay,
)
from fakes import FakeIMAP


@pytest.fixture
def pwfile(tmp_path):
    p = tmp_path / "pw"
    p.write_text("s3cret-app-password\n")
    return p


def make_mailbox(pwfile, factory, **kw):
    mb = Mailbox("me@gmail.com", pwfile, imap_factory=factory, rng=lambda: 0.5, **kw)
    mb.delays = []
    mb._sleep = lambda s: mb.delays.append(s)  # type: ignore[method-assign]
    return mb


def test_backoff_sequence_and_cap():
    seq = [backoff_delay(n, rng=lambda: 0.5) for n in range(9)]
    assert seq[:7] == [1, 2, 4, 8, 16, 32, 60]
    assert max(seq) <= 60
    assert backoff_delay(0, rng=lambda: 0.0) == pytest.approx(0.8)
    assert backoff_delay(10, rng=lambda: 1.0) == 60


def test_reconnects_with_exponential_backoff_then_reselects(pwfile, caplog):
    attempts = []
    ok = FakeIMAP()
    ok.lines = [b"+ idling"]

    def factory(host, port, **kw):
        attempts.append(1)
        if len(attempts) <= 3:
            raise ConnectionResetError("reset")
        return ok

    mb = make_mailbox(pwfile, factory)
    orig_select = ok.select
    ok.select = lambda *a, **k: (mb._stop.set(), orig_select(*a, **k))[1]
    with caplog.at_level("WARNING", logger="otp_broker.imap"):
        mb.run()
    assert mb.delays == [1, 2, 4]
    assert ok.selected_readonly is True  # INBOX re-selected, read-only (leave unread)
    assert mb.connected and mb._uidvalidity == 42
    assert "ConnectionResetError" in caplog.text
    assert "s3cret" not in caplog.text


def test_runtime_failure_drops_connection_and_reconnects(pwfile):
    bad, good = FakeIMAP(fail_noop=True), FakeIMAP()
    conns = iter([bad, good])
    mb = make_mailbox(pwfile, lambda h, p, **kw: next(conns))
    mb._enter_degraded()  # poll mode so noop is exercised
    good_select = good.select
    good.select = lambda *a, **k: (mb._stop.set(), good_select(*a, **k))[1]
    mb._degraded_since = 1e12
    mb.run()
    assert bad.closed and mb.delays[0] == 1  # backoff, then one poll tick
    assert mb._conn is good


def test_auth_circuit_breaker(pwfile):
    n = []

    def factory(host, port, **kw):
        n.append(1)
        return FakeIMAP(login_error="[AUTHENTICATIONFAILED] Invalid credentials (Failure)")

    mb = make_mailbox(pwfile, factory)
    mb._sleep = lambda s: (mb.delays.append(s), mb._stop.set() if mb._auth_tripped else None)  # type: ignore
    # after the breaker opens run() only idles; stop it from the sleep hook
    mb._sleep = lambda s: (mb.delays.append(s), mb._stop.set() if len(mb.delays) > 3 else None)  # type: ignore
    mb.run()
    assert len(n) == AUTH_FAILURE_LIMIT == 3  # no further auth attempts
    assert mb.auth_failed and mb.last_auth_failure is not None
    assert not mb.connected


def test_other_login_errors_are_not_auth_failures(pwfile):
    mb = make_mailbox(pwfile, lambda h, p, **kw: FakeIMAP(login_error="[UNAVAILABLE] try later"))
    mb._sleep = lambda s: (mb.delays.append(s), mb._stop.set())  # type: ignore
    mb.run()
    assert not mb.auth_failed and mb.delays == [1]


def test_password_never_logged_on_auth_failure(pwfile, caplog):
    mb = make_mailbox(pwfile, lambda h, p, **kw: FakeIMAP(login_error="[AUTHENTICATIONFAILED] nope"))
    mb._sleep = lambda s: (mb.delays.append(s), mb._stop.set() if len(mb.delays) > 3 else None)  # type: ignore
    with caplog.at_level("DEBUG"):
        mb.run()
    assert "s3cret" not in caplog.text


# ---- IDLE ---------------------------------------------------------------
def connected_mailbox(pwfile, **kw):
    fake = FakeIMAP()
    mb = make_mailbox(pwfile, lambda h, p, **k: fake, **kw)
    mb._connect()
    return mb, fake


def test_idle_exists_triggers_notify(pwfile):
    hits = []
    mb, fake = connected_mailbox(pwfile, on_new_mail=lambda: hits.append(1))
    fake.lines = [b"+ idling", b"* 5 EXISTS\r\n"]
    mb._wait_readable = lambda c, t: True  # type: ignore
    mb._idle_step()            # starts IDLE (+ notify for backlog)
    assert mb._idling and fake.sent[0].endswith(b" IDLE\r\n")
    hits.clear()
    fake.sock = type("S", (), {"pending": lambda self: 0})()
    mb._idle_step()            # reads EXISTS
    assert hits == [1]


def test_idle_reissued_after_25_minutes(pwfile):
    t = [0.0]
    mb, fake = connected_mailbox(pwfile, clock=lambda: t[0])
    fake.lines = [b"+ idling"]
    mb._idle_step()
    tag = mb._idle_tag
    t[0] = 25 * 60 + 1
    fake.lines = [tag + b" OK IDLE terminated\r\n", b"+ idling again"]
    mb._idle_step()   # ends idle (DONE), tagged OK is activity
    assert b"DONE\r\n" in fake.sent and not mb._idling
    mb._idle_step()   # re-issues
    assert mb._idling and mb._idle_tag != tag


def test_idle_stall_detected_after_2x_window(pwfile):
    t = [0.0]
    mb, fake = connected_mailbox(pwfile, clock=lambda: t[0], idle_reissue=100)
    fake.lines = [b"+ idling"]
    mb._idle_step()
    mb._idle_started = 1e9  # pretend re-issue never fires
    t[0] = 201
    with pytest.raises(IdleStalled):
        mb._idle_step()


def test_stall_falls_back_to_polling_mode(pwfile):
    mb, fake = connected_mailbox(pwfile)
    calls = []
    mb._idle_step = lambda: (_ for _ in ()).throw(IdleStalled()) if not calls else None  # type: ignore
    mb._poll_once = lambda: (calls.append(1), mb._stop.set())  # type: ignore
    mb._connect = lambda: setattr(mb, "_conn", fake)  # type: ignore
    mb.run()
    assert mb.degraded and calls == [1] and fake.closed


def test_idle_rejected_degrades(pwfile):
    mb, fake = connected_mailbox(pwfile)
    fake.lines = [b"OTPI1 BAD no idle\r\n"]
    mb._idle_step()
    assert mb.degraded


def test_borrowing_connection_ends_idle_first(pwfile):
    mb, fake = connected_mailbox(pwfile)
    fake.lines = [b"+ idling"]
    mb._idle_step()
    fake.lines = [mb._idle_tag + b" OK done\r\n"]
    with mb._exclusive():
        assert not mb._idling and b"DONE\r\n" in fake.sent


# ---- fetch / search -----------------------------------------------------
class SearchIMAP(FakeIMAP):
    def __init__(self, raw_by_uid):
        super().__init__()
        self.raw, self.fetches = raw_by_uid, 0

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return "OK", [" ".join(map(str, self.raw)).encode()]
        if cmd == "FETCH":
            self.fetches += 1
            uid = int(args[0])
            date, body = self.raw[uid]
            meta = f'{uid} (UID {uid} INTERNALDATE "{date}" BODY[] {{{len(body)}}}'.encode()
            return "OK", [(meta, body), b")"]
        if cmd == "STORE":
            self.stored = args
            return "OK", [b""]


def raw(sender, code):
    return (f"From: Svc <{sender}>\r\nSubject: Hi\r\nMessage-ID: <{code}@x>\r\n\r\nYour code is {code}\r\n").encode()


def test_fetch_parses_filters_and_caches(pwfile):
    now = datetime.now(timezone.utc)
    fmt = lambda d: d.strftime("%d-%b-%Y %H:%M:%S +0000")  # noqa: E731
    fake = SearchIMAP({1: (fmt(now - timedelta(hours=3)), raw("a@example.com", "111111")),
                       2: (fmt(now), raw("A@Example.com", "222222"))})
    mb = make_mailbox(pwfile, lambda h, p, **k: fake)
    mb._connect()
    got = mb.fetch_since(now - timedelta(minutes=5))
    assert [m.uid for m in got] == [2]
    assert got[0].sender == "a@example.com" and got[0].message_id == "<222222@x>"
    assert got[0].uidvalidity == 42
    mb.fetch_since(now - timedelta(minutes=5))
    assert fake.fetches == 2  # second call served from cache


def test_fetch_failure_drops_connection(pwfile):
    class Dead(FakeIMAP):
        def uid(self, *a):
            raise imaplib.IMAP4.abort("socket error")

    fake = Dead()
    mb = make_mailbox(pwfile, lambda h, p, **k: fake)
    mb._connect()
    with pytest.raises(MailboxUnavailable):
        mb.fetch_since(datetime.now(timezone.utc))
    assert not mb.connected and fake.closed


def test_mark_seen_default_off_and_opt_in(pwfile):
    fake = SearchIMAP({})
    mb = make_mailbox(pwfile, lambda h, p, **k: fake)
    mb._connect()
    mb.mark_seen(42, 1)
    assert not hasattr(fake, "stored")
    fake2 = SearchIMAP({})
    mb2 = make_mailbox(pwfile, lambda h, p, **k: fake2, mark_seen=True)
    mb2._connect()
    mb2.mark_seen(42, 1)
    assert fake2.stored == ("1", "+FLAGS", r"(\Seen)") and fake2.selected_readonly is False


# ---- hardening ----------------------------------------------------------------
def test_backoff_not_reset_by_flapping_connection(pwfile):
    """A link that connects then dies immediately must keep escalating the backoff."""
    conns = []

    def factory(host, port, **kw):
        f = FakeIMAP(fail_noop=True)
        conns.append(f)
        return f

    mb = make_mailbox(pwfile, factory)
    mb._enter_degraded()
    mb._degraded_since = 1e12
    mb._sleep = lambda s: (mb.delays.append(s), mb._stop.set() if len(mb.delays) >= 4 else None)  # type: ignore
    mb.run()
    assert mb.delays == [1, 2, 4, 8]


def test_keepalive_enabled(pwfile):
    opts = []
    fake = FakeIMAP()
    fake.sock = type("S", (), {"setsockopt": lambda self, *a: opts.append(a)})()
    mb = make_mailbox(pwfile, lambda h, p, **k: fake)
    mb._connect()
    import socket
    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in opts
    assert (socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60) in opts
    assert (socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10) in opts
    assert (socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3) in opts


@pytest.mark.parametrize("err", ["[ALERT] Application-specific password required", "Invalid credentials"])
def test_alert_and_invalid_credentials_trip_breaker(pwfile, err):
    mb = make_mailbox(pwfile, lambda h, p, **kw: FakeIMAP(login_error=err))
    mb._sleep = lambda s: (mb.delays.append(s), mb._stop.set() if len(mb.delays) > 3 else None)  # type: ignore
    mb.run()
    assert mb.auth_failed and mb.state == "auth_failed" and mb.last_error


def test_missing_password_file_counts_as_auth_failure(tmp_path):
    mb = make_mailbox(tmp_path / "missing", lambda h, p, **kw: FakeIMAP())
    mb._sleep = lambda s: (mb.delays.append(s), mb._stop.set() if len(mb.delays) > 3 else None)  # type: ignore
    mb.run()
    assert mb.auth_failed and "OSError" not in (mb.last_error or "") and "FileNotFoundError" in mb.last_error


def test_breaker_half_open_probe_recovers(pwfile):
    t = [0.0]
    login = {"err": "[AUTHENTICATIONFAILED] no"}
    mb = make_mailbox(pwfile, lambda h, p, **kw: FakeIMAP(login_error=login["err"]), clock=lambda: t[0])
    mb._trip_breaker()
    mb._sleep = lambda s: (t.__setitem__(0, t[0] + 1000), mb._stop.set() if mb.delays.append(s) is None and len(mb.delays) > 6 else None)  # type: ignore
    mb._probe_auth()
    assert mb.auth_failed                      # probe failed: stays open
    login["err"] = None
    mb._probe_auth()
    assert not mb.auth_failed and mb.connected and mb.state in ("connected", "degraded")


def test_state_transitions(pwfile):
    mb, _ = connected_mailbox(pwfile)
    assert mb.state == "connected"
    mb._enter_degraded()
    assert mb.state == "degraded"
    mb._drop_conn()
    assert mb.state == "reconnecting"


def test_idle_reissue_is_five_minutes_and_marks_poll():
    from imap_client import IDLE_REISSUE_S
    assert IDLE_REISSUE_S == 300
