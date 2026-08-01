"""Engine selection and the automatic upgrade from HTTP client to browser.

Background: eBay answered the plain HTTP client with 403 on the very first
request from a home connection -- a fingerprinting refusal, not throttling. The
"auto" engine exists so that neither the user nor the config has to know which
transport eBay will accept today.
"""

import pytest

from nflcarddb import fetch as fetch_mod
from nflcarddb.fetch import AutoFetcher, BlockedError, Fetcher, make_fetcher


def test_make_fetcher_returns_the_named_engine():
    assert isinstance(make_fetcher("requests"), Fetcher)
    assert isinstance(make_fetcher("auto"), AutoFetcher)
    assert isinstance(make_fetcher(), AutoFetcher)  # auto is the default


def test_unknown_engine_is_rejected():
    with pytest.raises(ValueError, match="unknown engine"):
        make_fetcher("carrier-pigeon")


def test_browser_only_kwargs_are_filtered():
    """user_agent is meaningful to requests and not to the browser."""
    f = make_fetcher("requests", delay=0, user_agent="X")
    assert f.session.headers["User-Agent"] == "X"


def test_plain_403_is_reported_as_fingerprinting_not_throttling(monkeypatch):
    """A 403 must not be retried as if it were rate limiting."""
    class Resp:
        status_code = 403
        text = ""

        def raise_for_status(self):
            raise AssertionError("403 must be handled before raise_for_status")

    f = Fetcher(delay=0, jitter=0, max_retries=3)
    monkeypatch.setattr(f.session, "get", lambda *a, **k: Resp())

    with pytest.raises(BlockedError) as err:
        f.get("https://www.ebay.com/sch/i.html")

    assert "not rate limiting" in str(err.value)
    assert "--engine browser" in str(err.value)
    assert f.stats.requests == 1  # refused immediately, not retried four times


def test_auto_upgrades_to_browser_on_block(monkeypatch):
    calls = {"http": 0, "browser": 0}

    def http_get(self, url, label=None):
        calls["http"] += 1
        raise BlockedError("403")

    class FakeBrowser:
        def __init__(self, **kwargs):
            self.stats = fetch_mod.FetchStats()

        def get(self, url, label=None):
            calls["browser"] += 1
            return "<html>ok</html>"

        def budget_exhausted(self):
            return False

        def close(self):
            pass

    monkeypatch.setattr(Fetcher, "get", http_get)
    import nflcarddb.browser as browser_mod
    monkeypatch.setattr(browser_mod, "BrowserFetcher", FakeBrowser)

    auto = make_fetcher("auto", ladder=("requests", "browser"), delay=0, jitter=0)
    assert auto.get("https://www.ebay.com/x") == "<html>ok</html>"
    assert auto.switched is True
    assert calls == {"http": 1, "browser": 1}

    # Once switched it stays switched -- no re-probing the refused transport.
    auto.get("https://www.ebay.com/y")
    assert calls == {"http": 1, "browser": 2}


def test_auto_carries_the_page_budget_across_the_switch(monkeypatch):
    """Switching engines must not hand back a fresh request allowance."""
    class FakeBrowser:
        def __init__(self, **kwargs):
            self.stats = fetch_mod.FetchStats()
            self.page_budget = kwargs.get("page_budget")

        def get(self, url, label=None):
            return "<html/>"

        def budget_exhausted(self):
            return self.page_budget is not None and self.stats.requests >= self.page_budget

        def close(self):
            pass

    def http_get(self, url, label=None):
        self.stats.requests += 7  # pretend seven pages were already spent
        raise BlockedError("403")

    monkeypatch.setattr(Fetcher, "get", http_get)
    import nflcarddb.browser as browser_mod
    monkeypatch.setattr(browser_mod, "BrowserFetcher", FakeBrowser)

    auto = make_fetcher("auto", ladder=("requests", "browser"), delay=0, jitter=0, page_budget=10)
    auto.get("https://www.ebay.com/x")
    assert auto.stats.requests == 7


def test_missing_browser_degrades_to_the_original_block(monkeypatch):
    """Without Playwright the user must still see 'blocked', not 'import error'."""
    from nflcarddb.browser import BrowserUnavailable

    def http_get(self, url, label=None):
        raise BlockedError("eBay refused the request outright (HTTP 403).")

    class Missing:
        def __init__(self, **kwargs):
            self.stats = fetch_mod.FetchStats()

        def get(self, url, label=None):
            raise BrowserUnavailable("Playwright is not installed.")

        def close(self):
            pass

    monkeypatch.setattr(Fetcher, "get", http_get)
    import nflcarddb.browser as browser_mod
    monkeypatch.setattr(browser_mod, "BrowserFetcher", Missing)

    auto = make_fetcher("auto", ladder=("requests", "browser"), delay=0, jitter=0)
    with pytest.raises(BlockedError) as err:
        auto.get("https://www.ebay.com/x")

    message = str(err.value)
    assert "HTTP 403" in message                    # what eBay actually said survives
    assert "Playwright is not installed" in message  # and so does the fixable part
    assert "never actually tried" in message         # named as untried, not refused


