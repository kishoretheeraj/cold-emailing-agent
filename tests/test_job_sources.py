"""job_sources: zero-token, no-auth public job sources normalized to one shape (spec 2026-10-08
fifty-a-day §3.3). All HTTP is mocked at job_sources._http_json; payload shapes follow the public
APIs (and Career-Ops' providers, MIT, which were verified against them)."""

import urllib.error
from datetime import datetime, timezone

import pytest

import job_sources

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def _http(mocker, responses):
    calls = []

    def fake(url, body=None):
        calls.append((url, body))
        for prefix, value in responses.items():
            if url.startswith(prefix):
                if isinstance(value, Exception):
                    raise value
                return value(body) if callable(value) else value
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    mocker.patch.object(job_sources, "_http_json", side_effect=fake)
    mocker.patch.object(job_sources.time, "sleep")
    return calls


# ── Simplify feed ──────────────────────────────────────────────────────────────

SIMPLIFY = [
    {"source": "Simplify", "category": "Product", "company_name": "Figma", "title": "Associate Product Manager",
     "active": True, "is_visible": True, "date_posted": 1791000000,
     "url": "https://boards.greenhouse.io/figma/jobs/6180116004?utm_source=Simplify&ref=Simplify",
     "locations": ["SF", "NYC"], "sponsorship": "Offers Sponsorship"},
    {"category": "Product", "company_name": "Old Co", "title": "PM", "active": False, "is_visible": True,
     "date_posted": 1791000000, "url": "https://jobs.lever.co/old/0a1b2c3d-1111-2222-3333-444455556666",
     "locations": ["SF"], "sponsorship": "Other"},
    {"category": "Software", "company_name": "Eng Co", "title": "SWE", "active": True, "is_visible": True,
     "date_posted": 1791000000, "url": "https://jobs.lever.co/eng/0a1b2c3d-1111-2222-3333-444455556667",
     "locations": ["SF"], "sponsorship": "Other"},
    {"category": "Product", "company_name": "Hidden", "title": "PM", "active": True, "is_visible": False,
     "date_posted": 1791000000, "url": "https://x.example/1", "locations": [], "sponsorship": "Other"},
    "not a dict",
    {"category": "Product", "company_name": "", "title": "PM", "active": True, "is_visible": True},
]


def test_simplify_keeps_active_visible_rows_in_the_wanted_categories(mocker):
    _http(mocker, {job_sources.config.SOURCING_SIMPLIFY_URL: SIMPLIFY})
    jobs = job_sources.fetch_simplify(["Product"])
    assert [j["company"] for j in jobs] == ["Figma"]
    job = jobs[0]
    assert job["title"] == "Associate Product Manager"
    assert job["location"] == "SF | NYC"
    assert job["sponsorship"] == "Offers Sponsorship"
    assert job["source"] == "simplify"
    assert job["posted_at"] == datetime.fromtimestamp(1791000000, tz=timezone.utc).isoformat()


@pytest.mark.parametrize("payload", [None, {}, "x", Exception("boom")])
def test_simplify_failures_return_nothing(mocker, payload):
    _http(mocker, {job_sources.config.SOURCING_SIMPLIFY_URL: payload})
    assert job_sources.fetch_simplify(["Product"]) == []


# ── Boards learned from URLs ───────────────────────────────────────────────────

@pytest.mark.parametrize("url,board", [
    ("https://boards.greenhouse.io/figma/jobs/6180116004?utm_source=Simplify", ("greenhouse", "figma")),
    ("https://jobs.ashbyhq.com/jerry.ai/7c3853bc-070f-4be4-b0a8-983459f1dee0/application?embed=true", ("ashby", "jerry.ai")),
    ("https://jobs.lever.co/palantir/0a1b2c3d-1111-2222-3333-444455556666", ("lever", "palantir")),
    ("https://amat.wd1.myworkdayjobs.com/en-US/External/job/Santa-ClaraCA/PM_R1",
     ("workday", "amat.wd1.myworkdayjobs.com/amat/External")),
    ("https://wd1.myworkdaysite.com/recruiting/amat/External/job/Santa-ClaraCA/PM_R1",
     ("workday", "wd1.myworkdaysite.com/amat/External")),
    ("https://www.ixl.com/company/jobs?gh_jid=8862211002", None),
    ("https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210752463", None),
    (None, None),
])
def test_board_from_url(url, board):
    found = job_sources.board_from_url(url, "Acme")
    assert (found and (found["platform"], found["board"])) == (board or None)


# ── Greenhouse / Ashby / Lever ─────────────────────────────────────────────────

