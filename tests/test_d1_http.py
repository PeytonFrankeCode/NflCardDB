"""Uploading to D1 over its HTTP API.

The statement splitter is the part that can corrupt data rather than merely
fail, so most of this is about it: card titles are seller-written and routinely
contain semicolons and apostrophes.
"""

from pathlib import Path

import pytest

from nflcarddb.d1_http import (
    D1Error,
    apply_migrations,
    batch_statements,
    push_sql,
    split_statements,
    verify,
)


def test_splits_plain_statements():
    sql = "CREATE TABLE a (x);\nINSERT INTO a VALUES (1);\n"
    assert list(split_statements(sql)) == [
        "CREATE TABLE a (x)",
        "INSERT INTO a VALUES (1)",
    ]


def test_a_semicolon_inside_a_title_does_not_split_the_statement():
    """'Lot of 3; Ja'Marr Chase' is an ordinary eBay listing name."""
    sql = "INSERT INTO sales VALUES ('Lot of 3; two rookies');\nSELECT 1;"
    out = list(split_statements(sql))
    assert len(out) == 2
    assert out[0] == "INSERT INTO sales VALUES ('Lot of 3; two rookies')"


def test_escaped_quotes_inside_a_title_are_handled():
    """Ja''Marr is how an apostrophe is escaped in SQL."""
    sql = "INSERT INTO sales VALUES ('2021 Prizm Ja''Marr Chase; RC');\nSELECT 2;"
    out = list(split_statements(sql))
    assert len(out) == 2
    assert "Ja''Marr" in out[0]
    assert "; RC" in out[0]


def test_multiple_semicolons_and_quotes_together():
    sql = (
        "INSERT INTO t VALUES ('a;b', 'it''s; fine');"
        "INSERT INTO t VALUES ('c;d');"
    )
    out = list(split_statements(sql))
    assert len(out) == 2
    assert out[0].count(";") == 2      # both semicolons stayed inside the strings
    assert out[1] == "INSERT INTO t VALUES ('c;d')"


def test_comments_do_not_end_a_statement():
    sql = "-- a note; with a semicolon\nSELECT 1;\nSELECT 2;"
    out = list(split_statements(sql))
    assert len(out) == 2
    assert "SELECT 1" in out[0]


def test_trailing_statement_without_a_semicolon_is_kept():
    assert list(split_statements("SELECT 1")) == ["SELECT 1"]


def test_empty_and_whitespace_only_input():
    assert list(split_statements("")) == []
    assert list(split_statements("   \n  ;  \n")) == []


def test_batches_respect_the_statement_cap():
    statements = [f"INSERT INTO t VALUES ({i})" for i in range(100)]
    batches = list(batch_statements(iter(statements), max_count=10))
    assert len(batches) == 10
    assert all(len(b) == 10 for b in batches)


def test_batches_respect_the_size_cap():
    big = "INSERT INTO t VALUES ('" + "x" * 500 + "')"
    batches = list(batch_statements(iter([big] * 10), max_bytes=1500, max_count=100))
    assert len(batches) > 1
    for batch in batches:
        assert sum(len(s) for s in batch) <= 1600


def test_a_single_oversized_statement_still_goes_out():
    """Better one over-large request than silently dropping a row."""
    huge = "INSERT INTO t VALUES ('" + "y" * 5000 + "')"
    batches = list(batch_statements(iter([huge]), max_bytes=100))
    assert len(batches) == 1
    assert batches[0] == [huge]


def test_push_counts_without_sending_on_dry_run(monkeypatch):
    sent = []
    monkeypatch.setattr("nflcarddb.d1_http.run_sql",
                        lambda *a, **k: sent.append(a) or {})

    sql = ";\n".join(f"INSERT INTO t VALUES ({i})" for i in range(25)) + ";"
    result = push_sql("acct", "db", "token", sql, dry_run=True)

    assert result.statements == 25
    assert result.batches >= 1
    assert sent == []          # nothing left the machine


def test_push_sends_every_statement(monkeypatch):
    seen = []

    def fake_run(account, database, token, payload):
        seen.append(payload)
        return {"success": True}

    monkeypatch.setattr("nflcarddb.d1_http.run_sql", fake_run)
    sql = ";\n".join(f"INSERT INTO t VALUES ({i})" for i in range(90)) + ";"
    result = push_sql("acct", "db", "token", sql)

    assert result.statements == 90
    combined = " ".join(seen)
    for i in range(90):
        assert f"VALUES ({i})" in combined


def test_push_retries_a_transient_failure(monkeypatch):
    attempts = {"n": 0}

    def flaky(account, database, token, payload):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise D1Error("temporary upstream error")
        return {"success": True}

    monkeypatch.setattr("nflcarddb.d1_http.run_sql", flaky)
    monkeypatch.setattr("nflcarddb.d1_http.time.sleep", lambda s: None)

    result = push_sql("acct", "db", "token", "SELECT 1;")
    assert result.retries == 1
    assert attempts["n"] == 2


