"""Export the local database into SQL that Cloudflare D1 can load.

Two things this deliberately does differently from the local schema:

* The sales/cards join is flattened. The API only reads, D1 bills by rows
  scanned, and joining on every request buys nothing that can be done once here.
* Best-offer prices become NULL rather than the asking price. eBay publishes the
  seller's ask on those, not what the buyer paid, so serving it as `price` would
  be publishing a wrong number. NULL cannot be averaged by accident.

API keys are generated here and only the SHA-256 hash is exported, so the
database never contains a working credential -- not even in the file that
creates it.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from . import db as store
from .publish import price_trend

# D1 rejects very large statements, so rows go out in batches.
ROWS_PER_INSERT = 200

EXPORT_COLUMNS = (
    "item_id", "sold_date", "title", "price_cents", "ask_cents", "shipping_cents",
    "currency", "best_offer", "listing_format", "bids", "image_url", "player",
    "team", "year", "brand", "set_name", "subset", "parallel", "card_number",
    "print_run", "grader", "grade", "is_rookie", "is_auto", "is_relic",
    "confidence", "card_key", "card_name",
)


def new_api_key(prefix: str = "nfl") -> tuple[str, str]:
    """Return (key, sha256). The key is shown once and never stored."""
    key = f"{prefix}_{secrets.token_urlsafe(32)}"
    return (key, hashlib.sha256(key.encode()).hexdigest())


def _sql_literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _rows_to_export(
    conn: sqlite3.Connection,
    since: Optional[str],
    changed_since: Optional[str] = None,
) -> list[sqlite3.Row]:
    """Rows for the export.

    `since` filters by sale date -- "only recent days". `changed_since` filters
    by when the row was last written, which is the one that makes an upload
    incremental: it catches new sales and re-collected ones alike, and skips
    everything already sent. Sending the whole table is fine at 20,000 rows and
    unusable at a million.
    """
    where = "WHERE s.sold_date IS NOT NULL"
    params: tuple = ()
    if since:
        where += " AND s.sold_date >= ?"
        params = (since,)
    if changed_since:
        where += " AND s.updated_at > ?"
        params = (*params, changed_since)

    return conn.execute(
        f"""
        SELECT s.item_id, s.sold_date, s.title,
               -- Every row carries the price eBay published. On a best offer
               -- that is the seller's ask and the buyer paid less, so this
               -- column mixes two things by deliberate choice; `best_offer`
               -- marks which is which.
               s.price_cents,
               -- Unchanged: set only on best-offer rows, so a caller who wants
               -- accepted-offer prices out can still filter on it alone.
               CASE WHEN s.best_offer = 1 THEN s.price_cents ELSE NULL END AS ask_cents,
               s.shipping_cents, s.currency, s.best_offer, s.listing_format, s.bids,
               s.image_url,
               c.player, c.team, c.year, c.brand, c.set_name, c.subset,
               c.parallel, c.card_number, c.print_run, c.grader, c.grade,
               c.is_rookie, c.is_auto, c.is_relic,
               COALESCE(c.confidence, 0) AS confidence,
               c.card_key, c.card_name
        FROM sales s LEFT JOIN cards c USING (item_id)
        {where}
        ORDER BY s.sold_date
        """,
        params,
    ).fetchall()


def _daily_rollups(conn: sqlite3.Connection, since: Optional[str]) -> list[dict]:
    where = "WHERE sold_date IS NOT NULL"
    params: tuple = ()
    if since:
        where += " AND sold_date >= ?"
        params = (since,)

    counts = {
        r[0]: r[1] for r in conn.execute(
            f"SELECT sold_date, COUNT(*) FROM sales {where} GROUP BY sold_date", params
        )
    }
    prices: dict[str, list[int]] = {}
    for day, cents in conn.execute(
        f"SELECT sold_date, price_cents FROM sales {where} "
        f"AND price_cents IS NOT NULL AND currency = 'USD'",
        params,
    ):
        prices.setdefault(day, []).append(cents)

    out = []
    for day in sorted(counts):
        vals = sorted(prices.get(day, []))
        median = int(statistics.median(vals)) if vals else None
        p90 = vals[min(len(vals) - 1, int(round((len(vals) - 1) * 0.9)))] if vals else None
        out.append({
            "sold_date": day,
            "sales": counts[day],
            "priced": len(vals),
            "median_cents": median,
            "p90_cents": p90,
            "total_cents": sum(vals) if vals else None,
        })
    return out


CARD_COLUMNS = (
    "card_key", "card_name", "player", "team", "year", "brand", "set_name",
    "subset", "parallel", "card_number", "print_run", "is_rookie", "is_auto",
    "is_relic", "numberless", "image_url", "sales", "median_cents",
    "low_cents", "high_cents", "raw_sales", "raw_median_cents",
    "first_sold", "last_sold", "trend_pct",
)

GRADE_COLUMNS = ("card_key", "grade_label", "sales", "median_cents",
                 "low_cents", "high_cents", "last_sold")


def _card_rollups(
    conn: sqlite3.Connection, keys: Optional[set[str]] = None,
) -> tuple[list[dict], list[dict]]:
    """One row per physical card, plus one row per card per grade.

    Computed here rather than in the Worker because the sort orders a browsing
    site offers -- biggest riser, most traded, highest value -- each need a
    number derived from every sale of every card. D1 bills by rows scanned, so
    deriving them per request means re-reading the whole table once per visitor
    per sort order. Locally they cost one pass.

    `keys` limits which cards are rebuilt, but never which sales are read: a
    card whose stats are recomputed is recomputed from all of its sales, not
    just the new ones. A median over "the rows that changed today" would be a
    different and meaningless number.
    """
    where = "WHERE c.card_key IS NOT NULL AND s.sold_date IS NOT NULL " \
            "AND s.price_cents IS NOT NULL AND s.currency = 'USD'"
    params: tuple = ()
    if keys is not None:
        if not keys:
            return ([], [])
        where += f" AND c.card_key IN ({','.join('?' * len(keys))})"
        params = tuple(keys)

    grouped: dict[str, dict] = {}
    for r in conn.execute(
        f"""
        SELECT c.card_key, c.card_name, c.player, c.team, c.year, c.brand,
               c.set_name, c.subset, c.parallel, c.card_number, c.print_run,
               c.is_rookie, c.is_auto, c.is_relic, c.grader, c.grade,
               s.sold_date, s.price_cents, s.image_url
        FROM cards c JOIN sales s USING (item_id)
        {where}
        ORDER BY s.sold_date, s.item_id
        """,
        params,
    ):
        card = grouped.setdefault(r["card_key"], {
            "card_key": r["card_key"], "names": Counter(), "prices": [],
            "by_grade": {}, "image_url": None, "numberless": 1,
        })
        card["names"][r["card_name"]] += 1
        if r["card_number"]:
            card["numberless"] = 0
        if card["image_url"] is None and r["image_url"]:
            card["image_url"] = r["image_url"]
        # Last writer wins on the descriptive columns, and they are read from
        # the newest sale because the parser improves: an older row was keyed
        # by a version that knew fewer set and colour names.
        for col in ("player", "team", "year", "brand", "set_name", "subset",
                    "parallel", "card_number", "print_run"):
            if r[col] is not None:
                card[col] = r[col]
        for flag in ("is_rookie", "is_auto", "is_relic"):
            card[flag] = max(card.get(flag, 0), r[flag] or 0)

        card["prices"].append(r["price_cents"])
        label = (f"{r['grader']} {r['grade']:g}"
                 if r["grader"] and r["grade"] is not None
                 else (r["grader"] or "Raw"))
        g = card["by_grade"].setdefault(label, {"prices": [], "last": None})
        g["prices"].append(r["price_cents"])
        g["last"] = r["sold_date"]
        card.setdefault("first_sold", r["sold_date"])
        card["last_sold"] = r["sold_date"]

    cards: list[dict] = []
    grades: list[dict] = []
    for key, c in grouped.items():
        prices = c["prices"]
        raw = c["by_grade"].get("Raw", {}).get("prices", [])
        cards.append({
            "card_key": key,
            # The spelling most of the group agrees on. card_name carries words
            # that are claimed but not keyed, so members differ; taking the
            # first would make a card's name depend on the order it sold in.
            "card_name": min((n for n in c["names"] if n),
                             key=lambda n: (-c["names"][n], len(n), n),
                             default=key),
            **{col: c.get(col) for col in (
                "player", "team", "year", "brand", "set_name", "subset",
                "parallel", "card_number", "print_run", "image_url")},
            **{f: c.get(f, 0) for f in ("is_rookie", "is_auto", "is_relic")},
            "numberless": c["numberless"],
            "sales": len(prices),
            "median_cents": int(statistics.median(prices)),
            "low_cents": min(prices),
            "high_cents": max(prices),
            "raw_sales": len(raw),
            "raw_median_cents": int(statistics.median(raw)) if raw else None,
            "first_sold": c.get("first_sold"),
            "last_sold": c.get("last_sold"),
            # Chronological, because the trend compares halves of a timeline.
            "trend_pct": price_trend([p / 100.0 for p in prices]),
        })
        for label, g in c["by_grade"].items():
            gp = g["prices"]
            grades.append({
                "card_key": key, "grade_label": label, "sales": len(gp),
                "median_cents": int(statistics.median(gp)),
                "low_cents": min(gp), "high_cents": max(gp),
                "last_sold": g["last"],
            })
    return (cards, grades)


def build_sql(
    db_path: str | Path,
    since: Optional[str] = None,
    key_hashes: Optional[Iterable[tuple[str, str]]] = None,
    changed_since: Optional[str] = None,
) -> tuple[str, dict]:
    """Build the D1 import script. Returns (sql, stats)."""
    conn = store.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = _rows_to_export(conn, since, changed_since)
        # Daily rollups are recomputed in full regardless: there is one row per
        # day, so ~90 of them, and a partial day's medians would be wrong.
        rollups = _daily_rollups(conn, since)
        # Only the cards this upload touches, on an incremental run. A card
        # whose sales did not change has the same stats it had last time, and
        # there are tens of thousands of them -- rebuilding all of them on every
        # push would make an incremental upload no smaller than a full one.
        touched = None
        if changed_since:
            touched = {r["card_key"] for r in rows if r["card_key"]}
        cards, card_grades = _card_rollups(conn, touched)
        watermark = store.max_updated_at(conn)
    finally:
        conn.close()

    lines: list[str] = [
        "-- Generated by `nflcarddb export-api`. Safe to re-run:",
        "-- sales upsert on item_id, so a re-import updates rather than duplicates.",
    ]

    cols = ", ".join(EXPORT_COLUMNS)
    updates = ", ".join(
        f"{c} = excluded.{c}" for c in EXPORT_COLUMNS if c != "item_id"
    )
    for start in range(0, len(rows), ROWS_PER_INSERT):
        chunk = rows[start:start + ROWS_PER_INSERT]
        values = ",\n  ".join(
            "(" + ", ".join(_sql_literal(r[c]) for c in EXPORT_COLUMNS) + ")"
            for r in chunk
        )
        lines.append(
            f"INSERT INTO sales ({cols}) VALUES\n  {values}\n"
            f"ON CONFLICT(item_id) DO UPDATE SET {updates};"
        )

    for start in range(0, len(rollups), ROWS_PER_INSERT):
        chunk = rollups[start:start + ROWS_PER_INSERT]
        values = ",\n  ".join(
            "(" + ", ".join(_sql_literal(d[k]) for k in
                            ("sold_date", "sales", "priced", "median_cents",
                             "p90_cents", "total_cents")) + ")"
            for d in chunk
        )
        lines.append(
            "INSERT INTO daily (sold_date, sales, priced, median_cents, p90_cents, "
            f"total_cents) VALUES\n  {values}\n"
            "ON CONFLICT(sold_date) DO UPDATE SET sales = excluded.sales, "
            "priced = excluded.priced, median_cents = excluded.median_cents, "
            "p90_cents = excluded.p90_cents, total_cents = excluded.total_cents;"
        )

    for table, columns, payload, pk in (
        ("cards", CARD_COLUMNS, cards, "card_key"),
        ("card_grades", GRADE_COLUMNS, card_grades, "card_key, grade_label"),
    ):
        cols = ", ".join(columns)
        keys = {k.strip() for k in pk.split(",")}
        updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c not in keys)
        for start in range(0, len(payload), ROWS_PER_INSERT):
            chunk = payload[start:start + ROWS_PER_INSERT]
            values = ",\n  ".join(
                "(" + ", ".join(_sql_literal(row[c]) for c in columns) + ")"
                for row in chunk
            )
            lines.append(
                f"INSERT INTO {table} ({cols}) VALUES\n  {values}\n"
                f"ON CONFLICT({pk}) DO UPDATE SET {updates};"
            )

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for key_hash, label in (key_hashes or []):
        lines.append(
            "INSERT INTO api_keys (key_hash, label, created_at) VALUES "
            f"({_sql_literal(key_hash)}, {_sql_literal(label)}, {_sql_literal(now)}) "
            "ON CONFLICT(key_hash) DO UPDATE SET revoked = 0;"
        )

    lines.append(
        f"INSERT INTO meta (k, v) VALUES ('updated_at', {_sql_literal(now)}) "
        "ON CONFLICT(k) DO UPDATE SET v = excluded.v;"
    )

    stats = {
        "rows": len(rows),
        "days": len(rollups),
        "cards": len(cards),
        "card_grades": len(card_grades),
        "since": since,
        "changed_since": changed_since,
        "keys_added": len(list(key_hashes or [])),
        "generated_at": now,
        # The highest sales.updated_at in the database, which becomes the next
        # push's starting point once this one has actually landed.
        "watermark": watermark,
    }
    return ("\n".join(lines) + "\n", stats)


def export_api_sql(
    db_path: str | Path,
    out_path: str | Path,
    since: Optional[str] = None,
    key_hashes: Optional[Iterable[tuple[str, str]]] = None,
    changed_since: Optional[str] = None,
) -> dict:
    sql, stats = build_sql(db_path, since, list(key_hashes or []), changed_since)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sql, encoding="utf-8")
    stats["file"] = str(out)
    stats["bytes"] = len(sql)
    return stats
