"""Lever public postings API client (no API key needed).

Endpoints: GET /v0/postings/{company} and /v0/postings/{company}/{id}.
"""
import logging
from datetime import datetime, timezone

import httpx

from ats_api import _http

log = logging.getLogger(__name__)

BASE_URL = "https://api.lever.co/v0/postings"
PAGE_SIZE = 100
MAX_PAGES = 50


def _iso_from_ms(value) -> str:
    if not isinstance(value, (int, float)):
        return ""
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def normalize_posting(raw: dict) -> dict:
    """Map a raw Lever posting to the pipeline's normalized shape."""
    categories = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
    return {
        "id": str(raw.get("id", "")),
        "title": raw.get("text", ""),
        "location": categories.get("location", "") or "",
        "categories": dict(categories),
        "workplace_type": raw.get("workplaceType", "") or "",
        "hosted_url": raw.get("hostedUrl", ""),
        "created_at": _iso_from_ms(raw.get("createdAt")),
        "description": raw.get("descriptionPlain") or raw.get("description") or "",
    }


class LeverAPI:
    """Client for one company's Lever postings."""

    def __init__(self, company_token: str, timeout: float = 30.0, client: httpx.Client | None = None) -> None:
        """Create a client; pass client only to inject a custom transport (tests)."""
        self.company_token = company_token
        self._client = client or httpx.Client(timeout=timeout)

    def list_postings(self) -> list[dict]:
        """Return all published postings, normalized.

        Pages with skip/limit until a page is short, empty, or has no unseen ids.
        """
        url = f"{BASE_URL}/{self.company_token}"
        postings: list[dict] = []
        seen: set[str] = set()
        for page in range(MAX_PAGES):
            data = _http.get_json(
                self._client, url, {"mode": "json", "skip": page * PAGE_SIZE, "limit": PAGE_SIZE}
            )
            raw_postings = data if isinstance(data, list) else []
            fresh = [p for p in raw_postings if str(p.get("id", "")) not in seen]
            if not fresh:
                break
            for raw in fresh:
                posting = normalize_posting(raw)
                seen.add(posting["id"])
                postings.append(posting)
            if len(raw_postings) < PAGE_SIZE:
                break
        return postings

    def get_posting(self, posting_id: str) -> dict | None:
        """Return one normalized posting, or None if it no longer exists."""
        data = _http.get_json(self._client, f"{BASE_URL}/{self.company_token}/{posting_id}")
        return normalize_posting(data) if isinstance(data, dict) else None

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> "LeverAPI":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
