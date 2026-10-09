"""
Which filler apply_agent.py uses for a job_applications.job_url: one of 'greenhouse', 'ashby',
'lever', 'workday', 'aggregator' or 'generic'. Decided by hostname through job_identity, never by
substring ("clever.com" is not Lever, "?src=greenhouse.io" is not Greenhouse). Platforms with no
adapter here (SmartRecruiters, Workable, Oracle, iCIMS, unknown hosts) are 'generic'. Pure, no I/O.
See docs/superpowers/specs/2026-10-08-fifty-a-day-design.md §3.1.
"""

import config
import job_identity

_ROUTED = ("greenhouse", "ashby", "lever", "workday", "aggregator")
# job_identity platforms that route to the generic adapter (classify -> 'generic').
GENERIC_PLATFORMS = ("generic", "smartrecruiters", "workable", "oracle", "icims")


def classify(job_url):
    platform = job_identity.identify(job_url)["platform"]
    return platform if platform in _ROUTED else "generic"


def universal_platform(job_url):
    """The job_identity platform when the universal filler handles this URL on this host, else None."""
    platform = job_identity.identify(job_url)["platform"]
    if config.APPLY_UNIVERSAL_ENABLED and platform in GENERIC_PLATFORMS and platform in config.APPLY_UNIVERSAL_PLATFORMS:
        return platform
    return None


def unpreparable_platforms():
    """job_identity platforms this host can only skip: generic sites when no generic adapter is
    configured and the universal filler does not cover them, Workday when it is enabled but the account vault key is missing. Queues exclude
    them in the query itself, so a pile of them can never fill a batch and starve the rest, and the
    resume worker never builds documents nobody here can submit."""
    excluded = []
    universal = config.APPLY_UNIVERSAL_PLATFORMS if config.APPLY_UNIVERSAL_ENABLED else ()
    if config.APPLY_GENERIC_ADAPTER == "none":
        excluded += [p for p in GENERIC_PLATFORMS if p not in universal]
    if config.APPLY_WORKDAY_ENABLED and not config.VAULT_KEY:
        excluded.append("workday")
    return excluded
