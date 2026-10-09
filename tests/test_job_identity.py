"""job_identity: one canonical key per real job, whatever URL spelling a source used (spec
2026-10-08 fifty-a-day §3.1). Classification is by hostname, never by substring."""

import pytest

import job_identity


# ── Same job, different spellings: one key ────────────────────────────────────

@pytest.mark.parametrize("urls,key", [
    ([
        "https://jobs.ashbyhq.com/fireworks/69375f41-258a-4c25-ad78-5985606c438a",
        "https://jobs.ashbyhq.com/fireworks/69375f41-258a-4c25-ad78-5985606c438a/application?embed=true",
        "https://jobs.ashbyhq.com/fireworks/69375f41-258a-4c25-ad78-5985606c438a?utm_source=Simplify",
        "http://JOBS.ASHBYHQ.COM/fireworks/69375F41-258A-4C25-AD78-5985606C438A/",
    ], "ashby:69375f41-258a-4c25-ad78-5985606c438a"),
    ([
        "https://boards.greenhouse.io/figma/jobs/6180116004",
        "https://job-boards.greenhouse.io/figma/jobs/6180116004?gh_src=abc",
        "https://www.figma.com/careers/job/?gh_jid=6180116004",
        "https://boards.greenhouse.io/embed/job_app?for=figma&token=6180116004",
        "https://job-boards.eu.greenhouse.io/figma/jobs/6180116004#app",
    ], "greenhouse:6180116004"),
    ([
        "https://jobs.lever.co/acme/0a1b2c3d-1111-2222-3333-444455556666",
        "https://jobs.lever.co/acme/0a1b2c3d-1111-2222-3333-444455556666/apply?lever-source=LinkedIn",
        "https://jobs.eu.lever.co/acme/0a1b2c3d-1111-2222-3333-444455556666/",
    ], "lever:0a1b2c3d-1111-2222-3333-444455556666"),
    ([
        "https://amat.wd1.myworkdayjobs.com/External/job/Santa-ClaraCA/Product-Manager-II_R2512345",
        "https://amat.wd1.myworkdayjobs.com/en-US/External/job/Santa-ClaraCA/Product-Manager-II_R2512345?source=Simplify",
        "https://amat.wd1.myworkdayjobs.com/en-US/External/details/Product-Manager-II_R2512345",
        "https://wd1.myworkdaysite.com/recruiting/amat/External/job/Santa-ClaraCA/Product-Manager-II_R2512345",
        "https://amat.wd1.myworkdayjobs.com/IndeedFeed/job/Santa-ClaraCA/Product-Manager-II_R2512345",
    ], "workday:amat:r2512345"),
])
def test_spellings_of_one_job_share_a_key(urls, key):
    assert {job_identity.identify(u)["job_key"] for u in urls} == {key}


def test_different_jobs_never_share_a_key():
    urls = [
        "https://boards.greenhouse.io/figma/jobs/6180116004",
        "https://boards.greenhouse.io/figma/jobs/6180116005",
        "https://jobs.lever.co/acme/0a1b2c3d-1111-2222-3333-444455556666",
        "https://jobs.lever.co/acme/0a1b2c3d-1111-2222-3333-444455556667",
        "https://amat.wd1.myworkdayjobs.com/External/job/Austin-TX/Product-Manager-II_R2512345",
        "https://amat.wd1.myworkdayjobs.com/External/job/Austin-TX/Product-Manager-II_R2512346",
        "https://other.wd1.myworkdayjobs.com/External/job/Austin-TX/Product-Manager-II_R2512345",
        "https://example.com/careers/pm-1",
        "https://example.com/careers/pm-2",
    ]
    keys = [job_identity.identify(u)["job_key"] for u in urls]
    assert len(set(keys)) == len(keys)


# ── Platform by hostname, never by substring ──────────────────────────────────

@pytest.mark.parametrize("url,platform", [
    ("https://jobs.ashbyhq.com/langchain/27af5f96-b287-4bcc-8679-f96686dc7c8d/application", "ashby"),
    ("https://boards.greenhouse.io/embed/job_app?token=5225255007", "greenhouse"),
    ("https://www.ixl.com/company/jobs?gh_jid=8862211002", "greenhouse"),
    ("https://jobs.lever.co/trevipay/974ba7f3-f1cd-4413-ba6a-e0218945ece2/apply", "lever"),
    ("https://starz.wd5.myworkdayjobs.com/Starz/job/Los-Angeles-CA/Product-Manager_JR100420", "workday"),
    ("https://acme.myworkdaysite.com/en-US/careers/job/Product-Manager_JR1", "workday"),
    ("https://www.indeed.com/viewjob?jk=c332a32a80b25ee9", "aggregator"),
    ("https://www.linkedin.com/jobs/view/4012345678/", "aggregator"),
    ("https://www.ycombinator.com/companies/agave/jobs/Z4eqc5c-product-analyst", "aggregator"),
    ("https://jobs.smartrecruiters.com/Visa/744000080000000-product-manager", "smartrecruiters"),
    ("https://apply.workable.com/acme/j/ABC123DEF4/", "workable"),
    ("https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210752463", "oracle"),
    ("https://careers-orientaltrading.icims.com/jobs/3290/e-commerce-product-manager/job", "icims"),
    # Substring look-alikes are not those platforms.
    ("https://clever.com/about/careers/123", "generic"),
    ("https://jobs.example.com/apply?src=greenhouse.io", "generic"),
    ("https://careers.ashbyhq.com.evil.example/x/27af5f96-b287-4bcc-8679-f96686dc7c8d", "generic"),
    ("https://notlever.co/acme/974ba7f3-f1cd-4413-ba6a-e0218945ece2", "generic"),
    ("https://www.linkedin.com/company/acme/", "generic"),
    (None, "generic"),
    ("", "generic"),
    ("not a url", "generic"),
])
def test_platform(url, platform):
    assert job_identity.identify(url)["platform"] == platform