def test_a_bad_token_is_not_retried(monkeypatch):
    """Retrying a refused token wastes a minute to reach the same answer."""
    attempts = {"n": 0}

    def refused(account, database, token, payload):
        attempts["n"] += 1
        raise D1Error("Cloudflare refused the token (HTTP 403).")

    monkeypatch.setattr("nflcarddb.d1_http.run_sql", refused)
    monkeypatch.setattr("nflcarddb.d1_http.time.sleep", lambda s: None)

    with pytest.raises(D1Error, match="refused the token"):
        push_sql("acct", "db", "token", "SELECT 1;")
    assert attempts["n"] == 1


def test_a_migration_already_applied_is_not_an_error(monkeypatch):
    """Every push replays the ALTERs; after the first they are all duplicates."""
    from nflcarddb.d1_http import apply_migrations

    def duplicate(account, database, token, sql):
        raise D1Error("duplicate column name: image_url")

    monkeypatch.setattr("nflcarddb.d1_http.run_sql", duplicate)
    assert apply_migrations("acct", "db", "token") == []


def test_a_migration_runs_on_a_database_that_predates_the_column(monkeypatch):
    seen = []
    monkeypatch.setattr("nflcarddb.d1_http.run_sql",
                        lambda a, d, t, sql: seen.append(sql) or {"success": True})

    applied = apply_migrations("acct", "db", "token",
                               ("ALTER TABLE sales ADD COLUMN image_url TEXT",))
    assert applied == ["ALTER TABLE sales ADD COLUMN image_url TEXT"]
    assert "image_url" in seen[0]


def test_a_real_migration_failure_is_raised(monkeypatch):
    """Swallowing every error would hide a broken schema until the INSERTs fail."""
    monkeypatch.setattr(
        "nflcarddb.d1_http.run_sql",
        lambda *a, **k: (_ for _ in ()).throw(D1Error("no such table: sales")),
    )
    with pytest.raises(D1Error, match="no such table"):
        apply_migrations("acct", "db", "token")


def test_the_shipped_migrations_cover_every_exported_column():
    """A column added to the export but not to MIGRATIONS breaks every push
    into a database created before it."""
    import re

    from nflcarddb.api_export import EXPORT_COLUMNS
    from nflcarddb.d1_http import MIGRATIONS

    schema = (Path(__file__).resolve().parents[1] / "api" / "schema.sql").read_text()
    create = re.search(r"CREATE TABLE IF NOT EXISTS sales \((.*?)\n\);", schema,
                       re.S).group(1)
    in_schema = set(re.findall(r"^\s{4}(\w+)", create, re.M))
    added = {re.search(r"ADD COLUMN (\w+)", m).group(1) for m in MIGRATIONS}

    missing = set(EXPORT_COLUMNS) - in_schema
    assert not missing, f"exported but not in api/schema.sql: {missing}"
    assert added <= in_schema, f"migrated in but not in api/schema.sql: {added - in_schema}"


def test_verify_reports_priced_sales_separately(monkeypatch):
    """`sales` alone reads as wrong to anyone comparing it with a price chart."""
    captured = {}

    def fake_run(account, database, token, sql):
        captured["sql"] = sql
        return {"result": [{"results": [{
            "sales": 20665, "priced_sales": 11160, "days": 1,
            "first_day": "2026-08-03", "last_day": "2026-08-03",
            "active_keys": 1,
        }]}]}

    monkeypatch.setattr("nflcarddb.d1_http.run_sql", fake_run)
    state = verify("acct", "db", "token")

    assert state["sales"] == 20665
    assert state["priced_sales"] == 11160
    assert state["first_day"] == "2026-08-03"
    assert "price_cents IS NOT NULL" in captured["sql"]


def test_verify_survives_an_empty_database(monkeypatch):
    monkeypatch.setattr("nflcarddb.d1_http.run_sql",
                        lambda *a, **k: {"result": [{"results": []}]})
    assert verify("acct", "db", "token") == {}


def test_local_sale_count_matches_what_the_export_sends(tmp_path):
    """The comparison is only useful if both sides count the same rows."""
    from nflcarddb import db as store
    from nflcarddb.api_export import _rows_to_export
    from nflcarddb.cli import _local_sale_count
    from nflcarddb.models import Sale

    path = tmp_path / "count.db"
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    store.upsert_sales(conn, [
        Sale(item_id="1", title="a", price_cents=100, sold_date="2026-08-03"),
        Sale(item_id="2", title="b", price_cents=200, sold_date="2026-08-03"),
        Sale(item_id="3", title="undated", price_cents=300, sold_date=None),
    ], run)
    exported = len(_rows_to_export(conn, None))
    conn.close()

    assert _local_sale_count(path) == exported == 2


