"""Fetch eBay pages with a real browser engine.

Why this exists: eBay refused the plain HTTP client with 403 on the very first
request from an ordinary home connection. That is not rate limiting -- there was
no second request yet. A Python HTTP library is simply distinguishable from a
browser: different TLS handshake, HTTP/1.1 instead of HTTP/2, different header
order. No amount of waiting changes that.

Driving actual Chromium removes the difference, because the requests genuinely
are browser requests. Everything else stays as conservative as the HTTP client:
one page at a time, the same delay floor between navigations, the same hard stop
when a challenge appears.

Playwright is an optional dependency. Import failures are reported with the
command to fix them rather than a traceback.
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Optional

from .fetch import CHALLENGE_MARKERS, BlockedError, FetchError, FetchStats

log = logging.getLogger(__name__)

INSTALL_HINT = (
    "The browser engine needs Playwright:\n"
    "    pip install playwright\n"
    "    playwright install chromium"
)


class BrowserUnavailable(RuntimeError):
    """Playwright or its Chromium build is missing."""


class BrowserFetcher:
    """Same surface as Fetcher, backed by Chromium."""

    def __init__(
        self,
        delay: float = 2.5,
        jitter: float = 1.0,
        max_retries: int = 2,
        timeout: float = 45.0,
        page_budget: Optional[int] = None,
        save_dir: Optional[str] = None,
        headless: bool = True,
        executable_path: Optional[str] = None,
    ) -> None:
        self.delay = delay
        self.jitter = jitter
        self.max_retries = max_retries
        self.timeout = timeout * 1000  # Playwright works in milliseconds
        self.page_budget = page_budget
        self.save_dir = Path(save_dir) if save_dir else None
        if self.save_dir:
            self.save_dir.mkdir(parents=True, exist_ok=True)

        self.stats = FetchStats()
        self._last_request = 0.0
        self._pw = None
        self._browser = None
        self._page = None
        self._headless = headless
        self._executable_path = executable_path

    # -- lifecycle ---------------------------------------------------------

    def _ensure_browser(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise BrowserUnavailable(f"Playwright is not installed.\n{INSTALL_HINT}") from exc

        self._pw = sync_playwright().start()
        launch_kwargs = {"headless": self._headless}
        if self._executable_path:
            launch_kwargs["executable_path"] = self._executable_path
        try:
            self._browser = self._pw.chromium.launch(**launch_kwargs)
        except Exception as exc:  # pragma: no cover - depends on environment
            self.close()
            raise BrowserUnavailable(
                f"Could not start Chromium: {exc}\n{INSTALL_HINT}"
            ) from exc

        context = self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
        )
        self._page = context.new_page()

    def close(self) -> None:
        for attr in ("_browser", "_pw"):
            obj = getattr(self, attr, None)
            if obj is None:
                continue
            try:
                obj.stop() if attr == "_pw" else obj.close()
            except Exception:  # pragma: no cover - best effort teardown
                pass
            setattr(self, attr, None)
        self._page = None

    def __enter__(self) -> "BrowserFetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- fetching ----------------------------------------------------------

    def budget_exhausted(self) -> bool:
        return self.page_budget is not None and self.stats.requests >= self.page_budget

    def _sleep_until_allowed(self) -> None:
        elapsed = time.monotonic() - self._last_request
        wait = self.delay + random.uniform(0, self.jitter) - elapsed
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, label: Optional[str] = None) -> str:
        if self.budget_exhausted():
            raise FetchError(f"page budget of {self.page_budget} exhausted")

        self._ensure_browser()
        last_err: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            self._sleep_until_allowed()
            try:
                response = self._page.goto(
                    url, timeout=self.timeout, wait_until="domcontentloaded"
                )
                self._last_request = time.monotonic()
                self.stats.requests += 1

                status = response.status if response else 0
                if status in (429, 503):
                    self.stats.retries += 1
                    backoff = min(60.0, (2 ** attempt) * 5) + random.uniform(0, 3)
                    log.warning("throttled (HTTP %s); backing off %.1fs", status, backoff)
                    time.sleep(backoff)
                    last_err = FetchError(f"HTTP {status}")
                    continue

                html = self._page.content()
                self.stats.bytes += len(html)

                if self.save_dir and label:
                    (self.save_dir / f"{label}.html").write_text(html, encoding="utf-8")

                low = html[:6000].lower()
                if any(marker in low for marker in CHALLENGE_MARKERS):
                    self.stats.blocked += 1
                    raise BlockedError(
                        "eBay served a bot-check page even through a real browser. "
                        "Wait a while, then try again with a longer --delay."
                    )
                if status >= 400:
                    last_err = FetchError(f"HTTP {status}")
                    self.stats.retries += 1
                    time.sleep(min(30.0, (2 ** attempt) * 3))
                    continue

                return html

            except BlockedError:
                raise
            except Exception as exc:
                if isinstance(exc, (FetchError, BrowserUnavailable)):
                    raise
                self.stats.retries += 1
                last_err = exc
                backoff = min(30.0, (2 ** attempt) * 2) + random.uniform(0, 2)
                log.warning("navigation failed (%s); retrying in %.1fs", exc, backoff)
                time.sleep(backoff)

        raise FetchError(f"giving up on {url}: {last_err}")
