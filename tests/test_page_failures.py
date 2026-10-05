"""One page eBay never answers must cost that band, not the night.

A failed page used to raise all the way to the top of the run and end the
whole day: every remaining band of every remaining search abandoned, two hours
of collecting thrown away over one URL. With nine searches the odds that SOME
page somewhere stalls through all its retries became close to certain, which
is why "giving up... stopping" started appearing most nights.

The rules these tests hold to:
  * a failed page costs its band, and the sales already collected in it stay
  * the band is retried once, at the end of its search, from near the page
    that failed -- so a brief stall ends as a fully collected band
  * a band that fails twice is a gap for recheck, and the day says so
  * several failures in a row with nothing succeeding is the connection, and
    THAT still stops the run, exactly as any failure used to
  * a block or a sign-out is never mistaken for a flaky page
"""

from urllib.parse import parse_qs, urlparse

import pytest

from nflcarddb.fetch import BlockedError, FetchError, FetchStats, SignedOutError
from nflcarddb.search import (CONNECTION_DOWN_AFTER, ConnectionDown, PriceBand,
                              walk_query, walk_segment)

DAY = "2025-07-30"
OLDER = "2025-07-29"
# Small pages keep the suite fast. What matters is a page being full
# relative to items_per_page, which the walker reads as "keep going".
PER_PAGE = 10


def _page(ids, date=DAY, per_page=240):
    """A page of listings. Full-sized by default so the walker keeps going."""
    tiles = "".join(
        f'<li class="s-item"><a class="s-item__link" href="https://www.ebay.com/itm/{i}">'
        f'<div class="s-item__title"><span role="heading">2024 Prizm Card {i} #{i}</span></div></a>'
        f'<span class="s-item__price">$10.00</span>'
        f'<div class="s-item__caption"><span>Sold  Jul {date[-2:]}, 2025</span></div></li>'
        for i in ids
    )
    return (f'<h1 class="srp-controls__count-heading">500 results</h1>'
            f'<ul class="srp-results">{tiles}</ul>')


def _ids(start, n=PER_PAGE):
    return [str(900000000000 + start + k) for k in range(n)]


class FlakyFetcher:
    """Serves pages per (band, page), failing exactly the ones it is told to.

    `fail` maps (band_low, page) -> how many times that page should fail
    before it starts working. Everything else answers normally. The real
    FetchStats is used, so the counters the walker reads are the real ones.
    """

    def __init__(self, pages, fail=None, raise_as=FetchError):
        self.pages = pages
        self.fail = dict(fail or {})
        self.raise_as = raise_as
        self.calls = []
        self.stats = FetchStats()

    def get(self, url, label=None):
        params = parse_qs(urlparse(url).query)
        page = int(params["_pgn"][0])
        low = float(params["_udlo"][0]) if "_udlo" in params else None
        self.calls.append((low, page))
        self.stats.requests += 1
        if self.fail.get((low, page), 0) > 0:
            self.fail[(low, page)] -= 1
            raise self.raise_as(f"giving up on {url}: timed out")
        return self.pages.get((low, page), "")

    def budget_exhausted(self):
        return False


def _two_bands():
    """Band A: three full pages on the day, then a page from the day before
    (which ends the walk). Band B: one full page, then the older page."""
    return {
        (None, 1): _page(_ids(0)),
        (None, 2): _page(_ids(100)),
        (None, 3): _page(_ids(200)),
        (None, 4): _page(_ids(300), date=OLDER),
        (10.0, 1): _page(_ids(5000)),
        (10.0, 2): _page(_ids(5100), date=OLDER),
    }


BANDS = [PriceBand(None, 10), PriceBand(10, None)]


def _walk(fetcher, bands=BANDS):
    seen = []
    sales = []
    try:
        for sale in walk_query(
            fetcher, "football_singles", "football", "261328", bands, DAY,
            items_per_page=PER_PAGE,
            on_segment=lambda q, b, status, r, note: seen.append((b.lo, status, note)),
        ):
            sales.append(sale)
    except ConnectionDown:
        seen.append(("connection", "down", None))
    return sales, seen


