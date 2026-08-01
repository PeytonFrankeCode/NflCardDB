"""Test every fetch method against eBay and report exactly what each one gets.

Three attempts have now failed for three different reasons, each only visible
after the fact. Guessing at the next layer is wasteful; this measures instead.

Every method gets exactly one request. Nothing is retried, nothing is inferred:
the HTTP status, the response size, whether the page is a challenge, and how
many listings the parser can actually read are all reported separately, so a
"200 OK that is really a bot check" cannot be mistaken for success.
"""

from __future__ import annotations

import importlib
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .fetch import CHALLENGE_MARKERS
from .parse_listing import parse_search_page

# Outcomes, worst to best.
REFUSED = "REFUSED"          # rejected outright, usually 403
CHALLENGED = "BOT CHECK"     # a page came back, but it is an interstitial
UNREADABLE = "UNREADABLE"    # real page, but no listings could be parsed
WORKING = "OK"
UNAVAILABLE = "NOT INSTALLED"
ERROR = "ERROR"


@dataclass
class EngineCheck:
    engine: str
    outcome: str
    status: Optional[int] = None
    size: int = 0
    listings: int = 0
    detail: str = ""
    saved_to: Optional[str] = None


@dataclass
class StageCheck:
    """One URL in the escalation from 'any eBay page' to 'the sold search'."""

    name: str
    url: str
    status: Optional[int] = None
    outcome: str = ""
    detail: str = ""


@dataclass
class Diagnosis:
    python: str = ""
    platform_name: str = ""
    packages: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)
    stages: list = field(default_factory=list)
    url: str = ""
    signed_in: Optional[bool] = None

    @property
    def any_working(self) -> bool:
        return any(c.outcome == WORKING for c in self.checks)


def _package_report() -> dict:
    report = {}
    for name in ("requests", "curl_cffi", "playwright", "bs4", "lxml"):
        try:
            mod = importlib.import_module(name)
            report[name] = getattr(mod, "__version__", "installed")
        except Exception:
            report[name] = None
    return report


def _classify(html: str, status: Optional[int]) -> tuple[str, int, str]:
    """Turn a response into (outcome, listings parsed, detail)."""
    if status is not None and status == 403:
        return (REFUSED, 0, "HTTP 403 - refused before any page was sent")

    low = html[:8000].lower()
    hit = next((m for m in CHALLENGE_MARKERS if m in low), None)
    if hit:
        return (CHALLENGED, 0, f"challenge page (matched {hit!r})")

    result = parse_search_page(html)
    n = len(result.sales)
    if n:
        return (WORKING, n, f"parsed {n} listing(s)")

    if status is not None and status >= 400:
        return (REFUSED, 0, f"HTTP {status}")
    return (UNREADABLE, 0, "page returned, but no listings recognised on it")


def _save(html: str, save_dir: Optional[Path], engine: str) -> Optional[str]:
    if not save_dir or not html:
        return None
    save_dir.mkdir(parents=True, exist_ok=True)
    path = save_dir / f"doctor_{engine}.html"
    path.write_text(html, encoding="utf-8")
    return str(path)


def check_requests(url: str, save_dir: Optional[Path], timeout: float) -> EngineCheck:
    from .fetch import Fetcher

    try:
        f = Fetcher(delay=0, jitter=0, max_retries=0, timeout=timeout)
        resp = f.session.get(url, timeout=timeout)
        html = resp.text
        outcome, n, detail = _classify(html, resp.status_code)
        return EngineCheck("requests", outcome, resp.status_code, len(html), n, detail,
                           _save(html, save_dir, "requests"))
    except Exception as exc:
        return EngineCheck("requests", ERROR, detail=f"{type(exc).__name__}: {exc}")


