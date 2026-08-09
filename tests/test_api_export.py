"""Exporting the local database into Cloudflare D1.

The output is loaded into a real SQLite database here, because D1 *is* SQLite --
if the script loads cleanly locally it loads cleanly there.
"""

import hashlib
import sqlite3

from nflcarddb import db as store
from nflcarddb.api_export import build_sql, export_api_sql, new_api_key
from nflcarddb.models import CardAttrs, Sale


def seed(db_path, rows):
    conn = store.connect(db_path)
    run = store.start_run(conn, "2026-08-03")
    sales, cards = [], []
    for item_id, price, sold, bo, title, player in rows:
        sales.append(Sale(item_id=item_id, title=title, price_cents=price,
                          shipping_cents=0, sold_date=sold, best_offer=bo,
                          currency="USD"))
        cards.append((item_id, CardAttrs(player=player, year=2023,
                                         set_name="Prizm", confidence=0.9)))
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, cards, "title/test")
    conn.close()


def load(schema_path, sql):
    """Apply the API schema and then the generated import, as D1 would."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(open(schema_path, encoding="utf-8").read())
    conn.executescript(sql)
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def test_export_loads_into_a_d1_shaped_database(tmp_path):
    db = tmp_path / "src.db"
    seed(db, [
        ("100000000001", 2000, "2026-08-03", False, "2023 Prizm CJ Stroud #339", "CJ Stroud"),
        ("100000000002", 4000, "2026-08-03", False, "2023 Prizm Bijan Robinson #44", "Bijan Robinson"),
    ])
    sql, stats = build_sql(db)
    assert stats["rows"] == 2

    conn = load("api/schema.sql", sql)
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 2
    row = conn.execute("SELECT * FROM sales WHERE item_id='100000000001'").fetchone()
    assert row["player"] == "CJ Stroud"
    assert row["price_cents"] == 2000
    assert row["set_name"] == "Prizm"
    conn.close()


def test_best_offer_prices_are_exported_with_the_ask(tmp_path):
    """The ask is what eBay publishes on those, and it is now the price served.
    `best_offer` and `ask_cents` are what mark it as an ask rather than a sale."""
    db = tmp_path / "bo.db"
    seed(db, [
        ("200000000001", 99900, "2026-08-03", True, "2023 Prizm Ask Only #1", "Someone"),
        ("200000000002", 5000, "2026-08-03", False, "2023 Prizm Real Sale #2", "Someone"),
    ])
    conn = load("api/schema.sql", build_sql(db)[0])

    offer = conn.execute("SELECT price_cents, ask_cents, best_offer FROM sales "
                         "WHERE item_id='200000000001'").fetchone()
    assert offer["best_offer"] == 1
    assert offer["price_cents"] == 99900      # the ask, served as the price
    assert offer["ask_cents"] == 99900        # and still identifiable as one

    real = conn.execute("SELECT price_cents, ask_cents FROM sales "
                        "WHERE item_id='200000000002'").fetchone()
    assert real["price_cents"] == 5000
    # Only best-offer rows carry an ask, so this stays a usable filter.
    assert real["ask_cents"] is None
    conn.close()


def test_daily_rollups_count_asks_in_the_prices(tmp_path):
    db = tmp_path / "d.db"
    seed(db, [
        ("300000000001", 1000, "2026-08-03", False, "a", "P"),
        ("300000000002", 3000, "2026-08-03", False, "b", "P"),
        ("300000000003", 99900, "2026-08-03", True, "c", "P"),
    ])
    conn = load("api/schema.sql", build_sql(db)[0])
    day = conn.execute("SELECT * FROM daily WHERE sold_date='2026-08-03'").fetchone()
    assert day["sales"] == 3        # every sale counts as volume
    assert day["priced"] == 3       # asks included, so all three are priced
    assert day["median_cents"] == 3000
    conn.close()


def test_reimporting_updates_instead_of_duplicating(tmp_path):
    db = tmp_path / "r.db"
    seed(db, [("400000000001", 1000, "2026-08-03", False, "a", "P")])
    sql = build_sql(db)[0]

    conn = load("api/schema.sql", sql)
    conn.executescript(sql)          # same import a second time
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 1
    conn.close()


def test_since_limits_the_export(tmp_path):
    db = tmp_path / "s.db"
    seed(db, [
        ("500000000001", 1000, "2026-07-01", False, "old", "P"),
        ("500000000002", 2000, "2026-08-03", False, "new", "P"),
    ])
    _, stats = build_sql(db, since="2026-08-01")
    assert stats["rows"] == 1


def test_api_keys_are_exported_only_as_hashes(tmp_path):
    db = tmp_path / "k.db"
    seed(db, [("600000000001", 1000, "2026-08-03", False, "a", "P")])

    key, key_hash = new_api_key()
    sql, stats = build_sql(db, key_hashes=[(key_hash, "website")])

    assert stats["keys_added"] == 1
    assert key_hash in sql
    assert key not in sql            # the usable credential never lands in the file

    conn = load("api/schema.sql", sql)
    stored = conn.execute("SELECT key_hash, revoked FROM api_keys").fetchone()
    assert stored["key_hash"] == hashlib.sha256(key.encode()).hexdigest()
    assert stored["revoked"] == 0
    conn.close()


def test_generated_keys_are_unique_and_long_enough():
    keys = {new_api_key()[0] for _ in range(50)}
    assert len(keys) == 50
    assert all(len(k) > 32 for k in keys)


def test_quotes_in_titles_do_not_break_the_sql(tmp_path):
    """Ja'Marr Chase would end the string literal if it were not escaped."""
    db = tmp_path / "q.db"
    seed(db, [("700000000001", 1000, "2026-08-03", False,
               "2021 Prizm Ja'Marr Chase #201 -- it's a 'test'", "Ja'Marr Chase")])
    conn = load("api/schema.sql", build_sql(db)[0])
    title = conn.execute("SELECT title FROM sales").fetchone()[0]
    assert "Ja'Marr" in title
    assert "it's a 'test'" in title
    conn.close()


def test_export_writes_a_file_and_reports_stats(tmp_path):
    db = tmp_path / "f.db"
    seed(db, [("800000000001", 1000, "2026-08-03", False, "a", "P")])
    out = tmp_path / "out" / "import.sql"

    stats = export_api_sql(db, out)
    assert out.exists()
    assert stats["rows"] == 1
    assert stats["bytes"] > 0
    assert "INSERT INTO sales" in out.read_text()


def test_empty_database_exports_without_error(tmp_path):
    db = tmp_path / "empty.db"
    store.connect(db).close()
    sql, stats = build_sql(db)
    assert stats["rows"] == 0
    # Still valid SQL, and still records when it was generated.
    conn = load("api/schema.sql", sql)
    assert conn.execute("SELECT v FROM meta WHERE k='updated_at'").fetchone() is not None
    conn.close()
