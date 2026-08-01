"""The static export that feeds the GitHub Pages dashboard.

The important guarantees are about which rows count as *prices*: best-offer
listings show eBay's asking price rather than the accepted one, and non-USD
listings are not FX-converted, so neither may reach a median.
"""

import json

import pytest

from nflcarddb import db as store
from nflcarddb.models import Sale
from nflcarddb.parse_title import parse_title
from nflcarddb.publish import publish


def sale(item_id, price, sold="2025-07-30", *, bo=False, cur="USD",
         title="2023 Panini Prizm CJ Stroud Silver RC #339 PSA 10"):
    return Sale(item_id=item_id, title=title, price_cents=price, shipping_cents=0,
                sold_date=sold, currency=cur, best_offer=bo, query_id="q1")


@pytest.fixture
def published(tmp_path):
    """A db with a known price mix, published to tmp_path/site."""
    db_path = tmp_path / "p.db"
    conn = store.connect(db_path)
    run = store.start_run(conn, "2025-07-30")
    sales = [
        sale("100000000001", 1000),
        sale("100000000002", 2000),
        sale("100000000003", 3000),
        sale("100000000004", 999_00, bo=True),           # ask, not a sale price
        sale("100000000005", 888_00, cur="CAD"),          # no FX conversion
        sale("100000000006", 5000, sold="2025-07-29",
             title="1998 Topps Chrome Peyton Manning Refractor #165 SGC 8.5"),
    ]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in sales], "title/1")
    store.finish_run(conn, run, "ok", 10, len(sales), len(sales))
    conn.close()

    out = tmp_path / "site"
    meta = publish(db_path, out)
    data = {p.stem: json.loads(p.read_text()) for p in out.glob("*.json")}
    return meta, data


def test_writes_every_file_the_dashboard_fetches(published):
    _, data = published
    assert {"meta", "daily", "players", "sets", "grades", "recent"} <= set(data)


def test_volume_counts_everything(published):
    meta, data = published
    assert meta["total_sales"] == 6
    by_day = {d["d"]: d for d in data["daily"]}
    assert by_day["2025-07-30"]["n"] == 5  # includes best-offer and CAD rows


def test_price_stats_exclude_best_offer_and_non_usd(published):
    meta, data = published
    # Only 10.00 / 20.00 / 30.00 on the 30th are usable, so the median is 20.
    by_day = {d["d"]: d for d in data["daily"]}
    assert by_day["2025-07-30"]["median"] == 20.0
    assert by_day["2025-07-30"]["priced"] == 3

    # The 999.00 ask and 888.00 CAD row must not drag the overall median up.
    assert meta["priced_sales"] == 4
    assert meta["median_price"] == 25.0
    assert meta["best_offer_sales"] == 1
    assert meta["non_usd_sales"] == 1


def test_excluded_rows_still_appear_in_the_table(published):
    _, data = published
    ids = {r["id"] for r in data["recent"]}
    assert "100000000004" in ids  # best offer, flagged rather than dropped
    assert "100000000005" in ids  # CAD, currency carried through

    bo = next(r for r in data["recent"] if r["id"] == "100000000004")
    assert bo["bo"] == 1
    cad = next(r for r in data["recent"] if r["id"] == "100000000005")
    assert cad["cur"] == "CAD"


def test_players_aggregate_only_usable_prices(published):
    _, data = published
    stroud = next(r for r in data["players"] if r["player"] == "CJ Stroud")
    assert stroud["n"] == 3          # the ask and the CAD row are excluded
    assert stroud["median"] == 20.0
    assert stroud["max"] == 30.0


def test_grades_and_sets_present(published):
    _, data = published
    assert any(g["grade"] == "PSA 10" for g in data["grades"])
    assert any(s["set"] == "Prizm" for s in data["sets"])


def test_daily_is_sorted_ascending(published):
    _, data = published
    days = [d["d"] for d in data["daily"]]
    assert days == sorted(days)


def test_empty_database_still_publishes_valid_files(tmp_path):
    db_path = tmp_path / "empty.db"
    store.connect(db_path).close()
    out = tmp_path / "site"

    meta = publish(db_path, out)
    assert meta["total_sales"] == 0
    assert meta["median_price"] is None
    assert meta["date_min"] is None
    # The page reads all six regardless; none may be missing or malformed.
    for name in ("meta", "daily", "players", "sets", "grades", "recent"):
        payload = json.loads((out / f"{name}.json").read_text())
        assert payload == [] or isinstance(payload, dict)


def test_republish_overwrites_cleanly(published, tmp_path):
    meta, _ = published
    again = publish(tmp_path / "p.db", tmp_path / "site")
    assert again["total_sales"] == meta["total_sales"]
    assert json.loads((tmp_path / "site" / "meta.json").read_text())["total_sales"] == 6
