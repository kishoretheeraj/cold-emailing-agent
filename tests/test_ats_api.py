"""Unit tests for ats_api (httpx.MockTransport, no network)."""
import httpx
import pytest

from ats_api import _http
from ats_api.greenhouse_api import GreenhouseAPI, normalize_job
from ats_api.lever_api import LeverAPI, normalize_posting


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


@pytest.fixture(autouse=True)
def no_sleep(mocker):
    return mocker.patch.object(_http.time, "sleep")


# ── normalize ──────────────────────────────────────────────────────────────────

def test_normalize_job_maps_fields():
    raw = {
        "id": 123,
        "title": "Product Manager",
        "location": {"name": "Remote, US"},
        "departments": [{"name": "Product"}],
        "offices": [{"name": "NYC"}, {"name": ""}],
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
        "updated_at": "2026-10-01T00:00:00Z",
        "content": "&lt;p&gt;Hi&lt;/p&gt;",
    }
    assert normalize_job(raw) == {
        "id": "123",
        "title": "Product Manager",
        "location": "Remote, US",
        "departments": ["Product"],
        "offices": ["NYC"],
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
        "updated_at": "2026-10-01T00:00:00Z",
        "content": "<p>Hi</p>",
    }


def test_normalize_job_tolerates_missing_fields():
    job = normalize_job({"id": 1})
    assert job["location"] == "" and job["departments"] == [] and job["content"] == ""


def test_normalize_posting_maps_fields():
    raw = {
        "id": "abc",
        "text": "APM",
        "categories": {"location": "SF", "team": "Product"},
        "workplaceType": "hybrid",
        "hostedUrl": "https://jobs.lever.co/acme/abc",
        "createdAt": 1767225600000,
        "descriptionPlain": "Plain text",
        "description": "<p>Html</p>",
    }
    assert normalize_posting(raw) == {
        "id": "abc",
        "title": "APM",
        "location": "SF",
        "categories": {"location": "SF", "team": "Product"},
        "workplace_type": "hybrid",
        "hosted_url": "https://jobs.lever.co/acme/abc",
        "created_at": "2026-01-01T00:00:00+00:00",
        "description": "Plain text",
    }


def test_normalize_posting_tolerates_missing_fields():
    posting = normalize_posting({"id": "x"})
    assert posting["created_at"] == "" and posting["categories"] == {} and posting["location"] == ""


# ── pagination ─────────────────────────────────────────────────────────────────

def test_list_jobs_paginates(mocker):
    mocker.patch("ats_api.greenhouse_api.PAGE_SIZE", 2)
    pages = {"1": [{"id": 1}, {"id": 2}], "2": [{"id": 3}]}

    def handler(request):
        return httpx.Response(200, json={"jobs": pages.get(request.url.params["page"], [])})

    jobs = GreenhouseAPI("acme", client=_client(handler)).list_jobs()
    assert [j["id"] for j in jobs] == ["1", "2", "3"]


def test_list_jobs_stops_when_api_ignores_paging(mocker):
    mocker.patch("ats_api.greenhouse_api.PAGE_SIZE", 2)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"jobs": [{"id": 1}, {"id": 2}]})

    jobs = GreenhouseAPI("acme", client=_client(handler)).list_jobs()
    assert len(jobs) == 2 and len(calls) == 2


def test_list_postings_paginates(mocker):
    mocker.patch("ats_api.lever_api.PAGE_SIZE", 2)

    def handler(request):
        skip = int(request.url.params["skip"])
        data = [{"id": "a"}, {"id": "b"}] if skip == 0 else [{"id": "c"}]
        return httpx.Response(200, json=data)

    postings = LeverAPI("acme", client=_client(handler)).list_postings()
    assert [p["id"] for p in postings] == ["a", "b", "c"]


# ── single lookups ─────────────────────────────────────────────────────────────

def test_get_job_and_posting_return_none_on_404():
    miss = _client(lambda r: httpx.Response(404))
    assert GreenhouseAPI("acme", client=miss).get_job("1") is None
    assert LeverAPI("acme", client=miss).get_posting("1") is None


def test_get_job_returns_normalized():
    client = _client(lambda r: httpx.Response(200, json={"id": 9, "title": "PM"}))
    assert GreenhouseAPI("acme", client=client).get_job("9")["title"] == "PM"


# ── rate limiting ──────────────────────────────────────────────────────────────

def test_429_backs_off_then_succeeds(no_sleep):
    responses = iter([
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(429),
        httpx.Response(200, json={"jobs": []}),
    ])
    GreenhouseAPI("acme", client=_client(lambda r: next(responses))).list_jobs()
    assert [c.args[0] for c in no_sleep.call_args_list] == [3.0, 4.0]


def test_429_gives_up_after_max_retries(no_sleep):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429)

    with pytest.raises(httpx.HTTPStatusError):
        LeverAPI("acme", client=_client(handler)).list_postings()
    assert len(calls) == _http.MAX_RETRIES + 1
    assert no_sleep.call_count == _http.MAX_RETRIES


def test_retry_after_is_capped(no_sleep):
    responses = iter([httpx.Response(429, headers={"Retry-After": "9999"}), httpx.Response(200, json=[])])
    LeverAPI("acme", client=_client(lambda r: next(responses))).list_postings()
    no_sleep.assert_called_once_with(_http.MAX_SLEEP_SECONDS)


def test_server_error_raises():
    with pytest.raises(httpx.HTTPStatusError):
        GreenhouseAPI("acme", client=_client(lambda r: httpx.Response(500))).list_jobs()


# ── timeouts ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cls", [GreenhouseAPI, LeverAPI])
def test_default_client_has_timeouts(cls):
    assert cls("acme")._client.timeout == httpx.Timeout(30.0)
    assert cls("acme", timeout=7.0)._client.timeout == httpx.Timeout(7.0)