def check_impersonate(url: str, save_dir: Optional[Path], timeout: float) -> EngineCheck:
    try:
        from curl_cffi import requests as cffi
    except ImportError:
        return EngineCheck("impersonate", UNAVAILABLE,
                           detail="curl_cffi not installed (pip install curl_cffi)")

    from .impersonate import DEFAULT_IMPERSONATE, FALLBACK_IMPERSONATE

    last = None
    for target in (DEFAULT_IMPERSONATE, *FALLBACK_IMPERSONATE):
        try:
            with cffi.Session(impersonate=target) as s:
                resp = s.get(url, timeout=timeout)
                html = resp.text
                outcome, n, detail = _classify(html, resp.status_code)
                return EngineCheck("impersonate", outcome, resp.status_code, len(html), n,
                                   f"{detail} [as {target}]",
                                   _save(html, save_dir, "impersonate"))
        except Exception as exc:
            last = exc
    return EngineCheck("impersonate", ERROR, detail=f"{type(last).__name__}: {last}")


def check_browser(url: str, save_dir: Optional[Path], timeout: float,
                  headless: bool = True,
                  profile_dir: Optional[str] = None) -> EngineCheck:
    try:
        importlib.import_module("playwright.sync_api")
    except ImportError:
        return EngineCheck("browser", UNAVAILABLE,
                           detail="playwright not installed (pip install playwright)")

    from .browser import BrowserFetcher, BrowserUnavailable

    f = BrowserFetcher(delay=0, jitter=0, max_retries=0, timeout=timeout,
                       headless=headless, profile_dir=profile_dir)
    try:
        f._ensure_browser()
    except BrowserUnavailable as exc:
        return EngineCheck("browser", UNAVAILABLE, detail=str(exc).splitlines()[0])
    except Exception as exc:
        return EngineCheck("browser", ERROR, detail=f"{type(exc).__name__}: {exc}")

    try:
        response = f._page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
        status = response.status if response else None
        html = f._page.content()
        outcome, n, detail = _classify(html, status)
        return EngineCheck("browser", outcome, status, len(html), n, detail,
                           _save(html, save_dir, "browser"))
    except Exception as exc:
        return EngineCheck("browser", ERROR, detail=f"{type(exc).__name__}: {exc}")
    finally:
        f.close()


def check_stages(save_dir: Optional[Path], timeout: float,
                 headless: bool = True,
                 profile_dir: Optional[str] = None) -> tuple[list, Optional[bool]]:
    """Escalate from the homepage to the sold search, using one browser session.

    Which *request* is refused matters more than which method sends it. If the
    homepage loads and only the sold-listings search is refused, the problem is
    access to sold data -- eBay increasingly gates that behind a signed-in
    account -- and no amount of fingerprint work will change it. If everything
    is refused, the block is at the connection level instead.
    """
    stages = [
        ("homepage", "https://www.ebay.com/"),
        ("plain search", "https://www.ebay.com/sch/i.html?_nkw=football+cards"),
        ("sold search",
         "https://www.ebay.com/sch/i.html?_nkw=football+cards&LH_Sold=1&LH_Complete=1"),
    ]

    try:
        importlib.import_module("playwright.sync_api")
    except ImportError:
        return ([], None)

    from .browser import BrowserFetcher, BrowserUnavailable

    f = BrowserFetcher(delay=1.0, jitter=0.5, max_retries=0, timeout=timeout,
                       headless=headless, warm_up=False, profile_dir=profile_dir)
    results: list[StageCheck] = []
    signed_in: Optional[bool] = None
    try:
        f._ensure_browser()
    except (BrowserUnavailable, Exception):
        return ([], None)

    try:
        for name, url in stages:
            try:
                response = f._page.goto(url, timeout=timeout * 1000,
                                        wait_until="domcontentloaded")
                status = response.status if response else None
                html = f._page.content()
                outcome, n, detail = _classify(html, status)
                results.append(StageCheck(name, url, status, outcome, detail))
                _save(html, save_dir, f"stage_{name.replace(' ', '_')}")
                if name == "homepage" and outcome != REFUSED:
                    # "Sign in" in the header means this session is anonymous.
                    low = html[:200000].lower()
                    signed_in = ("sign in" not in low) and ("my ebay" in low)
                f._page.wait_for_timeout(800)
            except Exception as exc:
                results.append(StageCheck(name, url, None, ERROR,
                                          f"{type(exc).__name__}: {exc}"))
    finally:
        f.close()
    return (results, signed_in)


