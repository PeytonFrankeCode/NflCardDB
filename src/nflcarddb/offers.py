"""Look up the accepted price of best-offer sales, one item page each.

**The cost decides the design.** A best offer's accepted price is only on its
own item page, so every lookup is a full page request at the collector's pace
-- three to four seconds. Best offers are close to half of all sales, several
thousand a day across three sports, which is more hours than a night has. So
this does not try to finish. It works through the ones where the ask is most
wrong in dollars first, for as long as it is given, and picks up where it
stopped the next night:

* **Largest ask first.** A best offer typically closes ten to twenty percent
  under the ask. On a $400 card that is $60 of error in a median; on a $4 card
  it is sixty cents. The same page request buys a hundred times more
  correction at the top of the list.
* **Cards the site shows, before sales that never formed a card.** A sale
  with no card_key is in no price history, so correcting it changes nothing
  anyone sees.
* **Inside eBay's window.** Item pages disappear about ninety days after the
  sale; asking for an older one is a request spent on "gone".

**What it changes.** On a confirmed reading the accepted amount replaces the
ask in `price_cents` and the ask moves to `ask_cents`. Every median, chart,
rollup and upload reads `price_cents`, so all of them are corrected without
any of them changing. A routine re-scrape cannot put the ask back -- the
upsert protects a confirmed price. And `updated_at` moves, so the next upload
carries the correction to Cloudflare.

**What stops it.** A time limit, the page budget, a bot check or a sign-out
(exactly as for collection), the connection going down, and one more: if the
first pages it reads find no accepted price at all, the page is not shaped the
way the parser expects, and it stops rather than spend the night confirming
that.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from . import db as store
from .fetch import BlockedError, FetchError, SignedOutError
from .item_page import OfferReading, read_offer

log = logging.getLogger(__name__)

# eBay keeps a sold listing's page for about ninety days. A few days short of
# that, so a lookup is not spent on a page that disappears overnight.
LOOKUP_WINDOW_DAYS = 85

# Pages read, with not one accepted price among them, before concluding the
# markup is not what the parser expects. Among the highest asks there should
# be several best offers with the amount shown, if eBay shows it at all.
MARKUP_GUARD_PAGES = 25

# Failed page loads in a row before the connection is treated as down.
CONNECTION_DOWN_AFTER = 3

# A page that would not load is tried again, but not on the same night.
ERROR_RETRY_AFTER = timedelta(days=1)


@dataclass
class OfferReport:
    looked_up: int = 0
    found: int = 0
    not_shown: int = 0
    ambiguous: int = 0
    gone: int = 0
    errors: int = 0
    seconds: float = 0.0
    stopped: Optional[str] = None      # time | budget | blocked | signed_out |
    error: Optional[str] = None        # network | markup | None when it ran out
    asked_cents: int = 0               # summed over the found ones only
    accepted_cents: int = 0
    pending_before: int = 0

    @property
    def discount(self) -> Optional[float]:
        """How far under the ask the confirmed offers closed, on average."""
        if not self.asked_cents:
            return None
        return 1.0 - self.accepted_cents / self.asked_cents

    def as_dict(self) -> dict:
        return {
            "looked_up": self.looked_up, "found": self.found,
            "not_shown": self.not_shown, "ambiguous": self.ambiguous,
            "gone": self.gone, "errors": self.errors,
            "seconds": round(self.seconds, 1),
            "average_discount": (round(self.discount, 3)
                                 if self.discount is not None else None),
            "pending_before": self.pending_before,
            "stopped": self.stopped, "error": self.error,
        }


def _cutoff(today: Optional[date]) -> str:
    return ((today or date.today()) - timedelta(days=LOOKUP_WINDOW_DAYS)).isoformat()


def pending(conn, min_ask_cents: int, limit: Optional[int] = None,
            today: Optional[date] = None, retry: tuple[str, ...] = ()) -> list[dict]:
    """Best-offer sales still carrying their ask, most worth correcting first.

    A page that would not load is tried again -- but not the same night, or a
    permanently broken high-ask listing would be the first thing every run
    spent its time on.
    """
    retry_after = (datetime.now(timezone.utc) - ERROR_RETRY_AFTER).isoformat(
        timespec="milliseconds")
    status_clause = ("s.offer_status IS NULL "
                     "OR (s.offer_status = 'error' AND s.offer_checked_at < ?)")
    params: list = [retry_after]
    if retry:
        status_clause += f" OR s.offer_status IN ({','.join('?' * len(retry))})"
        params.extend(retry)

    sql = f"""
        SELECT s.item_id,
               COALESCE(s.url, 'https://www.ebay.com/itm/' || s.item_id) AS url,
               s.price_cents, s.sold_date, c.card_key
        FROM sales s LEFT JOIN cards c USING (item_id)
        WHERE s.best_offer = 1
          AND s.currency = 'USD'
          AND s.price_cents >= ?
          AND s.sold_date >= ?
          AND ({status_clause})
        ORDER BY (c.card_key IS NOT NULL) DESC, s.price_cents DESC, s.item_id
    """
    args = [min_ask_cents, _cutoff(today), *params]
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def record(conn, item_id: str, reading: OfferReading) -> None:
    """Write one reading. Only a confirmed one touches the price."""
    now = store.utcnow()
    if reading.status == "found" and reading.accepted_cents:
        conn.execute(
            "UPDATE sales SET ask_cents = COALESCE(ask_cents, price_cents), "
            "price_cents = ?, offer_status = 'found', offer_checked_at = ?, "
            # Moved so the incremental upload picks the row up: a corrected
            # price that never reaches Cloudflare corrects nothing the website
            # shows.
            "updated_at = ? "
            "WHERE item_id = ? AND COALESCE(offer_status, '') != 'found'",
            (reading.accepted_cents, now, now, item_id),
        )
    else:
        conn.execute(
            "UPDATE sales SET offer_status = ?, offer_checked_at = ? "
            "WHERE item_id = ? AND COALESCE(offer_status, '') != 'found'",
            (reading.status, now, item_id),
        )
    conn.commit()


def run_offer_lookup(
    fetcher,
    db_path: str,
    minutes: float,
    min_ask_cents: int,
    limit: Optional[int] = None,
    retry: tuple[str, ...] = (),
    today: Optional[date] = None,
    clock: Callable[[], float] = time.monotonic,
    on_item: Optional[Callable[[dict, OfferReading], None]] = None,
) -> OfferReport:
    report = OfferReport()
    started = clock()
    deadline = started + minutes * 60 if minutes else None

    conn = store.connect(db_path)
    try:
        queue = pending(conn, min_ask_cents, limit=limit, today=today, retry=retry)
        report.pending_before = len(queue)
        failures_in_a_row = 0

        for item in queue:
            if deadline is not None and clock() >= deadline:
                report.stopped = "time"
                break
            if fetcher.budget_exhausted():
                report.stopped = "budget"
                break

            try:
                html = fetcher.get(item["url"], label=f"offer_{item['item_id']}")
            except (BlockedError, SignedOutError) as exc:
                # Every later page fails the same way. Stop, as collection does.
                report.stopped = ("signed_out" if isinstance(exc, SignedOutError)
                                  else "blocked")
                report.error = str(exc)
                break
            except FetchError as exc:
                failures_in_a_row += 1
                report.errors += 1
                record(conn, item["item_id"], OfferReading("error", reason=str(exc)))
                if failures_in_a_row >= CONNECTION_DOWN_AFTER:
                    report.stopped = "network"
                    report.error = (f"{failures_in_a_row} item pages in a row "
                                    "would not load -- the connection looks down")
                    break
                continue
            failures_in_a_row = 0

            reading = read_offer(html, ask_cents=item["price_cents"])
            record(conn, item["item_id"], reading)
            report.looked_up += 1
            if reading.status == "found":
                report.found += 1
                report.asked_cents += item["price_cents"] or 0
                report.accepted_cents += reading.accepted_cents or 0
            elif reading.status == "not_shown":
                report.not_shown += 1
            elif reading.status == "ambiguous":
                report.ambiguous += 1
            elif reading.status == "gone":
                report.gone += 1
            if on_item:
                on_item(item, reading)

            # The first pages are a test of the markup as much as a lookup. If
            # none of them shows an accepted price the parser recognises, the
            # rest will not either; say so now rather than at dawn.
            live = report.looked_up - report.gone
            if live >= MARKUP_GUARD_PAGES and report.found == 0:
                report.stopped = "markup"
                report.error = (
                    f"{live} item pages read and not one showed an accepted "
                    "offer this recognises. eBay's page is probably laid out "
                    "differently than expected -- save one sold best-offer "
                    "listing and drag it onto offer-check.bat."
                )
                break
    finally:
        report.seconds = clock() - started
        conn.close()
    return report


def labelling_by_week(conn, weeks: int = 8) -> list[dict]:
    """The share of sales flagged as best offers, week by week.

    Worth watching because the lookup only ever visits sales flagged as best
    offers. Sellers have reported eBay showing accepted-offer sales at the full
    ask with no label at all; if that happened, the share would fall off a
    cliff in one week, and those sales would never be looked up.
    """
    return [dict(r) for r in conn.execute("""
        SELECT strftime('%Y-W%W', sold_date) AS week,
               COUNT(*) AS sales,
               SUM(best_offer) AS best_offers,
               ROUND(100.0 * SUM(best_offer) / COUNT(*), 1) AS pct
        FROM sales
        WHERE sold_date IS NOT NULL
        GROUP BY week ORDER BY week DESC LIMIT ?
    """, (weeks,))]


def backlog(conn, min_ask_cents: int, today: Optional[date] = None) -> dict:
    """What is left to look up, and what has already been learned."""
    row = conn.execute("""
        SELECT COUNT(*) AS pending,
               SUM(CASE WHEN s.offer_status = 'found' THEN 1 ELSE 0 END) AS found
        FROM sales s
        WHERE s.best_offer = 1 AND s.currency = 'USD' AND s.sold_date >= ?
          AND s.price_cents >= ?
          AND (s.offer_status IS NULL OR s.offer_status = 'found')
    """, (_cutoff(today), min_ask_cents)).fetchone()
    found = row["found"] or 0
    return {"pending": (row["pending"] or 0) - found, "found": found}
