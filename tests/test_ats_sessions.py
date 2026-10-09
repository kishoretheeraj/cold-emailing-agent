"""ats_sessions.py: which URLs share an account, and per-tenant browser session files."""

import json
import os
import stat

import pytest

import ats_sessions
import config


@pytest.mark.parametrize("url,key", [
    # Workday: one candidate account per tenant host, whatever the site, locale or job path.
    ("https://acme.wd5.myworkdayjobs.com/en-US/External/job/Remote/PM_R-1", "acme.wd5.myworkdayjobs.com"),
    ("https://acme.wd5.myworkdayjobs.com/External/job/NYC/Engineer_R-2/apply", "acme.wd5.myworkdayjobs.com"),
    ("https://ACME.wd5.myworkdayjobs.com/Careers", "acme.wd5.myworkdayjobs.com"),
    ("https://wd3.myworkdaysite.com/recruiting/acme/External/job/x", "wd3.myworkdaysite.com/acme"),
    # Shared multi-company hosts: the company slug is the boundary.
    ("https://jobs.lever.co/northwind/abc-123/apply", "jobs.lever.co/northwind"),
    ("https://jobs.eu.lever.co/northwind/abc", "jobs.eu.lever.co/northwind"),
    ("https://boards.greenhouse.io/acme/jobs/1?gh_src=x", "boards.greenhouse.io/acme"),
    ("https://job-boards.greenhouse.io/Acme/jobs/1", "job-boards.greenhouse.io/acme"),
    ("https://jobs.ashbyhq.com/acme/uuid/application", "jobs.ashbyhq.com/acme"),
    ("https://apply.workable.com/acme/j/ABC/", "apply.workable.com/acme"),
    ("https://jobs.smartrecruiters.com/Acme/7444-pm", "jobs.smartrecruiters.com/acme"),
    # Anything else: the host is the account boundary.
    ("https://careers.example.com/jobs/1", "careers.example.com"),
    ("https://careers.example.com:443/jobs/1", "careers.example.com"),
])
def test_tenant_key(url, key):
    assert ats_sessions.tenant_key(url) == key


@pytest.mark.parametrize("url", [None, "", "not a url", "mailto:x@y.z", "javascript:alert(1)", "https:///nohost"])
def test_tenant_key_rejects_non_web_urls(url):
    assert ats_sessions.tenant_key(url) is None


def test_shared_host_without_a_company_slug_has_no_tenant():
    assert ats_sessions.tenant_key("https://jobs.lever.co/") is None


@pytest.fixture
def sessions_dir(tmp_path, mocker):
    d = tmp_path / "sessions"
    mocker.patch.object(config, "APPLY_SESSIONS_DIR", str(d))
    return d


def test_no_state_until_one_is_saved(sessions_dir):
    assert ats_sessions.state_path("jobs.lever.co/northwind") is None


class _FakeContext:
    def __init__(self, state):
        self.state = state

    def storage_state(self, path):
        with open(path, "w") as f:
            json.dump(self.state, f)


def test_save_then_load_round_trips_with_private_permissions(sessions_dir):
    ats_sessions.save_state(_FakeContext({"cookies": [{"name": "s"}]}), "jobs.lever.co/northwind")
    path = ats_sessions.state_path("jobs.lever.co/northwind")
    with open(path) as f:
        assert json.load(f) == {"cookies": [{"name": "s"}]}
    assert stat.S_IMODE(os.stat(sessions_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # file names never reveal which companies the operator applied to
    assert "northwind" not in os.path.basename(path)


def test_tenants_do_not_share_state(sessions_dir):
    ats_sessions.save_state(_FakeContext({"cookies": [1]}), "jobs.lever.co/a")
    assert ats_sessions.state_path("jobs.lever.co/b") is None


def test_a_failed_save_leaves_the_previous_state(sessions_dir):
    ats_sessions.save_state(_FakeContext({"v": 1}), "t")

    class Broken:
        def storage_state(self, path):
            with open(path, "w") as f:
                f.write("{partial")
            raise RuntimeError("browser closed")

    with pytest.raises(RuntimeError):
        ats_sessions.save_state(Broken(), "t")
    with open(ats_sessions.state_path("t")) as f:
        assert json.load(f) == {"v": 1}
    assert [p for p in os.listdir(sessions_dir) if p.endswith(".tmp")] == []


def test_forget_removes_a_tenant_state(sessions_dir):
    ats_sessions.save_state(_FakeContext({"v": 1}), "t")
    ats_sessions.forget("t")
    assert ats_sessions.state_path("t") is None
    ats_sessions.forget("t")  # idempotent


class _Context:
    def __init__(self, state):
        self.state, self.added = state, []

    def storage_state(self, path=None):
        if path:
            with open(path, "w") as fh:
                json.dump(self.state, fh)
        return self.state

    def add_cookies(self, cookies):
        self.added.extend(cookies)


_MIXED = {
    "cookies": [{"name": "sid", "domain": "careers.fixtureco.com", "value": "a"},
                {"name": "pref", "domain": ".fixtureco.com", "value": "b"},
                {"name": "SID", "domain": ".google.com", "value": "operator-google-session"},
                {"name": "x", "domain": "evilfixtureco.com", "value": "c"}],
    "origins": [{"origin": "https://careers.fixtureco.com", "localStorage": []},
                {"origin": "https://accounts.google.com", "localStorage": []}],
}


def test_a_domain_filtered_save_keeps_only_that_site(sessions_dir):
    ats_sessions.save_state(_Context(_MIXED), "careers.fixtureco.com", domain="fixtureco.com")
    with open(ats_sessions.state_path("careers.fixtureco.com")) as fh:
        saved = json.load(fh)
    assert [c["name"] for c in saved["cookies"]] == ["sid", "pref"]
    assert [o["origin"] for o in saved["origins"]] == ["https://careers.fixtureco.com"]
    assert stat.S_IMODE(os.stat(ats_sessions.state_path("careers.fixtureco.com")).st_mode) == 0o600


def test_restore_cookies_adds_a_saved_tenant_to_an_open_context(sessions_dir):
    ats_sessions.save_state(_Context(_MIXED), "careers.fixtureco.com", domain="fixtureco.com")
    context = _Context({})
    assert ats_sessions.restore_cookies(context, "careers.fixtureco.com") is True
    assert {c["name"] for c in context.added} == {"sid", "pref"}


def test_restore_cookies_without_a_saved_tenant_does_nothing(sessions_dir):
    context = _Context({})
    assert ats_sessions.restore_cookies(context, "nobody.example") is False
    assert context.added == []
