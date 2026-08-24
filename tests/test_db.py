import sqlite3

from nflcarddb import db as store
from nflcarddb.models import CardAttrs, Sale


def make_sale(item_id="111111111111", price=1000, sold="2025-07-30", title="2023 Prizm CJ Stroud #339"):
    return Sale(
        item_id=item_id,
        title=title,
        price_cents=price,
        shipping_cents=500,
        sold_date=sold,
        query_id="q1",
    )


def test_schema_applies_and_is_reentrant(tmp_path):
    path = tmp_path / "a.db"
    store.connect(path).close()
    conn = store.connect(path)  # second apply must not error
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sales", "cards", "scrape_runs", "scrape_segments"} <= tables
    conn.close()


def test_upsert_is_idempotent(tmp_path):
    conn = store.connect(tmp_path / "b.db")
    run = store.start_run(conn, "2025-07-30")

    seen, new = store.upsert_sales(conn, [make_sale()], run)
    assert (seen, new) == (1, 1)

    # Re-scraping the same listing must not duplicate it.
    seen, new = store.upsert_sales(conn, [make_sale(price=1200)], run)
    assert (seen, new) == (1, 0)

    rows = conn.execute("SELECT price_cents, total_cents FROM sales").fetchall()
    assert len(rows) == 1
    assert rows[0]["price_cents"] == 1200
    assert rows[0]["total_cents"] == 1700  # generated column tracks the update
    conn.close()


def test_duplicate_items_within_one_batch_counted_once(tmp_path):
    # Subdivided price bands overlap their parent, so a run really does hand the
    # same listing to upsert_sales twice. `new` must match rows actually inserted.
    conn = store.connect(tmp_path / "dup.db")
    run = store.start_run(conn, "2025-07-30")

    seen, new = store.upsert_sales(conn, [make_sale(), make_sale(price=1500)], run)
    assert (seen, new) == (1, 1)
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 1
    conn.close()


def test_first_seen_survives_rescrape(tmp_path):
    conn = store.connect(tmp_path / "c.db")
    run = store.start_run(conn, "2025-07-30")
    store.upsert_sales(conn, [make_sale()], run)
    first = conn.execute("SELECT first_seen_at FROM sales").fetchone()[0]

    store.upsert_sales(conn, [make_sale(price=999)], run)
    again = conn.execute("SELECT first_seen_at FROM sales").fetchone()[0]
    assert first == again
    conn.close()


def test_cards_join_and_view(tmp_path):
    conn = store.connect(tmp_path / "d.db")
    run = store.start_run(conn, "2025-07-30")
    store.upsert_sales(conn, [make_sale()], run)
    store.upsert_cards(
        conn,
        [("111111111111", CardAttrs(player="CJ Stroud", year=2023, set_name="Prizm", confidence=0.9))],
        "title/test",
    )

    row = conn.execute("SELECT * FROM v_sales WHERE item_id = '111111111111'").fetchone()
    assert row["player"] == "CJ Stroud"
    assert row["price"] == 10.0
    assert row["total"] == 15.0
    conn.close()


def test_fts_index_finds_titles(tmp_path):
    conn = store.connect(tmp_path / "e.db")
    run = store.start_run(conn, "2025-07-30")
    store.upsert_sales(conn, [
        make_sale("222222222222", title="2023 Panini Prizm Bijan Robinson RC"),
        make_sale("333333333333", title="1986 Topps Jerry Rice Rookie"),
    ], run)

    hits = conn.execute(
        "SELECT s.item_id FROM sales_fts f JOIN sales s ON s.rowid = f.rowid "
        "WHERE sales_fts MATCH 'Bijan'"
    ).fetchall()
    assert [h[0] for h in hits] == ["222222222222"]
    conn.close()


def test_unparsed_items_only_returns_missing(tmp_path):
    conn = store.connect(tmp_path / "f.db")
    run = store.start_run(conn, "2025-07-30")
    store.upsert_sales(conn, [make_sale("444444444444"), make_sale("555555555555")], run)
    store.upsert_cards(conn, [("444444444444", CardAttrs(player="X"))], "title/test")

    pending = store.unparsed_items(conn)
    assert [p[0] for p in pending] == ["555555555555"]
    conn.close()


