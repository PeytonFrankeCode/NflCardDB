"""HTTP client that impersonates a real Chrome at the TLS layer.

This is the piece the first two attempts were missing.

When Python's `requests` opens an HTTPS connection, the TLS ClientHello it sends
-- cipher order, extensions, elliptic curves, ALPN -- is distinctive. Fingerprint
it (JA3/JA4) and you can identify the client before a single byte of HTTP is
exchanged. That is why eBay answered 403 on the very first request while the
headers looked perfectly browser-like: the refusal happened below HTTP.

curl_cffi links against a curl built to reproduce a real browser's ClientHello,
and speaks HTTP/2 with the same frame settings and header order. So the
handshake matches Chrome because it *is* Chrome's handshake.

Everything else stays as conservative as the plain client: one connection, the
same delay floor, the same backoff, the same hard stop on a challenge page.
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Optional

from .fetch import (
    CHALLENGE_MARKERS,
    BlockedError,
    EngineUnavailable,
    FetchError,
    FetchStats,
)

log = logging.getLogger(__name__)

IMPERSONATE_KWARGS = frozenset({
    "delay", "jitter", "max_retries", "timeout", "page_budget", "save_dir",
    "impersonate",
})

INSTALL_HINT = "The impersonating client needs curl_cffi:\n    pip install curl_cffi"

# Newer targets track current Chrome; an old one is itself a tell.
DEFAULT_IMPERSONATE = "chrome136"
FALLBACK_IMPERSONATE = ("chrome133a", "chrome131", "chrome124", "chrome120")


class ImpersonateUnavailable(EngineUnavailable):
    """curl_cffi is not installed."""


class ImpersonateFetcher:
    """Same surface as Fetcher, with a browser TLS fingerprint."""

    def __init__(
        self,
        delay: float = 2.5,
        jitter: float = 1.0,
        max_retries: int = 4,
        timeout: float = 30.0,
        page_budget: Optional[int] = None,
        save_dir: Optional[str] = None,
        impersonate: str = DEFAULT_IMPERSONATE,
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
        self.impersonate = impersonate
        self._session = None

    def _ensure_session(self):
        if self._session is not None:
            return self._session
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImpersonateUnavailable(
                f"curl_cffi is not installed.\n{INSTALL_HINT}"
            ) from exc

        targets = (self.impersonate, *FALLBACK_IMPERSONATE)
        last: Optional[Exception] = None
        for target in targets:
            try:
                self._session = cffi_requests.Session(impersonate=target)
                self.impersonate = target
                log.debug("impersonating %s", target)
                return self._session
            except Exception as exc:  # older builds lack the newest targets
                last = exc
        raise ImpersonateUnavailable(
            f"No usable impersonation target ({last}).\n{INSTALL_HINT}"
        )

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

        session = self._ensure_session()
        last_err: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            self._sleep_until_allowed()
            try:
                resp = session.get(url, timeout=self.timeout)
                self._last_request = time.monotonic()
                self.stats.requests += 1

                if resp.status_code in (429, 503):
                    self.stats.retries += 1
                    backoff = min(60.0, (2 ** attempt) * 5) + random.uniform(0, 3)
                    log.warning("throttled (HTTP %s); backing off %.1fs",
                                resp.status_code, backoff)
                    time.sleep(backoff)
                    last_err = FetchError(f"HTTP {resp.status_code}")
                    continue

                if resp.status_code == 403:
                    self.stats.blocked += 1
                    raise BlockedError(
                        "eBay refused the request (HTTP 403) even with a browser "
                        "TLS fingerprint. Try the browser engine: --engine browser"
                    )

                html = resp.text
                self.stats.bytes += len(html)

                if self.save_dir and label:
                    (self.save_dir / f"{label}.html").write_text(html, encoding="utf-8")

                low = html[:6000].lower()
                if any(marker in low for marker in CHALLENGE_MARKERS):
                    self.stats.blocked += 1
                    raise BlockedError(
                        "eBay served a bot-check page. Wait a while, then retry "
                        "with a longer --delay."
                    )

                if resp.status_code >= 400:
                    last_err = FetchError(f"HTTP {resp.status_code}")
                    self.stats.retries += 1
                    time.sleep(min(30.0, (2 ** attempt) * 3))
                    continue

                return html

            except (BlockedError, ImpersonateUnavailable):
                raise
            except Exception as exc:
                self.stats.retries += 1
                last_err = exc
                backoff = min(30.0, (2 ** attempt) * 2) + random.uniform(0, 2)
                log.warning("request failed (%s); retrying in %.1fs", exc, backoff)
                time.sleep(backoff)

        raise FetchError(f"giving up on {url}: {last_err}")

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:  # pragma: no cover - best effort
                pass
            self._session = None
