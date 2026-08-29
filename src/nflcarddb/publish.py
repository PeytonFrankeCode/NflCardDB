"""Export the SQLite database to static JSON for the GitHub Pages dashboard.

GitHub Pages serves static files only -- no Python, no database. And a browser
page cannot query eBay directly (eBay sends no CORS headers). So the split is:
the scraper runs wherever it can reach eBay, this module flattens the results
into small JSON files, and the site reads those.

Price statistics cover every listing with a published price in USD, best offers
included. On a best offer eBay shows the seller's ask rather than the accepted
amount, so those figures sit above what was actually paid; rows keep their
`best_offer` flag and the dashboard labels them. Non-USD listings are still
excluded, because there is no FX conversion in this project.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import db as store

PUBLISH_VERSION = "publish/1"

# Caps that keep the payload small enough for a static site to load instantly.
MAX_PLAYERS = 250
MAX_SETS = 60
MAX_RECENT = 1500
MAX_DAILY = 400

# Cards published with a price history. Every keyed card would be 35,000 of
# them and most sold once, which is a point rather than a trend -- so this is
# the cards that actually have a history, ordered by how much of one.
MAX_CARDS = 2500

# A card needs at least this many sales before a trend means anything. Two
# points make a line through anything.
MIN_SALES_FOR_TREND = 4

# Rows counted in price statistics: any published price, in a single currency.
#
# Best offers are included by choice. On those, the number eBay publishes is the
# seller's ask and the buyer paid less, so every figure here reads slightly high
# -- knowingly. `best_offer` is still stored per row, so excluding them again is
# a filter change rather than a re-collection.
PRICE_FILTER = "price_cents IS NOT NULL AND currency = 'USD'"


def _median(values: list[int]) -> Optional[float]:
    return round(statistics.median(values) / 100.0, 2) if values else None


def _percentile(values: list[int], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((len(ordered) - 1) * pct)))
    return round(ordered[idx] / 100.0, 2)


def _daily(conn: sqlite3.Connection) -> list[dict]:
    prices: dict[str, list[int]] = {}
    for row in conn.execute(
        f"SELECT sold_date, price_cents FROM sales "
        f"WHERE sold_date IS NOT NULL AND {PRICE_FILTER}"
    ):
        prices.setdefault(row[0], []).append(row[1])

    counts = {
        r[0]: r[1] for r in conn.execute(
            "SELECT sold_date, COUNT(*) FROM sales WHERE sold_date IS NOT NULL "
            "GROUP BY sold_date"
        )
    }

    out = []
    for day in sorted(counts)[-MAX_DAILY:]:
        vals = prices.get(day, [])
        out.append({
            "d": day,
            "n": counts[day],
            "priced": len(vals),
            "median": _median(vals),
            "p90": _percentile(vals, 0.90),
            "total": round(sum(vals) / 100.0, 2) if vals else 0.0,
        })
    return out


def _players(conn: sqlite3.Connection) -> list[dict]:
    prices: dict[str, list[int]] = {}
    meta: dict[str, dict] = {}
    for row in conn.execute(
        f"SELECT c.player, c.team, s.price_cents FROM cards c "
        f"JOIN sales s USING (item_id) "
        f"WHERE c.player IS NOT NULL AND c.confidence >= 0.5 AND s.{PRICE_FILTER}"
    ):
        prices.setdefault(row[0], []).append(row[2])
        meta.setdefault(row[0], {"team": row[1]})

    rows = [
        {
            "player": name,
            "team": meta[name]["team"],
            "n": len(vals),
            "median": _median(vals),
            "max": round(max(vals) / 100.0, 2),
            "total": round(sum(vals) / 100.0, 2),
        }
        for name, vals in prices.items()
    ]
    rows.sort(key=lambda r: (-r["n"], -(r["median"] or 0)))
    return rows[:MAX_PLAYERS]


def _sets(conn: sqlite3.Connection) -> list[dict]:
    prices: dict[str, list[int]] = {}
    for row in conn.execute(
        f"SELECT c.set_name, s.price_cents FROM cards c JOIN sales s USING (item_id) "
        f"WHERE c.set_name IS NOT NULL AND s.{PRICE_FILTER}"
    ):
        prices.setdefault(row[0], []).append(row[1])

    rows = [
        {"set": name, "n": len(vals), "median": _median(vals)}
        for name, vals in prices.items()
    ]
    rows.sort(key=lambda r: -r["n"])
    return rows[:MAX_SETS]


def _grades(conn: sqlite3.Connection) -> list[dict]:
    prices: dict[str, list[int]] = {}
    for row in conn.execute(
        f"SELECT c.grader, c.grade, s.price_cents FROM cards c "
        f"JOIN sales s USING (item_id) WHERE s.{PRICE_FILTER}"
    ):
        label = f"{row[0]} {row[1]:g}" if row[0] and row[1] is not None else (row[0] or "Raw")
        prices.setdefault(label, []).append(row[2])

    rows = [
        {"grade": label, "n": len(vals), "median": _median(vals)}
        for label, vals in prices.items()
    ]
    rows.sort(key=lambda r: -r["n"])
    return rows[:24]


def _recent(conn: sqlite3.Connection) -> list[dict]:
    rows = []
    for r in conn.execute(
        "SELECT s.item_id, s.sold_date, s.title, s.price_cents, s.currency, "
        "       s.best_offer, s.bids, s.image_url, c.player, c.team, c.year, "
        "       c.set_name, c.parallel, c.grader, c.grade, c.confidence "
        "FROM sales s LEFT JOIN cards c USING (item_id) "
        "WHERE s.sold_date IS NOT NULL "
        "ORDER BY s.sold_date DESC, s.price_cents DESC LIMIT ?",
        (MAX_RECENT,),
    ):
        rows.append({
            "id": r["item_id"],
            "d": r["sold_date"],
            "t": r["title"],
            "p": round(r["price_cents"] / 100.0, 2) if r["price_cents"] else None,
            "cur": r["currency"],
            "bo": r["best_offer"],
            "img": r["image_url"],
            "player": r["player"],
            "team": r["team"],
            "yr": r["year"],
            "set": r["set_name"],
            "par": r["parallel"],
            "g": (f"{r['grader']} {r['grade']:g}" if r["grader"] and r["grade"] is not None
                  else (r["grader"] or None)),
            "conf": r["confidence"],
        })
    return rows


def card_histories(conn: sqlite3.Connection, limit: int = MAX_CARDS) -> list[dict]:
    """One entry per physical card, with its sales over time.

    This is what the whole identity layer was built for. The database has
    grouped sales under a shared `card_key` since the beginning, and nothing
    ever published it -- so the dashboard could show what sold yesterday and
    which players are hot, but never "this card, over time", which is the only
    thing that makes a price a market rather than an anecdote.

    Every sale carries its own grade label rather than being split into a
    separate card by it. A PSA 10 and a raw copy are the same cardboard and
    different market items, so the page filters the line down to one grade --
    but keying on grade would hide the fact that they are one card at all.
    """
    by_card: dict[str, dict] = {}
    for r in conn.execute(
        f"SELECT c.card_key, c.card_name, c.player, c.year, c.set_name, "
        f"       c.card_number, c.grader, c.grade, s.sold_date, s.price_cents, "
        f"       s.image_url "
        f"FROM cards c JOIN sales s USING (item_id) "
        f"WHERE c.card_key IS NOT NULL AND s.sold_date IS NOT NULL "
        f"  AND s.{PRICE_FILTER} "
        # item_id breaks ties within a day. Without it two sales on the same
        # date come back in whatever order SQLite chooses, and the trend --
        # which compares the older half against the newer -- would change
        # between publishes of an unchanged database.
        f"ORDER BY s.sold_date, s.item_id"
    ):
        card = by_card.setdefault(r["card_key"], {
            "key": r["card_key"],
            "player": r["player"],
            "year": r["year"],
            "set": r["set_name"],
            "img": None,
            # No card number anywhere in the group means the key fell back to
            # the player's name, so this is not one card -- it is everything of
            # that player in that set whose number could not be read. Flagged
            # rather than dropped: the sales are real, but a price history
            # drawn across them is not.
            "nonum": 1,
            "sales": [],
            "names": Counter(),
        })
        card["names"][r["card_name"]] += 1
        if r["card_number"]:
            card["nonum"] = 0
        if card["img"] is None and r["image_url"]:
            card["img"] = r["image_url"]
        grade = (f"{r['grader']} {r['grade']:g}"
                 if r["grader"] and r["grade"] is not None
                 else (r["grader"] or "Raw"))
        card["sales"].append([r["sold_date"], round(r["price_cents"] / 100.0, 2),
                              grade])

    out = []
    for card in by_card.values():
        sales = card["sales"]
        if len(sales) < 2:
            # One sale is a price, not a history. Publishing it as a chart
            # would draw a trend line through a single point.
            continue
        # The name most of the group agrees on, not whichever sale came first.
        # card_name carries claimed-but-unkeyed words like "Rookie Card", so
        # members of one group spell themselves differently; picking the first
        # made a card's name depend on which day it happened to sell. Ties go
        # to the shortest, which is the spelling carrying the fewest of those
        # stray words.
        names = card.pop("names")
        card["name"] = min(
            (n for n in names if n),
            key=lambda n: (-names[n], len(n), n),
            default=card["key"],
        )
        prices = [s[1] for s in sales]
        card["n"] = len(sales)
        card["median"] = round(statistics.median(prices), 2)
        card["low"] = min(prices)
        card["high"] = max(prices)
        card["first"] = sales[0][0]
        card["last"] = sales[-1][0]
        card["trend"] = price_trend(prices)
        out.append(card)

    # Most sales first: those are the cards with something to say.
    out.sort(key=lambda c: (-c["n"], -(c["median"] or 0)))
    return out[:limit]


def price_trend(prices: list[float]) -> Optional[float]:
    """Percent change from the older half of a card's sales to the newer.

    Halves rather than first-versus-last, because a single unusual sale at
    either end would otherwise be the entire trend. Below a handful of sales
    no number is reported at all -- two points make a line through anything.
    """
    if len(prices) < MIN_SALES_FOR_TREND:
        return None
    middle = len(prices) // 2
    older = statistics.median(prices[:middle])
    newer = statistics.median(prices[middle:])
    if not older:
        return None
    return round(100.0 * (newer - older) / older, 1)


def _top(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    """The biggest sales across everything collected.

    Includes best offers, whose price is the seller's ask rather than what was
    paid -- so they rank higher than they earned. `bo` marks them, and the
    dashboard labels them, because a leaderboard is exactly where an ask gets
    read as a sale.
    """
    rows = []
    for r in conn.execute(
        f"SELECT s.item_id, s.sold_date, s.title, s.price_cents, s.best_offer, "
        f"       s.image_url, c.player, c.year, c.set_name, c.grader, c.grade "
        f"FROM sales s LEFT JOIN cards c USING (item_id) "
        f"WHERE s.sold_date IS NOT NULL AND {PRICE_FILTER} "
        f"ORDER BY s.price_cents DESC LIMIT ?",
        (limit,),
    ):
        rows.append({
            "id": r["item_id"],
            "d": r["sold_date"],
            "t": r["title"],
            "p": round(r["price_cents"] / 100.0, 2),
            "bo": r["best_offer"],
            "img": r["image_url"],
            "player": r["player"],
            "yr": r["year"],
            "set": r["set_name"],
            "g": (f"{r['grader']} {r['grade']:g}" if r["grader"] and r["grade"] is not None
                  else (r["grader"] or None)),
        })
    return rows


def _meta(conn: sqlite3.Connection, daily: list[dict]) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    best_offers = conn.execute("SELECT COUNT(*) FROM sales WHERE best_offer = 1").fetchone()[0]
    non_usd = conn.execute("SELECT COUNT(*) FROM sales WHERE currency != 'USD'").fetchone()[0]
    confident = conn.execute("SELECT COUNT(*) FROM cards WHERE confidence >= 0.5").fetchone()[0]

    prices = [
        r[0] for r in conn.execute(f"SELECT price_cents FROM sales WHERE {PRICE_FILTER}")
    ]

    last_run = conn.execute(
        "SELECT run_id, target_date, status, finished_at, items_seen, items_new, "
        "       pages_fetched, error FROM scrape_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()

    latest = daily[-1] if daily else None
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "publish_version": PUBLISH_VERSION,
        "total_sales": total,
        "priced_sales": len(prices),
        "best_offer_sales": best_offers,
        "non_usd_sales": non_usd,
        "confident_parses": confident,
        "days_covered": len(daily),
        "date_min": daily[0]["d"] if daily else None,
        "date_max": daily[-1]["d"] if daily else None,
        "latest_day_sales": latest["n"] if latest else 0,
        "median_price": _median(prices),
        "p90_price": _percentile(prices, 0.90),
        "last_run": dict(last_run) if last_run else None,
    }


def publish(db_path: str | Path, out_dir: str | Path) -> dict:
    """Write the dashboard's JSON files. Returns the meta payload."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = store.connect(db_path)
    try:
        daily = _daily(conn)
        payloads = {
            "daily.json": daily,
            "players.json": _players(conn),
            "sets.json": _sets(conn),
            "grades.json": _grades(conn),
            "recent.json": _recent(conn),
            # recent.json is capped at MAX_RECENT rows ordered newest-first, and
            # one day is ~23,000 sales -- so it never reaches beyond the latest
            # day. The biggest sales of the window would be invisible without
            # their own file.
            "top.json": _top(conn),
            "cards.json": card_histories(conn),
        }
        meta = _meta(conn, daily)
        payloads["meta.json"] = meta
    finally:
        conn.close()

    for name, payload in payloads.items():
        (out_dir / name).write_text(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )
    return meta
