"""
Pure URL-pattern classifier for job_applications.job_url -- decides which filler
apply_agent.py uses. No I/O, no logging, no state. Matches on domain/path
fragments only, never a "did not match anything known" guess: unmatched URLs
fall to 'generic' (browser-use handles it), never silently into 'workday' or
'aggregator'. See docs/superpowers/specs/2026-08-30-phase2.5-auto-apply-design.md.
"""

import re

import config

_WORKDAY_PATTERN = re.compile(r"\.wd\d+\.myworkdayjobs\.com", re.IGNORECASE)


def classify(job_url):
    if not job_url:
        return "generic"
    lowered = job_url.lower()

    if "ashbyhq.com" in lowered:
        return "ashby"
    if "greenhouse.io" in lowered:
        return "greenhouse"
    if "lever.co" in lowered:
        return "lever"
    if _WORKDAY_PATTERN.search(lowered):
        return "workday"
    for domain in config.APPLY_AGENT_AGGREGATOR_DOMAINS:
        if domain in lowered:
            return "aggregator"
    return "generic"
