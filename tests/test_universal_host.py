"""universal_filler.allowed_host / site_of: the operator's details are typed only on the posting's
own site or a known applicant-tracking host, never on wherever a tab or redirect lands."""

import pytest

import universal_filler as uf

JOB = "https://careers.fixtureco.com/jobs/123"


@pytest.mark.parametrize("url", [
    "https://careers.fixtureco.com/jobs/123/apply",
    "https://fixtureco.com/apply",
    "https://apply.fixtureco.com/form",
    "https://boards.greenhouse.io/fixtureco/jobs/1",
    "https://jobs.lever.co/fixtureco/abc",
    "https://fixtureco.wd5.myworkdayjobs.com/en-US/External/job/X",
    "https://jobs.smartrecruiters.com/Fixtureco/123",
    "https://apply.workable.com/fixtureco/j/ABC/",
    "https://careers-fixtureco.icims.com/jobs/1/job",
    "https://fixtureco.applytojob.com/apply/abc",
    "https://fixtureco.bamboohr.com/careers/1",
])
def test_own_site_and_known_ats_hosts(url):
    assert uf.allowed_host(JOB, url)


@pytest.mark.parametrize("url", [
    "https://fixtureco.com.evil.example/apply",
    "https://evilfixtureco.com/apply",
    "https://www.linkedin.com/jobs/view/1",
    "https://forms.gle/abc",
    "https://docs.google.com/forms/d/x",
    "http://localhost:8000/apply",
    "javascript:alert(1)",
    "file:///etc/passwd",
    "",
    None,
])
def test_anywhere_else_is_refused(url):
    assert not uf.allowed_host(JOB, url)


def test_a_local_test_host_matches_only_itself():
    assert uf.allowed_host("http://127.0.0.1:5000/posting", "http://127.0.0.1:5000/form")
    assert not uf.allowed_host("http://127.0.0.1:5000/posting", "http://localhost:5000/form")


@pytest.mark.parametrize("host,site", [
    ("careers.tesla.com", "tesla.com"), ("tesla.com", "tesla.com"), ("jobs.foo.co.uk", "foo.co.uk"),
    ("a.b.c.example.org", "example.org"), ("", ""),
])
def test_site_of(host, site):
    assert uf.site_of(host) == site