GREENHOUSE = {"jobs": [
    {"id": 6180116004, "title": "Associate Product Manager", "absolute_url": "https://boards.greenhouse.io/figma/jobs/6180116004",
     "location": {"name": "San Francisco, CA"}, "first_published": "2026-10-01T12:00:00-04:00",
     "updated_at": "2026-10-05T12:00:00-04:00", "content": "&lt;p&gt;Own the &lt;b&gt;roadmap&lt;/b&gt;.&lt;/p&gt;"},
    {"id": 2, "title": "", "absolute_url": "https://boards.greenhouse.io/figma/jobs/2"},
    {"id": 3, "title": "No URL"},
]}

ASHBY = {"jobs": [
    {"id": "69375f41-258a-4c25-ad78-5985606c438a", "title": "Associate Product Manager", "location": "San Mateo, CA",
     "secondaryLocations": [{"location": "New York", "address": {"postalAddress": {"addressLocality": "New York",
                                                                                     "addressCountry": "United States"}}}],
     "workplaceType": "Hybrid", "isRemote": True, "isListed": True, "publishedAt": "2026-10-02T00:00:00.000+00:00",
     "jobUrl": "https://jobs.ashbyhq.com/fireworks/69375f41-258a-4c25-ad78-5985606c438a",
     "descriptionPlain": "Ship things."},
    {"id": "x", "title": "Unlisted PM", "isListed": False, "jobUrl": "https://jobs.ashbyhq.com/fireworks/x"},
    {"id": "y", "title": "Remote PM", "location": "San Francisco", "workplaceType": "Remote", "isListed": True,
     "jobUrl": "https://jobs.ashbyhq.com/fireworks/y", "descriptionHtml": "<p>Remote role</p>"},
]}

LEVER = [
    {"id": "0a1b2c3d-1111-2222-3333-444455556666", "text": "Product Manager", "hostedUrl":
     "https://jobs.lever.co/acme/0a1b2c3d-1111-2222-3333-444455556666",
     "categories": {"location": "Austin, TX", "allLocations": ["Austin, TX", "Remote - US"], "commitment": "Full-time"},
     "createdAt": 1791000000000, "descriptionPlain": "Build the platform."},
    {"id": "z", "text": "", "hostedUrl": "https://jobs.lever.co/acme/z"},
]


def test_greenhouse_board(mocker):
    calls = _http(mocker, {"https://boards-api.greenhouse.io/v1/boards/figma/jobs": GREENHOUSE})
    jobs, status = job_sources.fetch_board({"platform": "greenhouse", "board": "figma", "company": "Figma"})
    assert status == "ok"
    assert len(jobs) == 1
    job = jobs[0]
    assert (job["company"], job["title"], job["location"]) == ("Figma", "Associate Product Manager", "San Francisco, CA")
    assert job["description"] == "Own the roadmap."
    assert job["posted_at"] == "2026-10-01T12:00:00-04:00"
    assert job["source"] == "greenhouse"
    assert job["detail"] == {"board": "figma", "id": "6180116004"}
    assert "content=true" not in calls[0][0]      # descriptions come later, per surviving posting


def test_greenhouse_detail_adds_the_description(mocker):
    _http(mocker, {"https://boards-api.greenhouse.io/v1/boards/figma/jobs/6180116004":
                   {"content": "&lt;p&gt;Ship &lt;b&gt;it&lt;/b&gt;&lt;/p&gt;"}})
    job = {"source": "greenhouse", "description": "", "detail": {"board": "figma", "id": "6180116004"}}
    assert job_sources.needs_detail(job)
    job_sources.add_detail(job)
    assert job["description"] == "Ship it" and not job_sources.needs_detail(job)


def test_detail_refuses_unsafe_references(mocker):
    calls = _http(mocker, {})
    job_sources.add_detail({"source": "greenhouse", "description": "", "detail": {"board": "../x", "id": "1"}})
    assert calls == []


def test_ashby_board_folds_every_location_and_skips_unlisted(mocker):
    _http(mocker, {"https://api.ashbyhq.com/posting-api/job-board/fireworks": ASHBY})
    jobs, status = job_sources.fetch_board({"platform": "ashby", "board": "fireworks", "company": "Fireworks AI"})
    assert status == "ok"
    assert [j["title"] for j in jobs] == ["Associate Product Manager", "Remote PM"]
    assert jobs[0]["location"] == "San Mateo, CA · New York · United States"
    assert jobs[1]["location"] == "San Francisco · Remote"
    assert jobs[1]["description"] == "Remote role"


def test_lever_board_uses_all_locations_and_epoch_ms(mocker):
    _http(mocker, {"https://api.lever.co/v0/postings/acme": LEVER})
    jobs, status = job_sources.fetch_board({"platform": "lever", "board": "acme", "company": "Acme"})
    assert status == "ok" and len(jobs) == 1
    assert jobs[0]["location"] == "Austin, TX · Remote - US"
    assert jobs[0]["posted_at"] == datetime.fromtimestamp(1791000000, tz=timezone.utc).isoformat()


