"""The static export that feeds the GitHub Pages dashboard.

The guarantee here is about which rows count as *prices*. Best offers are
included by choice, even though their number is the seller's ask rather than
what was paid -- so the medians read high, knowingly, and the rows stay flagged.
Non-USD listings are still excluded, because nothing converts them.
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
        sale("100000000004", 999_00, bo=True),           # an ask, counted anyway
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


def test_price_stats_include_asks_and_still_exclude_non_usd(published):
    meta, data = published
    # 10.00 / 20.00 / 30.00 plus the 999.00 ask -> median 25. The CAD row is
    # still out: there is no FX conversion, so it is not comparable at all.
    by_day = {d["d"]: d for d in data["daily"]}
    assert by_day["2025-07-30"]["median"] == 25.0
    assert by_day["2025-07-30"]["priced"] == 4

    assert meta["priced_sales"] == 5
    assert meta["non_usd_sales"] == 1
    # Still counted separately, so the effect of including them stays visible.
    assert meta["best_offer_sales"] == 1


def test_the_ask_is_what_lifts_the_median(published):
    """Guards the trade-off rather than assuming it: excluding the one ask
    would put this back at 20.00, and that difference is the whole choice."""
    _, data = published
    by_day = {d["d"]: d for d in data["daily"]}
    assert by_day["2025-07-30"]["median"] == 25.0     # with the 999.00 ask
    assert by_day["2025-07-30"]["p90"] == 999.0


def test_rows_stay_flagged_in_the_table(published):
    _, data = published
    ids = {r["id"] for r in data["recent"]}
    assert "100000000004" in ids  # best offer, counted AND flagged
    assert "100000000005" in ids  # CAD, currency carried through

    bo = next(r for r in data["recent"] if r["id"] == "100000000004")
    assert bo["bo"] == 1
    cad = next(r for r in data["recent"] if r["id"] == "100000000005")
    assert cad["cur"] == "CAD"


def test_players_aggregate_every_priced_row(published):
    _, data = published
    stroud = next(r for r in data["players"] if r["player"] == "CJ Stroud")
    assert stroud["n"] == 4          # the ask counts; the CAD row still does not
    assert stroud["median"] == 25.0
    assert stroud["max"] == 999.0


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


# ---------------------------------------------------------------- cards.json
#
# The identity layer's payoff. Sales have carried a shared `card_key` since the
# beginning; until this file published it, the dashboard could say what sold
# yesterday but never what one card has done over time.


def test_cards_group_sales_of_the_same_card(published):
    _, data = published
    # Six sales, two distinct cards -- but the Manning sold once, and one price
    # is not a history, so only the Stroud is published.
    assert len(data["cards"]) == 1
    stroud = data["cards"][0]
    assert stroud["player"] == "CJ Stroud"
    assert stroud["n"] == 4              # the ask counts; the CAD row does not
    assert stroud["median"] == 25.0
    assert stroud["low"] == 10.0
    assert stroud["high"] == 999.0


def test_a_card_that_sold_once_is_not_a_history(published):
    _, data = published
    assert not any("Manning" in (c["player"] or "") for c in data["cards"])


def test_every_sale_carries_its_date_price_and_grade(published):
    _, data = published
    series = data["cards"][0]["sales"]
    assert len(series) == 4
    assert all(len(s) == 3 for s in series)
    assert [s[0] for s in series] == sorted(s[0] for s in series)
    assert {s[2] for s in series} == {"PSA 10"}


def _history(tmp_path, prices, name="hist.db"):
    """A single card sold on consecutive days at the given prices."""
    db_path = tmp_path / name
    conn = store.connect(db_path)
    run = store.start_run(conn, "2025-08-01")
    sales = [
        sale(f"20000000000{i}", int(p * 100), sold=f"2025-08-0{i + 1}")
        for i, p in enumerate(prices)
    ]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in sales], "title/1")
    store.finish_run(conn, run, "ok", len(sales), len(sales), len(sales))
    conn.close()
    out = tmp_path / f"site-{name}"
    publish(db_path, out)
    return json.loads((out / "cards.json").read_text())[0]


def test_trend_compares_halves_not_endpoints(tmp_path):
    """One odd sale at either end must not become the whole trend.

    Flat at 10 except a single 100 on the last day: endpoints would report
    +900%, halves report nothing at all, which is the honest answer.
    """
    card = _history(tmp_path, [10, 10, 10, 10, 10, 100])
    assert card["trend"] == 0.0


def test_trend_reads_a_real_move(tmp_path):
    card = _history(tmp_path, [10, 10, 10, 20, 20, 20])
    assert card["trend"] == 100.0


def test_no_trend_from_too_few_sales(tmp_path):
    """Two points make a line through anything."""
    card = _history(tmp_path, [10, 40], name="two.db")
    assert card["n"] == 2
    assert card["trend"] is None


def test_empty_database_publishes_an_empty_card_list(tmp_path):
    db_path = tmp_path / "empty-cards.db"
    store.connect(db_path).close()
    out = tmp_path / "site"
    publish(db_path, out)
    assert json.loads((out / "cards.json").read_text()) == []


def test_the_group_is_named_by_what_it_agrees_on(tmp_path):
    """Not by whichever sale happened to come first.

    card_name carries claimed-but-unkeyed words, so members of one group spell
    themselves differently. Picking the first made a card's displayed name
    depend on the order it sold in -- which is why one line read "2024 Prizm
    Rookie Card Drake Maye #329" and the next read plain "#301 Base".
    """
    db_path = tmp_path / "name.db"
    conn = store.connect(db_path)
    run = store.start_run(conn, "2025-07-30")
    titles = [
        "2024 Panini Prizm Drake Maye #329 Rookie Card",   # sells first
        "2024 Panini Prizm Drake Maye #329",
        "2024 Panini Prizm Drake Maye #329",
    ]
    sales = [
        sale(f"5000000000{i:02d}", 5000, sold=f"2025-07-2{i + 1}", title=t)
        for i, t in enumerate(titles)
    ]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in sales], "title/1")
    store.finish_run(conn, run, "ok", 3, 3, 3)
    conn.close()

    publish(db_path, tmp_path / "s")
    card = json.loads((tmp_path / "s" / "cards.json").read_text())[0]
    assert card["n"] == 3
    assert "Rookie Card" not in card["name"]


def test_a_group_with_no_card_number_is_flagged(tmp_path):
    """Keyed by player, so it is a bucket rather than a card, and says so."""
    db_path = tmp_path / "nonum.db"
    conn = store.connect(db_path)
    run = store.start_run(conn, "2025-07-30")
    sales = [
        sale("600000000001", 500, sold="2025-07-21",
             title="2025 Topps Chrome Cam Ward Refractor"),
        sale("600000000002", 42500, sold="2025-07-22",
             title="2025 Topps Chrome Cam Ward Refractor"),
        sale("600000000003", 5000, sold="2025-07-21",
             title="2025 Topps Chrome Cam Ward #314 Refractor"),
        sale("600000000004", 5200, sold="2025-07-22",
             title="2025 Topps Chrome Cam Ward #314 Refractor"),
    ]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in sales], "title/1")
    store.finish_run(conn, run, "ok", 4, 4, 4)
    conn.close()

    publish(db_path, tmp_path / "s")
    cards = {c["nonum"]: c for c in
             json.loads((tmp_path / "s" / "cards.json").read_text())}
    assert set(cards) == {0, 1}
    assert "314" in cards[0]["name"]        # the real card
    assert "314" not in cards[1]["name"]    # the bucket


# ------------------------------------------------------------------- quality
#
# A third of the catalogue is doubtful, and the useful answer is not to delete
# it but to sort it into piles. The signal is price dispersion INSIDE one grade:
# a card whose raw copies run $1 to $29 is two cards sharing a key.


def test_dispersion_is_measured_within_a_grade_not_across_them():
    """Grade is not part of a card's identity, so comparing a PSA 10 against a
    raw copy measures grading rather than a bad grouping. This card is coherent
    in both markets and must not be flagged for the gap between them."""
    from nflcarddb.publish import price_dispersion
    spread = price_dispersion({
        "Raw": [10.0, 11.0, 12.0, 10.5, 11.5],
        "PSA 10": [300.0, 310.0, 305.0, 295.0, 302.0],
    })
    assert spread is not None and spread < 2


def test_one_odd_sale_does_not_condemn_a_card():
    """p90 over p10, not high over low: an outlier at either end is exactly
    what a robust measure has to survive."""
    from nflcarddb.publish import price_dispersion
    spread = price_dispersion({"Raw": [10, 11, 10.5, 11.5, 12, 10, 11, 900]})
    assert spread < 8


def test_a_merged_card_is_caught():
    """2026 Topps Drew Allar #304 Base: raw copies from $1.00 to $28.99, which
    is a colour parallel that was never recognised."""
    from nflcarddb.publish import price_dispersion
    spread = price_dispersion(
        {"Raw": [1.0, 1.0, 1.25, 2.0, 6.5, 8.0, 15.0, 22.0, 28.99, 25.0]})
    assert spread >= 8


def test_too_few_sales_is_not_a_verdict():
    from nflcarddb.publish import price_dispersion
    assert price_dispersion({"Raw": [1.0, 500.0]}) is None


def test_quality_separates_cannot_tell_from_looks_wrong():
    """The distinction that stops the tiers overstating themselves."""
    from nflcarddb.publish import card_quality
    assert card_quality(False, 1.5) == "clean"
    assert card_quality(False, 30.0) == "suspect"
    assert card_quality(False, None) == "unproven"
    # A bucket is a bucket whatever its prices happen to look like -- it is not
    # one card, so a tight spread would only mean the guesses agreed.
    assert card_quality(True, 1.1) == "bucket"


def test_published_cards_carry_their_verdict(published):
    _, data = published
    card = data["cards"][0]
    assert card["quality"] in {"clean", "suspect", "unproven", "bucket"}
    assert "spread" in card
