"""Shared GET helper with 429 backoff for the ATS API clients."""
import logging
import time

import httpx

log = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2.0
MAX_SLEEP_SECONDS = 60.0


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After", "")
    try:
        delay = float(retry_after)
    except ValueError:
        delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
    return max(0.0, min(delay, MAX_SLEEP_SECONDS))


def get_json(client: httpx.Client, url: str, params: dict | None = None):
    """GET url and return parsed JSON, or None on 404.

    A 429 sleeps (Retry-After, else exponential backoff) and retries up to
    MAX_RETRIES times; after that, or on any other error status, it raises
    httpx.HTTPStatusError. Transport errors and timeouts propagate.
    """
    for attempt in range(MAX_RETRIES + 1):
        response = client.get(url, params=params)
        if response.status_code == 404:
            return None
        if response.status_code == 429 and attempt < MAX_RETRIES:
            delay = _retry_delay(response, attempt)
            log.warning(f"[ATS-API] rate limited | retry {attempt + 1}/{MAX_RETRIES} in {delay:.0f}s")
            time.sleep(delay)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("unreachable")
