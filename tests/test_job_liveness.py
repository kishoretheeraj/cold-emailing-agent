"""job_liveness: is a posting still open, asked of the ATS's own API before any resume is built
(spec 2026-10-08 fifty-a-day §3.3). Conservative: only a definitive answer says 'closed'."""

import urllib.error

import pytest

import job_liveness

GH = "https://boards.greenhouse.io/figma/jobs/6180116004"
LEVER = "https://jobs.lever.co/acme/0a1b2c3d-1111-2222-3333-444455556666"
ASHBY = "https://jobs.ashbyhq.com/fireworks/69375f41-258a-4c25-ad78-5985606c438a"
WD = "https://amat.wd1.myworkdayjobs.com/en-US/External/job/Santa-Clara-CA/PM_R1"


def _http(mocker, result):
    calls = []

    def fake(url):
        calls.append(url)
        if isinstance(result, Exception):
            raise result
        return result

    mocker.patch.object(job_liveness, "_http_json", side_effect=fake)
    return calls


def _status(code):
    return urllib.error.HTTPError("u", code, "x", {}, None)


@pytest.mark.parametrize("url,result,expected,endpoint", [
    (GH, {"id": 6180116004}, "live", "https://boards-api.greenhouse.io/v1/boards/figma/jobs/6180116004"),
    (GH, _status(404), "closed", None),
    (GH, _status(410), "closed", None),
    (GH, _status(429), "unknown", None),
    (GH, TimeoutError(), "unknown", None),
    (LEVER, {"id": "x"}, "live", "https://api.lever.co/v0/postings/acme/0a1b2c3d-1111-2222-3333-444455556666"),
    # Lever's API 404s confidential postings whose page still works: never proof of closure.
    (LEVER, _status(404), "unknown", None),
    (ASHBY, {"jobs": [{"id": "69375f41-258a-4c25-ad78-5985606c438a"}]}, "live",
     "https://api.ashbyhq.com/posting-api/job-board/fireworks"),
    (ASHBY, {"jobs": [{"id": "something-else"}]}, "closed", None),
    (ASHBY, {"unexpected": True}, "unknown", None),
    (WD, {"jobPostingInfo": {"title": "PM"}}, "live",
     "https://amat.wd1.myworkdayjobs.com/wday/cxs/amat/External/job/Santa-Clara-CA/PM_R1"),
    (WD, _status(404), "closed", None),
    (WD, _status(403), "unknown", None),
])
def test_check(mocker, url, result, expected, endpoint):
    calls = _http(mocker, result)
    assert job_liveness.check(url) == expected
    if endpoint:
        assert calls == [endpoint]


@pytest.mark.parametrize("url", [
    None, "", "https://example.com/careers/pm", "https://www.ixl.com/company/jobs?gh_jid=8862211002",
    "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/C/job/1",
])
def test_unknown_sites_are_never_called(mocker, url):
    calls = _http(mocker, {})
    assert job_liveness.check(url) == "unknown"
    assert calls == []
