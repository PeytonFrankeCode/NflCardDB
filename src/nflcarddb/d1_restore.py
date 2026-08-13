"""Rebuild the local database from what was uploaded to Cloudflare D1.

The SQLite file is gitignored -- it is working data, not a project file -- so a
wiped PC does not get it back from GitHub. What does survive is D1: the upload
is a full copy of every sale, sitting on Cloudflare, unaffected by anything that
happens to the machine that produced it.

This is not a perfect inverse of the export. D1 holds the flattened public view,
so three things do not come back:

* per-run history (which run collected what, pages fetched, errors),
* the fields the API never served -- condition, seller, query id,
* anything collected but never uploaded.

The parsed card attributes are recovered by re-running the title parser rather
than by reading D1's copies, which is both cleaner and better: the parser has
improved since some of those rows were written.
"""

from __future__ import annotations

import logging
from typing import Iterator, Optional

from . import db as store
from .d1_http import D1Error, run_sql
from .models import Sale
from .parse_title import PARSER_VERSION as TITLE_PARSER_VERSION
from .parse_title import load_roster, parse_title

log = logging.getLogger(__name__)

# D1 caps how much one response may carry, and a day is ~23,000 rows, so the
# table comes back a page at a time.
PAGE_SIZE = 2_000


def count_rows(account_id: str, database_id: str, token: str) -> int:
    out = run_sql(account_id, database_id, token,
                  "SELECT COUNT(*) AS n FROM sales;")
    rows = (out.get("result") or [{}])[0].get("results") or [{}]
    return int(rows[0].get("n", 0)) if rows else 0


def fetch_pages(
    account_id: str,
    database_id: str,
    token: str,
    page_size: int = PAGE_SIZE,
    since: Optional[str] = None,
) -> Iterator[list[dict]]:
    """Yield the sales table in ordered pages.

    Ordered by item_id rather than date so the paging is stable: an ORDER BY on
    a non-unique column can repeat or skip rows across LIMIT/OFFSET boundaries.
    """
    where = f"WHERE sold_date >= '{since}' " if since else ""
    offset = 0
    while True:
        out = run_sql(
            account_id, database_id, token,
            f"SELECT * FROM sales {where}ORDER BY item_id "
            f"LIMIT {page_size} OFFSET {offset};",
        )
        rows = (out.get("result") or [{}])[0].get("results") or []
        if not rows:
            return
        yield rows
        if len(rows) < page_size:
            return
        offset += page_size


def _to_sale(row: dict) -> Sale:
    return Sale(
        item_id=str(row.get("item_id")),
        title=row.get("title") or "",
        price_cents=row.get("price_cents"),
        currency=row.get("currency") or "USD",
        shipping_cents=row.get("shipping_cents"),
        sold_date=row.get("sold_date"),
        listing_format=row.get("listing_format") or "unknown",
        bids=row.get("bids"),
        best_offer=bool(row.get("best_offer")),
        url=f"https://www.ebay.com/itm/{row.get('item_id')}",
        image_url=row.get("image_url"),
    )


def restore(
    account_id: str,
    database_id: str,
    token: str,
    db_path: str,
    roster_path: Optional[str] = None,
    since: Optional[str] = None,
    on_progress=None,
) -> dict:
    """Pull every sale out of D1 and rebuild the local database.

    Safe to run against an existing database: item_id is the primary key
    throughout, so this merges rather than replacing. A machine that still has
    some data keeps it and gains whatever D1 has that it lacks.
    """
    conn = store.connect(db_path)
    roster = load_roster(roster_path) if roster_path else None
    restored = 0
    days: set[str] = set()

    try:
        for page in fetch_pages(account_id, database_id, token, since=since):
            sales = [_to_sale(r) for r in page if r.get("item_id")]
            if not sales:
                continue

            # One synthetic run per restore batch: the real run history is not
            # in D1 and inventing per-day detail would be fiction.
            run_id = store.start_run(conn, None)
            store.upsert_sales(conn, sales, run_id)
            store.upsert_cards(
                conn,
                [(s.item_id, parse_title(s.title, roster)) for s in sales],
                TITLE_PARSER_VERSION,
            )
            store.finish_run(conn, run_id, "ok", 0, len(sales), len(sales))

            restored += len(sales)
            days.update(s.sold_date for s in sales if s.sold_date)
            if on_progress:
                on_progress(restored)

        # Days present in the restored data are marked collected, so the
        # backfill does not immediately re-fetch everything that was just
        # downloaded. `nflcarddb recheck` still finds any that came back thin,
        # which is the right way to catch days that were incomplete before.
        for day in sorted(days):
            run_id = store.start_run(conn, day)
            store.finish_run(conn, run_id, "ok", 0, 0, 0)

        return {
            "sales_restored": restored,
            "days": len(days),
            "first_day": min(days) if days else None,
            "last_day": max(days) if days else None,
        }
    finally:
        conn.close()
