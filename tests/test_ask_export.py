"""The asking price on best-offer sales.

Those sales are real -- a card changed hands -- but eBay publishes only what the
seller wanted, not what the buyer paid. That ask is now served as the row's
price, by choice, so the invariant these tests defend is the one that keeps the
choice reversible: `ask_cents` is set on exactly the best-offer rows and nowhere
else, so "which of these figures is an ask" stays answerable.
"""

import re

from nflcarddb import db as store
from nflcarddb.api_export import EXPORT_COLUMNS, build_sql
from nflcarddb.models import CardAttrs, Sale


def _db(tmp_path):
    path = tmp_path / "ask.db"
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    sales = [
        # Sold outright: the price is what was paid.
        Sale(item_id="1", title="fixed sale", price_cents=8800,
             sold_date="2026-08-03", best_offer=False),
        # Offer accepted: 42000 is the ask, the sale was some unknown amount less.
        Sale(item_id="2", title="offer accepted", price_cents=42000,
             sold_date="2026-08-03", best_offer=True),
        # No price at all on the tile.
        Sale(item_id="3", title="no price", price_cents=None,
             sold_date="2026-08-03", best_offer=False),
    ]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, CardAttrs(player="P")) for s in sales], "t")
    conn.close()
    return path


def _rows(sql):
    """Pull the VALUES tuples out of the generated sales INSERT."""
    block = re.search(r"INSERT INTO sales \((.*?)\) VALUES\n(.*?);\n", sql, re.S)
    columns = [c.strip() for c in block.group(1).split(",")]
    out = []
    for line in block.group(2).strip().splitlines():
        line = line.strip().rstrip(",")
        # The statement ends with an ON CONFLICT clause; only tuples are rows.
        if not (line.startswith("(") and line.endswith(")")):
            continue
        values = next(csv_split(line[1:-1]))
        out.append(dict(zip(columns, values)))
    assert out, "no VALUES tuples found -- the export format changed"
    return out


def csv_split(text):
    """Split a SQL VALUES tuple, respecting quoted strings."""
    field, fields, in_string = "", [], False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "'" and text[i + 1:i + 2] == "'":
                field += "''"
                i += 2
                continue
            if ch == "'":
                in_string = False
            field += ch
        elif ch == "'":
            in_string = True
            field += ch
        elif ch == ",":
            fields.append(field.strip())
            field = ""
        else:
            field += ch
        i += 1
    fields.append(field.strip())
    yield fields


def test_ask_is_exported(tmp_path):
    assert "ask_cents" in EXPORT_COLUMNS
    sql, _ = build_sql(_db(tmp_path))
    assert "ask_cents" in sql


def test_a_best_offer_row_carries_the_ask_as_its_price(tmp_path):
    sql, _ = build_sql(_db(tmp_path))
    row = next(r for r in _rows(sql) if r["item_id"] == "'2'")

    assert row["price_cents"] == "42000"     # the ask, serving as the price
    assert row["ask_cents"] == "42000"       # and marked as an ask


def test_an_ordinary_sale_carries_the_price_and_no_ask(tmp_path):
    """Filling ask on every row would make `ask IS NOT NULL` meaningless."""
    sql, _ = build_sql(_db(tmp_path))
    row = next(r for r in _rows(sql) if r["item_id"] == "'1'")

    assert row["price_cents"] == "8800"
    assert row["ask_cents"] == "NULL"


def test_a_row_with_no_price_at_all_gets_neither(tmp_path):
    sql, _ = build_sql(_db(tmp_path))
    row = next(r for r in _rows(sql) if r["item_id"] == "'3'")

    assert row["price_cents"] == "NULL"
    assert row["ask_cents"] == "NULL"


def test_ask_is_set_on_exactly_the_best_offer_rows(tmp_path):
    """What makes including asks reversible: filter ask_cents IS NULL and you
    are back to confirmed sale prices, without re-collecting anything."""
    sql, _ = build_sql(_db(tmp_path))
    for row in _rows(sql):
        has_ask = row["ask_cents"] != "NULL"
        assert has_ask == (row["best_offer"] == "1"), row
        if has_ask:
            assert row["ask_cents"] == row["price_cents"], row


def test_the_ask_reaches_the_daily_medians(tmp_path):
    """Daily rollups are the numbers a chart plots, and asks now count toward
    them -- which is exactly why they read above true sale prices."""
    sql, _ = build_sql(_db(tmp_path))
    daily = re.search(r"INSERT INTO daily .*?VALUES\n(.*?);", sql, re.S).group(1)

    # 8800 and 42000 both priced; two rows, so the p90 is the ask.
    assert "42000" in daily


def test_the_migration_exists_for_databases_that_predate_the_column():
    from nflcarddb.d1_http import MIGRATIONS

    assert any("ask_cents" in m for m in MIGRATIONS)
