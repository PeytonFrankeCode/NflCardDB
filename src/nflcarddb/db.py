"""SQLite storage layer.

Idempotent by design: item_id is the primary key, so re-running a day's scrape
updates rows in place instead of duplicating them.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .models import CardAttrs, Sale

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    return f"run_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:6]}"


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the database and apply the schema."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    return conn


_SALE_COLS = (
    "item_id", "title", "price_cents", "currency", "shipping_cents", "sold_date",
    "listing_format", "bids", "best_offer", "condition", "seller", "url",
    "image_url", "query_id",
)


def upsert_sales(conn: sqlite3.Connection, sales: Iterable[Sale], run_id: str) -> tuple[int, int]:
    """Insert or refresh sale rows.

    Returns (seen, new) counted over *distinct* item ids. A batch can contain
    the same listing more than once -- a capped price band overlaps the child
    bands it was subdivided into, and configured queries overlap each other --
    so the batch is deduplicated before counting, otherwise `new` would report
    more rows than SQLite actually inserted.

    A row already in the table keeps its original first_seen_at and run_id, so
    the record of when we first observed a sale survives re-scrapes.
    """
    by_id = {s.item_id: s for s in sales}  # last occurrence wins
    sales = list(by_id.values())
    if not sales:
        return (0, 0)

    now = utcnow()
    ids = list(by_id)
    placeholders = ",".join("?" * len(ids))
    existing = {
        r[0] for r in conn.execute(
            f"SELECT item_id FROM sales WHERE item_id IN ({placeholders})", ids
        )
    }

    cols = ", ".join(_SALE_COLS) + ", run_id, first_seen_at, updated_at"
    binds = ", ".join(f":{c}" for c in _SALE_COLS) + ", :run_id, :first_seen_at, :updated_at"
    # On conflict, refresh the volatile fields only.
    updates = ", ".join(
        f"{c} = excluded.{c}"
        for c in _SALE_COLS
        if c not in ("item_id", "query_id")
    )

    rows = []
    for s in sales:
        row = s.as_row()
        row["run_id"] = run_id
        row["first_seen_at"] = now
        row["updated_at"] = now
        rows.append(row)

    conn.executemany(
        f"INSERT INTO sales ({cols}) VALUES ({binds}) "
        f"ON CONFLICT(item_id) DO UPDATE SET {updates}, updated_at = excluded.updated_at",
        rows,
    )
    conn.commit()
    return (len(sales), len(sales) - len(existing))


_CARD_COLS = (
    "player", "team", "year", "brand", "set_name", "parallel", "card_number",
    "serial_number", "print_run", "grader", "grade", "is_graded", "is_rookie",
    "is_auto", "is_relic", "confidence",
)


def upsert_cards(
    conn: sqlite3.Connection,
    parsed: Iterable[tuple[str, CardAttrs]],
    parser_version: str,
) -> int:
    parsed = list(parsed)
    if not parsed:
        return 0

    now = utcnow()
    rows = []
    for item_id, attrs in parsed:
        row = attrs.as_row()
        row["item_id"] = item_id
        row["parser_version"] = parser_version
        row["parsed_at"] = now
        rows.append(row)

    cols = "item_id, " + ", ".join(_CARD_COLS) + ", parser_version, parsed_at"
    binds = ":item_id, " + ", ".join(f":{c}" for c in _CARD_COLS) + ", :parser_version, :parsed_at"
    updates = ", ".join(f"{c} = excluded.{c}" for c in _CARD_COLS)

    conn.executemany(
        f"INSERT INTO cards ({cols}) VALUES ({binds}) "
        f"ON CONFLICT(item_id) DO UPDATE SET {updates}, "
        f"parser_version = excluded.parser_version, parsed_at = excluded.parsed_at",
        rows,
    )
    conn.commit()
    return len(rows)


def start_run(conn: sqlite3.Connection, target_date: Optional[str]) -> str:
    run_id = new_run_id()
    conn.execute(
        "INSERT INTO scrape_runs (run_id, target_date, started_at) VALUES (?, ?, ?)",
        (run_id, target_date, utcnow()),
    )
    conn.commit()
    return run_id


def finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    pages: int,
    seen: int,
    new: int,
    error: Optional[str] = None,
) -> None:
    conn.execute(
        "UPDATE scrape_runs SET finished_at = ?, status = ?, pages_fetched = ?, "
        "items_seen = ?, items_new = ?, error = ? WHERE run_id = ?",
        (utcnow(), status, pages, seen, new, error, run_id),
    )
    conn.commit()


def record_segment(
    conn: sqlite3.Connection,
    run_id: str,
    segment_id: str,
    query_id: str,
    price_lo: Optional[float],
    price_hi: Optional[float],
    status: str,
    pages: int,
    items: int,
    note: Optional[str] = None,
) -> None:
    conn.execute(
        "INSERT INTO scrape_segments (run_id, segment_id, query_id, price_lo, price_hi, "
        "status, pages, items, note, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(run_id, segment_id) DO UPDATE SET status = excluded.status, "
        "pages = excluded.pages, items = excluded.items, note = excluded.note, "
        "updated_at = excluded.updated_at",
        (run_id, segment_id, query_id, price_lo, price_hi, status, pages, items, note, utcnow()),
    )
    conn.commit()


def completed_segments(conn: sqlite3.Connection, run_id: str) -> set[str]:
    return {
        r[0] for r in conn.execute(
            "SELECT segment_id FROM scrape_segments WHERE run_id = ? AND status IN ('done', 'capped')",
            (run_id,),
        )
    }


def resumable_run(conn: sqlite3.Connection, target_date: str) -> Optional[str]:
    """Most recent unfinished run for a target date, if any."""
    row = conn.execute(
        "SELECT run_id FROM scrape_runs WHERE target_date = ? AND status = 'running' "
        "ORDER BY started_at DESC LIMIT 1",
        (target_date,),
    ).fetchone()
    return row[0] if row else None


def unparsed_items(conn: sqlite3.Connection, limit: Optional[int] = None) -> list[tuple[str, str]]:
    sql = (
        "SELECT s.item_id, s.title FROM sales s "
        "LEFT JOIN cards c USING (item_id) WHERE c.item_id IS NULL"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [(r[0], r[1]) for r in conn.execute(sql)]


def completed_days(conn: sqlite3.Connection) -> set[str]:
    """Days already collected successfully, so a backfill can skip them.

    A day counts as done when a run finished 'ok' for it AND rows exist. Either
    alone is not enough: an 'ok' run that collected nothing usually means the
    date was mistyped or fell outside eBay's window, and rows without a
    successful run may be a partial left by a block.
    """
    ran_ok = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT target_date FROM scrape_runs "
            "WHERE status = 'ok' AND target_date IS NOT NULL"
        )
    }
    have_rows = {
        r[0] for r in conn.execute(
            "SELECT sold_date FROM sales WHERE sold_date IS NOT NULL "
            "GROUP BY sold_date HAVING COUNT(*) > 0"
        )
    }
    return ran_ok & have_rows


def daily_summary(conn: sqlite3.Connection, sold_date: str) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS n, "
        "       SUM(best_offer) AS best_offers, "
        "       ROUND(AVG(price_cents) / 100.0, 2) AS avg_price, "
        "       ROUND(MAX(price_cents) / 100.0, 2) AS max_price "
        "FROM sales WHERE sold_date = ?",
        (sold_date,),
    ).fetchone()
    return dict(row) if row else {}
