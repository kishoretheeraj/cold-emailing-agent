"""Pure function, exhaustive URL-pattern test table -- one case per platform, including the
aggregator-domain exclusions found live during this phase's own research (real URLs pulled from
a jobright.py run)."""

import pytest

import ats_platform
import config


@pytest.mark.parametrize("url,expected", [
    ("https://jobs.ashbyhq.com/langchain/27af5f96-b287-4bcc-8679-f96686dc7c8d/application", "ashby"),
    ("https://boards.greenhouse.io/embed/job_app?token=5225255007", "greenhouse"),
    ("https://jobs.lever.co/trevipay/974ba7f3-f1cd-4413-ba6a-e0218945ece2/apply", "lever"),
    ("https://starz.wd5.myworkdayjobs.com/Starz/job/Los-Angeles-CA/Product-Manager_JR100420", "workday"),
    ("https://hdsupply.wd1.myworkdayjobs.com/external/job/Atlanta-GA-US/Product-Manager_R26004414", "workday"),
    ("https://www.indeed.com/viewjob?jk=c332a32a80b25ee9", "aggregator"),
    ("https://www.ziprecruiter.com/kn/AAJgauwB", "aggregator"),
    ("https://www.ycombinator.com/companies/agave/jobs/Z4eqc5c-product-analyst", "aggregator"),
    ("https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210752463", "generic"),
    ("https://careers-orientaltrading.icims.com/jobs/3290/e-commerce-product-manager/job", "generic"),
    (None, "generic"),
    ("", "generic"),
])
def test_classify(url, expected):
    assert ats_platform.classify(url) == expected


@pytest.mark.parametrize("url", [
    # Workday also serves career sites on myworkdaysite.com, and the `.wdN.` tenant segment
    # is not universal. Requiring it let these fall through to 'generic' -- the browser-use
    # path -- despite Workday being permanently excluded.
    "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/Product-Manager_JR1",
    "https://myworkdayjobs.com/en-US/acme/job/Product-Manager_JR1",
    "https://acme.myworkdaysite.com/en-US/careers/job/Product-Manager_JR1",
    "https://ACME.WD1.MYWORKDAYJOBS.COM/job/1",
])
def test_workday_variants_are_all_excluded(url):
    assert ats_platform.classify(url) == "workday"


@pytest.mark.parametrize("url,expected", [
    # Substring look-alikes used to route to a hand-mapped filler (fifty-a-day F6).
    ("https://clever.com/about/careers/123", "generic"),
    ("https://jobs.example.com/apply?src=greenhouse.io", "generic"),
    ("https://careers.ashbyhq.com.evil.example/x/27af5f96-b287-4bcc-8679-f96686dc7c8d", "generic"),
    # A company page embedding the Greenhouse form is fillable as Greenhouse.
    ("https://www.ixl.com/company/jobs?gh_jid=8862211002", "greenhouse"),
    # Platforms without an adapter here stay generic for routing.
    ("https://jobs.smartrecruiters.com/Visa/744000080000000-product-manager", "generic"),
])
def test_classify_by_hostname(url, expected):
    assert ats_platform.classify(url) == expected


# ── Universal filler routing (spec 2026-10-09) ─────────────────────────────────

@pytest.mark.parametrize("enabled,platforms,url,expected", [
    (True, ("generic",), "https://careers.fixtureco.com/jobs/1", "generic"),
    (True, ("generic",), "https://apply.workable.com/fixtureco/j/ABC/", None),
    (True, ("workable",), "https://apply.workable.com/fixtureco/j/ABC/", "workable"),
    (False, ("generic",), "https://careers.fixtureco.com/jobs/1", None),
    (True, ("generic", "greenhouse"), "https://boards.greenhouse.io/x/jobs/1", None),  # hand-mapped stays hand-mapped
    (True, ("generic",), "https://www.linkedin.com/jobs/view/1", None),                # aggregators never
])
def test_universal_platform(mocker, enabled, platforms, url, expected):
    mocker.patch.object(config, "APPLY_UNIVERSAL_ENABLED", enabled)
    mocker.patch.object(config, "APPLY_UNIVERSAL_PLATFORMS", platforms)
    assert ats_platform.universal_platform(url) == expected


def test_unpreparable_keeps_only_the_platforms_the_universal_filler_does_not_cover(mocker):
    mocker.patch.object(config, "APPLY_GENERIC_ADAPTER", "none")
    mocker.patch.object(config, "APPLY_WORKDAY_ENABLED", False)
    mocker.patch.object(config, "APPLY_UNIVERSAL_ENABLED", True)
    mocker.patch.object(config, "APPLY_UNIVERSAL_PLATFORMS", ("generic", "workable"))
    assert ats_platform.unpreparable_platforms() == ["smartrecruiters", "oracle", "icims"]
    mocker.patch.object(config, "APPLY_UNIVERSAL_ENABLED", False)
    assert ats_platform.unpreparable_platforms() == list(ats_platform.GENERIC_PLATFORMS)
