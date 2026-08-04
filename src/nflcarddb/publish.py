"""Export the SQLite database to static JSON for the GitHub Pages dashboard.

GitHub Pages serves static files only -- no Python, no database. And a browser
page cannot query eBay directly (eBay sends no CORS headers). So the split is:
the scraper runs wherever it can reach eBay, this module flattens the results
into small JSON files, and the site reads those.

Price statistics deliberately exclude two things:
  * best-offer rows, where eBay shows the asking price rather than the accepted
    one, so the number is not a sale price at all;
  * non-USD listings, since there is no FX conversion in this project.
Volume counts include everything, so the two never silently disagree -- the
dashboard states which is which.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
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

# Rows usable as prices: a real accepted amount, in a single currency.
PRICE_FILTER = "price_cents IS NOT NULL AND best_offer = 0 AND currency = 'USD'"


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
