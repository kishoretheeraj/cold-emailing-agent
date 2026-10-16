from job_contract import CANONICAL_KEYS, canonical_job, canonical_jobs

GREENHOUSE = {"id": "1", "title": "Product Manager", "location": "Remote",
              "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "updated_at": "2026-10-01T00:00:00Z",
              "content": "<p>Build &amp; ship</p>"}
LEVER = {"id": "u", "title": "APM", "location": "NYC", "hosted_url": "https://jobs.lever.co/acme/u",
         "created_at": "2026-10-02T00:00:00+00:00", "description": "Plain text"}


def test_greenhouse_shape_maps_to_canonical():
    job = canonical_job(GREENHOUSE, company="acme", source="greenhouse")
    assert set(job) == set(CANONICAL_KEYS)
    assert job["url"] == GREENHOUSE["absolute_url"]
    assert job["description"] == "Build & ship"
    assert job["posted_at"] == "2026-10-01T00:00:00Z"
    assert job["company"] == "acme" and job["source"] == "greenhouse"


def test_lever_shape_maps_to_canonical():
    job = canonical_job(LEVER, company="acme", source="lever")
    assert job["url"] == LEVER["hosted_url"] and job["description"] == "Plain text"
    assert job["posted_at"] == LEVER["created_at"]


def test_canonical_input_is_returned_unchanged_same_object():
    raw = {"company": "A", "title": "T", "url": "https://x/y", "description": "d"}
    assert canonical_job(raw) is raw


def test_non_dict_is_none_and_list_helper_drops_it():
    assert canonical_job(None) is None and canonical_job("x") is None
    assert len(canonical_jobs([GREENHOUSE, None, LEVER], company="acme")) == 2


def test_missing_fields_default_to_empty():
    job = canonical_job({"title": "T"})
    assert job["company"] == "" and job["url"] == "" and job["posted_at"] is None
