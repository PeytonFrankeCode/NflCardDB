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
        # A bare User-Agent is not enough: eBay compares the whole header set
        # against what a real Chrome sends, and a short list stands out.
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "sec-ch-ua": '"Chromium";v="126", "Not;A=Brand";v="24", "Google Chrome";v="126"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Connection": "keep-alive",
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

                if resp.status_code == 403:
                    # 403 on a plain HTTP client is a fingerprinting refusal, not
                    # throttling -- it happens on the very first request, before
                    # any rate could have been exceeded. Retrying or waiting does
                    # not help; only looking like a real browser does.
                    self.stats.blocked += 1
                    raise BlockedError(
                        "eBay refused the request outright (HTTP 403). This is not "
                        "rate limiting -- it means eBay can tell these requests are "
                        "not coming from a real web browser. Waiting will not help. "
                        "Use the browser engine instead: --engine browser"
                    )

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

    def close(self) -> None:
        self.session.close()


# Each engine accepts a different set of options -- headless means nothing to an
# HTTP client, user_agent means nothing to a browser that sets its own. Callers
# pass the union, and these filter it.
HTTP_KWARGS = frozenset({
    "delay", "jitter", "max_retries", "timeout", "user_agent", "page_budget", "save_dir",
})
BROWSER_KWARGS = frozenset({
    "delay", "jitter", "max_retries", "timeout", "page_budget", "save_dir",
    "headless", "executable_path",
})


class AutoFetcher:
    """Start with the light HTTP client; switch to a browser if eBay refuses.

    eBay's answer differs by machine and by day, so hard-coding one engine means
    somebody always gets the wrong one. This tries the cheap path first and
    upgrades permanently on the first refusal, carrying the page budget across so
    the switch cannot buy extra requests.
    """

    def __init__(self, **kwargs) -> None:
        self._kwargs = kwargs
        self._impl: object = Fetcher(**{k: v for k, v in kwargs.items() if k in HTTP_KWARGS})
        self.switched = False

    @property
    def stats(self) -> FetchStats:
        return self._impl.stats  # type: ignore[attr-defined]

    def budget_exhausted(self) -> bool:
        return self._impl.budget_exhausted()  # type: ignore[attr-defined]

    def get(self, url: str, label: Optional[str] = None) -> str:
        try:
            return self._impl.get(url, label)  # type: ignore[attr-defined]
        except BlockedError as blocked:
            if self.switched:
                raise
            log.warning("eBay refused the plain HTTP client; switching to a browser")
            spent = self._impl.stats  # type: ignore[attr-defined]
            from .browser import INSTALL_HINT, BrowserUnavailable

            try:
                # Chromium only launches on the first navigation, so the
                # "no browser here" failure surfaces from get(), not the
                # constructor -- both have to be inside this guard.
                self._upgrade()
                self._impl.stats.requests = spent.requests  # type: ignore[attr-defined]
                self._impl.stats.blocked = spent.blocked  # type: ignore[attr-defined]
                return self._impl.get(url, label)  # type: ignore[attr-defined]
            except BrowserUnavailable as exc:
                # Report the block that actually happened, with the fix appended
                # -- not a confusing "browser missing" error for an engine the
                # user never asked for.
                raise BlockedError(
                    f"{blocked}\n\nA real browser would likely get through, but the "
                    f"browser engine is not available here.\n{INSTALL_HINT}"
                ) from exc

    def _upgrade(self) -> None:
        from .browser import BrowserFetcher

        self.close()
        self._impl = BrowserFetcher(
            **{k: v for k, v in self._kwargs.items() if k in BROWSER_KWARGS}
        )
        self.switched = True

    def close(self) -> None:
        closer = getattr(self._impl, "close", None)
        if closer:
            closer()


def make_fetcher(engine: str = "auto", **kwargs):
    """Build the fetcher for an engine name: auto | requests | browser."""
    engine = (engine or "auto").lower()
    if engine in ("browser", "chromium", "playwright"):
        from .browser import BrowserFetcher

        return BrowserFetcher(**{k: v for k, v in kwargs.items() if k in BROWSER_KWARGS})
    if engine in ("requests", "http", "plain"):
        return Fetcher(**{k: v for k, v in kwargs.items() if k in HTTP_KWARGS})
    if engine == "auto":
        return AutoFetcher(**kwargs)
    raise ValueError(f"unknown engine {engine!r}; use auto, requests, or browser")
