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

from .fetch import Fetcher, FetchError
from .models import Sale
from .parse_listing import parse_search_page

# Pages given up on in a row, with nothing succeeding between them, before the
# connection is treated as down. One failure is a hiccup and costs a band; this
# many with no success between is not a hiccup, and carrying on would spend a
# minute of retries on every remaining band to learn the same thing.
CONNECTION_DOWN_AFTER = 3


class ConnectionDown(FetchError):
    """Several pages in a row failed: the connection, not one page, is the problem.

    A FetchError, so a run stops on it exactly as it always did on any network
    failure. What changed is that a SINGLE failed page no longer does.
    """

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
    # Why the walk stopped, when it was a page eBay never answered rather than
    # the band ending. The sales before it are real and are kept.
    failed: Optional[str] = None
    # The page that failed, so a retry can pick up there rather than paying
    # again for every page that already worked.
    failed_page: Optional[int] = None

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
    start_page: int = 1,
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
    failed: Optional[str] = None
    failed_page: Optional[int] = None

    for page in range(start_page, max_pages + 1):
        if fetcher.budget_exhausted():
            log.warning("page budget exhausted mid-segment %s %s", query_id, band.label)
            budget_out = True
            break

        url = build_url(keywords, category, page, band, items_per_page, extra,
                        direction=direction)
        try:
            html = fetcher.get(url, label=f"{query_id}_{band.label}_p{page}")
        except FetchError as exc:
            # One page eBay never answered, after the fetcher's own retries.
            # This used to propagate all the way up and end the DAY -- every
            # remaining band of every remaining search abandoned, two hours of
            # run thrown away over one URL. It now costs this band, the sales
            # already collected in it are kept, and the walker decides whether
            # to come back for the rest.
            #
            # Block and sign-out are separate exception types, deliberately
            # not caught here: those mean every later request fails too.
            _note_failure(fetcher)
            failed, failed_page = _why(exc), page
            log.warning("%s %s: page %d did not load (%s). Kept the %d sale(s) "
                        "already collected in this band; carrying on.",
                        query_id, band.label, page, failed, len(collected))
            break
        _note_success(fetcher)
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
    if ran_out and not failed:
        log.warning(
            "%s band %s: %s before reaching %s -- day is incomplete",
            query_id, band.label,
            "page budget ran out" if budget_out else f"hit the {max_pages}-page limit",
            target_date,
        )

    return SegmentResult(collected, pages, capped, total, stopped_on_date,
                         ran_out or bool(failed), failed, failed_page)


def _why(exc: Exception) -> str:
    """The cause, without the fetcher's "giving up on <long url>:" preamble.

    That preamble is what made a single stalled page read as the collector
    giving up altogether. The band and page are already named in the line
    this goes into, so only the reason itself is worth repeating.
    """
    text = str(exc)
    if text.startswith("giving up on ") and ": " in text:
        text = text.split(": ", 1)[1]
    return text or exc.__class__.__name__


def _failures_in_a_row(fetcher: Fetcher) -> int:
    """Read the counter tolerantly: a fetcher is duck-typed, and one written
    before the counter existed should behave as though nothing has failed
    rather than crash the walk."""
    return getattr(fetcher.stats, "failures_in_a_row", 0)


def _note_failure(fetcher: Fetcher) -> None:
    stats = fetcher.stats
    stats.failures_in_a_row = getattr(stats, "failures_in_a_row", 0) + 1
    stats.pages_given_up = getattr(stats, "pages_given_up", 0) + 1


def _note_success(fetcher: Fetcher) -> None:
    stats = fetcher.stats
    stats.failures_in_a_row = 0
    stats.pages_ok = getattr(stats, "pages_ok", 0) + 1


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
    """Walk every band of a query, subdividing any band that hits the cap.

    A band whose page fails is put back at the END of this query's queue and
    tried once more, starting near the page that failed. By the time the walk
    gets back to it the rest of the query has run, which is usually long
    enough for whatever stalled eBay to have passed -- so most failures end as
    a fully collected band rather than a hole for recheck to fill tomorrow.
    """
    # (band, subdivision depth, page to resume from -- None if never failed)
    queue: list[tuple[PriceBand, int, Optional[int]]] = [(b, 0, None) for b in bands]

    while queue:
        band, depth, resume = queue.pop(0)
        if fetcher.budget_exhausted():
            log.warning("page budget exhausted; %d band(s) left unscraped", len(queue) + 1)
            # Bands never walked are missing sales just as surely as a band cut
            # off mid-walk, and the caller has to hear about it.
            if on_segment:
                for pending, _, _ in queue:
                    on_segment(query_id, pending, "unreached",
                               SegmentResult([], 0, False, None, False, ran_out=True),
                               "page budget ran out before this band was walked")
            return

        # Resume a page early rather than on the failed page itself: new sales
        # arrive while the rest of the query runs and push listings down the
        # results, and one page of overlap covers that. Duplicates cost
        # nothing, since item_id is the key.
        start = max(1, resume - 1) if resume else 1
        result = walk_segment(
            fetcher, query_id, keywords, category, band, target_date,
            max_pages, items_per_page, extra, direction=direction,
            start_page=start,
        )
        log.info(
            "%s band %s -> %d sales across %d page(s)%s",
            query_id, band.label, len(result.sales), result.pages,
            " [capped]" if result.capped else "",
        )

        # Several failed pages in a row, with nothing succeeding between them,
        # is the connection rather than a page. Checked at the moment of the
        # failure, not before the next band: when the last bands of a run are
        # the ones failing there IS no next band, and a dead connection would
        # end the night reported as an ordinary incomplete day -- the wrong
        # diagnosis, sending someone to recheck instead of to their router.
        #
        # Stopping here is what the run always did on ANY failure. The
        # difference is that it now takes more than one.
        if result.failed and _failures_in_a_row(fetcher) >= CONNECTION_DOWN_AFTER:
            if on_segment:
                on_segment(query_id, band, "failed", result,
                           f"connection lost: {result.failed}")
            yield from result.sales
            raise ConnectionDown(
                f"{_failures_in_a_row(fetcher)} pages in a row failed with "
                "none succeeding between them -- the connection to eBay looks "
                "down rather than one page being slow. Stopping so the "
                "remaining bands do not each spend a minute retrying."
            )

        status = "done"
        note = None
        if result.capped and depth < max_depth:
            # Checked before the failure, deliberately: a capped band is about
            # to be replaced by its two halves, which between them collect
            # everything it held. Retrying the parent as well would pay for
            # the same listings twice.
            lower, upper = band.split()
            queue.extend([(lower, depth + 1, None), (upper, depth + 1, None)])
            status = "capped"
            note = f"subdivided into {lower.label} and {upper.label}"
        elif result.failed and resume is None:
            queue.append((band, depth, result.failed_page))
            status = "retrying"
            note = (f"page {result.failed_page} failed ({result.failed}); "
                    f"trying again once this search's other bands are done")
            log.info("%s %s: will try page %d again once the rest of %s "
                     "is done", query_id, band.label, result.failed_page,
                     query_id)
        elif result.failed:
            status = "failed"
            note = (f"page {result.failed_page} failed twice ({result.failed}); "
                    f"the rest of this band is left for recheck")
            log.warning("%s band %s %s", query_id, band.label, note)
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
