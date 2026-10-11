"""Playwright browser lifecycle: reuse, health checks, recycling, and human pacing."""
import random
import threading
import time


def human_delay(min_s: float = 2.0, max_s: float = 5.0) -> None:
    """Sleep a random duration in [min_s, max_s]; standing rule is 2-5s between browser actions."""
    time.sleep(random.uniform(min_s, max_s))


class BrowserPool:
    """Hands out one healthy browser at a time and recycles it after max_uses releases."""

    def __init__(self, max_uses: int = 50, headless: bool = True) -> None:
        """Configure the pool; no browser is started until get_browser()."""
        self.max_uses = max_uses
        self.headless = headless
        self._lock = threading.RLock()
        self._playwright = None
        self._browser = None
        self._uses = 0
        self._total_uses = 0
        self._recycles = 0

    def get_browser(self):
        """Return a connected browser, launching (or relaunching) one if needed."""
        with self._lock:
            if self._browser is not None and not self._browser.is_connected():
                self._close_all()
            if self._browser is None:
                # Lazy import so modules and tests that never launch a browser don't need playwright.
                from playwright.sync_api import sync_playwright

                if self._playwright is None:
                    self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.launch(headless=self.headless)
                self._uses = 0
            return self._browser

    def release_browser(self, browser) -> None:
        """Count one use; recycle once the browser has reached max_uses."""
        with self._lock:
            self._uses += 1
            self._total_uses += 1
            if self._uses >= self.max_uses:
                self.recycle()

    def recycle(self) -> None:
        """Close the browser and playwright driver; the next get_browser() starts fresh."""
        with self._lock:
            self._close_all()
            self._recycles += 1

    def stats(self) -> dict:
        """Return {active_browsers, total_uses, recycles}."""
        with self._lock:
            return {
                "active_browsers": 1 if self._browser is not None else 0,
                "total_uses": self._total_uses,
                "recycles": self._recycles,
            }

    def _close_all(self):
        # Best-effort: a dead browser or driver must not block recycling.
        for closer in (
            getattr(self._browser, "close", None),
            getattr(self._playwright, "stop", None),
        ):
            if closer is not None:
                try:
                    closer()
                except Exception:
                    pass
        self._browser = None
        self._playwright = None
        self._uses = 0
