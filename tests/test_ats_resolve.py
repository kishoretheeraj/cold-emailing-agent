import pytest

from ats_api import resolve


@pytest.mark.parametrize("url,expected", [
    ("https://boards.greenhouse.io/acme/jobs/123", ("greenhouse", "acme", "123")),
    ("https://job-boards.greenhouse.io/acme/jobs/9?gh_src=x", ("greenhouse", "acme", "9")),
    ("https://jobs.lever.co/acme/abc-uuid", ("lever", "acme", "abc-uuid")),
    ("https://evilgreenhouse.io/acme/jobs/1", None),
    ("https://acme.com/careers?gh_jid=5", None),
    ("https://boards.greenhouse.io/acme", None),
    ("", None), (None, None),
])
def test_board_ref(url, expected):
    assert resolve.board_ref(url) == expected


class _Client:
    def __init__(self, token): self.token = token
    def __enter__(self): return self
    def __exit__(self, *exc): return None


class _GH(_Client):
    def get_job(self, job_id):
        return {"id": job_id, "title": "PM", "location": "Remote", "content": "<p>Own the roadmap</p>",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "updated_at": "2026-10-01"}


class _Lever(_Client):
    def get_posting(self, posting_id):
        return None


class _Boom(_Client):
    def get_job(self, job_id):
        raise RuntimeError("network down")


def test_fetch_posting_greenhouse_returns_canonical(monkeypatch):
    monkeypatch.setattr(resolve, "GreenhouseAPI", _GH)
    job = resolve.fetch_posting("https://boards.greenhouse.io/acme/jobs/1")
    assert job["company"] == "acme" and job["description"] == "Own the roadmap" and job["source"] == "greenhouse"


def test_fetch_posting_missing_posting_is_none(monkeypatch):
    monkeypatch.setattr(resolve, "LeverAPI", _Lever)
    assert resolve.fetch_posting("https://jobs.lever.co/acme/u") is None


def test_fetch_posting_failure_never_raises(monkeypatch):
    monkeypatch.setattr(resolve, "GreenhouseAPI", _Boom)
    assert resolve.fetch_posting("https://boards.greenhouse.io/acme/jobs/1") is None


def test_unsupported_url_makes_no_client(monkeypatch):
    monkeypatch.setattr(resolve, "GreenhouseAPI", lambda *a: pytest.fail("client built"))
    assert resolve.fetch_posting("https://example.com/job/1") is None
