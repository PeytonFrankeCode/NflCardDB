from urllib.parse import parse_qs, urlparse

from nflcarddb.models import PageResult, Sale
from nflcarddb.search import PriceBand, build_url, walk_query, walk_segment


def q(url):
    return parse_qs(urlparse(url).query)


def test_url_requests_sold_and_completed_only():
    params = q(build_url("football", "261328"))
    assert params["LH_Sold"] == ["1"]
    assert params["LH_Complete"] == ["1"]
    assert params["_nkw"] == ["football"]
    assert params["_sacat"] == ["261328"]
    assert params["_sop"] == ["13"]  # ended most recently


def test_url_price_band_bounds():
    params = q(build_url("football", None, page=3, band=PriceBand(10, 25)))
    assert params["_udlo"] == ["10"]
    assert params["_udhi"] == ["25"]
    assert params["_pgn"] == ["3"]


def test_url_open_ended_bands_omit_the_missing_bound():
    lower = q(build_url("f", None, band=PriceBand(None, 10)))
    assert "_udlo" not in lower and lower["_udhi"] == ["10"]

    upper = q(build_url("f", None, band=PriceBand(1000, None)))
    assert upper["_udlo"] == ["1000"] and "_udhi" not in upper


def test_band_splits_at_geometric_midpoint():
    lower, upper = PriceBand(10, 1000).split()
    assert lower.lo == 10 and upper.hi == 1000
    assert lower.hi == upper.lo == 100.0  # sqrt(10 * 1000)


def test_band_split_handles_open_bounds():
    lower, upper = PriceBand(None, 10).split()
    assert lower.lo is None and upper.hi == 10
    assert 0 < lower.hi < 10


class FakeFetcher:
    """Serves canned pages so pagination logic can be tested offline."""

    def __init__(self, pages):
        self.pages = pages
        self.urls = []
        self.stats = type("S", (), {"requests": 0})()

    def get(self, url, label=None):
        self.urls.append(url)
        self.stats.requests += 1
        page_num = int(parse_qs(urlparse(url).query)["_pgn"][0])
        return self.pages.get(page_num, "")

    def budget_exhausted(self):
        return False


def _page_html(sales, count="500"):
    """Minimal markup the real parser can read."""
    tiles = "".join(
        f'<li class="s-item"><a class="s-item__link" href="https://www.ebay.com/itm/{i}">'
        f'<div class="s-item__title"><span role="heading">{t}</span></div></a>'
        f'<span class="s-item__price">$10.00</span>'
        f'<div class="s-item__caption"><span>Sold  {d}</span></div></li>'
        for i, t, d in sales
    )
    return (
        f'<h1 class="srp-controls__count-heading">{count} results</h1>'
        f'<ul class="srp-results">{tiles}</ul>'
    )


def test_walk_segment_stops_once_past_target_date():
    pages = {
        1: _page_html([(f"10000000000{i}", f"card {i}", "Jul 30, 2025") for i in range(5)]),
        2: _page_html([(f"20000000000{i}", f"card {i}", "Jul 29, 2025") for i in range(5)]),
        3: _page_html([(f"30000000000{i}", f"card {i}", "Jul 28, 2025") for i in range(5)]),
    }
    fetcher = FakeFetcher(pages)
    result = walk_segment(
        fetcher, "q1", "football", None, PriceBand(None, None),
        target_date="2025-07-30", max_pages=10, items_per_page=5,
    )

    # Page 2 is entirely older than the target, so page 3 is never requested.
    assert result.pages == 2
    assert result.stopped_on_date is True
    assert len(result.sales) == 5
    assert {s.sold_date for s in result.sales} == {"2025-07-30"}


def test_walk_segment_keeps_only_target_date_from_mixed_page():
    pages = {
        1: _page_html(
            [("100000000001", "a", "Jul 30, 2025"), ("100000000002", "b", "Jul 29, 2025")]
        ),
    }
    fetcher = FakeFetcher(pages)
    result = walk_segment(
        fetcher, "q1", "football", None, PriceBand(None, None),
        target_date="2025-07-30", max_pages=5, items_per_page=2,
    )
    assert [s.item_id for s in result.sales] == ["100000000001"]


def test_capped_segment_gets_subdivided():
    # Every page reports a capped count, forcing subdivision.
    html = _page_html([("100000000001", "a", "Jul 30, 2025")], count="10,000+")
    fetcher = FakeFetcher({n: html for n in range(1, 50)})
    segments = []

    list(walk_query(
        fetcher, "q1", "football", None, [PriceBand(10, 1000)],
        target_date="2025-07-30", max_pages=1, max_depth=1, items_per_page=1,
        on_segment=lambda qid, band, status, res, note: segments.append((band.label, status)),
    ))

    assert segments[0] == ("10-1000", "capped")
    # One split at depth 1 produces two child bands, which are then walked.
    assert ("10-100", "capped") in segments
    assert ("100-1000", "capped") in segments


def test_uncapped_query_is_not_subdivided():
    fetcher = FakeFetcher({
        1: _page_html([("100000000001", "a", "Jul 30, 2025")], count="42"),
        2: _page_html([]),          # eBay has no more: the segment is complete
    })
    segments = []
    list(walk_query(
        fetcher, "q1", "football", None, [PriceBand(10, 25)],
        target_date="2025-07-30", max_pages=3, max_depth=2, items_per_page=1,
        on_segment=lambda qid, band, status, res, note: segments.append((band.label, status)),
    ))
    assert segments == [("10-25", "done")]