def test_http_client_sends_a_full_browser_header_set():
    """A lone User-Agent is what gets a plain client spotted."""
    headers = Fetcher(delay=0).session.headers
    for required in ("Accept-Language", "Accept-Encoding", "Sec-Fetch-Mode",
                     "Upgrade-Insecure-Requests", "sec-ch-ua"):
        assert required in headers


def test_auto_ladder_starts_with_tls_impersonation():
    """Plain requests is refused at the TLS layer, so auto must not start there."""
    from nflcarddb.fetch import DEFAULT_LADDER

    assert DEFAULT_LADDER[0] == "impersonate"
    assert "browser" in DEFAULT_LADDER
    assert "requests" not in DEFAULT_LADDER  # spending a request to relearn 403 is waste
    assert make_fetcher("auto").engine == "impersonate"


def test_engine_aliases_resolve():
    from nflcarddb.fetch import build_engine
    from nflcarddb.impersonate import ImpersonateFetcher

    assert isinstance(build_engine("tls", delay=0), ImpersonateFetcher)
    assert isinstance(build_engine("curl_cffi", delay=0), ImpersonateFetcher)
    assert isinstance(build_engine("http", delay=0), Fetcher)


def test_impersonate_rejects_a_403_rather_than_retrying(monkeypatch):
    from nflcarddb.impersonate import ImpersonateFetcher

    class Resp:
        status_code = 403
        text = ""

    f = ImpersonateFetcher(delay=0, jitter=0, max_retries=3)

    class FakeSession:
        def get(self, *a, **k):
            return Resp()

        def close(self):
            pass

    monkeypatch.setattr(f, "_ensure_session", lambda: FakeSession())

    with pytest.raises(BlockedError) as err:
        f.get("https://www.ebay.com/x")
    assert "browser TLS fingerprint" in str(err.value)
    assert f.stats.requests == 1


def test_ladder_walks_all_the_way_to_the_browser(monkeypatch):
    """impersonate refused -> browser tried, budget carried across."""
    from nflcarddb.impersonate import ImpersonateFetcher

    seen = []

    def imp_get(self, url, label=None):
        seen.append("impersonate")
        self.stats.requests += 3
        raise BlockedError("403 even with a browser TLS fingerprint")

    class FakeBrowser:
        def __init__(self, **kwargs):
            self.stats = fetch_mod.FetchStats()

        def get(self, url, label=None):
            seen.append("browser")
            return "<html>ok</html>"

        def budget_exhausted(self):
            return False

        def close(self):
            pass

    monkeypatch.setattr(ImpersonateFetcher, "get", imp_get)
    import nflcarddb.browser as browser_mod
    monkeypatch.setattr(browser_mod, "BrowserFetcher", FakeBrowser)

    auto = make_fetcher("auto", delay=0, jitter=0)
    assert auto.get("https://www.ebay.com/x") == "<html>ok</html>"
    assert seen == ["impersonate", "browser"]
    assert auto.engine == "browser"
    assert auto.stats.requests == 3  # the three spent requests carried over


def test_browser_stealth_script_covers_the_known_tells():
    from nflcarddb.browser import LAUNCH_ARGS, STEALTH_SCRIPT

    assert "navigator" in STEALTH_SCRIPT and "webdriver" in STEALTH_SCRIPT
    assert "plugins" in STEALTH_SCRIPT
    assert "languages" in STEALTH_SCRIPT
    assert any("AutomationControlled" in a for a in LAUNCH_ARGS)


def test_doctor_classifies_each_kind_of_response():
    """A 200 that is really a bot check must never read as success."""
    from nflcarddb.diagnose import CHALLENGED, REFUSED, UNREADABLE, WORKING, _classify

    listings = open("tests/fixtures/sold_s_item.html", encoding="utf-8").read()
    assert _classify(listings, 200)[0] == WORKING
    assert _classify(listings, 200)[1] == 3

    assert _classify("<html>Pardon Our Interruption</html>", 200)[0] == CHALLENGED
    assert _classify("", 403)[0] == REFUSED
    assert _classify("<html><body>hello</body></html>", 200)[0] == UNREADABLE


def test_doctor_report_separates_untried_from_refused():
    from nflcarddb.diagnose import (
        ERROR, UNAVAILABLE, Diagnosis, EngineCheck, format_report,
    )

    nothing_installed = Diagnosis(checks=[
        EngineCheck("requests", UNAVAILABLE), EngineCheck("impersonate", UNAVAILABLE),
        EngineCheck("browser", UNAVAILABLE),
    ])
    assert "nothing was installed" in format_report(nothing_installed)

    cannot_connect = Diagnosis(checks=[
        EngineCheck("requests", ERROR), EngineCheck("impersonate", ERROR),
        EngineCheck("browser", UNAVAILABLE),
    ])
    report = format_report(cannot_connect)
    assert "not refusals" in report
    assert "nothing was installed" not in report


