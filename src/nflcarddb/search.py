"""Build eBay sold-listing search URLs and walk them page by page.

Two problems this module solves:

1. eBay caps any one search at ~10,000 results no matter how many pages you
   request. A busy day of football singles exceeds that, so a query is split
   into price bands and each band is walked separately. Bands that still report
   a capped result count are subdivided (geometric midpoint, since card prices
   are roughly log-distributed).

2. There is no "sold on date X" URL parameter. Instead we sort by end time and
   stop paging once listings fall past the target date.

   That second point has a cost worth stating plainly: reaching a day N days
   back means paging through everything sold since. At ~25,000 football sales a
   day and 240 per page, day 21 sits about 2,300 pages in -- and subdividing
   into price bands does not help, because the total volume between today and
   the target is the same however it is sliced. It only gets each query under
   eBay's 10,000-result cap.

   So the day is approached from whichever end of eBay's ~90-day window is
   nearer. Sorted oldest-first, day 85 is five days of paging from the far end
   instead of eighty-five from the near one -- and the oldest days, the ones
   about to age out for good, become the cheapest to collect rather than the
   most expensive. Whether eBay honours an ascending sort on completed listings
   is checked at runtime rather than assumed; see `probe_oldest_first`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator, Optional
from urllib.parse import urlencode

from .fetch import Fetcher
from .models import Sale
from .parse_listing import parse_search_page

log = logging.getLogger(__name__)

BASE_URL = "https://www.ebay.com/sch/i.html"

# eBay's practical ceiling on results per query.
RESULT_CAP = 10_000
# Subdivide a band when its reported count gets close to the cap.
SUBDIVIDE_THRESHOLD = 9_000

# Sort by end time, newest ended first. The reliable one.
SORT_ENDED_RECENTLY = 13
# "Ending soonest": ascending end time. On completed listings every end time is
# in the past, so ascending means oldest first -- the far end of the window.
SORT_ENDING_SOONEST = 1

NEWEST_FIRST = "newest"
OLDEST_FIRST = "oldest"

ITEMS_PER_PAGE = 240


@dataclass(frozen=True)
class PriceBand:
    lo: Optional[float]
    hi: Optional[float]

    @property
    def label(self) -> str:
        lo = "" if self.lo is None else f"{self.lo:g}"
        hi = "" if self.hi is None else f"{self.hi:g}"
        return f"{lo}-{hi}" or "all"

    def split(self) -> tuple["PriceBand", "PriceBand"]:
        """Split at the geometric midpoint; card prices are log-distributed."""
        lo = self.lo if self.lo and self.lo > 0 else 0.99
        hi = self.hi if self.hi else lo * 100
        mid = round((lo * hi) ** 0.5, 2)
        if mid <= lo or mid >= hi:
            mid = round((lo + hi) / 2, 2)
        return (PriceBand(self.lo, mid), PriceBand(mid, self.hi))


def build_url(
    keywords: str = "",
    category: Optional[str] = None,
    page: int = 1,
    band: Optional[PriceBand] = None,
    items_per_page: int = ITEMS_PER_PAGE,
    extra: Optional[dict] = None,
    direction: str = NEWEST_FIRST,
) -> str:
    params: dict[str, object] = {
        "_nkw": keywords,
        "LH_Sold": 1,        # sold listings only
        "LH_Complete": 1,    # completed listings only
        "_sop": SORT_ENDING_SOONEST if direction == OLDEST_FIRST else SORT_ENDED_RECENTLY,
        "_ipg": items_per_page,
        "_pgn": page,
    }
    if category:
        params["_sacat"] = category
    if band:
        if band.lo is not None:
            params["_udlo"] = band.lo
        if band.hi is not None:
            params["_udhi"] = band.hi
    if extra:
        params.update(extra)
    return f"{BASE_URL}?{urlencode(params)}"


@dataclass
class SegmentResult:
    sales: list[Sale]
    pages: int
    capped: bool
    total_results: Optional[int]
    stopped_on_date: bool
    # True when the walk ran out of pages, budget or eBay results before it
    # could prove it had passed the target date. The sales collected are real,
    # but there are more that were never reached -- so the day is incomplete,
    # and saying so is what stops it being recorded as finished.
    ran_out: bool = False

    @property
    def reached_target(self) -> bool:
        return not self.ran_out


def walk_segment(
    fetcher: Fetcher,
    query_id: str,
    keywords: str,
    category: Optional[str],
    band: PriceBand,
    target_date: Optional[str],
    max_pages: int = 42,
    items_per_page: int = ITEMS_PER_PAGE,
    extra: Optional[dict] = None,
    direction: str = NEWEST_FIRST,
) -> SegmentResult:
    """Page through one (query, price band) until the target date is passed.

    ``target_date`` is an ISO date. Pages are sorted by end time -- newest first
    by default, oldest first when ``direction`` says so -- and we keep listings
    sold on the target date, stopping once a whole page has gone past it.
    Passing ``None`` collects everything the segment returns.
    """
    collected: list[Sale] = []
    pages = 0
    capped = False
    total: Optional[int] = None
    stopped_on_date = False
    exhausted = False
    budget_out = False

    for page in range(1, max_pages + 1):
        if fetcher.budget_exhausted():
            log.warning("page budget exhausted mid-segment %s %s", query_id, band.label)
            budget_out = True
            break

        url = build_url(keywords, category, page, band, items_per_page, extra,
                        direction=direction)
        html = fetcher.get(url, label=f"{query_id}_{band.label}_p{page}")
        result = parse_search_page(html, query_id=query_id)
        pages += 1

        if page == 1:
            total = result.total_results
            capped = result.total_is_capped or (
                result.total_results is not None and result.total_results >= SUBDIVIDE_THRESHOLD
            )

        if not result.sales:
            exhausted = True
            break

        if target_date is None:
            collected.extend(result.sales)
        else:
            kept = [s for s in result.sales if s.sold_date == target_date]
            collected.extend(kept)
            # A whole page past the target means the target is behind us. Which
            # side "past" is on depends on the sort.
            dated = [s.sold_date for s in result.sales if s.sold_date]
            if dated and all(
                (d < target_date) if direction == NEWEST_FIRST else (d > target_date)
                for d in dated
            ):
                stopped_on_date = True
                break

        if len(result.sales) < items_per_page * 0.5:
            # Short page: eBay ran out of results for this segment.
            exhausted = True
            break

    # Having seen everything eBay offered is as good as stopping on the date.
    # Anything else means the walk was cut off with the target still ahead.
    ran_out = bool(target_date) and not (stopped_on_date or exhausted)
    if ran_out:
        log.warning(
            "%s band %s: %s before reaching %s -- day is incomplete",
            query_id, band.label,
            "page budget ran out" if budget_out else f"hit the {max_pages}-page limit",
            target_date,
        )

    return SegmentResult(collected, pages, capped, total, stopped_on_date, ran_out)


def plan_bands(bands: list[tuple[Optional[float], Optional[float]]]) -> list[PriceBand]:
    return [PriceBand(lo, hi) for lo, hi in bands]


def probe_oldest_first(
    fetcher: Fetcher,
    keywords: str = "",
    category: Optional[str] = None,
    extra: Optional[dict] = None,
) -> bool:
    """Does eBay actually return oldest-ended listings first for `_sop=1`?

    One request. "Ending soonest" is documented for live listings, where every
    end time is in the future; on completed listings the meaning is not
    promised, and eBay could reasonably ignore it or fall back to Best Match.
    Getting that wrong would silently collect the wrong end of the window, so
    it is measured rather than assumed: ask for oldest-first and check the
    dates that come back really are old.
    """
    url = build_url(keywords, category, page=1, items_per_page=60, extra=extra,
                    direction=OLDEST_FIRST)
    try:
        result = parse_search_page(fetcher.get(url, label="probe_oldest"))
    except Exception as exc:                      # a probe must never end a run
        log.warning("oldest-first probe failed (%s); using newest-first", exc)
        return False

    dated = sorted(s.sold_date for s in result.sales if s.sold_date)
    if len(dated) < 5:
        log.info("oldest-first probe inconclusive (%d dated results)", len(dated))
        return False

    # eBay keeps ~90 days. If this really is the far end, the dates cluster
    # there; if the sort was ignored we get the last day or two instead.
    from datetime import date as _date

    age = (_date.today() - _date.fromisoformat(dated[0])).days
    works = age >= 30
    log.info("oldest-first probe: oldest result is %d day(s) old -> %s",
             age, "supported" if works else "not supported")
    return works


def walk_query(
    fetcher: Fetcher,
    query_id: str,
    keywords: str,
    category: Optional[str],
    bands: list[PriceBand],
    target_date: Optional[str],
    max_pages: int = 42,
    max_depth: int = 3,
    items_per_page: int = ITEMS_PER_PAGE,
    extra: Optional[dict] = None,
    direction: str = NEWEST_FIRST,
    on_segment=None,
) -> Iterator[Sale]:
    """Walk every band of a query, subdividing any band that hits the cap."""
    queue: list[tuple[PriceBand, int]] = [(b, 0) for b in bands]

    while queue:
        band, depth = queue.pop(0)
        if fetcher.budget_exhausted():
            log.warning("page budget exhausted; %d band(s) left unscraped", len(queue) + 1)
            # Bands never walked are missing sales just as surely as a band cut
            # off mid-walk, and the caller has to hear about it.
            if on_segment:
                for pending, _ in queue:
                    on_segment(query_id, pending, "unreached",
                               SegmentResult([], 0, False, None, False, ran_out=True),
                               "page budget ran out before this band was walked")
            return

        result = walk_segment(
            fetcher, query_id, keywords, category, band, target_date,
            max_pages, items_per_page, extra, direction=direction,
        )
        log.info(
            "%s band %s -> %d sales across %d page(s)%s",
            query_id, band.label, len(result.sales), result.pages,
            " [capped]" if result.capped else "",
        )

        status = "done"
        note = None
        if result.capped and depth < max_depth:
            lower, upper = band.split()
            queue.extend([(lower, depth + 1), (upper, depth + 1)])
            status = "capped"
            note = f"subdivided into {lower.label} and {upper.label}"
        elif result.capped:
            status = "capped"
            note = f"still capped at max depth {max_depth}; some sales may be missed"
            log.warning("%s band %s %s", query_id, band.label, note)
        elif result.ran_out:
            status = "incomplete"
            note = f"never reached {target_date}; more sales exist in this band"

        if on_segment:
            on_segment(query_id, band, status, result, note)

        yield from result.sales