def test_run_and_segment_bookkeeping(tmp_path):
    conn = store.connect(tmp_path / "g.db")
    run = store.start_run(conn, "2025-07-30")
    store.record_segment(conn, run, "q1:0-10", "q1", 0, 10, "done", 3, 120)
    store.record_segment(conn, run, "q1:10-25", "q1", 10, 25, "capped", 42, 9000, "subdivided")
    store.finish_run(conn, run, "ok", 45, 9120, 9120)

    assert store.completed_segments(conn, run) == {"q1:0-10", "q1:10-25"}
    row = conn.execute("SELECT status, items_new FROM scrape_runs WHERE run_id = ?", (run,)).fetchone()
    assert row["status"] == "ok"
    assert row["items_new"] == 9120
    conn.close()


def test_daily_summary(tmp_path):
    conn = store.connect(tmp_path / "h.db")
    run = store.start_run(conn, "2025-07-30")
    store.upsert_sales(conn, [
        make_sale("666666666666", price=1000),
        make_sale("777777777777", price=3000),
    ], run)
    summary = store.daily_summary(conn, "2025-07-30")
    assert summary["n"] == 2
    assert summary["avg_price"] == 20.0
    assert summary["max_price"] == 30.0
    conn.close()


def _old_format_database(path):
    """A database from before card_key and image_url existed."""
    import sqlite3

    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sales (
            item_id TEXT PRIMARY KEY, title TEXT NOT NULL, price_cents INTEGER,
            currency TEXT NOT NULL DEFAULT 'USD', shipping_cents INTEGER,
            sold_date TEXT, listing_format TEXT NOT NULL DEFAULT 'unknown',
            bids INTEGER, best_offer INTEGER NOT NULL DEFAULT 0,
            condition TEXT, seller TEXT, url TEXT, query_id TEXT, run_id TEXT,
            first_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE cards (
            item_id TEXT PRIMARY KEY, player TEXT, team TEXT, year INTEGER,
            brand TEXT, set_name TEXT, parallel TEXT, card_number TEXT,
            serial_number INTEGER, print_run INTEGER, grader TEXT, grade REAL,
            is_graded INTEGER NOT NULL DEFAULT 0,
            is_rookie INTEGER NOT NULL DEFAULT 0,
            is_auto INTEGER NOT NULL DEFAULT 0,
            is_relic INTEGER NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0,
            parser_version TEXT, parsed_at TEXT);
        INSERT INTO sales VALUES ('900000000001', 'a card', 9000, 'USD', 0,
            '2026-08-03', 'fixed', NULL, 0, NULL, NULL, NULL, NULL, NULL,
            '2026-08-03T00:00:00+00:00', '2026-08-03T00:00:00+00:00');
        INSERT INTO cards (item_id, player, confidence)
            VALUES ('900000000001', 'Someone', 0.9);
    """)
    conn.commit()
    conn.close()
    return path


def test_an_older_database_gains_new_columns_on_open(tmp_path):
    """CREATE TABLE IF NOT EXISTS never alters an existing table, so a column
    added to schema.sql never reached a database made before it -- and the
    schema then failed on the first index over it. This is the exact failure
    users hit as `no such column: card_key`."""
    path = _old_format_database(tmp_path / "old.db")

    conn = store.connect(path)          # must not raise
    cards = {r[1] for r in conn.execute("PRAGMA table_info(cards)")}
    sales = {r[1] for r in conn.execute("PRAGMA table_info(sales)")}
    conn.close()

    assert {"card_key", "card_name"} <= cards
    assert "image_url" in sales


def test_upgrading_keeps_the_rows_that_were_already_there(tmp_path):
    path = _old_format_database(tmp_path / "keep.db")

    conn = store.connect(path)
    row = conn.execute(
        "SELECT title, price_cents, card_key FROM sales JOIN cards USING (item_id)"
    ).fetchone()
    conn.close()

    assert row["title"] == "a card"
    assert row["price_cents"] == 9000
    assert row["card_key"] is None      # not yet parsed, but the column exists


def test_opening_an_up_to_date_database_changes_nothing(tmp_path):
    path = tmp_path / "current.db"
    store.connect(path).close()

    conn = sqlite3.connect(path)
    added = store._add_missing_columns(conn)
    conn.close()
    assert added == []


def test_every_declared_column_is_discovered():
    """The migration reads schema.sql rather than a hand-kept list, so a future
    column needs no second edit. That only holds if the parse actually works."""
    declared = store._declared_columns()

    assert {"sales", "cards", "scrape_runs"} <= set(declared)
    cards = dict(declared["cards"])
    assert "card_key" in cards and "card_name" in cards
    # Constraint lines are not columns.
    assert "PRIMARY" not in cards and "FOREIGN" not in cards
