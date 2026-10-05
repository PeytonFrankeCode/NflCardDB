"""Why a run takes as long as it does, and what a change would really save.

The numbers here decide whether to delete a search, so the arithmetic matters
more than usual: a saving reported without what it gives up is how a
collection quietly loses a slice of itself to a tidy-up.
"""

from datetime import datetime, timedelta, timezone

from nflcarddb import db as store
from nflcarddb.db import Sale
from nflcarddb.pace import (band_efficiency, cost_by_query, measured_pace,
                            projected_minutes, savings)

START = datetime(2026, 10, 4, 2, 0, tzinfo=timezone.utc)


def _db(tmp_path, queries, hours=3.0, sales=()):
    """queries: {query_id: (pages, bands)}"""
    conn = store.connect(tmp_path / "pace.sqlite")
    run = store.start_run(conn, "2026-10-03")
    total = 0
    for qid, (pages, bands) in queries.items():
        total += pages
        # Distributed exactly, remainder included: a fixture that loses three
        # pages to integer division makes every assertion about pages look
        # like an off-by-one in the code.
        for i in range(bands):
            share = pages // bands + (1 if i < pages % bands else 0)
            store.record_segment(conn, run, f"{qid}:{i}", qid, i * 10, (i + 1) * 10,
                                 "done", share, 3000, None)
    conn.execute(
        "UPDATE scrape_runs SET started_at=?, finished_at=?, pages_fetched=?, "
        "items_seen=?, status='ok' WHERE run_id=?",
        (START.isoformat(), (START + timedelta(hours=hours)).isoformat(),
         total, 1000, run))
    conn.commit()
    if sales:
        store.upsert_sales(conn, [
            Sale(item_id=f"s{i}", title=f"2025 Prizm P{i} #{i}", price_cents=100,
                 currency="USD", shipping_cents=0, sold_date="2026-10-03",
                 listing_format="Auction", bids=1, best_offer=0, condition=None,
                 seller="x", url="u", image_url=None, query_id=q)
            for i, q in enumerate(sales)
        ], run)
    return conn


def test_pace_is_measured_from_the_clock_not_the_setting():
    """The configured delay is a floor. The page still has to load, and a
    retry costs time the setting knows nothing about -- so predicting a run's
    length from the config alone understates it every time."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(Path(tmp), {"a": (1200, 7)}, hours=2.0)
        try:
            pace = measured_pace(conn)
        finally:
            conn.close()

    assert pace["pages"] == 1200
    assert pace["seconds_per_page"] == 6.0      # 2 hours / 1200 pages


def test_a_run_with_no_clock_does_not_invent_a_pace(tmp_path):
    conn = store.connect(tmp_path / "unfinished.sqlite")
    store.start_run(conn, "2026-10-03")
    try:
        pace = measured_pace(conn)
    finally:
        conn.close()

    assert pace["runs"] == 0
    assert pace["seconds_per_page"] is None


def test_value_compares_share_of_data_against_share_of_pages(tmp_path):
    """Not sales per page in the abstract -- share against share, so the
    number says "this query is spending more of the run than it is worth"
    rather than something that depends on how busy the day was."""
    conn = _db(tmp_path,
               {"broad": (400, 7), "alternate": (400, 7)},
               sales=["broad"] * 90 + ["alternate"] * 10)
    try:
        cost = {q["query_id"]: q for q in cost_by_query(conn)}
    finally:
        conn.close()

    assert cost["broad"]["page_share"] == cost["alternate"]["page_share"]
    assert cost["broad"]["value"] > 1.0
    assert cost["alternate"]["value"] < 0.5


def test_the_worst_value_is_listed_first(tmp_path):
    """The report's job is to name what to cut, so the order is the answer."""
    conn = _db(tmp_path, {"good": (100, 7), "bad": (400, 7)},
               sales=["good"] * 50 + ["bad"] * 2)
    try:
        cost = cost_by_query(conn)
    finally:
        conn.close()

    assert cost[0]["query_id"] == "bad"


def test_a_query_that_found_nothing_is_not_a_division_by_zero(tmp_path):
    """A query eBay dislikes returns zero results rather than an error, so
    this is the shape of a broken query and it must survive being reported."""
    conn = _db(tmp_path, {"empty": (300, 7)}, sales=[])
    try:
        cost = cost_by_query(conn)
    finally:
        conn.close()

    assert cost[0]["sales"] == 0
    assert cost[0]["pages_per_sale"] is None
    assert cost[0]["value"] == 0.0


def test_savings_report_both_halves(tmp_path):
    """Time saved and data lost, together. Either alone is a sales pitch."""
    conn = _db(tmp_path, {"keep": (400, 7), "drop": (400, 7)}, hours=2.0,
               sales=["keep"] * 95 + ["drop"] * 5)
    try:
        pace = measured_pace(conn)
        cost = cost_by_query(conn)
    finally:
        conn.close()

    effect = savings(cost, {"drop"}, pace)
    assert effect["pages_saved"] == 400
    assert round(effect["share_of_run"], 2) == 0.5
    assert effect["sales_lost"] == 5
    assert round(effect["share_of_data"], 2) == 0.05
    # 400 pages at the measured pace, not at the configured delay.
    assert round(effect["minutes_saved"], 0) == round(
        projected_minutes(400, pace["seconds_per_page"]), 0)


def test_dropping_nothing_saves_nothing(tmp_path):
    conn = _db(tmp_path, {"a": (400, 7)}, sales=["a"] * 10)
    try:
        effect = savings(cost_by_query(conn), set(), measured_pace(conn))
    finally:
        conn.close()

    assert effect["pages_saved"] == 0
    assert effect["sales_lost"] == 0


def test_a_band_returning_almost_nothing_is_flagged(tmp_path):
    conn = store.connect(tmp_path / "bands.sqlite")
    run = store.start_run(conn, "2026-10-03")
    store.record_segment(conn, run, "a:0", "q", None, 10, "done", 40, 9000, None)
    store.record_segment(conn, run, "a:1", "q", 1000, None, "done", 40, 30, None)
    conn.execute("UPDATE scrape_runs SET finished_at=?, pages_fetched=80 "
                 "WHERE run_id=?", (START.isoformat(), run))
    conn.commit()
    try:
        idle = band_efficiency(conn)
    finally:
        conn.close()

    assert len(idle) == 1
    assert idle[0]["price_lo"] == 1000
