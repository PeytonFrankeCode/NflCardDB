"""Uploading to D1 over its HTTP API.

The statement splitter is the part that can corrupt data rather than merely
fail, so most of this is about it: card titles are seller-written and routinely
contain semicolons and apostrophes.
"""

import pytest

from nflcarddb.d1_http import (
    D1Error,
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
