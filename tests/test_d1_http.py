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


def _schema_tables():
    """{table: {column, ...}} as api/schema.sql declares them."""
    import re
    schema = (Path(__file__).resolve().parents[1] / "api" / "schema.sql").read_text()
    tables = {}
    for name, body in re.findall(
        r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", schema, re.S
    ):
        tables[name] = set(re.findall(r"^\s{4}(\w+)", body, re.M))
    return tables


def test_every_exported_column_exists_in_the_schema():
    """A column written by the exporter but absent from the schema fails the
    upload on a fresh database, where nothing can be blamed on history."""
    from nflcarddb.api_export import CARD_COLUMNS, EXPORT_COLUMNS, GRADE_COLUMNS

    tables = _schema_tables()
    for columns, table in ((EXPORT_COLUMNS, "sales"),
                           (CARD_COLUMNS, "cards"),
                           (GRADE_COLUMNS, "card_grades")):
        missing = set(columns) - tables[table]
        assert not missing, f"exported into {table} but not declared: {missing}"


def test_every_migrated_column_exists_in_the_schema():
    """The two must not drift apart in either direction.

    A column in the schema but not in MIGRATIONS never reaches the database
    that is already deployed, and the next push fails on it. A column in
    MIGRATIONS but not in the schema means a *fresh* database is missing it
    instead -- the same bug, found by whoever sets up next rather than by the
    person upgrading, which is worse because it looks like a broken project.
    """
    import re

    from nflcarddb.d1_http import MIGRATIONS

    tables = _schema_tables()
    for statement in MIGRATIONS:
        m = re.search(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", statement)
        if not m:
            continue
        table, column = m.group(1), m.group(2)
        assert table in tables, f"migration targets unknown table {table}"
        assert column in tables[table], \
            f"migrated into {table} but not declared in api/schema.sql: {column}"


def test_the_catalogue_tables_are_migrated_in_too():
    """They were added after the database shipped, so CREATE TABLE in the
    schema file alone would never reach it."""
    from nflcarddb.d1_http import MIGRATIONS

    created = " ".join(MIGRATIONS)
    assert "CREATE TABLE IF NOT EXISTS cards" in created
    assert "CREATE TABLE IF NOT EXISTS card_grades" in created


def _plan(sql, params=()):
    """The query plan SQLite picks for `sql` against api/schema.sql."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript(Path("api/schema.sql").read_text(encoding="utf-8"))
    rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    conn.close()
    return " | ".join(r[-1] for r in rows)


@pytest.mark.parametrize("order,extra", [
    ("sales DESC, median_cents DESC", ""),
    ("median_cents DESC, sales DESC", ""),
    ("median_cents ASC, sales DESC", ""),
    ("trend_pct DESC, sales DESC", "AND trend_pct IS NOT NULL"),
    ("trend_pct ASC, sales DESC", "AND trend_pct IS NOT NULL"),
    ("last_sold DESC, sales DESC", ""),
])
def test_the_site_sorts_do_not_scan_the_catalogue(order, extra):
    """Every sort a browsing site offers is `quality` filtered and then ordered.

    An index on the sort column alone cannot serve that -- the filter is
    applied after the scan, so each page view reads all 341,783 cards and
    throws most of them away. D1 bills by rows read, so that is the whole
    catalogue per visitor per sort tab.
    """
    plan = _plan(f"SELECT * FROM cards WHERE quality = 'clean' {extra} "
                 f"ORDER BY {order} LIMIT 50")

    assert "SCAN cards" not in plan, f"full scan for ORDER BY {order}: {plan}"
    assert "USING INDEX" in plan or "USING COVERING INDEX" in plan


def test_one_cards_price_history_reads_only_that_card():
    """The chart query. `sales` is 600,000 rows and growing daily; without the
    index this is the single most expensive thing the site could ask for."""
    plan = _plan("SELECT sold_date, price_cents FROM sales "
                 "WHERE card_key = ? AND price_cents IS NOT NULL "
                 "ORDER BY sold_date", ("k",))

    assert "idx_sales_card" in plan, plan
    assert "SCAN sales" not in plan


def test_every_catalogue_index_is_in_the_migrations_too():
    """The `cards` table arrived after the database shipped.

    Its indexes therefore travel by migration as well as by schema file. The
    sales indexes are not checked: those were in the schema from the start, so
    every database that exists already has them.
    """
    import re

    from nflcarddb.d1_http import MIGRATIONS

    schema = Path("api/schema.sql").read_text(encoding="utf-8")
    migrated = " ".join(MIGRATIONS)
    names = [n for n in re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", schema)
             if n.startswith("idx_cards_")]

    assert names, "no catalogue indexes found -- has the schema been renamed?"
    for name in names:
        assert name in migrated, \
            f"{name} is in api/schema.sql but no migration adds it to a live database"


def test_verify_reports_priced_sales_separately(monkeypatch):
    """`sales` alone reads as wrong to anyone comparing it with a price chart."""
    captured = []

    def fake_run(account, database, token, sql):
        captured.append(sql)
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
    assert "price_cents IS NOT NULL" in captured[0]


def test_verify_survives_an_empty_database(monkeypatch):
    monkeypatch.setattr("nflcarddb.d1_http.run_sql",
                        lambda *a, **k: {"result": [{"results": []}]})
    assert verify("acct", "db", "token") == {}


def test_verify_reports_the_catalogue_not_just_the_sales(monkeypatch):
    """Sales landing while `cards` stays empty is a real, silent half-push.

    The website browses `cards`. A database with 500,000 sales and no
    catalogue looks healthy by every number the old check printed and serves
    an empty site.
    """
    def fake_run(account, database, token, sql):
        if "FROM cards" in sql:
            return {"result": [{"results": [{
                "cards": 41230, "clean_cards": 3110, "card_grades": 52907,
            }]}]}
        return {"result": [{"results": [{
            "sales": 543935, "priced_sales": 300000, "days": 38,
            "first_day": "2026-08-03", "last_day": "2026-09-09",
            "active_keys": 1,
        }]}]}

    monkeypatch.setattr("nflcarddb.d1_http.run_sql", fake_run)
    state = verify("acct", "db", "token")

    assert state["sales"] == 543935
    assert state["cards"] == 41230
    assert state["clean_cards"] == 3110
    assert state["card_grades"] == 52907


def test_verify_still_reports_sales_when_the_catalogue_query_fails(monkeypatch):
    """An older database has no `cards` table. Losing every other number to
    that is the opposite of what this check is for."""
    def fake_run(account, database, token, sql):
        if "FROM cards" in sql:
            raise D1Error("no such table: cards")
        return {"result": [{"results": [{"sales": 12, "active_keys": 0}]}]}

    monkeypatch.setattr("nflcarddb.d1_http.run_sql", fake_run)
    state = verify("acct", "db", "token")

    assert state["sales"] == 12
    assert "cards" not in state


def test_local_sale_count_matches_what_the_export_sends(tmp_path):
    """The comparison is only useful if both sides count the same rows."""
    from nflcarddb import db as store
    from nflcarddb.api_export import _iter_rows_to_export
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
    exported = sum(1 for _ in _iter_rows_to_export(conn, None))
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


# --------------------------------------------------------------- migrations
#
# The live database was created before the catalogue existed. CREATE TABLE IF
# NOT EXISTS is a no-op on a table that is already there, so a column added to
# schema.sql never reaches it and the next upload fails on an unknown column.
# These run the real MIGRATIONS against a real copy of the old schema.


def _old_schema_db(tmp_path):
    """A database shaped like the deployed one: sales, no card columns."""
    import sqlite3
    db = sqlite3.connect(tmp_path / "old.db")
    db.executescript("""
        CREATE TABLE sales (
            item_id TEXT PRIMARY KEY, sold_date TEXT NOT NULL, title TEXT NOT NULL,
            price_cents INTEGER, shipping_cents INTEGER,
            currency TEXT NOT NULL DEFAULT 'USD',
            best_offer INTEGER NOT NULL DEFAULT 0, listing_format TEXT,
            bids INTEGER, player TEXT, team TEXT, year INTEGER, brand TEXT,
            set_name TEXT, parallel TEXT, card_number TEXT, grader TEXT,
            grade REAL, is_rookie INTEGER NOT NULL DEFAULT 0,
            is_auto INTEGER NOT NULL DEFAULT 0, confidence REAL NOT NULL DEFAULT 0);
        CREATE TABLE daily (sold_date TEXT PRIMARY KEY, sales INTEGER NOT NULL,
            priced INTEGER NOT NULL, median_cents INTEGER, p90_cents INTEGER,
            total_cents INTEGER);
        CREATE TABLE api_keys (key_hash TEXT PRIMARY KEY, label TEXT NOT NULL,
            created_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
            daily_quota INTEGER NOT NULL DEFAULT 10000);
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
    """)
    db.commit()
    return db


def _run_migrations(db):
    """What apply_migrations does, minus the HTTP."""
    import sqlite3
    from nflcarddb.d1_http import ALREADY_APPLIED, MIGRATIONS
    applied = 0
    for statement in MIGRATIONS:
        try:
            db.execute(statement)
            applied += 1
        except sqlite3.OperationalError as exc:
            if not any(hint in str(exc).lower() for hint in ALREADY_APPLIED):
                raise
    db.commit()
    return applied


def test_migrations_bring_an_old_database_up_to_the_catalogue(tmp_path):
    db = _old_schema_db(tmp_path)
    _run_migrations(db)

    columns = {r[1] for r in db.execute("PRAGMA table_info(sales)")}
    assert {"subset", "print_run", "is_relic", "card_key"} <= columns
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"cards", "card_grades"} <= tables


def test_migrations_are_safe_to_run_twice(tmp_path):
    """They run on every push, so the second time must do nothing quietly."""
    db = _old_schema_db(tmp_path)
    first = _run_migrations(db)
    second = _run_migrations(db)
    assert first > 0
    # CREATE ... IF NOT EXISTS still "succeeds" the second time; the ALTERs must
    # not, and none of them may raise.
    assert second < first


def test_an_upload_lands_in_a_migrated_database(tmp_path):
    """The end of the chain: migrate, then load what the exporter produces."""
    from nflcarddb import db as store
    from nflcarddb.api_export import build_sql
    from nflcarddb.models import Sale
    from nflcarddb.parse_title import parse_title

    local = tmp_path / "local.db"
    conn = store.connect(local)
    run = store.start_run(conn, "2025-07-30")
    sales = [
        Sale(item_id=f"7000000000{i:02d}",
             title="2024 Panini Prizm Caleb Williams #301 Silver Prizm RC",
             price_cents=5000 + i * 100, shipping_cents=0,
             sold_date=f"2025-07-2{i + 1}", currency="USD", best_offer=False,
             query_id="q1")
        for i in range(4)
    ]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in sales],
                       "title/1")
    store.finish_run(conn, run, "ok", 4, 4, 4)
    conn.close()

    remote = _old_schema_db(tmp_path)
    _run_migrations(remote)
    sql, stats = build_sql(local)
    remote.executescript(sql)

    assert stats["cards"] == 1
    assert remote.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 4
    row = remote.execute(
        "SELECT card_name, sales, subset, numberless FROM cards").fetchone()
    assert row[1] == 4 and row[3] == 0


def _push(tmp_path, monkeypatch, local, remote):
    """Run cmd_d1_push with the network replaced, and return (exit code, IO).

    The helpers are imported inside the function, so they are patched on the
    modules they come from rather than on cli.
    """
    import nflcarddb.cli as cli

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    monkeypatch.setattr(cli, "_local_sale_count", lambda path: local)
    monkeypatch.setattr("nflcarddb.d1_http.verify",
                        lambda *a, **k: {"sales": remote})
    monkeypatch.setattr("nflcarddb.d1_http.push_sql",
                        lambda *a, **k: __import__("nflcarddb.d1_http",
                                                   fromlist=["PushResult"]
                                                   ).PushResult(statements=1, batches=1))
    monkeypatch.setattr("nflcarddb.d1_http.apply_migrations", lambda *a, **k: [])
    monkeypatch.setattr("nflcarddb.api_export.export_api_sql",
                        lambda *a, **k: {"rows": 10, "bytes": 100,
                                         "watermark": "2026-01-01"})
    out = tmp_path / "o.sql"
    out.write_text("SELECT 1;")
    args = cli.build_parser().parse_args(
        ["d1-push", "--account-id", "a", "--database-id", "d",
         "--db", str(tmp_path / "x.db"), "--out", str(out)])
    return cli.cmd_d1_push(args)


def test_d1_holding_more_than_this_pc_is_not_an_upload_failure(
        tmp_path, capsys, monkeypatch):
    """It reported a successful upload of 151,721 rows as a failure.

    D1 keeps every sale ever sent to it; a local database can be rebuilt,
    restored or pruned. So the remote legitimately runs ahead, and only a
    SHORTFALL means something did not land.
    """
    code = _push(tmp_path, monkeypatch, local=151_721, remote=543_935)
    assert code == 0, "a remote ahead of local is not a failure"
    assert "cannot reach them" in capsys.readouterr().out


def test_fewer_rows_in_d1_than_here_is_still_a_failure(
        tmp_path, capsys, monkeypatch):
    """The direction that does mean something did not land."""
    assert _push(tmp_path, monkeypatch, local=151_721, remote=100_000) == 1
    assert "Short:" in capsys.readouterr().err


def test_matching_counts_are_silent(tmp_path, capsys, monkeypatch):
    assert _push(tmp_path, monkeypatch, local=5000, remote=5000) == 0
    captured = capsys.readouterr()
    assert "Short:" not in captured.err and "cannot reach" not in captured.out


def test_the_read_limit_is_named_rather_than_blamed_on_the_token(
        tmp_path, capsys, monkeypatch):
    """It reported Cloudflare's daily quota as "token missing permission".

    Which sent the reader to check credentials that were never wrong, for a
    condition that fixes itself at midnight.
    """
    import nflcarddb.cli as cli
    from nflcarddb.d1_http import D1Error

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")

    def refuse(*a, **k):
        raise D1Error(
            '{"code":7500,"message":"Your account has exceeded D1 free tier '
            'daily row read limit. Upgrade to a paid plan or wait until '
            'tomorrow (midnight UTC)."}')

    # The schema file is pushed before the migrations run, so both must be
    # stubbed or the test reaches the network.
    monkeypatch.setattr("nflcarddb.d1_http.push_sql", refuse)
    monkeypatch.setattr("nflcarddb.d1_http.apply_migrations", refuse)
    args = cli.build_parser().parse_args(
        ["d1-push", "--account-id", "a", "--database-id", "d",
         "--db", str(tmp_path / "z.db"), "--schema", str(tmp_path / "s.sql"),
         "--schema-only"])
    (tmp_path / "s.sql").write_text("SELECT 1;")

    assert cli.cmd_d1_push(args) == 4          # its own code, not a generic fail
    err = capsys.readouterr().err
    assert "5,000,000 rows read" in err
    assert "midnight UTC" in err
    assert "token" in err and "Nothing is wrong with your token" in err


def _push_with(tmp_path, monkeypatch, *, rows, extra_args=()):
    """Run cmd_d1_push with an export of `rows` rows, and report what happened.

    Returns (exit code, dict) where the dict records whether the SQL was
    actually uploaded and what watermark, if any, was written down.
    """
    import nflcarddb.cli as cli
    from nflcarddb.d1_http import PushResult

    seen = {"uploaded": False, "watermark": None}

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    monkeypatch.setattr(cli, "_local_sale_count", lambda path: 0)
    monkeypatch.setattr("nflcarddb.d1_http.verify", lambda *a, **k: {"sales": 5})
    monkeypatch.setattr("nflcarddb.d1_http.apply_migrations", lambda *a, **k: [])

    def upload(*a, **k):
        seen["uploaded"] = True
        return PushResult(statements=1, batches=1)

    monkeypatch.setattr("nflcarddb.d1_http.push_sql", upload)
    monkeypatch.setattr("nflcarddb.api_export.export_api_sql",
                        lambda *a, **k: {"rows": rows, "bytes": 2048,
                                         "watermark": "2026-09-09T21:22:00.477"})
    monkeypatch.setattr(cli.store, "sync_watermark", lambda *a, **k: "2026-09-01")
    monkeypatch.setattr(cli.store, "connect", lambda *a, **k: _NullConn())
    monkeypatch.setattr(
        cli.store, "record_sync",
        lambda conn, target, pushed_at, rows_sent: seen.update(watermark=pushed_at))

    out = tmp_path / "o.sql"
    out.write_text("INSERT INTO api_keys VALUES ('h','website','now');")
    args = cli.build_parser().parse_args(
        ["d1-push", "--account-id", "a", "--database-id", "d",
         "--db", str(tmp_path / "x.db"), "--out", str(out), *extra_args])
    return cli.cmd_d1_push(args), seen


class _NullConn:
    def close(self):
        pass


def test_a_key_still_goes_up_when_no_sale_changed(tmp_path, capsys, monkeypatch):
    """website-key.bat minted a key, printed "registering 1 API key(s)", and
    then skipped the upload because no sale had changed since the last push.

    The key existed only on this PC. Cloudflare had never heard of it, so the
    website got 401 from a database it was supposedly authorised to read. A
    key is not a row, and "no new rows" is not "nothing to send".
    """
    code, seen = _push_with(tmp_path, monkeypatch, rows=0,
                            extra_args=["--add-key", "abc123:website"])

    assert code == 0
    assert seen["uploaded"], "the key was built into the file and never sent"
    assert "sending the key on its own" in capsys.readouterr().out


def test_nothing_at_all_to_send_still_skips_the_upload(tmp_path, capsys, monkeypatch):
    """The saving this early return exists for: a daily push with no new sales
    should not re-upload the file for the sake of it."""
    code, seen = _push_with(tmp_path, monkeypatch, rows=0)

    assert code == 0
    assert not seen["uploaded"]
    assert "Nothing new to upload" in capsys.readouterr().out


def test_a_date_filtered_push_does_not_claim_everything_was_sent(
        tmp_path, capsys, monkeypatch):
    """`--since` deliberately leaves older rows out of the export.

    The watermark is the newest timestamp in the whole local database, so
    recording it after a narrowed run marks rows delivered that were never
    built into the file -- and every later incremental push then skips them.
    """
    code, seen = _push_with(tmp_path, monkeypatch, rows=10,
                            extra_args=["--since", "2026-09-01"])

    assert code == 0
    assert seen["uploaded"]
    assert seen["watermark"] is None, "a partial run must not move the marker"
    assert "--since was used" in capsys.readouterr().out


def test_an_ordinary_push_does_move_the_marker(tmp_path, monkeypatch):
    code, seen = _push_with(tmp_path, monkeypatch, rows=10)
    assert code == 0
    assert seen["watermark"] == "2026-09-09T21:22:00.477"


def test_a_schema_only_push_does_not_count_every_row(tmp_path, monkeypatch):
    """The check is a COUNT over the whole table, and after a schema push there
    is nothing to check -- so it spent the read allowance on nothing."""
    import nflcarddb.cli as cli

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    monkeypatch.setattr("nflcarddb.d1_http.apply_migrations", lambda *a, **k: [])
    monkeypatch.setattr("nflcarddb.d1_http.push_sql",
                        lambda *a, **k: __import__("nflcarddb.d1_http",
                                                   fromlist=["PushResult"]
                                                   ).PushResult())
    called = {"verify": False}

    def spy(*a, **k):
        called["verify"] = True
        return {"sales": 1}

    monkeypatch.setattr("nflcarddb.d1_http.verify", spy)
    (tmp_path / "s.sql").write_text("SELECT 1;")
    args = cli.build_parser().parse_args(
        ["d1-push", "--account-id", "a", "--database-id", "d",
         "--db", str(tmp_path / "z.db"), "--schema", str(tmp_path / "s.sql"),
         "--schema-only"])

    assert cli.cmd_d1_push(args) == 0
    assert not called["verify"]


# --- the browse query a directly-bound website runs -------------------------


def _d1_cards(monkeypatch, argv, rows):
    """Run `d1-cards` with Cloudflare replaced, and return (code, sql, output)."""
    import nflcarddb.cli as cli

    seen = {}

    def fake_run(account, database, token, sql):
        seen["sql"] = sql
        return {"result": [{"results": rows}]}

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    monkeypatch.setattr("nflcarddb.d1_http.run_sql", fake_run)
    args = cli.build_parser().parse_args(
        ["d1-cards", "--account-id", "a", "--database-id", "d", *argv])
    return cli.cmd_d1_cards(args), seen


ONE_CARD = [{"card_name": "2025 Prizm Caleb Williams #1", "player": "Caleb Williams",
             "year": 2025, "set_name": "Prizm", "sales": 40,
             "median_cents": 1250, "trend_pct": 12.5, "last_sold": "2026-09-08"}]


def test_the_preview_defaults_to_the_trustworthy_pile(monkeypatch, capsys):
    """`bucket` rows are not cards -- each is a player's whole run in a set
    gathered under one key. A preview that showed them by default would be
    demonstrating the wrong thing."""
    code, seen = _d1_cards(monkeypatch, [], ONE_CARD)

    assert code == 0
    assert "quality = 'clean'" in seen["sql"]
    assert "Caleb Williams" in capsys.readouterr().out


def test_sorting_by_trend_excludes_cards_that_have_none(monkeypatch):
    """Under four sales there is no trend, and ordering by a NULL puts every
    history-less card at one end of the list."""
    _, seen = _d1_cards(monkeypatch, ["--sort", "rising"], ONE_CARD)
    assert "trend_pct IS NOT NULL" in seen["sql"]
    assert "ORDER BY trend_pct DESC" in seen["sql"]

    _, seen = _d1_cards(monkeypatch, ["--sort", "traded"], ONE_CARD)
    assert "trend_pct IS NOT NULL" not in seen["sql"]


def test_the_printed_sql_matches_what_the_worker_serves():
    """The command exists to hand a site owner the query their site should
    run. Two spellings of "biggest riser" is how they drift apart."""
    from nflcarddb.cli import D1_CARD_SORTS

    worker = Path("api/worker.js").read_text(encoding="utf-8")
    for name, order in D1_CARD_SORTS.items():
        assert f'"{order}"' in worker, f"{name} disagrees with api/worker.js"


def test_every_previewed_sort_is_documented():
    doc = Path("api/SQL.md").read_text(encoding="utf-8")
    from nflcarddb.cli import D1_CARD_SORTS

    for order in D1_CARD_SORTS.values():
        assert order in doc, f"{order} is offered but not in api/SQL.md"


def test_a_players_name_with_an_apostrophe_does_not_break_the_query(monkeypatch):
    """Ja'Marr Chase. The escaping is the upload's own, but this path quotes
    values in by hand, so it needs its own proof."""
    _, seen = _d1_cards(monkeypatch, ["--player", "Ja'Marr Chase"], ONE_CARD)
    assert "'Ja''Marr Chase'" in seen["sql"]
    assert "?" not in seen["sql"]


def test_an_empty_catalogue_says_so_rather_than_printing_a_blank_table(
        monkeypatch, capsys):
    code, _ = _d1_cards(monkeypatch, [], [])
    assert code == 1
    assert "d1-check.bat" in capsys.readouterr().out