def run_diagnosis(
    url: str,
    save_dir: Optional[str] = "data/html",
    timeout: float = 30.0,
    headed: bool = False,
    profile_dir: Optional[str] = None,
) -> Diagnosis:
    save = Path(save_dir) if save_dir else None
    diag = Diagnosis(
        python=f"{sys.version.split()[0]} ({sys.executable})",
        platform_name=platform.platform(),
        packages=_package_report(),
        url=url,
    )
    diag.checks = [
        check_requests(url, save, timeout),
        check_impersonate(url, save, timeout),
        check_browser(url, save, timeout, headless=not headed, profile_dir=profile_dir),
    ]
    # Only worth escalating URLs when every method was refused -- if one worked,
    # the answer is already known.
    if not diag.any_working:
        diag.stages, diag.signed_in = check_stages(
            save, timeout, headless=not headed, profile_dir=profile_dir)
    return diag


def format_report(diag: Diagnosis) -> str:
    lines = [
        "NflCardDB doctor",
        "=" * 58,
        f"Python    {diag.python}",
        f"System    {diag.platform_name}",
        "",
        "Packages",
    ]
    for name, version in diag.packages.items():
        lines.append(f"  {name:<12} {version or 'NOT INSTALLED'}")

    lines += ["", f"Testing each method (one request each)", f"URL: {diag.url}", "-" * 58]
    for c in diag.checks:
        status = f"HTTP {c.status}" if c.status else "-"
        lines.append(f"  {c.engine:<13} {status:<10} {c.outcome:<14} {c.detail}")
        if c.saved_to:
            lines.append(f"  {'':<13} saved: {c.saved_to}")

    if diag.stages:
        lines += ["", "Which request gets refused (one browser session)", "-" * 58]
        for s in diag.stages:
            status = f"HTTP {s.status}" if s.status else "-"
            lines.append(f"  {s.name:<13} {status:<10} {s.outcome:<14} {s.detail}")
        if diag.signed_in is not None:
            lines.append(f"  signed in to eBay: {'yes' if diag.signed_in else 'no'}")

        by_name = {s.name: s for s in diag.stages}
        home, sold = by_name.get("homepage"), by_name.get("sold search")
        if home and sold and home.outcome != REFUSED and sold.outcome == REFUSED:
            lines += [
                "",
                "  >> eBay serves ordinary pages but refuses the SOLD search.",
                "     That is an access rule, not bot detection -- eBay gates sold",
                "     listings behind a signed-in account. Run  login.bat  to sign",
                "     in once; the collector then reuses that session.",
            ]
        elif home and home.outcome == REFUSED:
            lines += [
                "",
                "  >> Even the eBay homepage is refused, which means the block is",
                "     not about this project at all. Check VPN, DNS filtering, or",
                "     network-level security software on this PC.",
            ]

    lines += ["", "-" * 58]
    if diag.any_working:
        best = next(c for c in diag.checks if c.outcome == WORKING)
        lines += [
            f"  GOOD: '{best.engine}' got through and read {best.listings} listings.",
            f"  Set  engine: {best.engine}  in config/queries.yml, then collect.",
        ]
    else:
        lines.append("  Nothing got through. Send the saved HTML files to Claude --")
        lines.append("  they show exactly what eBay returned to each method.")
        # Distinguish "tried and refused" from "never ran", which need opposite fixes.
        if all(c.outcome == UNAVAILABLE for c in diag.checks):
            lines.append("  NOTE: nothing was installed, so no method actually ran.")
            lines.append("        Re-run setup.bat -- the install step likely failed.")
        elif all(c.outcome in (UNAVAILABLE, ERROR) for c in diag.checks):
            lines.append("  NOTE: no method reached eBay at all -- these are connection")
            lines.append("        errors, not refusals. Check internet access, and any")
            lines.append("        VPN, proxy or firewall between this PC and ebay.com.")
    return "\n".join(lines)
