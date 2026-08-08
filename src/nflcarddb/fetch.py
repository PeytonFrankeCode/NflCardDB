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
)

# eBay answers a sold-listings search from a logged-out session by redirecting to
# the sign-in form. That comes back HTTP 200 with a perfectly valid page on which
# there is simply nothing to parse -- which is indistinguishable from "no cards
# sold" unless it is recognised. It cost a whole run reported as "0 sales".
SIGNED_OUT_MARKERS = (
    "sign in or register",
    'action="https://signin.ebay.com/signin/s',
    'name="userid"',
)


def looks_signed_out(html: str) -> bool:
    """True when this is eBay's sign-in page rather than search results."""
    head = html[:20000].lower()
    if "srp-results" in head or "/itm/" in head:
        return False
    return sum(m in head for m in SIGNED_OUT_MARKERS) >= 2


def session_state(html: str) -> Optional[bool]:
    """Whether an ordinary eBay page shows a signed-in header.

    Different question from `looks_signed_out`, which recognises the sign-in
    page itself. This reads the header of a normal page -- signed in, eBay
    greets you and offers "My eBay"; signed out it offers "Sign in".

    It matters because a signed-out session does not always get redirected to
    sign-in. Ask for a deep, filtered sold search without one and eBay serves a
    bot check instead, which reports as "blocked" and sends you looking for a
    rate-limit problem you do not have. Returns None when the page is not an
    ordinary eBay page at all -- a challenge, an error -- since then it says
    nothing either way.
    """
    head = html[:200000].lower()
    if any(m in head for m in CHALLENGE_MARKERS):
        return None
    if "ebay" not in head:
        return None
    if "my ebay" in head and "sign in" not in head:
        return True
    if "sign in" in head:
        return False
    return None


class BlockedError(RuntimeError):
    """eBay served a challenge/interstitial instead of results."""


class SignedOutError(RuntimeError):
    """eBay redirected to sign-in: the session is not authenticated."""


class EngineUnavailable(RuntimeError):
    """An engine's dependency is missing, so the ladder should move past it."""


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

                if looks_signed_out(html):
                    raise SignedOutError(
                        "eBay redirected to its sign-in page, so this session is not signed in.\n"
                        "Sold listings are only shown to signed-in accounts.\n"
                        "Run login.bat, or use --chrome-profile with Chrome fully closed."
                    )

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

            except (BlockedError, SignedOutError):
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
    "headless", "executable_path", "profile_dir", "warm_up",
    "profile_directory",
})


# What "auto" walks, cheapest first. Plain requests is deliberately absent: eBay
# refuses it at the TLS layer before HTTP is even spoken, so spending a request
# to rediscover that only costs time.
DEFAULT_LADDER = ("impersonate", "browser")

ENGINE_ALIASES = {
    "http": "requests", "plain": "requests",
    "chromium": "browser", "playwright": "browser",
    "curl": "impersonate", "curl_cffi": "impersonate", "tls": "impersonate",
}


def build_engine(name: str, **kwargs):
    """Construct one engine by name, passing only the options it understands."""
    name = ENGINE_ALIASES.get(name, name)
    if name == "requests":
        return Fetcher(**{k: v for k, v in kwargs.items() if k in HTTP_KWARGS})
    if name == "impersonate":
        from .impersonate import IMPERSONATE_KWARGS, ImpersonateFetcher

        return ImpersonateFetcher(
            **{k: v for k, v in kwargs.items() if k in IMPERSONATE_KWARGS}
        )
    if name == "browser":
        from .browser import BrowserFetcher

        return BrowserFetcher(**{k: v for k, v in kwargs.items() if k in BROWSER_KWARGS})
    raise ValueError(
        f"unknown engine {name!r}; use auto, impersonate, browser, or requests"
    )


