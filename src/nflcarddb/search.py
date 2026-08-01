"""Build eBay sold-listing search URLs and walk them page by page.

Two problems this module solves:

1. eBay caps any one search at ~10,000 results no matter how many pages you
   request. A busy day of football singles exceeds that, so a query is split
   into price bands and each band is walked separately. Bands that still report
   a capped result count are subdivided (geometric midpoint, since card prices
   are roughly log-distributed).

2. There is no "sold on date X" URL parameter. Instead we sort by most recently
   ended and stop paging once listings fall past the target date.
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

SORT_ENDED_RECENTLY = 13
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
) -> str:
    params: dict[str, object] = {
        "_nkw": keywords,
        "LH_Sold": 1,        # sold listings only
        "LH_Complete": 1,    # completed listings only
        "_sop": SORT_ENDED_RECENTLY,
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
) -> SegmentResult:
    """Page through one (query, price band) until the target date is passed.

    ``target_date`` is an ISO date. Pages are sorted newest-ended first, so we
    keep only listings sold on that date and stop once a page is entirely older.
    Passing ``None`` collects everything the segment returns.
    """
    collected: list[Sale] = []
    pages = 0
    capped = False
    total: Optional[int] = None
    stopped_on_date = False

    for page in range(1, max_pages + 1):
        if fetcher.budget_exhausted():
            log.warning("page budget exhausted mid-segment %s %s", query_id, band.label)
            break

        url = build_url(keywords, category, page, band, items_per_page, extra)
        html = fetcher.get(url, label=f"{query_id}_{band.label}_p{page}")
        result = parse_search_page(html, query_id=query_id)
        pages += 1

        if page == 1:
            total = result.total_results
            capped = result.total_is_capped or (
                result.total_results is not None and result.total_results >= SUBDIVIDE_THRESHOLD
            )

        if not result.sales:
            break

        if target_date is None:
            collected.extend(result.sales)
        else:
            kept = [s for s in result.sales if s.sold_date == target_date]
            collected.extend(kept)
            # Dates present and all older than the target -> we've paged past it.
            dated = [s.sold_date for s in result.sales if s.sold_date]
            if dated and all(d < target_date for d in dated):
                stopped_on_date = True
                break

        if len(result.sales) < items_per_page * 0.5:
            # Short page: eBay ran out of results for this segment.
            break

    return SegmentResult(collected, pages, capped, total, stopped_on_date)


def plan_bands(bands: list[tuple[Optional[float], Optional[float]]]) -> list[PriceBand]:
    return [PriceBand(lo, hi) for lo, hi in bands]


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
    on_segment=None,
) -> Iterator[Sale]:
    """Walk every band of a query, subdividing any band that hits the cap."""
    queue: list[tuple[PriceBand, int]] = [(b, 0) for b in bands]

    while queue:
        band, depth = queue.pop(0)
        if fetcher.budget_exhausted():
            log.warning("page budget exhausted; %d band(s) left unscraped", len(queue) + 1)
            return

        result = walk_segment(
            fetcher, query_id, keywords, category, band, target_date,
            max_pages, items_per_page, extra,
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

        if on_segment:
            on_segment(query_id, band, status, result, note)

        yield from result.sales
