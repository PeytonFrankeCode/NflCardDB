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

    auto = make_fetcher("auto", delay=0, jitter=0)
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

    auto = make_fetcher("auto", delay=0, jitter=0, page_budget=10)
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

    auto = make_fetcher("auto", delay=0, jitter=0)
    with pytest.raises(BlockedError) as err:
        auto.get("https://www.ebay.com/x")

    message = str(err.value)
    assert "HTTP 403" in message           # the real cause survives
    assert "playwright install" in message  # and the fix is attached


def test_http_client_sends_a_full_browser_header_set():
    """A lone User-Agent is what gets a plain client spotted."""
    headers = Fetcher(delay=0).session.headers
    for required in ("Accept-Language", "Accept-Encoding", "Sec-Fetch-Mode",
                     "Upgrade-Insecure-Requests", "sec-ch-ua"):
        assert required in headers