class AutoFetcher:
    """Walk a ladder of engines, moving up whenever eBay refuses the current one.

    eBay's answer varies by machine, connection and day, so pinning one engine
    leaves somebody on the wrong one. Each rung is tried until one gets through;
    the choice then sticks. The spent page budget carries across every switch, so
    changing engine can never buy extra requests.
    """

    def __init__(self, ladder: Optional[tuple] = None, **kwargs) -> None:
        self._kwargs = kwargs
        self._ladder = [ENGINE_ALIASES.get(n, n) for n in (ladder or DEFAULT_LADDER)]
        self._pos = 0
        self.engine = self._ladder[0]
        self._impl = build_engine(self.engine, **kwargs)
        self.switched = False
        # The first refusal is the one that explains *why* nothing worked. A
        # later rung may fail merely because its dependency is missing, and that
        # must not become the headline.
        self._first_block: Optional[Exception] = None

    @property
    def stats(self) -> FetchStats:
        return self._impl.stats

    @property
    def signed_in(self) -> Optional[bool]:
        """Whatever the engine currently in use managed to observe."""
        return getattr(self._impl, "signed_in", None)

    def budget_exhausted(self) -> bool:
        return self._impl.budget_exhausted()

    def get(self, url: str, label: Optional[str] = None) -> str:
        while True:
            try:
                return self._impl.get(url, label)
            except SignedOutError:
                # Being logged out is only worth escalating when the *current*
                # engine cannot carry a session at all. The HTTP transports have
                # no cookie jar, so they are signed out by construction -- the
                # browser holds the stored session and may well be signed in.
                # Once the browser itself reports signed out, nothing left to try.
                remaining = self._ladder[self._pos + 1:]
                if self.engine != "browser" and "browser" in remaining:
                    log.warning("%s has no session; trying the browser, which does",
                                self.engine)
                    self._advance_to("browser")
                    continue
                raise
            except (BlockedError, EngineUnavailable) as exc:
                if self._first_block is None and isinstance(exc, BlockedError):
                    self._first_block = exc
                if self._pos + 1 >= len(self._ladder):
                    raise self._exhausted(exc) from exc
                self._advance(exc)

    def _advance_to(self, name: str) -> None:
        """Jump straight to a named rung, keeping the spent budget."""
        spent = self._impl.stats
        self.close()
        self._pos = self._ladder.index(name)
        self.engine = name
        self._impl = build_engine(name, **self._kwargs)
        self._impl.stats.requests = spent.requests
        self._impl.stats.blocked = spent.blocked
        self.switched = True

    def _advance(self, exc: Exception) -> None:
        # Engines construct lazily, so a missing dependency surfaces from get()
        # rather than the constructor -- both paths land here.
        spent = self._impl.stats
        self.close()
        previous = self._ladder[self._pos]
        self._pos += 1
        self.engine = self._ladder[self._pos]
        log.warning("%s did not get through (%s); trying %s",
                    previous, type(exc).__name__, self.engine)
        self._impl = build_engine(self.engine, **self._kwargs)
        # Carry the budget so switching engines cannot reset the allowance.
        self._impl.stats.requests = spent.requests
        self._impl.stats.blocked = spent.blocked
        self.switched = True

    def _exhausted(self, last: Exception) -> BlockedError:
        parts = [f"Every method was refused (tried: {', '.join(self._ladder)})."]
        if self._first_block is not None and self._first_block is not last:
            parts.append(f"\nWhat eBay said first: {self._first_block}")
        parts.append(f"\nLast error: {last}")
        if isinstance(last, EngineUnavailable):
            # A missing dependency is fixable, unlike a refusal -- say so.
            parts.append(
                "\nThat last method was never actually tried, because it is not "
                "installed. Installing it may well be the fix."
            )
        return BlockedError("".join(parts))

    def close(self) -> None:
        closer = getattr(self._impl, "close", None)
        if closer:
            closer()


def make_fetcher(engine: str = "auto", **kwargs):
    """Build a fetcher: auto | impersonate | browser | requests."""
    name = ENGINE_ALIASES.get((engine or "auto").lower(), (engine or "auto").lower())
    if name == "auto":
        return AutoFetcher(**kwargs)
    return build_engine(name, **kwargs)
