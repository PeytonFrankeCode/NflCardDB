"""Polite HTTP client for eBay search pages.

Deliberately conservative: one session, one connection, a floor on the delay
between requests, exponential backoff on throttling responses, and a hard stop
when eBay serves a bot-check page. If you get blocked, slow down -- do not add
concurrency.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Phrases eBay serves on its interstitial bot check.
CHALLENGE_MARKERS = (
    "pardon our interruption",
    "checking your browser",
    "please verify yourself",
    "unusual traffic",
    "captcha",
)


class BlockedError(RuntimeError):
    """eBay served a challenge/interstitial instead of results."""


class FetchError(RuntimeError):
    """Request failed after exhausting retries."""


@dataclass
class FetchStats:
    requests: int = 0
    retries: int = 0
    blocked: int = 0
    bytes: int = 0


class Fetcher:
    def __init__(
        self,
        delay: float = 2.5,
        jitter: float = 1.0,
        max_retries: int = 4,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_UA,
        page_budget: Optional[int] = None,
        save_dir: Optional[str] = None,
    ) -> None:
        self.delay = delay
        self.jitter = jitter
        self.max_retries = max_retries
        self.timeout = timeout
        self.page_budget = page_budget
        self.save_dir = Path(save_dir) if save_dir else None
        if self.save_dir:
            self.save_dir.mkdir(parents=True, exist_ok=True)

        self.stats = FetchStats()
        self._last_request = 0.0

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        })

    def _sleep_until_allowed(self) -> None:
        elapsed = time.monotonic() - self._last_request
        wait = self.delay + random.uniform(0, self.jitter) - elapsed
        if wait > 0:
            time.sleep(wait)

    def budget_exhausted(self) -> bool:
        return self.page_budget is not None and self.stats.requests >= self.page_budget

    def get(self, url: str, label: Optional[str] = None) -> str:
        """Fetch a URL, honouring rate limits and retrying transient failures."""
        if self.budget_exhausted():
            raise FetchError(f"page budget of {self.page_budget} exhausted")

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._sleep_until_allowed()
            try:
                resp = self.session.get(url, timeout=self.timeout)
                self._last_request = time.monotonic()
                self.stats.requests += 1

                if resp.status_code in (429, 503):
                    self.stats.retries += 1
                    backoff = min(60.0, (2 ** attempt) * 5) + random.uniform(0, 3)
                    log.warning(
                        "throttled (HTTP %s) on attempt %s; backing off %.1fs",
                        resp.status_code, attempt + 1, backoff,
                    )
                    time.sleep(backoff)
                    last_err = FetchError(f"HTTP {resp.status_code}")
                    continue

                resp.raise_for_status()
                html = resp.text
                self.stats.bytes += len(html)

                low = html[:6000].lower()
                if any(marker in low for marker in CHALLENGE_MARKERS):
                    self.stats.blocked += 1
                    raise BlockedError(
                        "eBay served a bot-check page. Stop, wait a while, and "
                        "increase --delay before retrying."
                    )

                if self.save_dir and label:
                    (self.save_dir / f"{label}.html").write_text(html, encoding="utf-8")
                return html

            except BlockedError:
                raise
            except requests.RequestException as exc:
                self.stats.retries += 1
                last_err = exc
                backoff = min(30.0, (2 ** attempt) * 2) + random.uniform(0, 2)
                log.warning("request failed (%s); retrying in %.1fs", exc, backoff)
                time.sleep(backoff)

        raise FetchError(f"giving up on {url}: {last_err}")