# --- a single bad page --------------------------------------------------------


def test_one_failed_page_does_not_end_the_search():
    """The bug. Band A's second page stalls through every retry. Band B must
    still be walked -- it used to be abandoned along with everything else."""
    fetcher = FlakyFetcher(_two_bands(), fail={(None, 2): 99})
    sales, seen = _walk(fetcher)

    assert (10.0, 1) in fetcher.calls, "the next band was never reached"
    assert any(lo == 10.0 and status == "done" for lo, status, _ in seen)


def test_sales_before_the_failed_page_are_kept():
    """Page 1 of band A worked. Its sales are real and must survive the
    failure of page 2."""
    fetcher = FlakyFetcher(_two_bands(), fail={(None, 2): 99})
    sales, _ = _walk(fetcher)

    collected = {s.item_id for s in sales}
    assert set(_ids(0)) <= collected


def test_a_brief_stall_is_collected_in_full_on_the_retry():
    """Fails once, then works. By the time the walk returns to it the rest of
    the search has run, and the day should end complete -- not as a gap for
    tomorrow's recheck."""
    fetcher = FlakyFetcher(_two_bands(), fail={(None, 2): 1})
    sales, seen = _walk(fetcher)

    collected = {s.item_id for s in sales}
    for start in (0, 100, 200):
        assert set(_ids(start)) <= collected, f"page starting {start} missing"
    statuses = [status for lo, status, _ in seen if lo is None]
    assert statuses == ["retrying", "done"]


def test_the_retry_resumes_near_the_failed_page_rather_than_from_scratch():
    """A band can be forty pages deep. Re-paying for thirty-nine that already
    worked to recover the fortieth would double the cost of every stall.
    One page of overlap covers listings pushed down by new sales meanwhile."""
    fetcher = FlakyFetcher(_two_bands(), fail={(None, 3): 1})
    _walk(fetcher)

    band_a = [page for low, page in fetcher.calls if low is None]
    # First pass: 1, 2, 3 (fails). Retry resumes one early: 2, 3, 4.
    assert band_a == [1, 2, 3, 2, 3, 4]


def test_the_retry_waits_until_the_rest_of_the_search_has_run():
    """Retrying immediately would hit the same stall. Going to the back of the
    queue buys the time a brief eBay hiccup needs to pass."""
    fetcher = FlakyFetcher(_two_bands(), fail={(None, 2): 1})
    _walk(fetcher)

    first_b = fetcher.calls.index((10.0, 1))
    retry_a = [i for i, c in enumerate(fetcher.calls) if c == (None, 2)][1]
    assert first_b < retry_a


def test_a_page_that_fails_twice_leaves_a_gap_and_says_so():
    """Retried once, failed again. The band's remainder is missing, and the
    record has to say "failed" so leaks and recheck can find it."""
    fetcher = FlakyFetcher(_two_bands(), fail={(None, 2): 2})
    _, seen = _walk(fetcher)

    final = [(status, note) for lo, status, note in seen if lo is None][-1]
    assert final[0] == "failed"
    assert "failed twice" in final[1]


def test_a_failed_band_is_retried_only_once():
    """A page that fails forever must not loop forever."""
    fetcher = FlakyFetcher(_two_bands(), fail={(None, 2): 99})
    _walk(fetcher)

    attempts = [c for c in fetcher.calls if c == (None, 2)]
    assert len(attempts) == 2


def test_a_segment_reports_where_it_failed():
    fetcher = FlakyFetcher(_two_bands(), fail={(None, 3): 99})
    result = walk_segment(fetcher, "q", "football", "261328",
                          PriceBand(None, 10), DAY, items_per_page=PER_PAGE)

    assert result.failed and "timed out" in result.failed
    assert result.failed_page == 3
    assert result.ran_out, "a band cut off by a failure did not reach the day"
    assert len(result.sales) == 2 * PER_PAGE, "pages 1 and 2 were collected"


# --- the connection, not a page ---------------------------------------------