def test_local_sale_count_returns_none_for_a_missing_database(tmp_path):
    from nflcarddb.cli import _local_sale_count

    assert _local_sale_count(tmp_path / "nope.db") is None


def test_real_export_survives_a_round_trip(tmp_path):
    """The generated import must split back into exactly its statements."""
    from nflcarddb import db as store
    from nflcarddb.api_export import build_sql
    from nflcarddb.models import CardAttrs, Sale

    db = tmp_path / "rt.db"
    conn = store.connect(db)
    run = store.start_run(conn, "2026-08-03")
    sales = [
        Sale(item_id="900000000001", title="2021 Prizm Ja'Marr Chase; RC #201",
             price_cents=8800, sold_date="2026-08-03"),
        Sale(item_id="900000000002", title="Lot of 3; mixed rookies",
             price_cents=2500, sold_date="2026-08-03"),
    ]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, CardAttrs(player="X")) for s in sales],
                       "t")
    conn.close()

    sql, _ = build_sql(db)
    statements = list(split_statements(sql))

    # Every statement must be complete: balanced quotes once escapes are removed.
    for statement in statements:
        assert statement.replace("''", "").count("'") % 2 == 0, statement[:200]
    assert any("Ja''Marr" in s for s in statements)
    assert any("Lot of 3; mixed rookies" in s for s in statements)


def test_only_changed_rows_are_exported(tmp_path):
    """Re-sending 150,000 rows to deliver one new day is what stops working."""
    from nflcarddb import db as store
    from nflcarddb.api_export import build_sql
    from nflcarddb.models import CardAttrs, Sale

    db = tmp_path / "delta.db"
    conn = store.connect(db)
    run = store.start_run(conn, "2026-08-03")
    store.upsert_sales(conn, [
        Sale(item_id="100000000001", title="old", price_cents=100,
             sold_date="2026-08-03"),
    ], run)
    store.upsert_cards(conn, [("100000000001", CardAttrs(player="A"))], "t")
    mark = store.max_updated_at(conn)

    # A later collection writes a second row with a newer updated_at.
    conn.execute("UPDATE sales SET updated_at = '2999-01-01T00:00:00+00:00' "
                 "WHERE item_id = '100000000001'")
    store.upsert_sales(conn, [
        Sale(item_id="100000000002", title="new", price_cents=200,
             sold_date="2026-08-04"),
    ], run)
    conn.execute("UPDATE sales SET updated_at = '3000-01-01T00:00:00+00:00' "
                 "WHERE item_id = '100000000002'")
    conn.commit()
    conn.close()

    everything, _ = build_sql(db)
    assert "100000000001" in everything and "100000000002" in everything

    delta, stats = build_sql(db, changed_since="2999-06-01T00:00:00+00:00")
    assert "100000000002" in delta
    assert "100000000001" not in delta
    assert stats["rows"] == 1
    # The watermark is the whole table's high-water mark, not the delta's.
    assert stats["watermark"] == "3000-01-01T00:00:00+00:00"


def test_the_watermark_only_advances_on_a_recorded_sync(tmp_path):
    from nflcarddb import db as store

    conn = store.connect(tmp_path / "wm.db")
    assert store.sync_watermark(conn, "db-1") is None

    store.record_sync(conn, "db-1", "2026-08-05T00:00:00+00:00", 500)
    assert store.sync_watermark(conn, "db-1") == "2026-08-05T00:00:00+00:00"

    # Per target: two databases track their own progress.
    assert store.sync_watermark(conn, "db-2") is None

    store.record_sync(conn, "db-1", "2026-08-06T00:00:00+00:00", 20)
    assert store.sync_watermark(conn, "db-1") == "2026-08-06T00:00:00+00:00"
    conn.close()


def test_a_re_collected_day_is_sent_again(tmp_path):
    """upsert_sales bumps updated_at, so fixing a thin day re-uploads it."""
    from nflcarddb import db as store
    from nflcarddb.api_export import build_sql
    from nflcarddb.models import Sale

    db = tmp_path / "recollect.db"
    conn = store.connect(db)
    run = store.start_run(conn, "2026-07-20")
    sale = Sale(item_id="100000000001", title="thin day", price_cents=100,
                sold_date="2026-07-20")
    store.upsert_sales(conn, [sale], run)
    mark = store.max_updated_at(conn)

    # Nothing has changed, so nothing to send.
    assert build_sql(db, changed_since=mark)[1]["rows"] == 0

    # Re-collecting rewrites the row.
    store.upsert_sales(conn, [sale], run)
    conn.execute("UPDATE sales SET updated_at = '3000-01-01T00:00:00+00:00'")
    conn.commit()
    conn.close()

    assert build_sql(db, changed_since=mark)[1]["rows"] == 1
