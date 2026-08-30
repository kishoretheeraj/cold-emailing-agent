"""Pure function, exhaustive URL-pattern test table -- one case per platform, including the
aggregator-domain exclusions found live during this phase's own research (real URLs pulled from
a jobright.py run)."""

import pytest

import ats_platform


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
