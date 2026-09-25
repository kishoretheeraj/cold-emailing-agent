"""Tests for cu_linkedin.py's posting extraction and job_applications persistence.
Supabase is mocked at db.create_job_application -- nothing here talks to a real project."""

import pytest

import config
import cu_linkedin
import db


# ── _canonical_job_url ─────────────────────────────────────────────────────────

def test_canonical_job_url_strips_linkedin_tracking_params():
    # LinkedIn appends a per-impression refId/trackingId. Dedup in db.create_job_application is
    # an exact match on job_url, so without this the same posting seen twice makes two rows.
    url = "https://www.linkedin.com/jobs/view/4123456789/?refId=abc&trackingId=xyz"
    assert cu_linkedin._canonical_job_url(url) == "https://www.linkedin.com/jobs/view/4123456789"


def test_canonical_job_url_strips_fragments_and_trailing_slash():
    url = "https://www.linkedin.com/jobs/view/42/#top"
    assert cu_linkedin._canonical_job_url(url) == "https://www.linkedin.com/jobs/view/42"


@pytest.mark.parametrize("value", [None, "", "   ", 42, {"url": "x"}])
def test_canonical_job_url_returns_none_for_junk(value):
    assert cu_linkedin._canonical_job_url(value) is None


def test_canonical_job_url_recovers_current_job_id_from_the_query_string():
    # Clicking a result in LinkedIn's list pane commonly leaves the address bar on
    # .../jobs/collections/recommended/?currentJobId=<id> -- the posting id lives only in the
    # query string there, not the path. The old split("?")[0] behavior stripped it along with the
    # tracking noise, collapsing every posting in a session onto the same bare "recommended" URL.
    url = ("https://www.linkedin.com/jobs/collections/recommended/"
           "?currentJobId=4123456789&some=other&params=here")
    assert cu_linkedin._canonical_job_url(url) == "https://www.linkedin.com/jobs/view/4123456789"


def test_canonical_job_url_returns_none_for_a_non_posting_url():
    # No currentJobId in the query string and not a /jobs/view/<id> path -- this is the
    # "can't be parsed" case, not a posting. Must return None, never a truthy garbage string.
    url = "https://www.linkedin.com/jobs/search/?keywords=engineer"
    assert cu_linkedin._canonical_job_url(url) is None


# ── extract_postings ───────────────────────────────────────────────────────────

def test_extract_postings_parses_a_plain_json_array():
    text = """[
      {"company": "Acme", "role": "Product Manager",
       "job_url": "https://www.linkedin.com/jobs/view/1?refId=z",
       "location": "Remote", "description": "Own the roadmap."}
    ]"""
    assert cu_linkedin.extract_postings(text) == [{
        "company": "Acme",
        "role": "Product Manager",
        "job_url": "https://www.linkedin.com/jobs/view/1",
        "location": "Remote",
        "description": "Own the roadmap.",
        "source": "linkedin",
    }]


def test_extract_postings_strips_a_markdown_json_fence():
    text = ('```json\n[{"company": "Acme", "role": "PM", '
            '"job_url": "https://www.linkedin.com/jobs/view/1"}]\n```')
    assert len(cu_linkedin.extract_postings(text)) == 1


def test_extract_postings_skips_a_posting_whose_url_cannot_be_canonicalized():
    # _canonical_job_url returns None for a non-posting URL (no /jobs/view/<id>, no
    # currentJobId) -- the posting must be skipped entirely, never persisted with a bad or
    # missing job_url.
    import json
    text = json.dumps([{
        "company": "Acme", "role": "PM",
        "job_url": "https://www.linkedin.com/jobs/search/?keywords=engineer",
    }])
    assert cu_linkedin.extract_postings(text) == []


@pytest.mark.parametrize("posting", [
    {"role": "PM", "job_url": "https://x/1"},
    {"company": "Acme", "job_url": "https://x/1"},
    {"company": "Acme", "role": "PM"},
    {"company": "", "role": "PM", "job_url": "https://x/1"},
    {"company": "Acme", "role": "   ", "job_url": "https://x/1"},
    {"company": None, "role": None, "job_url": None},
])
def test_extract_postings_skips_rows_missing_a_required_field(posting):
    import json
    assert cu_linkedin.extract_postings(json.dumps([posting])) == []


@pytest.mark.parametrize("text", [
    "", "   ", "CAPTCHA_OR_CHALLENGE", "not json at all", "{}", '{"company": "Acme"}',
    "[1, 2, 3]", '["a string"]', None,
])
def test_extract_postings_never_raises_on_junk(text):
    assert cu_linkedin.extract_postings(text) == []


# ── persist_postings ───────────────────────────────────────────────────────────

def _posting(company="Acme", role="PM", url="https://x/1"):
    return {"company": company, "role": role, "job_url": url,
            "location": "Remote", "description": "d", "source": "linkedin"}


def test_persist_postings_writes_with_the_linkedin_source(mocker):
    create = mocker.patch.object(db, "create_job_application", return_value={"id": 7})
    assert cu_linkedin.persist_postings([_posting()]) == (1, 0, 0)
    create.assert_called_once_with(
        company="Acme", role="PM", job_url="https://x/1",
        source="linkedin", posting_snapshot=_posting(),
    )


def test_persist_postings_counts_a_dedup_none_as_skipped_not_an_error(mocker):
    mocker.patch.object(db, "create_job_application", return_value=None)
    assert cu_linkedin.persist_postings([_posting()]) == (0, 1, 0)


def test_persist_postings_isolates_a_single_row_failure(mocker):
    mocker.patch.object(db, "create_job_application",
                        side_effect=[RuntimeError("boom"), {"id": 2}])
    saved, skipped, errors = cu_linkedin.persist_postings(
        [_posting(url="https://x/1"), _posting(url="https://x/2")]
    )
    assert (saved, skipped, errors) == (1, 0, 1)


def test_persist_postings_truncates_to_the_per_session_cap(mocker):
    create = mocker.patch.object(db, "create_job_application", return_value={"id": 1})
    postings = [_posting(url=f"https://x/{i}")
                for i in range(config.CU_LINKEDIN_MAX_POSTINGS_PER_SESSION + 10)]
    saved, _, _ = cu_linkedin.persist_postings(postings)
    assert saved == config.CU_LINKEDIN_MAX_POSTINGS_PER_SESSION
    assert create.call_count == config.CU_LINKEDIN_MAX_POSTINGS_PER_SESSION
