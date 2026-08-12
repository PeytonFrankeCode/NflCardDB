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
import os
import random
import sys
import time
from pathlib import Path
from typing import Optional

from .fetch import (
    CHALLENGE_MARKERS,
    BlockedError,
    EngineUnavailable,
    FetchError,
    FetchStats,
    SignedOutError,
    looks_signed_out,
)

log = logging.getLogger(__name__)

INSTALL_HINT = (
    "The browser engine needs Playwright:\n"
    "    pip install playwright\n"
    "    playwright install chromium"
)

# Playwright leaves automation markers that bot detection reads directly --
# navigator.webdriver is set to true, window.chrome is missing, the plugin and
# language arrays come back empty. A stock headless launch is identifiable from
# JavaScript in about three lines, which is why the first browser attempt was
# challenged. This runs before any page script and restores what a normal Chrome
# reports.
STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

window.chrome = window.chrome || {};
window.chrome.runtime = window.chrome.runtime || {};

Object.defineProperty(navigator, 'plugins', {
  get: () => [
    {name: 'PDF Viewer', filename: 'internal-pdf-viewer'},
    {name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer'},
    {name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer'},
  ],
});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});

const originalQuery = window.navigator.permissions &&
                      window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) =>
    parameters && parameters.name === 'notifications'
      ? Promise.resolve({state: Notification.permission})
      : originalQuery(parameters);
}

