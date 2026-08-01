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