def test_doctor_names_the_winning_engine():
    from nflcarddb.diagnose import WORKING, Diagnosis, EngineCheck, format_report

    diag = Diagnosis(checks=[
        EngineCheck("requests", "REFUSED", 403),
        EngineCheck("impersonate", WORKING, 200, 5000, 240, "parsed 240 listing(s)"),
    ])
    assert diag.any_working
    report = format_report(diag)
    assert "engine: impersonate" in report
    assert "240 listings" in report


def test_browser_warms_up_before_the_first_search(monkeypatch):
    """Arriving cold on a deep search URL with no cookies is the scraper shape."""
    from nflcarddb.browser import BrowserFetcher

    visited = []

    class FakePage:
        def goto(self, url, **kw):
            visited.append(url)
            return type("R", (), {"status": 200})()

        def wait_for_timeout(self, ms):
            pass

        def query_selector(self, sel):
            return None

        def content(self):
            return "<html><a href='/itm/123456789012'>x</a></html>"

    f = BrowserFetcher(delay=0, jitter=0, profile_dir=None)
    monkeypatch.setattr(f, "_ensure_browser", lambda: None)
    f._page = FakePage()

    f.get("https://www.ebay.com/sch/i.html?_nkw=football")
    assert visited[0] == "https://www.ebay.com/"        # homepage first
    assert "sch/i.html" in visited[1]                    # then the search

    # A second fetch must not warm up again.
    f.get("https://www.ebay.com/sch/i.html?_pgn=2")
    assert visited.count("https://www.ebay.com/") == 1


def test_warm_up_failure_does_not_stop_the_real_request(monkeypatch):
    from nflcarddb.browser import BrowserFetcher

    calls = []

    class FlakyPage:
        def goto(self, url, **kw):
            calls.append(url)
            if url == "https://www.ebay.com/":
                raise RuntimeError("homepage unreachable")
            return type("R", (), {"status": 200})()

        def wait_for_timeout(self, ms):
            pass

        def query_selector(self, sel):
            return None

        def content(self):
            return "<html><a href='/itm/123456789012'>x</a></html>"

    f = BrowserFetcher(delay=0, jitter=0, profile_dir=None)
    monkeypatch.setattr(f, "_ensure_browser", lambda: None)
    f._page = FlakyPage()

    assert f.get("https://www.ebay.com/sch/i.html") is not None
    assert len(calls) == 2  # warm-up attempted, then the real request went ahead


def test_browser_kwargs_include_the_profile_options():
    from nflcarddb.fetch import BROWSER_KWARGS, build_engine

    assert {"profile_dir", "warm_up"} <= BROWSER_KWARGS
    f = build_engine("browser", delay=0, profile_dir=None, warm_up=False, user_agent="ignored")
    assert f._warm_up is False


def test_doctor_stage_report_names_the_sold_listing_gate():
    """Homepage fine + sold search refused is an access rule, not bot detection."""
    from nflcarddb.diagnose import (
        REFUSED, WORKING, Diagnosis, EngineCheck, StageCheck, format_report,
    )

    diag = Diagnosis(
        checks=[EngineCheck("browser", REFUSED, 403)],
        stages=[
            StageCheck("homepage", "u", 200, WORKING, "ok"),
            StageCheck("plain search", "u", 200, WORKING, "parsed 60"),
            StageCheck("sold search", "u", 403, REFUSED, "HTTP 403"),
        ],
        signed_in=False,
    )
    report = format_report(diag)
    assert "refuses the SOLD search" in report
    assert "login.bat" in report
    assert "signed in to eBay: no" in report


def test_doctor_stage_report_flags_a_network_level_block():
    from nflcarddb.diagnose import REFUSED, Diagnosis, EngineCheck, StageCheck, format_report

    diag = Diagnosis(
        checks=[EngineCheck("browser", REFUSED, 403)],
        stages=[
            StageCheck("homepage", "u", 403, REFUSED, "HTTP 403"),
            StageCheck("plain search", "u", 403, REFUSED, "HTTP 403"),
            StageCheck("sold search", "u", 403, REFUSED, "HTTP 403"),
        ],
    )
    report = format_report(diag)
    assert "not about this project" in report
    assert "VPN" in report
    assert "login.bat" not in report  # signing in would not help here


def test_stages_are_skipped_when_something_already_works():
    """No point escalating URLs if a method got through."""
    from nflcarddb.diagnose import WORKING, Diagnosis, EngineCheck

    diag = Diagnosis(checks=[EngineCheck("impersonate", WORKING, 200, 500, 60)])
    assert diag.any_working
    assert diag.stages == []
