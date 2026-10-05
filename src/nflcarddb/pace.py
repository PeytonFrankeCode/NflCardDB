"""Where a run's time actually goes, and what it would cost to cut it.

A run's time is pages. Each page is one request, and the configured delay sits
between requests, so minutes = pages x seconds-per-page and nothing else
matters much. Both numbers are recorded: `scrape_runs` has the wall clock and
the page count, `scrape_segments` has the pages per search.

The structure worth understanding before changing anything:

**Price bands do not multiply the work.** Bands partition the price range, so
walking seven of them pages through roughly the same number of listings as
walking one would -- each band only contains its own slice. Cutting bands down
saves almost nothing, and costs the protection against eBay's result ceiling.

**Queries DO multiply the work.** Every query is an independent walk back
through the calendar to reach the day being collected. Nine queries means
paging past the same recent days nine times over. That is why three sports
cost three times one sport, almost exactly.

So the question for any query is not "how many sales did it return" -- they
overlap, and a listing found twice is stored once -- but "how many pages did
it spend per sale that nothing else would have found". A query that spends a
seventh of the run to add one-and-a-half percent is the thing to cut, and
cutting it costs a seventh of the time.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

# Below this a query is paying far more than its share of the run for far less
# than its share of the data. Not a law -- a line to sort by.
POOR_VALUE_SHARE = 0.5


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, params).fetchall()


def _seconds(started: Optional[str], finished: Optional[str]) -> Optional[float]:
    if not started or not finished:
        return None
    try:
        a = datetime.fromisoformat(started.replace("Z", "+00:00"))
        b = datetime.fromisoformat(finished.replace("Z", "+00:00"))
    except ValueError:
        return None
    gap = (b - a).total_seconds()
    return gap if gap > 0 else None


def measured_pace(conn: sqlite3.Connection, runs: int = 14) -> dict:
    """Real seconds per page, from the clock rather than from the config.

    The configured delay is a floor, not the rate: the page has to load as
    well, and a retry or a bot check costs time the setting knows nothing
    about. Measuring it is the difference between predicting a run's length
    and guessing it.
    """
    rows = _rows(conn, """
        SELECT started_at, finished_at, pages_fetched, items_seen, target_date
        FROM scrape_runs
        WHERE finished_at IS NOT NULL AND pages_fetched > 0
        ORDER BY started_at DESC LIMIT ?
    """, (runs,))

    pages = sum(r["pages_fetched"] for r in rows)
    seconds = 0.0
    timed_pages = 0
    for r in rows:
        gap = _seconds(r["started_at"], r["finished_at"])
        if gap:
            seconds += gap
            timed_pages += r["pages_fetched"]

    return {
        "runs": len(rows),
        "pages": pages,
        "seconds": seconds,
        "seconds_per_page": (seconds / timed_pages) if timed_pages else None,
        "items": sum(r["items_seen"] or 0 for r in rows),
    }


def cost_by_query(conn: sqlite3.Connection, runs: int = 14) -> list[dict]:
    """Pages spent and unique sales gained, per search.

    `sales.query_id` holds whichever query inserted a row first, so the sale
    counts here are already each query's unique contribution rather than how
    many results it saw. Pages come from the segment records. Together they
    give the only number that decides whether a query earns its place: pages
    per sale nothing else would have found.
    """
    recent = [r["run_id"] for r in _rows(conn, """
        SELECT run_id FROM scrape_runs
        WHERE target_date IS NOT NULL ORDER BY started_at DESC LIMIT ?
    """, (runs,))]
    if not recent:
        return []

    marks = ",".join("?" * len(recent))
    pages = {r["query_id"]: r["pages"] for r in _rows(conn, f"""
        SELECT query_id, SUM(pages) AS pages FROM scrape_segments
        WHERE run_id IN ({marks}) GROUP BY query_id
    """, tuple(recent))}
    bands = {r["query_id"]: r["bands"] for r in _rows(conn, f"""
        SELECT query_id, COUNT(*) AS bands FROM scrape_segments
        WHERE run_id IN ({marks}) GROUP BY query_id
    """, tuple(recent))}
    # Unique sales are counted over all of history, not just these runs: a
    # query's job is what it adds to the database, and limiting it to a
    # fortnight would make a long-standing query look newly useless.
    gained = {r["query_id"]: r["sales"] for r in _rows(conn, """
        SELECT query_id, COUNT(*) AS sales FROM sales
        WHERE query_id IS NOT NULL GROUP BY query_id
    """)}

    total_pages = sum(pages.values()) or 1
    total_gained = sum(gained.get(q, 0) for q in pages) or 1

    out = []
    for query_id, spent in pages.items():
        got = gained.get(query_id, 0)
        page_share = spent / total_pages
        sale_share = got / total_gained
        out.append({
            "query_id": query_id,
            "pages": spent,
            "bands": bands.get(query_id, 0),
            "sales": got,
            "page_share": page_share,
            "sale_share": sale_share,
            # Above 1.0 the query pulls its weight; well below it, the query
            # is spending other searches' time.
            "value": (sale_share / page_share) if page_share else 0.0,
            "pages_per_sale": (spent / got) if got else None,
        })
    out.sort(key=lambda q: q["value"])
    return out


def band_efficiency(conn: sqlite3.Connection, runs: int = 14) -> list[dict]:
    """Pages and items per price band, to spot a band that is pure overhead.

    Bands do not multiply a run's work, so this is not where the savings are.
    It is here because a band that returns nothing on every run is still a
    request every run, and because an empty band in one sport is usually a
    sign the price range is wrong for that sport rather than for all of them.
    """
    recent = [r["run_id"] for r in _rows(conn, """
        SELECT run_id FROM scrape_runs
        WHERE target_date IS NOT NULL ORDER BY started_at DESC LIMIT ?
    """, (runs,))]
    if not recent:
        return []

    marks = ",".join("?" * len(recent))
    return [dict(r) for r in _rows(conn, f"""
        SELECT query_id, price_lo, price_hi,
               SUM(pages) AS pages, SUM(items) AS items, COUNT(*) AS walks
        FROM scrape_segments
        WHERE run_id IN ({marks})
        GROUP BY query_id, price_lo, price_hi
        HAVING items IS NULL OR items < pages * 20
        ORDER BY pages DESC
    """, tuple(recent))]


def projected_minutes(pages: int, seconds_per_page: float) -> float:
    return pages * seconds_per_page / 60.0


def savings(cost: list[dict], drop: set[str], pace: dict) -> dict:
    """What dropping a set of queries would cost and save.

    Stated as both halves. A saving reported without what it gives up is how
    a collection quietly loses a slice of itself to a tidy-up.
    """
    spp = pace.get("seconds_per_page") or 3.0
    pages_saved = sum(q["pages"] for q in cost if q["query_id"] in drop)
    sales_lost = sum(q["sales"] for q in cost if q["query_id"] in drop)
    total_pages = sum(q["pages"] for q in cost) or 1
    total_sales = sum(q["sales"] for q in cost) or 1
    return {
        "pages_saved": pages_saved,
        "minutes_saved": projected_minutes(pages_saved, spp),
        "share_of_run": pages_saved / total_pages,
        "sales_lost": sales_lost,
        "share_of_data": sales_lost / total_sales,
    }
