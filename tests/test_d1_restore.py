"""Getting the local database back from Cloudflare.

The SQLite file is gitignored, so a wiped PC does not get it back from GitHub.
D1 is the copy that survives, and this is the way back from it.
"""

import pytest

from nflcarddb import db as store
from nflcarddb.d1_restore import PAGE_SIZE, count_rows, fetch_pages, restore


def _row(item_id, day="2026-08-03", price=1000, **kw):
    row = {
        "item_id": item_id, "sold_date": day, "title": "2021 Prizm Ja'Marr Chase RC",
        "price_cents": price, "ask_cents": None, "shipping_cents": 0,
        "currency": "USD", "best_offer": 0, "listing_format": "fixed", "bids": None,
        "image_url": "https://i.ebayimg.com/images/g/A/s-l500.jpg",
    }
    row.update(kw)
    return row


def _serving(pages, monkeypatch):
    """Fake D1 returning `pages` of rows, then nothing."""
    sent = []
    queue = list(pages)

    def fake_run(account, database, token, sql):
        sent.append(sql)
        if "COUNT(*)" in sql:
            return {"result": [{"results": [{"n": sum(len(p) for p in pages)}]}]}
        rows = queue.pop(0) if queue else []
        return {"result": [{"results": rows}]}

    monkeypatch.setattr("nflcarddb.d1_restore.run_sql", fake_run)
    return sent


def test_counts_before_downloading(monkeypatch):
    _serving([[_row("1")]], monkeypatch)
    assert count_rows("a", "d", "t") == 1


def test_pages_until_a_short_page(monkeypatch):
    sent = _serving([[_row(str(i)) for i in range(3)], [_row("9")]], monkeypatch)
    pages = list(fetch_pages("a", "d", "t", page_size=3))

    assert [len(p) for p in pages] == [3, 1]
    assert "OFFSET 0" in sent[0]
    assert "OFFSET 3" in sent[1]


def test_paging_orders_by_the_primary_key(monkeypatch):
    """Ordering on a non-unique column lets LIMIT/OFFSET repeat or skip rows."""
    sent = _serving([[]], monkeypatch)
    list(fetch_pages("a", "d", "t"))
    assert "ORDER BY item_id" in sent[0]


def test_restore_rebuilds_sales_and_cards(tmp_path, monkeypatch):
    _serving([[
        _row("100000000001", price=8800),
        _row("100000000002", day="2026-08-02", price=2500, best_offer=1),
    ]], monkeypatch)

    db = tmp_path / "restored.db"
    result = restore("a", "d", "t", str(db))

    assert result["sales_restored"] == 2
    assert result["days"] == 2
    assert result["first_day"] == "2026-08-02"

    conn = store.connect(db)
    sale = conn.execute(
        "SELECT price_cents, best_offer, image_url FROM sales WHERE item_id='100000000001'"
    ).fetchone()
    assert sale["price_cents"] == 8800
    assert sale["best_offer"] == 0
    assert sale["image_url"].endswith("s-l500.jpg")

    # Card attributes come from re-parsing the title, not from D1's copy --
    # the parser has improved since some of those rows were written.
    card = conn.execute(
        "SELECT player, year FROM cards WHERE item_id='100000000001'"
    ).fetchone()
    assert card["player"] == "Ja'Marr Chase"
    assert card["year"] == 2021
    conn.close()


def test_restored_days_are_not_immediately_re_collected(tmp_path, monkeypatch):
    """Otherwise the first backfill after a restore refetches everything that
    was just downloaded."""
    _serving([[_row("100000000001"), _row("100000000002", day="2026-08-02")]],
             monkeypatch)

    db = tmp_path / "days.db"
    restore("a", "d", "t", str(db))

    conn = store.connect(db)
    done = store.completed_days(conn)
    conn.close()
    assert done == {"2026-08-03", "2026-08-02"}


def test_restoring_over_existing_data_merges(tmp_path, monkeypatch):
    """A machine that still has some data keeps it and gains what D1 has."""
    from nflcarddb.models import Sale

    db = tmp_path / "merge.db"
    conn = store.connect(db)
    run = store.start_run(conn, "2026-08-03")
    store.upsert_sales(conn, [
        Sale(item_id="100000000001", title="local copy", price_cents=8800,
             sold_date="2026-08-03"),
        Sale(item_id="999999999999", title="only here", price_cents=500,
             sold_date="2026-08-03"),
    ], run)
    conn.close()

    _serving([[_row("100000000001", price=8800), _row("100000000003")]], monkeypatch)
    restore("a", "d", "t", str(db))

    conn = store.connect(db)
    ids = {r[0] for r in conn.execute("SELECT item_id FROM sales")}
    conn.close()
    # Nothing dropped, the overlap not duplicated, the new row added.
    assert ids == {"100000000001", "999999999999", "100000000003"}


def test_a_row_without_an_id_is_skipped(tmp_path, monkeypatch):
    _serving([[_row("100000000001"), {"item_id": None, "title": "junk"}]],
             monkeypatch)

    result = restore("a", "d", "t", str(tmp_path / "skip.db"))
    assert result["sales_restored"] == 1


def test_since_narrows_the_download(monkeypatch):
    sent = _serving([[]], monkeypatch)
    list(fetch_pages("a", "d", "t", since="2026-08-01"))
    assert "sold_date >= '2026-08-01'" in sent[0]


def test_an_empty_d1_restores_nothing_without_erroring(tmp_path, monkeypatch):
    _serving([[]], monkeypatch)
    result = restore("a", "d", "t", str(tmp_path / "empty.db"))
    assert result["sales_restored"] == 0
    assert result["first_day"] is None