def test_several_failures_in_a_row_stop_the_run():
    """Nothing succeeding between failures is the connection. Every further
    band would spend a minute of retries to learn the same thing."""
    pages = {(float(lo) if lo else None, 1): "" for lo in range(0, 50, 10)}
    bands = [PriceBand(lo, lo + 10) for lo in range(0, 50, 10)]
    fail = {(PriceBand(lo, lo + 10).lo, 1): 99 for lo in range(0, 50, 10)}
    fetcher = FlakyFetcher(pages, fail=fail)

    _, seen = _walk(fetcher, bands)

    assert ("connection", "down", None) in seen
    assert fetcher.stats.failures_in_a_row >= CONNECTION_DOWN_AFTER
    walked = {low for low, _ in fetcher.calls}
    assert len(walked) == CONNECTION_DOWN_AFTER, "kept trying after the line"


def test_a_success_between_failures_resets_the_count():
    """Scattered failures across a long healthy run are eBay stalling now and
    then. Only an unbroken run of them is an outage."""
    stats = FetchStats()
    pages = _two_bands()
    fetcher = FlakyFetcher(pages, fail={(None, 2): 1, (10.0, 2): 1})
    fetcher.stats = stats
    _walk(fetcher)

    assert stats.pages_given_up == 2
    assert stats.failures_in_a_row == 0
    assert stats.pages_ok > 0


def test_connection_down_is_still_a_fetch_error():
    """So the run's existing network handling catches it unchanged -- the
    exit code and the reason are what they always were."""
    assert issubclass(ConnectionDown, FetchError)


# --- what must NOT be contained ---------------------------------------------


@pytest.mark.parametrize("error", [BlockedError, SignedOutError])
def test_a_block_or_sign_out_still_stops_everything(error):
    """These mean every later request fails too. Treating one as a flaky page
    would spend the rest of the night being refused one band at a time."""
    fetcher = FlakyFetcher(_two_bands(), fail={(None, 2): 1}, raise_as=error)

    with pytest.raises(error):
        list(walk_query(fetcher, "q", "football", "261328", BANDS, DAY,
                        items_per_page=PER_PAGE))
    assert (10.0, 1) not in fetcher.calls


# --- what gets recorded -------------------------------------------------------


def test_a_retried_band_records_both_walks(tmp_path):
    """Both walks were paid for. If the second replaced the first, the speed
    report would undercount precisely the bands that cost the most."""
    from nflcarddb import db as store

    conn = store.connect(tmp_path / "seg.sqlite")
    run = store.start_run(conn, DAY)
    store.record_segment(conn, run, "q:a", "q", None, 10, "retrying", 2, 20,
                         "page 3 failed")
    store.record_segment(conn, run, "q:a", "q", None, 10, "done", 3, 25, None)

    row = conn.execute("SELECT status, pages, items FROM scrape_segments "
                       "WHERE segment_id = 'q:a'").fetchone()
    conn.close()
    assert tuple(row) == ("done", 5, 45)


def test_an_ordinary_rewrite_still_replaces(tmp_path):
    """Only a retry accumulates. A resumed run re-recording a band it already
    finished must not double its pages."""
    from nflcarddb import db as store

    conn = store.connect(tmp_path / "seg.sqlite")
    run = store.start_run(conn, DAY)
    store.record_segment(conn, run, "q:a", "q", None, 10, "done", 3, 25, None)
    store.record_segment(conn, run, "q:a", "q", None, 10, "done", 3, 25, None)

    row = conn.execute("SELECT pages, items FROM scrape_segments "
                       "WHERE segment_id = 'q:a'").fetchone()
    conn.close()
    assert tuple(row) == (3, 25)


def test_the_logged_reason_drops_the_alarming_preamble():
    """"giving up on https://www.ebay.com/sch/...: timed out" is what made one
    stalled page read like the collector quitting. The band and page are named
    in the log line already; the reason is all that needs repeating."""
    from nflcarddb.search import _why

    assert _why(FetchError("giving up on https://www.ebay.com/sch/i.html?x=1: "
                           "read timed out")) == "read timed out"
    assert _why(FetchError("HTTP 503")) == "HTTP 503"
    assert _why(FetchError("")) == "FetchError"
