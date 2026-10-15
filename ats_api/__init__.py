"""Direct REST clients for public ATS job-board APIs (Greenhouse, Lever).

Separate from the stdlib-only ats.py research channel: these use httpx, raise on
unrecoverable errors, and return normalized postings for the apply pipeline.
"""
from ats_api.greenhouse_api import GreenhouseAPI, normalize_job
from ats_api.lever_api import LeverAPI, normalize_posting

__all__ = ["GreenhouseAPI", "LeverAPI", "normalize_job", "normalize_posting"]
