"""Reading back what the collector recorded about its own coverage.

The collector has written a status and a note for every band it walked since
the beginning, and nothing ever read them. That is how "it feels like we are
getting less than we could" stayed a feeling instead of becoming a number.

The three losses need three different fixes, so the tests here are mostly
about keeping them apart.
"""

from nflcarddb import db as store
from nflcarddb.db import Sale
from nflcarddb.leaks import band_suggestions, leak_report, query_yield

WALLED = "still capped at max depth 3; some sales may be missed"
NO_TIME = "page budget ran out before this band was walked"


def _db(tmp_path, segments, sales=(), date="2026-09-15"):
    conn = store.connect(tmp_path / "leaks.sqlite")
    run = store.start_run(conn, date)
    for seg in segments:
        store.record_segment(conn, run, seg[0], seg[1], seg[2], seg[3],
                             seg[4], seg[5], seg[6], seg[7])
    if sales:
        store.upsert_sales(conn, [
            Sale(item_id=f"s{i}", title=f"2025 Prizm Player {i} #{i}",
                 price_cents=1000, currency="USD", shipping_cents=0,
                 sold_date=date, listing_format="Auction", bids=1,
                 best_offer=0, condition=None, seller="x", url="u",
                 image_url=None, query_id=q)
            for i, q in enumerate(sales)
        ], run)
    store.finish_run(conn, run, "ok", 1, 1, 1)
    return conn


def test_a_run_with_nothing_wrong_reports_no_losses(tmp_path):
    conn = _db(tmp_path, [("a", "football_singles", None, 10, "done", 5, 900, None)])
    try:
        report = leak_report(conn)
    finally:
        conn.close()

    assert report["segments"] == 1
    assert not [s for s in report["by_status"] if s != "done"]
    assert report["worst"] == []


def test_the_three_losses_are_counted_apart(tmp_path):
    """Each names a different fix. Summing them into "how much is missing"
    would produce a number nobody can act on."""
    conn = _db(tmp_path, [
        ("a", "football_singles", 25, 50, "capped", 42, 10080, WALLED),
        ("b", "nfl_singles", 1000, None, "unreached", 0, 0, NO_TIME),
        ("c", "football_graded", None, 50, "incomplete", 42, 3000,
         "never reached 2026-09-15; more sales exist in this band"),
        ("d", "football_singles", None, 10, "done", 8, 1900, None),
    ])
    try:
        report = leak_report(conn)
    finally:
        conn.close()

    assert report["by_status"]["capped"]["segments"] == 1
    assert report["by_status"]["unreached"]["segments"] == 1
    assert report["by_status"]["incomplete"]["segments"] == 1
    assert report["by_status"]["done"]["segments"] == 1


def test_a_band_that_was_subdivided_is_not_a_wall(tmp_path):
    """"capped" means two different things. A band that was split has had its
    contents collected by its children; only one still capped at full depth
    has sales behind a wall -- and confusing them would send someone tuning
    price bands that are already working."""
    conn = _db(tmp_path, [
        ("a", "football_singles", 25, 50, "capped", 42, 10080,
         "subdivided into $25-$35 and $35-$50"),
        ("b", "football_singles", 50, 100, "capped", 42, 10080, WALLED),
    ])
    try:
        report = leak_report(conn)
    finally:
        conn.close()

    assert len(report["worst"]) == 1
    assert report["worst"][0]["price_lo"] == 50


def test_the_worst_walls_come_first(tmp_path):
    conn = _db(tmp_path, [
        ("a", "football_singles", 10, 25, "capped", 42, 5000, WALLED),
        ("b", "football_singles", 25, 50, "capped", 42, 10080, WALLED),
    ])
    try:
        worst = leak_report(conn)["worst"]
    finally:
        conn.close()

    assert [w["items"] for w in worst] == [10080, 5000]


def test_suggested_bands_cut_where_the_walls_are(tmp_path):
    """The geometric midpoint, for the same reason the walker splits that way:
    card prices are log-distributed, so the arithmetic middle of $25-$1000
    puts nearly every sale on one side of the cut."""
    existing = [(None, 10), (10, 25), (25, 50), (50, None)]
    walls = [{"price_lo": 25, "price_hi": 50, "items": 10080}]

    bands = band_suggestions(walls, existing)

    edges = [b[0] for b in bands if b[0] is not None]
    assert 35.36 in edges, "the geometric midpoint of 25 and 50"
    assert len(bands) == len(existing) + 1
    assert bands[0][0] is None and bands[-1][1] is None


def test_suggested_bands_stay_in_order_and_cover_everything(tmp_path):
    bands = band_suggestions(
        [{"price_lo": 25, "price_hi": 50, "items": 1},
         {"price_lo": 100, "price_hi": 250, "items": 1}],
        [(None, 10), (10, 25), (25, 50), (50, 100), (100, 250), (250, None)],
    )

    assert bands[0][0] is None
    assert bands[-1][1] is None
    for (_, hi), (lo, _) in zip(bands, bands[1:]):
        assert hi == lo, "a gap between bands is sales nobody asks for"


def test_an_open_ended_wall_does_not_crash_the_split(tmp_path):
    """"$1000 and up" has no midpoint. Its edge is still where the wall is."""
    bands = band_suggestions(
        [{"price_lo": 1000, "price_hi": None, "items": 10080}],
        [(None, 10), (1000, None)],
    )
    assert bands
    assert bands[-1][1] is None


def test_query_contribution_counts_first_finder_not_busiest(tmp_path):
    """Queries overlap freely -- a listing found twice is stored once -- so
    "how many did this search return" says nothing about whether it earns its
    time. What matters is what would be missing without it."""
    conn = _db(tmp_path, [("a", "football_singles", None, 10, "done", 5, 9, None)],
               sales=["football_singles"] * 8 + ["nfl_singles"] * 2)
    try:
        rows = query_yield(conn)
    finally:
        conn.close()

    assert rows[0]["query_id"] == "football_singles"
    assert rows[0]["sales"] == 8
    assert rows[1]["sales"] == 2


def test_an_empty_database_reports_nothing_rather_than_failing(tmp_path):
    conn = store.connect(tmp_path / "empty.sqlite")
    try:
        report = leak_report(conn)
    finally:
        conn.close()

    assert report["runs"] == 0
    assert report["worst"] == []
