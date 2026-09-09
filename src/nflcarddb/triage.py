"""What is stopping cards from being usable, and what could actually fix it.

The quality tiers say which cards are trustworthy. They do not say *why* the
rest are not, and the why decides what is worth building next -- the answers
point at completely different work:

* A card in `bucket` has no card number. Nothing about it can be improved by
  looking harder at the sales it already has; the number has to come from
  somewhere else. A graded slab carries it on the label, which is a photo away.
  A raw card usually does not, which is a person away.

* A card in `suspect` has a number and prices that disagree inside one grade,
  which usually means two cards share a key. That is a splitting problem, and
  photos help by showing that two visibly different cards are filed together.

* A card in `unproven` is only new. Nothing is wrong with it and nothing needs
  doing; it becomes `clean` on its own as it sells.

Counting those three separately is the difference between "photos would fix
most of this" and "photos would fix four percent of this", and that is not a
thing to guess at.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from .publish import JUNK_PRICE

# A group this size is worth a person's attention: fewer sales than this and
# fixing it by hand buys a price history nobody would plot anyway.
WORTH_REVIEWING = 3


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, params).fetchall()


def triage(conn: sqlite3.Connection, min_sales: int = WORTH_REVIEWING) -> dict:
    """Count the fixable cards by what would actually fix them.

    Everything here is counted over card GROUPS rather than sales, because the
    work is per group: one decision about "2025 Prizm Caleb Williams" settles
    however many sales are under it.
    """
    # One pass. The alternative is six queries over the same join, and this
    # runs against a table with a sale per row and hundreds of thousands of
    # them.
    groups: dict[str, dict] = {}
    for r in _rows(conn, """
        SELECT c.card_key, c.card_number, c.grader, c.is_graded,
               s.image_url, s.price_cents
        FROM cards c JOIN sales s USING (item_id)
        WHERE c.card_key IS NOT NULL AND s.sold_date IS NOT NULL
    """):
        g = groups.setdefault(r["card_key"], {
            "sales": 0, "numbered": 0, "graded": 0, "photos": 0,
            "graded_with_photo": 0, "prices": [],
        })
        g["sales"] += 1
        if r["card_number"]:
            g["numbered"] += 1
        graded = bool(r["grader"] or r["is_graded"])
        has_photo = bool(r["image_url"])
        if graded:
            g["graded"] += 1
        if has_photo:
            g["photos"] += 1
        if graded and has_photo:
            # The one combination a machine can act on unaided: the grader
            # printed the card number on the label, and the label is in the
            # photo.
            g["graded_with_photo"] += 1
        if r["price_cents"] and r["price_cents"] >= JUNK_PRICE * 100:
            g["prices"].append(r["price_cents"])

    buckets = [g for g in groups.values() if not g["numbered"]]
    big = [g for g in buckets if g["sales"] >= min_sales]

    # Split by what could supply the missing number. A bucket group with a
    # graded sale in it has the number sitting in a photo; one without has
    # nowhere to get it but a person.
    ocr_reachable = [g for g in big if g["graded_with_photo"]]
    needs_a_person = [g for g in big if not g["graded_with_photo"]]
    # And of those, the ones where a person would at least have something to
    # look at.
    reviewable = [g for g in needs_a_person if g["photos"]]

    def summarise(rows: list[dict]) -> dict:
        return {"groups": len(rows), "sales": sum(g["sales"] for g in rows)}

    return {
        "cards": len(groups),
        "sales": sum(g["sales"] for g in groups.values()),
        "min_sales": min_sales,
        "numberless": summarise(buckets),
        "numberless_worth_fixing": summarise(big),
        "a_photo_could_read_it": summarise(ocr_reachable),
        "only_a_person_could": summarise(needs_a_person),
        "and_has_a_photo_to_look_at": summarise(reviewable),
        "photo_coverage": _coverage(conn),
    }


def _coverage(conn: sqlite3.Connection) -> dict:
    """How many sales carry a photo at all, and how many are graded.

    Both matter before building anything on photos: eBay drops the image about
    ninety days after the sale, so an old row's `image_url` points at nothing.
    """
    r = _rows(conn, """
        SELECT COUNT(*) AS sales,
               SUM(CASE WHEN s.image_url IS NOT NULL THEN 1 ELSE 0 END) AS with_photo,
               SUM(CASE WHEN c.grader IS NOT NULL OR c.is_graded = 1
                        THEN 1 ELSE 0 END) AS graded,
               SUM(CASE WHEN s.image_url IS NOT NULL
                         AND (c.grader IS NOT NULL OR c.is_graded = 1)
                        THEN 1 ELSE 0 END) AS graded_with_photo
        FROM sales s LEFT JOIN cards c USING (item_id)
        WHERE s.sold_date IS NOT NULL
    """)[0]
    return dict(r)


def review_queue(conn: sqlite3.Connection, limit: int = 50,
                 min_sales: int = WORTH_REVIEWING) -> list[dict]:
    """The numberless groups worth a person's time, biggest first.

    Ordered by sales rather than by price: a group of forty sales is forty
    prices filed under a name that is not a card, and settling it is worth
    forty times what settling a group of one is.
    """
    out = []
    for r in _rows(conn, """
        SELECT c.card_key,
               COUNT(*) AS sales,
               MIN(s.sold_date) AS first_sold,
               MAX(s.sold_date) AS last_sold,
               SUM(CASE WHEN s.image_url IS NOT NULL THEN 1 ELSE 0 END) AS photos
        FROM cards c JOIN sales s USING (item_id)
        WHERE c.card_key IS NOT NULL AND s.sold_date IS NOT NULL
        GROUP BY c.card_key
        HAVING SUM(CASE WHEN c.card_number IS NOT NULL THEN 1 ELSE 0 END) = 0
           AND COUNT(*) >= ?
        ORDER BY sales DESC
        LIMIT ?
    """, (min_sales, limit)):
        group = dict(r)
        # DISTINCT, because a group's sales are often worded identically and
        # four copies of one title tells a reviewer nothing. Where the titles
        # differ is exactly where the missing card number tends to be hiding.
        group["titles"] = [t[0] for t in conn.execute(
            "SELECT DISTINCT s.title FROM cards c JOIN sales s USING (item_id) "
            "WHERE c.card_key = ? LIMIT 4",
            (r["card_key"],)
        )]
        out.append(group)
    return out
