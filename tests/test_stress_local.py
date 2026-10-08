"""Pressure tests against a real Postgres + PostgREST (spec 2026-10-08 fifty-a-day §5): the real
db.py, real RPCs, real grants as the anon role, Supabase's 1000-row response cap.

Skipped unless STRESS_SUPABASE_URL is set. To run (see scripts/stress/README.md):

  scripts/stress/up.sh               # Postgres 16, every migration, PostgREST, the /rest/v1 proxy
  STRESS_SUPABASE_URL=http://127.0.0.1:54331 python3 -m pytest tests/test_stress_local.py

Every scenario here reproduced a real failure before its fix (F1-F5 in the spec)."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("STRESS_SUPABASE_URL"),
                                reason="needs the local stress stack (STRESS_SUPABASE_URL)")

_ROOT = Path(__file__).resolve().parent.parent
_UUID = "0a1b2c3d-1111-2222-3333-{:012d}"


@pytest.fixture(scope="module")
def live(request):
    # Re-point config/db at the local stack for this module only, then restore the fake env that
    # conftest set for every other test.
    import jwt
    saved = {k: os.environ.get(k) for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY")}
    secret = os.environ.get("STRESS_JWT_SECRET", "local-stress-secret-local-stress-secret-0123456789")
    key = jwt.encode({"role": "anon", "iss": "supabase", "exp": int(time.time()) + 3600}, secret, algorithm="HS256")
    import config
    import db
    old = (config.SUPABASE_URL, config.SUPABASE_ANON_KEY, db.SUPABASE_URL, db.SUPABASE_ANON_KEY, db._client)
    config.SUPABASE_URL = db.SUPABASE_URL = os.environ["STRESS_SUPABASE_URL"]
    config.SUPABASE_ANON_KEY = db.SUPABASE_ANON_KEY = key
    db._client = None
    yield db
    config.SUPABASE_URL, config.SUPABASE_ANON_KEY, db.SUPABASE_URL, db.SUPABASE_ANON_KEY, db._client = old
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v


def psql(sql):
    out = subprocess.run(["psql", "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1",
                          "-d", os.environ.get("STRESS_DB", "stress"), "-c", sql],
                         capture_output=True, text=True,
                         env={**os.environ, "PGHOST": os.environ.get("PGHOST", "/tmp"),
                              "PGPORT": os.environ.get("PGPORT", "54329"),
                              "PGUSER": os.environ.get("PGUSER", "postgres")})
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.fixture(autouse=True)
def clean(live):
    psql("TRUNCATE job_applications, job_boards RESTART IDENTITY CASCADE")
    yield


# ── F1/F2: one row per job ────────────────────────────────────────────────────

def test_spellings_of_four_jobs_make_four_rows(live):
    spellings = {
        "Fireworks AI": ["https://jobs.ashbyhq.com/fireworks/69375f41-258a-4c25-ad78-5985606c438a",
                         "https://jobs.ashbyhq.com/fireworks/69375f41-258a-4c25-ad78-5985606c438a/application?embed=true",
                         "https://jobs.ashbyhq.com/fireworks/69375f41-258a-4c25-ad78-5985606c438a?utm_source=Simplify"],
        "Figma": ["https://boards.greenhouse.io/figma/jobs/6180116004",
                  "https://job-boards.greenhouse.io/figma/jobs/6180116004",
                  "https://www.figma.com/careers/job/?gh_jid=6180116004"],
        "Acme": ["https://jobs.lever.co/acme/" + _UUID.format(1), "https://jobs.lever.co/acme/" + _UUID.format(1) + "/apply"],
        "AMAT": ["https://amat.wd1.myworkdayjobs.com/en-US/External/job/Boston-MA/Product-Manager_R123",
                 "https://amat.wd1.myworkdayjobs.com/IndeedFeed/job/Boston-MA/Product-Manager_R123"],
    }
    for company, urls in spellings.items():
        for i, url in enumerate(urls):
            # A different title per spelling, so only job identity (not title identity) can dedup.
            live.create_job_application(company, f"Product Manager {i}", job_url=url, source="stress")
    assert psql("select count(*) from job_applications") == "4"


def test_concurrent_inserts_of_one_job_never_raise(live):
    results, errors = [], []
    barrier = threading.Barrier(8)

    def insert():
        barrier.wait()
        try:
            results.append(live.save_job_application("Y", "Product Manager", job_url="https://jobs.lever.co/y/" + _UUID.format(2)))
        except Exception as exc:  # pragma: no cover - the failure this test exists to catch
            errors.append(exc)

    threads = [threading.Thread(target=insert) for _ in range(8)]
    started = time.time()
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert sum(1 for row, _ in results if row) == 1
    assert time.time() - started < 5                     # no retry backoff on a unique violation
    assert psql("select count(*) from job_applications") == "1"


def test_api_role_cannot_delete_applications(live):
    live.create_job_application("Z", "Product Manager", job_url="https://jobs.lever.co/z/" + _UUID.format(3))
    with pytest.raises(Exception):
        live.get_client().table("job_applications").delete().gt("id", 0).execute()
    assert psql("select count(*) from job_applications") == "1"


# ── F3/F4/F5: the preview queue ───────────────────────────────────────────────

def _ready(company, url, **extra):
    cols = {"company": company, "role": "Product Manager", "job_url": url, "stage": "saved",
            "resume_file_ref": "r.pdf", "cover_letter_file_ref": "c.pdf", **extra}
    names = ",".join(cols)
    values = ",".join("'" + str(v).replace("'", "''") + "'" for v in cols.values())
    psql(f"insert into job_applications ({names}) values ({values})")


def test_a_ready_row_is_found_behind_1500_newer_saved_rows(live, mocker):
    import apply_agent
    mocker.patch.object(apply_agent.config, "APPLY_GENERIC_ADAPTER", "none")
    _ready("Ready Co", "https://jobs.lever.co/ready/" + _UUID.format(4), created_at="2026-10-01T00:00:00Z")
    psql("insert into job_applications(company, role, job_url, stage) select 'Co'||g, 'PM', "
         "'https://jobs.lever.co/co'||g||'/x', 'saved' from generate_series(1,1500) g")
    assert [j["company"] for j in apply_agent.preview_candidates()] == ["Ready Co"]


def test_rows_on_unpreparable_platforms_never_fill_the_batch(live, mocker):
    import apply_agent
    mocker.patch.object(apply_agent.config, "APPLY_GENERIC_ADAPTER", "none")
    for i in range(20):
        _ready(f"Oracle{i}", f"https://eofe.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/C/job/{i}",
               pick_score="0.9", platform="oracle")
    for i in range(3):
        _ready(f"Legacy{i}", f"https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/C/job/{i}", pick_score="0.95")
    _ready("Lever Co", "https://jobs.lever.co/leverco/" + _UUID.format(5), pick_score="0.1")
    batch = apply_agent.preview_candidates(limit=5)
    assert [j["company"] for j in batch] == ["Lever Co"]


def test_attempts_are_counted_and_cap_the_retries(live, mocker):
    import apply_agent
    mocker.patch.object(apply_agent.config, "APPLY_GENERIC_ADAPTER", "none")
    _ready("Flaky", "https://jobs.lever.co/flaky/" + _UUID.format(6))
    for _ in range(live.config.APPLY_PREPARE_MAX_ATTEMPTS):
        lease = live.claim_application(1, "preparing")
        assert lease
        assert live.release_application(1, lease, "failed_retryable", "page crashed")
    psql("update job_applications set updated_at = now() - interval '30 days' where id = 1")
    assert apply_agent.preview_candidates() == []
    assert psql("select prepare_attempts from job_applications where id = 1") == str(live.config.APPLY_PREPARE_MAX_ATTEMPTS)
    live.get_client().rpc("requeue_preview", {"p_id": 1}).execute()
    assert [j["id"] for j in apply_agent.preview_candidates()] == [1]


def test_twenty_workers_claim_one_row_once(live):
    _ready("Race", "https://jobs.lever.co/race/" + _UUID.format(7))
    leases, barrier = [], threading.Barrier(20)

    def claim():
        barrier.wait()
        leases.append(live.claim_application(1, "preparing"))

    threads = [threading.Thread(target=claim) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for lease in leases if lease) == 1


# ── Sourcing end to end ────────────────────────────────────────────────────────

def test_sourcing_twice_saves_nothing_new(live, mocker):
    import job_sourcing
    feed = [
        {"company": "Figma", "title": "Associate Product Manager", "url": "https://boards.greenhouse.io/figma/jobs/1?utm_source=Simplify",
         "location": "SF", "posted_at": None, "description": "", "sponsorship": "Other", "source": "simplify", "category": "Product"},
        {"company": "Acme", "title": "Senior Product Manager", "url": "https://jobs.lever.co/acme/" + _UUID.format(8),
         "location": "NYC", "posted_at": None, "description": "", "sponsorship": "Other", "source": "simplify", "category": "Product"},
        {"company": "Palantir", "title": "Software Engineer", "url": "https://jobs.lever.co/palantir/" + _UUID.format(9),
         "location": "NYC", "posted_at": None, "description": "", "sponsorship": "Other", "source": "simplify", "category": "Software"},
    ]
    mocker.patch.object(job_sourcing.job_sources, "fetch_simplify", return_value=feed)
    mocker.patch.object(job_sourcing.job_sources, "fetch_board", return_value=([], "ok"))
    mocker.patch.object(job_sourcing.db, "load_prompts", return_value={})
    mocker.patch.object(job_sourcing.db, "get_pause_scope", return_value="none")
    mocker.patch.object(job_sourcing.db, "record_run")
    mocker.patch.object(job_sourcing.time, "sleep")
    first = job_sourcing.run()
    second = job_sourcing.run()
    assert first["saved"] == 1 and first["skipped_seniority"] == 1
    assert second["saved"] == 0 and second["duplicate_same_job"] == 1
    assert psql("select platform||':'||board from job_boards order by 1") == "greenhouse:figma\nlever:acme\nlever:palantir"
    assert psql("select count(*) from job_applications") == "1"