// Headless reports 0 for these; a real window never does.
if (!window.outerWidth) {
  Object.defineProperty(window, 'outerWidth', {get: () => window.innerWidth});
  Object.defineProperty(window, 'outerHeight', {get: () => window.innerHeight + 74});
}
"""

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-first-run",
    "--no-default-browser-check",
]

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


class BrowserUnavailable(EngineUnavailable):
    """Playwright or its Chromium build is missing."""


class ProfileLocked(RuntimeError):
    """Chrome is running and holding its profile open."""


def default_chrome_profile() -> Optional[Path]:
    """Where this machine keeps its everyday Chrome profile, if it has one."""
    candidates = []
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Google" / "Chrome" / "User Data")
    elif sys.platform == "darwin":
        candidates.append(
            Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
        )
    else:
        candidates += [
            Path.home() / ".config" / "google-chrome",
            Path.home() / ".config" / "chromium",
        ]
    return next((p for p in candidates if p.exists()), None)


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
        profile_dir: Optional[str] = "data/browser-profile",
        warm_up: bool = True,
        challenge_retries: int = 4,
        block_media: bool = True,
        profile_directory: Optional[str] = None,
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
        self._context = None
        self._page = None
        self._headless = headless
        self._executable_path = executable_path
        self._profile_dir = profile_dir
        self._warm_up = warm_up
        self._warmed = False
        # How many bot checks on one request before calling it a real block.
        self.challenge_retries = challenge_retries
        self.block_media = block_media
        # None until the warm-up has seen an ordinary eBay page.
        self.signed_in: Optional[bool] = None
        self._profile_directory = profile_directory

    def _is_real_chrome_profile(self) -> bool:
        """A Chrome 'User Data' directory, as opposed to one of ours."""
        if not self._profile_dir:
            return False
        p = Path(self._profile_dir)
        return (p / "Local State").exists() or p.name == "User Data"

    # -- lifecycle ---------------------------------------------------------

    def _ensure_browser(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise BrowserUnavailable(f"Playwright is not installed.\n{INSTALL_HINT}") from exc

        self._pw = sync_playwright().start()
        launch_kwargs = {"headless": self._headless, "args": list(LAUNCH_ARGS)}
        if self._executable_path:
            launch_kwargs["executable_path"] = self._executable_path

        # Real Chrome beats bundled Chromium when it is installed: same binary
        # everyone else browses with, rather than the build automation ships.
        #
        # With a real Chrome profile it is not merely better, it is required.
        # Chrome encrypts its cookies with a key only Chrome itself can unwrap
        # (DPAPI, and app-bound encryption since Chrome 127), so bundled
        # Chromium opens the profile perfectly happily and finds no session in
        # it -- which lands on eBay's sign-in page and looks exactly like "no
        # results". Falling back silently there is worse than failing.
        real_profile = self._is_real_chrome_profile()
        if self._executable_path:
            attempts = [{}]
        elif real_profile:
            attempts = [{"channel": "chrome"}]
        else:
            attempts = [{"channel": "chrome"}, {}]

        if real_profile and self._profile_directory:
            launch_kwargs["args"] = launch_kwargs["args"] + [
                f"--profile-directory={self._profile_directory}"
            ]

        context_kwargs = {
            "viewport": {"width": 1440, "height": 900},
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "user_agent": BROWSER_UA,
            "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
        }

        # A persistent profile keeps cookies between runs. It matters more than it
        # looks: a browser arriving with no cookies at all, straight onto a deep
        # filtered search URL, is exactly the shape of a scraper. After one
        # ordinary visit this profile carries the same session state a returning
        # visitor has.
        last_exc: Optional[Exception] = None
        if self._profile_dir:
            profile = Path(self._profile_dir)
            profile.mkdir(parents=True, exist_ok=True)
            for extra in attempts:
                try:
                    context = self._pw.chromium.launch_persistent_context(
                        str(profile), **launch_kwargs, **context_kwargs, **extra
                    )
                    # launch_persistent_context returns None for .browser; the
                    # context is the thing that has to be closed.
                    self._browser = context.browser
                    context.add_init_script(STEALTH_SCRIPT)
                    if self.block_media:
                        self._block_heavy_resources(context)
                    # A real profile restores its previous tabs, so pages[0] is
                    # some old tab rather than anything we control -- and the
                    # user watches a window we are not driving. Always open a
                    # fresh page and raise it to the front.
                    self._page = context.new_page()
                    try:
                        self._page.bring_to_front()
                    except Exception:
                        pass
                    self._context = context
                    return
                except Exception as exc:  # pragma: no cover - environment dependent
                    last_exc = exc
                    # Chrome holds a lock on its own profile while running, and
                    # the failure text is opaque -- name it plainly instead.
                    text = str(exc)
                    if any(s in text for s in
                           ("ProcessSingleton", "profile appears to be in use",
                            "SingletonLock", "Failed to create a ProcessSingleton")):
                        self.close()
                        raise ProfileLocked(
                            "Google Chrome is running and is holding this profile "
                            "open.\nClose Chrome completely -- including anything "
                            "left in the system tray -- and try again."
                        ) from exc

        for extra in attempts:
            try:
                self._browser = self._pw.chromium.launch(**launch_kwargs, **extra)
                break
            except Exception as exc:  # pragma: no cover - depends on environment
                last_exc = exc
        if self._browser is None:
            self.close()
            if real_profile:
                raise BrowserUnavailable(
                    f"Could not start Google Chrome itself: {last_exc}\n\n"
                    "Your everyday Chrome profile can only be opened by Chrome -- "
                    "its cookies are encrypted so that nothing else can read "
                    "them. Install Google Chrome, or drop --chrome-profile and "
                    "sign in with login.bat instead."
                ) from last_exc
            raise BrowserUnavailable(
                f"Could not start a browser: {last_exc}\n{INSTALL_HINT}"
            ) from last_exc

        context = self._browser.new_context(**context_kwargs)
        context.add_init_script(STEALTH_SCRIPT)
        if self.block_media:
            self._block_heavy_resources(context)
        self._context = context
        self._page = context.new_page()

    def _block_heavy_resources(self, context) -> None:
        """Stop downloading things the parser never reads.

        A sold-search page at 240 results per page pulls 240 thumbnails, plus
        fonts, tracking pixels and video. All of it is fetched, decoded and
        never looked at: the parser reads markup, and photo URLs come from the
        `src` attribute, which is present whether or not the bytes arrive.
        Skipping them is most of the page load.

        Stylesheets and scripts are deliberately still fetched. They are cheap
        next to images, and a browser that runs no JavaScript is a far stranger
        thing than one that skips pictures -- which is just an ad blocker.
        """
        blocked = {"image", "media", "font"}

        def route(handler):
            try:
                if handler.request.resource_type in blocked:
                    handler.abort()
                else:
                    handler.continue_()
            except Exception:            # the page moved on; nothing to do
                pass

        try:
            context.route("**/*", route)
        except Exception as exc:         # pragma: no cover - environment dependent
            log.debug("could not install resource blocking (%s)", exc)

    def _warm_up_session(self) -> None:
        """Visit the homepage once before any search.

        Nobody arrives cold on a deep filtered search URL with no cookies and no
        referrer -- a person lands on ebay.com and searches from there. Doing the
        same picks up the session cookies a normal visit sets, and means the
        search request carries a referrer from the site itself. Best effort: if
        the homepage does not load, the real request still goes ahead.
        """
        if self._warmed or not self._warm_up:
            return
        self._warmed = True
        try:
            self._page.goto("https://www.ebay.com/", timeout=self.timeout,
                            wait_until="domcontentloaded")
            self._page.wait_for_timeout(random.randint(1200, 2600))
            # Dismiss a consent banner if one is shown; it blocks nothing, but a
            # real session would have answered it.
            for selector in ("#gdpr-banner-accept", "button[aria-label*='Accept']"):
                try:
                    button = self._page.query_selector(selector)
                    if button:
                        button.click(timeout=2000)
                        self._page.wait_for_timeout(400)
                        break
                except Exception:
                    pass
            log.debug("warm-up visit to ebay.com complete")
            self._check_signed_in()
            self._warm_up_search()
        except Exception as exc:
            log.debug("warm-up visit failed (%s); continuing anyway", exc)

    def _reestablish(self) -> None:
        """Return to ordinary browsing before retrying a challenged request.

        Reloading the same filtered URL straight into a challenge tends to earn
        another. Going back to the homepage is what a person does when a site
        interrupts them, and it is the state the successful requests were made
        from.
        """
        try:
            self._page.goto("https://www.ebay.com/", timeout=self.timeout,
                            wait_until="domcontentloaded")
            self._page.wait_for_timeout(random.randint(2000, 4000))
        except Exception as exc:
            log.debug("could not return to the homepage (%s)", exc)

    def _check_signed_in(self) -> None:
        """Settle the session question by asking a page that requires one.

        Reading "Sign in" out of the homepage markup was a guess that returned
        "don't know" in practice -- eBay's header contains that string either
        way, and a challenge page contains neither. My eBay is different: signed
        out, eBay redirects it to signin.ebay.com, and a redirect is a fact
        rather than a string match.
        """
        try:
            self._page.goto("https://www.ebay.com/mye/myebay/summary",
                            timeout=self.timeout, wait_until="domcontentloaded")
            self._page.wait_for_timeout(random.randint(600, 1400))
            landed = (self._page.url or "").lower()
        except Exception as exc:
            log.debug("sign-in check failed (%s); continuing", exc)
            return

        if "signin.ebay.com" in landed or "/signin" in landed:
            self.signed_in = False
            log.warning("NOT SIGNED IN to eBay -- this is why sold listings are "
                        "refused. Run login.bat and sign in, then try again.")
        elif "myebay" in landed or "/mys/" in landed:
            self.signed_in = True
            log.info("signed in to eBay")
        else:
            # A challenge, or somewhere unexpected. Say so rather than guess.
            log.warning("could not confirm sign-in (landed on %s)", landed[:80])

    def _warm_up_search(self) -> None:
        """Run an ordinary search before the filtered one the collector wants.

        Measured, not assumed: `nflcarddb doctor` escalates homepage -> plain
        search -> sold search in one session and gets results, while a cold
        request straight to a deep sold-search URL is challenged. The collector
        was jumping from the homepage to a filtered, price-banded, 240-per-page
        sold search in one step -- the shape the challenge fires on. This adds
        the middle step that demonstrably works.
        """
        try:
            self._page.goto("https://www.ebay.com/sch/i.html?_nkw=football+cards",
                            timeout=self.timeout, wait_until="domcontentloaded")
            self._page.wait_for_timeout(random.randint(1400, 2800))
            log.debug("warm-up search complete")
        except Exception as exc:
            log.debug("warm-up search failed (%s); continuing anyway", exc)

    def close(self) -> None:
        for attr in ("_context", "_browser", "_pw"):
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
        """Hold one request per `delay` seconds, counting from request *start*.

        Measuring from the end of the previous navigation made the real gap
        `page load + delay` -- around 7s a page when the setting said 2.5. eBay
        sees the same rate either way, because the rate is what the interval
        between requests describes; the extra seconds bought nothing and made a
        full day take three times as long as configured.
        """
        elapsed = time.monotonic() - self._last_request
        wait = self.delay + random.uniform(0, self.jitter) - elapsed
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, label: Optional[str] = None) -> str:
        if self.budget_exhausted():
            raise FetchError(f"page budget of {self.page_budget} exhausted")

        self._ensure_browser()
        self._warm_up_session()
        last_err: Optional[Exception] = None
        challenges = 0

        for attempt in range(self.max_retries + self.challenge_retries + 1):
            self._sleep_until_allowed()
            # Stamped before the navigation, so the interval is request-to-
            # request rather than request-to-finish.
            self._last_request = time.monotonic()
            try:
                response = self._page.goto(
                    url, timeout=self.timeout, wait_until="domcontentloaded"
                )
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

                if looks_signed_out(html):
                    raise SignedOutError(
                        "eBay redirected to its sign-in page, so this session is not signed in.\n"
                        "Sold listings are only shown to signed-in accounts.\n"
                        "Run login.bat, or use --chrome-profile with Chrome fully closed."
                    )

                low = html[:6000].lower()
                if any(marker in low for marker in CHALLENGE_MARKERS):
                    # A challenge is usually a speed bump, not a verdict:
                    # `nflcarddb bisect` gets one on a cold first navigation and
                    # then loads six harder URLs in the same session, ending with
                    # the collector's exact query returning 240 listings. Treating
                    # the first one as fatal threw away runs that would have
                    # finished, so back off, re-establish, and try again.
                    self.stats.blocked += 1
                    challenges += 1
                    if challenges > self.challenge_retries:
                        raise BlockedError(
                            f"eBay served a bot-check page {challenges} times in a "
                            f"row for one request, so this is not a passing one. "
                            f"Wait a while, then try again with a longer --delay."
                        )
                    backoff = min(60.0, 5.0 * (2 ** (challenges - 1))) + random.uniform(0, 3)
                    log.warning("bot check (%d/%d); waiting %.0fs and retrying",
                                challenges, self.challenge_retries, backoff)
                    time.sleep(backoff)
                    self._reestablish()
                    continue
                if status >= 400:
                    last_err = FetchError(f"HTTP {status}")
                    self.stats.retries += 1
                    time.sleep(min(30.0, (2 ** attempt) * 3))
                    continue

                return html

            except (BlockedError, SignedOutError):
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