@pytest.mark.parametrize("error,status", [
    (urllib.error.HTTPError("u", 404, "nf", {}, None), "missing"),
    (urllib.error.HTTPError("u", 422, "gone", {}, None), "missing"),
    (urllib.error.HTTPError("u", 503, "busy", {}, None), "error"),
    (TimeoutError("slow"), "error"),
    (ValueError("bad json"), "error"),
])
def test_board_failures_are_classified_and_never_raise(mocker, error, status):
    _http(mocker, {"https://boards-api.greenhouse.io": error})
    assert job_sources.fetch_board({"platform": "greenhouse", "board": "gone", "company": "Gone"}) == ([], status)


def test_unknown_board_platform_is_an_error_not_a_crash():
    assert job_sources.fetch_board({"platform": "indeed", "board": "x"}) == ([], "error")


# ── Workday ────────────────────────────────────────────────────────────────────

def _workday_page(body):
    import json as _json
    offset = _json.loads(body)["offset"]
    if offset == 0:
        return {"total": 21, "jobPostings": [
            {"title": "Product Manager II", "externalPath": "/job/Santa-Clara-CA/Product-Manager-II_R2512345",
             "locationsText": "Santa Clara, CA", "postedOn": "Posted 3 Days Ago", "bulletFields": ["R2512345"]},
            {"title": "Product Manager II", "externalPath": "/job/Santa-Clara-CA/Product-Manager-II_R2512345",
             "locationsText": "Santa Clara, CA", "postedOn": "Posted 3 Days Ago"},
            {"title": "Product Analyst", "externalPath": "/job/Austin-TX/Product-Analyst_R9", "locationsText": "2 Locations",
             "postedOn": "Posted 30+ Days Ago"},
        ] + [{"title": f"Filler {i}", "externalPath": f"/job/X/Filler_R{100 + i}", "postedOn": "Posted Today"}
             for i in range(17)]}
    return {"total": 21, "jobPostings": [{"title": "Last", "externalPath": "/job/X/Last_R999", "postedOn": "Posted Yesterday"}]}


def test_workday_search_paginates_dedups_and_builds_public_urls(mocker):
    calls = _http(mocker, {"https://amat.wd1.myworkdayjobs.com/wday/cxs/amat/External/jobs": _workday_page})
    board = {"platform": "workday", "board": "amat.wd1.myworkdayjobs.com/amat/External", "company": "Applied Materials"}
    jobs, status = job_sources.fetch_board(board, search_terms=["product manager"], now=NOW)
    assert status == "ok"
    urls = [j["url"] for j in jobs]
    assert urls[0] == "https://amat.wd1.myworkdayjobs.com/External/job/Santa-Clara-CA/Product-Manager-II_R2512345"
    assert len(urls) == len(set(urls)) == 20
    first = jobs[0]
    assert first["posted_at"][:10] == "2026-10-06"
    assert jobs[1]["posted_at"] is None            # "30+ Days Ago" is unbounded
    assert len(calls) == 2
    assert '"searchText": "product manager"' in calls[0][1]


def test_workday_site_tenant_urls(mocker):
    _http(mocker, {"https://wd1.myworkdaysite.com/wday/cxs/amat/External/jobs":
                   {"total": 1, "jobPostings": [{"title": "PM", "externalPath": "/job/X/PM_R1"}]}})
    jobs, _ = job_sources.fetch_board({"platform": "workday", "board": "wd1.myworkdaysite.com/amat/External",
                                       "company": "AMAT"}, search_terms=["pm"], now=NOW)
    assert jobs[0]["url"] == "https://wd1.myworkdaysite.com/recruiting/amat/External/job/X/PM_R1"


def test_workday_detail_adds_the_description(mocker):
    _http(mocker, {"https://amat.wd1.myworkdayjobs.com/wday/cxs/amat/External/job/X/PM_R1": {
        "jobPostingInfo": {"jobDescription": "<p>Own <b>it</b></p>", "startDate": "2026-10-02"}}})
    job = {"title": "PM", "url": "https://amat.wd1.myworkdayjobs.com/External/job/X/PM_R1", "description": "",
           "posted_at": None, "source": "workday"}
    job_sources.add_detail(job)
    assert job["description"] == "Own it"
    assert job["posted_at"] == "2026-10-02T00:00:00+00:00"


def test_workday_detail_failure_leaves_the_job_unchanged(mocker):
    _http(mocker, {})
    job = {"title": "PM", "url": "https://amat.wd1.myworkdayjobs.com/External/job/X/PM_R1", "description": "",
           "posted_at": None, "source": "workday"}
    job_sources.add_detail(job)
    assert job["description"] == ""


@pytest.mark.parametrize("label,days", [("Posted Today", 0), ("Posted Yesterday", 1), ("Posted 5 Days Ago", 5),
                                        ("Posted 30+ Days Ago", None), ("", None), (None, None)])
def test_workday_posted_on(label, days):
    value = job_sources._workday_posted_on(label, NOW)
    assert (value and (NOW - datetime.fromisoformat(value)).days) == days if days is not None else value is None
