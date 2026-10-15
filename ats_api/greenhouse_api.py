"""Greenhouse public job board API client (no API key needed).

Endpoints: GET /v1/boards/{token}/jobs and /v1/boards/{token}/jobs/{id}.
"""
import html
import logging

import httpx

from ats_api import _http

log = logging.getLogger(__name__)

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"
PAGE_SIZE = 100
MAX_PAGES = 50


def _names(items) -> list[str]:
    return [i["name"] for i in (items or []) if isinstance(i, dict) and i.get("name")]


def normalize_job(raw: dict) -> dict:
    """Map a raw Greenhouse job object to the pipeline's normalized shape."""
    location = raw.get("location")
    return {
        "id": str(raw.get("id", "")),
        "title": raw.get("title", ""),
        "location": location.get("name", "") if isinstance(location, dict) else (location or ""),
        "departments": _names(raw.get("departments")),
        "offices": _names(raw.get("offices")),
        "absolute_url": raw.get("absolute_url", ""),
        "updated_at": raw.get("updated_at", ""),
        # Greenhouse returns the description HTML-escaped.
        "content": html.unescape(raw.get("content") or ""),
    }


class GreenhouseAPI:
    """Client for one company's Greenhouse board."""

    def __init__(self, board_token: str, timeout: float = 30.0, client: httpx.Client | None = None) -> None:
        """Create a client; pass client only to inject a custom transport (tests)."""
        self.board_token = board_token
        self._client = client or httpx.Client(timeout=timeout)

    def list_jobs(self) -> list[dict]:
        """Return all open jobs, normalized, with descriptions included.

        Requests pages until one comes back short, empty, or with no unseen ids
        (the endpoint may ignore paging params and return everything at once).
        """
        url = f"{BASE_URL}/{self.board_token}/jobs"
        jobs: list[dict] = []
        seen: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            data = _http.get_json(
                self._client, url, {"content": "true", "page": page, "per_page": PAGE_SIZE}
            )
            raw_jobs = data.get("jobs", []) if isinstance(data, dict) else []
            fresh = [j for j in raw_jobs if str(j.get("id", "")) not in seen]
            if not fresh:
                break
            for raw in fresh:
                job = normalize_job(raw)
                seen.add(job["id"])
                jobs.append(job)
            if len(raw_jobs) < PAGE_SIZE:
                break
        return jobs

    def get_job(self, job_id: str) -> dict | None:
        """Return one normalized job, or None if it no longer exists."""
        data = _http.get_json(self._client, f"{BASE_URL}/{self.board_token}/jobs/{job_id}")
        return normalize_job(data) if isinstance(data, dict) else None

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> "GreenhouseAPI":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
