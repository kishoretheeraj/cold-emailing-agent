"""otp_client: HTTP client for the OTP broker. The HTTP layer is mocked."""

import logging
from datetime import datetime, timezone

import pytest

import config
import email_verification
import otp_client

SINCE = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_token_file(monkeypatch, tmp_path):
    monkeypatch.setenv("OTP_BROKER_TOKEN_FILE", str(tmp_path / "absent"))
    otp_client._token_cache.clear()
    yield
    otp_client._token_cache.clear()


def _get(**kw):
    return otp_client.get_verification_code(("myworkday.com",), SINCE, **kw)


def test_200_returns_the_dict_and_sends_the_documented_payload(mocker):
    post = mocker.patch.object(otp_client, "_post", return_value=(200, {"code": "482913"}))
    assert _get(timeout_seconds=30) == {"code": "482913"}
    path, payload, _timeout = post.call_args.args
    assert path == "/v1/wait"
    assert payload == {"sender_domains": ["myworkday.com"], "after": SINCE.isoformat(),
                       "kind": "otp", "timeout_s": 30}


def test_408_returns_none(mocker):
    mocker.patch.object(otp_client, "_post", return_value=(408, None))
    assert _get() is None


def test_409_warns_and_returns_none(mocker, caplog):
    mocker.patch.object(otp_client, "_post", return_value=(409, None))
    with caplog.at_level(logging.WARNING):
        assert _get() is None
    assert "409" in caplog.text


def test_unreachable_warns_and_returns_none(mocker, caplog):
    mocker.patch.object(otp_client, "_post", side_effect=otp_client.BrokerUnreachable("URLError"))
    with caplog.at_level(logging.WARNING):
        assert _get() is None
    assert "unreachable" in caplog.text


def test_unreachable_raises_when_asked(mocker):
    mocker.patch.object(otp_client, "_post", side_effect=otp_client.BrokerUnreachable("x"))
    with pytest.raises(otp_client.BrokerUnreachable):
        _get(raise_unreachable=True)


def test_post_maps_connection_refused_to_unreachable(mocker):
    mocker.patch.object(otp_client.urllib.request, "urlopen", side_effect=ConnectionRefusedError())
    with pytest.raises(otp_client.BrokerUnreachable):
        otp_client._post("/v1/wait", {}, 1)


def _capture_request(mocker):
    seen = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"code": "1"}'

    def fake(request, timeout=None):
        seen["headers"] = {k.lower(): v for k, v in request.header_items()}
        return _Resp()

    mocker.patch.object(otp_client.urllib.request, "urlopen", side_effect=fake)
    return seen


def test_token_file_present_sends_bearer_header(mocker, monkeypatch, tmp_path):
    f = tmp_path / "tok"
    f.write_text("s3cret-token\n")
    monkeypatch.setenv("OTP_BROKER_TOKEN_FILE", str(f))
    seen = _capture_request(mocker)
    assert otp_client._post("/v1/wait", {}, 1) == (200, {"code": "1"})
    assert seen["headers"]["authorization"] == "Bearer s3cret-token"


def test_missing_token_file_sends_no_header_and_still_works(mocker, caplog):
    seen = _capture_request(mocker)
    with caplog.at_level(logging.WARNING):
        assert otp_client._post("/v1/wait", {}, 1) == (200, {"code": "1"})
    assert "authorization" not in seen["headers"]
    assert "token" in caplog.text


def test_token_value_is_never_logged(mocker, monkeypatch, tmp_path, caplog):
    f = tmp_path / "tok"
    f.write_text("s3cret-token")
    monkeypatch.setenv("OTP_BROKER_TOKEN_FILE", str(f))
    mocker.patch.object(otp_client, "_post", return_value=(401, None))
    with caplog.at_level(logging.DEBUG):
        _get()
    assert "s3cret-token" not in caplog.text


def test_401_raises_unreachable_when_asked_and_warns(mocker, caplog):
    mocker.patch.object(otp_client, "_post", return_value=(401, None))
    with caplog.at_level(logging.WARNING):
        with pytest.raises(otp_client.BrokerUnreachable):
            _get(raise_unreachable=True)
    assert "unauthorized" in caplog.text


def test_401_returns_none_by_default(mocker):
    mocker.patch.object(otp_client, "_post", return_value=(401, None))
    assert _get() is None


def test_401_falls_back_to_imap(mocker):
    mocker.patch.object(otp_client, "_post", return_value=(401, None))
    mocker.patch.object(config, "RECEIPT_IMAP_ADDRESS", "me@example.com")
    mocker.patch.object(config, "RECEIPT_IMAP_APP_PASSWORD", "pw")
    fetch = mocker.patch.object(email_verification.gmail, "fetch_inbox_since", return_value=[{
        "from": "a@myworkday.com", "body": "Your verification code is 654321.",
        "date": SINCE.replace(minute=1), "is_reply": False}])
    assert email_verification.wait_for_verification(("myworkday.com",), SINCE) == {"code": "654321"}
    fetch.assert_called()


def test_the_code_value_is_never_logged(mocker, caplog):
    mocker.patch.object(otp_client, "_post", return_value=(200, {"code": "482913"}))
    with caplog.at_level(logging.DEBUG):
        _get()
    assert "482913" not in caplog.text


# ── email_verification wiring ──────────────────────────────────────────────────

@pytest.mark.parametrize("hosts,kind", [((), "otp"), (("acme.myworkdayjobs.com",), "link")])
def test_wait_for_verification_uses_the_broker_with_the_right_kind(mocker, hosts, kind):
    get = mocker.patch.object(email_verification.otp_client, "get_verification_code",
                              return_value={"code": "111222"})
    fetch = mocker.patch.object(email_verification.gmail, "fetch_inbox_since")
    got = email_verification.wait_for_verification(("myworkday.com",), SINCE, allowed_link_hosts=hosts)
    assert got == {"code": "111222"}
    assert get.call_args.kwargs["kind"] == kind
    fetch.assert_not_called()


def test_broker_timeout_does_not_fall_back_to_imap(mocker):
    mocker.patch.object(email_verification.otp_client, "get_verification_code", return_value=None)
    fetch = mocker.patch.object(email_verification.gmail, "fetch_inbox_since")
    assert email_verification.wait_for_verification(("myworkday.com",), SINCE) is None
    fetch.assert_not_called()


def test_unreachable_broker_falls_back_to_imap(mocker):
    mocker.patch.object(otp_client, "_post", side_effect=otp_client.BrokerUnreachable("down"))
    mocker.patch.object(config, "RECEIPT_IMAP_ADDRESS", "me@example.com")
    mocker.patch.object(config, "RECEIPT_IMAP_APP_PASSWORD", "pw")
    fetch = mocker.patch.object(email_verification.gmail, "fetch_inbox_since", return_value=[{
        "from": "a@myworkday.com", "body": "Your verification code is 654321.",
        "date": SINCE.replace(minute=1), "is_reply": False}])
    assert email_verification.wait_for_verification(("myworkday.com",), SINCE) == {"code": "654321"}
    fetch.assert_called()