def test_unknown_values_never_raise():
    for value in (None, "", "   ", "ftp://x", "https://", 42, "javascript:alert(1)", "https://[::1"):
        result = job_identity.identify(value)
        assert result["platform"] == "generic"


# ── Where the browser goes ─────────────────────────────────────────────────────

@pytest.mark.parametrize("url,apply_url", [
    ("https://jobs.ashbyhq.com/fireworks/69375f41-258a-4c25-ad78-5985606c438a?utm_source=x",
     "https://jobs.ashbyhq.com/fireworks/69375f41-258a-4c25-ad78-5985606c438a/application"),
    ("https://jobs.lever.co/acme/0a1b2c3d-1111-2222-3333-444455556666",
     "https://jobs.lever.co/acme/0a1b2c3d-1111-2222-3333-444455556666/apply"),
    ("https://jobs.eu.lever.co/acme/0a1b2c3d-1111-2222-3333-444455556666/apply",
     "https://jobs.eu.lever.co/acme/0a1b2c3d-1111-2222-3333-444455556666/apply"),
    ("https://boards.greenhouse.io/figma/jobs/6180116004?gh_src=1",
     "https://job-boards.greenhouse.io/figma/jobs/6180116004"),
    # A company page that only embeds the Greenhouse form: open the form itself.
    ("https://www.ixl.com/company/jobs?gh_jid=8862211002",
     "https://boards.greenhouse.io/embed/job_app?token=8862211002"),
    ("https://amat.wd1.myworkdayjobs.com/en-US/External/job/Santa-ClaraCA/PM_R1?source=x",
     "https://amat.wd1.myworkdayjobs.com/en-US/External/job/Santa-ClaraCA/PM_R1"),
    ("https://example.com/careers/pm-1?utm_source=a&utm_medium=b&id=7",
     "https://example.com/careers/pm-1?id=7"),
])
def test_apply_url(url, apply_url):
    assert job_identity.identify(url)["apply_url"] == apply_url


def test_generic_key_ignores_tracking_parameters_but_keeps_real_ones():
    a = job_identity.identify("https://Example.com/careers/pm/?utm_source=x&ref=y&id=7#top")
    b = job_identity.identify("https://example.com/careers/pm?id=7")
    c = job_identity.identify("https://example.com/careers/pm?id=8")
    assert a["job_key"] == b["job_key"] != c["job_key"]
    assert a["job_key"].startswith("url:")


# ── Company and title identity ─────────────────────────────────────────────────

@pytest.mark.parametrize("a,b", [
    ("Figma, Inc.", "figma"),
    ("The Walt Disney Company", "Walt Disney"),
    ("Fireworks AI", "Fireworks  AI"),
    ("AT&T", "AT & T"),
])
def test_company_key_folds_legal_noise(a, b):
    assert job_identity.company_key(a) == job_identity.company_key(b)


def test_company_key_keeps_different_companies_apart():
    assert job_identity.company_key("Meta") != job_identity.company_key("Metal")


@pytest.mark.parametrize("a,b,same", [
    ("Associate Product Manager", "Product Manager, Associate", True),
    ("Product Manager - Payments", "Product Manager (Payments)", True),
    ("Product Manager - Berlin", "Product Manager - Munich", False),
    ("Product Manager I", "Product Manager II", False),
    ("Senior Product Manager", "Product Manager", False),
])
def test_title_identity_is_the_same_set_of_words(a, b, same):
    assert (job_identity.title_key(a) == job_identity.title_key(b)) is same


# ── Description fingerprint (SimHash) ─────────────────────────────────────────

_JD = ("We are looking for a product manager to own the payments roadmap end to end. You will work with "
       "engineering, design and data science to ship features that move activation and retention. "
       "You have two or more years of experience in product or analytics, strong SQL, and a habit of "
       "writing crisp specs. Bonus points for lending or fintech experience and A/B testing at scale.")


def test_fingerprint_matches_a_lightly_edited_repost():
    a = job_identity.fingerprint(_JD)
    b = job_identity.fingerprint(_JD.replace("two or more", "2+").replace("Bonus points", "Extra credit"))
    assert len(a) == 16
    assert job_identity.similarity(a, b) >= job_identity.REPOST_SIMILARITY


def test_fingerprint_keeps_unrelated_descriptions_apart():
    other = ("Join our warehouse team as a forklift operator. Duties include loading trucks, scanning "
             "inventory, keeping aisles clear, following safety rules and working rotating weekend "
             "shifts in a fast paced distribution center with a friendly crew and great benefits.")
    assert job_identity.similarity(job_identity.fingerprint(_JD), job_identity.fingerprint(other)) < 0.8


@pytest.mark.parametrize("text", [None, "", "too short to fingerprint", "x" * 500])
def test_no_fingerprint_without_enough_text(text):
    assert job_identity.fingerprint(text) == ""
    assert job_identity.similarity("", "") == 0
