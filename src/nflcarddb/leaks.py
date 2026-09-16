"""Where the collector knows it missed sales.

Every band it walks is recorded in `scrape_segments` with a status and, when
something went wrong, a note saying what. Three of those statuses are losses:

* **capped** at the deepest subdivision -- eBay refuses to show past about
  10,000 results for one search, so a band still over that after being split
  three times has sales inside it that no page can reach. The fix is a
  narrower search, not more patience.
* **unreached** -- the run's page budget ran out with bands still queued.
  Nothing was wrong with those bands; there was no time left to walk them.
* **incomplete** -- the walk never got back as far as the day it was
  collecting, so the older part of that band was never seen.

The collector has written these down since the beginning and nothing has ever
read them back, which is how "it feels like we are getting less than we could"
stayed a feeling. Each one names a different fix, so they are counted apart.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

# Roughly where eBay stops paging a single search. A band that comes back at
# this number is not "big", it is truncated -- the true count is unknown and
# unknowable from the outside.
EBAY_RESULT_CEILING = 10_000

LOSS_STATUSES = ("capped", "unreached", "incomplete")

# What to do about each, in the words the fix is actually described in.
REMEDIES = {
    "capped": (
        "eBay would not show more of this search. Split it: a narrower "
        "keyword, or price bands that start finer in this range."
    ),
    "unreached": (
        "The run ran out of pages before walking this band. Raise "
        "page_budget, or give the run more time."
    ),
    "incomplete": (
        "The walk never reached the day being collected. The day is only "
        "partly here; `recheck` re-collects days like this."
    ),
}


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, params).fetchall()


def leak_report(conn: sqlite3.Connection, runs: int = 14) -> dict:
    """What the last `runs` collections could not reach.

    Scoped to recent runs rather than all history because the question is
    about the collector as configured now. A band that was capped two months
    ago under different settings is not evidence about tonight.
    """
    recent = [r["run_id"] for r in _rows(conn, """
        SELECT run_id FROM scrape_runs
        WHERE target_date IS NOT NULL
        ORDER BY started_at DESC LIMIT ?
    """, (runs,))]
    if not recent:
        return {"runs": 0, "segments": 0, "by_status": {}, "by_query": [],
                "worst": [], "days": [], "incomplete_days": [],
                "uncredited": 0}

    marks = ",".join("?" * len(recent))
    segments = _rows(conn, f"""
        SELECT s.*, r.target_date
        FROM scrape_segments s JOIN scrape_runs r USING (run_id)
        WHERE s.run_id IN ({marks})
    """, tuple(recent))

    by_status: dict[str, dict] = {}
    for seg in segments:
        entry = by_status.setdefault(seg["status"], {"segments": 0, "items": 0})
        entry["segments"] += 1
        entry["items"] += seg["items"] or 0

    # Per query, because the answer is usually "one query is doing all the
    # losing" and that names which search to narrow.
    by_query: dict[str, dict] = {}
    for seg in segments:
        q = by_query.setdefault(seg["query_id"], {
            "query_id": seg["query_id"], "segments": 0, "items": 0,
            "capped": 0, "unreached": 0, "incomplete": 0,
        })
        q["segments"] += 1
        q["items"] += seg["items"] or 0
        if seg["status"] in LOSS_STATUSES:
            q[seg["status"]] += 1

    # The individual bands that ended capped at full depth: each one is a
    # price range with an unknown number of sales behind a wall.
    worst = [
        {"query_id": s["query_id"], "segment_id": s["segment_id"],
         "price_lo": s["price_lo"], "price_hi": s["price_hi"],
         "items": s["items"], "pages": s["pages"], "note": s["note"],
         "target_date": s["target_date"]}
        for s in segments
        if s["status"] == "capped" and s["note"]
        and "still capped" in s["note"]
    ]
    worst.sort(key=lambda s: -(s["items"] or 0))

    days = [dict(r) for r in _rows(conn, f"""
        SELECT r.target_date,
               SUM(CASE WHEN s.status IN ('capped','unreached','incomplete')
                        THEN 1 ELSE 0 END) AS lost_segments,
               COUNT(*) AS segments,
               SUM(s.items) AS items
        FROM scrape_segments s JOIN scrape_runs r USING (run_id)
        WHERE s.run_id IN ({marks})
        GROUP BY r.target_date
        ORDER BY r.target_date DESC
    """, tuple(recent))]

    return {
        "runs": len(recent),
        "segments": len(segments),
        "by_status": by_status,
        "by_query": sorted(by_query.values(), key=lambda q: -q["items"]),
        "worst": worst,
        "days": days,
        # Not limited to `runs`: a day recorded as cut short three weeks ago
        # is still cut short today, and re-collecting it is still the gain.
        "incomplete_days": days_recorded_incomplete(conn),
        "uncredited": uncredited_sales(conn),
    }


def days_recorded_incomplete(conn: sqlite3.Connection) -> list[dict]:
    """Days the collector itself recorded as cut short, with the band that was.

    Better evidence than `find_thin_days`, which infers truncation from a day
    holding fewer sales than its neighbours. That inference is necessary for
    days collected before the status was written down, but where the status
    exists it is a record rather than a guess -- the walker knew it had not
    reached the date and said so.
    """
    return [dict(r) for r in _rows(conn, """
        SELECT r.target_date AS day,
               COUNT(*) AS bands,
               GROUP_CONCAT(DISTINCT s.query_id) AS queries
        FROM scrape_segments s JOIN scrape_runs r USING (run_id)
        WHERE s.status = 'incomplete' AND r.target_date IS NOT NULL
        GROUP BY r.target_date
        ORDER BY r.target_date DESC
    """)]


def uncredited_sales(conn: sqlite3.Connection) -> int:
    """Sales carrying no query_id, so no search can be credited with them.

    Rows restored from Cloudflare arrive without one -- the flattened upload
    has no column for it. They are real sales and they are in every other
    count; they simply cannot appear in a per-search breakdown, and leaving
    that unsaid makes the percentages look like the whole database.
    """
    return _rows(conn, """
        SELECT COUNT(*) AS n FROM sales
        WHERE query_id IS NULL AND sold_date IS NOT NULL
    """)[0]["n"]


def band_suggestions(worst: list[dict], existing: list) -> list[tuple]:
    """Price bands that would split the ranges eBay refused to show.

    A capped band is a range holding more sales than eBay will page through,
    so the only way to see inside it is to ask for less of it at a time. The
    geometric midpoint is used for the same reason the walker uses it: card
    prices are log-distributed, so the arithmetic middle of $25-$1000 puts
    almost every sale on one side.
    """
    cuts: set[float] = set()
    for seg in worst:
        lo, hi = seg["price_lo"], seg["price_hi"]
        if lo is None or hi is None or lo <= 0:
            # An open-ended band cannot be halved geometrically. Its own edge
            # is still a useful cut: it is where the wall is.
            if hi:
                cuts.add(float(hi))
            continue
        cuts.add(round((float(lo) * float(hi)) ** 0.5, 2))

    edges = sorted({float(e) for band in existing
                    for e in band if e is not None} | cuts)
    if not edges:
        return []

    out: list[tuple] = [(None, edges[0])]
    for a, b in zip(edges, edges[1:]):
        out.append((a, b))
    out.append((edges[-1], None))
    return out


def query_yield(conn: sqlite3.Connection, runs: int = 14) -> list[dict]:
    """How many sales each query found that no other query did.

    Queries may overlap freely -- item_id is the primary key, so a listing
    found twice is stored once -- which makes "items collected" a poor measure
    of whether a query earns its time. What matters is what would be missing
    without it.
    """
    rows = _rows(conn, """
        SELECT query_id, COUNT(*) AS sales
        FROM sales WHERE query_id IS NOT NULL
        GROUP BY query_id ORDER BY sales DESC
    """)
    # `sales.query_id` holds whichever query inserted the row first, so this
    # is already "found here and nowhere earlier" -- the unique contribution,
    # by construction rather than by a second pass over the table.
    return [dict(r) for r in rows]
