"""Reading accepted Best Offer prices off item pages.

The cost of getting this wrong is a price in the database that looks real and
is not -- worse than the known-high ask it replaced, because the ask was at
least labelled. So most of these tests are about the parser declining: a
shipping charge, a carousel of other listings, a struck-through original, a
foreign dollar, two competing amounts. And about the one thing a lookup is
allowed to do to a price, which is lower it toward what was paid.
"""

from datetime import date
from email.message import EmailMessage

import pytest

from nflcarddb import db as store
from nflcarddb.db import Sale
from nflcarddb.fetch import BlockedError, FetchError, SignedOutError
from nflcarddb.item_page import read_offer
from nflcarddb.offers import (MARKUP_GUARD_PAGES, labelling_by_week, pending,
                              record, run_offer_lookup)

TODAY = date(2026, 10, 13)
RECENT = "2026-10-10"


def _page(body, extra=""):
    return f'<html><body><div id="LeftSummaryPanel">{body}</div>{extra}</body></html>'


ACCEPTED_PAGE = _page(
    '<h1>2024 Prizm Caleb Williams #301</h1>'
    '<div class="x-price-section"><span class="strikethrough">US $45.00</span>'
    '<div>Best offer accepted</div><div>US $38.00</div></div>'
    '<div>US $4.99 shipping</div>',
    extra='<div class="x-sims carousel">Similar items <span>Sold for $12.00</span></div>',
)


# --- the parser ---------------------------------------------------------------


def test_the_accepted_amount_is_read():
    reading = read_offer(ACCEPTED_PAGE, ask_cents=4500)
    assert reading.status == "found"
    assert reading.accepted_cents == 3800


def test_other_listings_on_the_page_are_not_evidence():
    """Every item page ends in carousels of similar items with real prices --
    some even labelled "sold for". Those belong to other cards."""
    reading = read_offer(ACCEPTED_PAGE, ask_cents=4500)
    assert 1200 not in {c.cents for c in reading.candidates}


def test_shipping_is_never_the_price():
    reading = read_offer(_page("Best offer accepted <div>US $4.99 shipping</div>"),
                         ask_cents=4500)
    assert reading.status == "not_shown"
    assert reading.accepted_cents is None


def test_a_struck_through_price_is_never_the_accepted_one():
    """eBay prints "Best offer accepted" right after the crossed-out original.
    Being next to the label does not make the old price the paid one."""
    reading = read_offer(_page("<s>US $45.00</s> Best offer accepted US $38.00"),
                         ask_cents=4500)
    kinds = {c.cents: c.kind for c in reading.candidates}
    assert kinds[4500] == "struck"
    assert reading.accepted_cents == 3800


def test_the_old_layout_showing_only_the_ask_writes_nothing():
    """The ask with a "best offer accepted" note beside it says the price was
    lower and not by how much. Nothing new is learned, so nothing changes."""
    reading = read_offer(_page("US $45.00 Best offer accepted"), ask_cents=4500)
    assert reading.status == "not_shown"


def test_an_amount_above_the_ask_is_refused():
    """An offer cannot be accepted above what the seller asked. A larger
    labelled figure is a misread, and the lookup only ever LOWERS a price."""
    reading = read_offer(_page("Best offer accepted US $52.00"), ask_cents=4500)
    assert reading.status == "not_shown"
    assert reading.accepted_cents is None


def test_an_implausibly_low_amount_is_refused():
    reading = read_offer(_page("Best offer accepted US $2.00"), ask_cents=4500)
    assert reading.status == "not_shown"


def test_two_competing_amounts_write_neither():
    reading = read_offer(
        _page("Best offer accepted US $38.00 and also Accepted offer US $30.00"),
        ask_cents=4500)
    assert reading.status == "ambiguous"
    assert reading.accepted_cents is None


def test_a_foreign_dollar_is_not_a_us_dollar():
    reading = read_offer(_page("Best offer accepted C $30.00"), ask_cents=4500)
    assert reading.status == "not_shown"


def test_a_removed_listing_is_gone():
    reading = read_offer("<html><body><h1>We looked everywhere.</h1>"
                         "<p>Looks like this page is missing.</p></body></html>",
                         ask_cents=4500)
    assert reading.status == "gone"


def test_the_embedded_data_hint_is_reported_but_never_written_alone():
    """A forum lead, unconfirmed. Shown in the check output so a real page can
    settle whether it is trustworthy; never the basis for a price by itself."""
    page = _page("US $45.00",
                 extra='<script>{"taxExclusivePrice":{"value":"39.00"}}</script>')
    reading = read_offer(page, ask_cents=4500)
    assert reading.hint_cents == [3900]
    assert reading.status == "not_shown"


@pytest.mark.parametrize("wording", [
    "Best Offer accepted", "Offer accepted", "Accepted offer", "Sold for",
    "Accepted best offer",
])
def test_the_ways_the_label_might_be_worded(wording):
    reading = read_offer(_page(f"<s>US $45.00</s> {wording} US $40.00"), ask_cents=4500)
    assert reading.accepted_cents == 4000, wording


# --- what gets written --------------------------------------------------------


def _db(tmp_path, rows):
    """rows: (item_id, ask_cents, best_offer, sold_date, title)"""
    conn = store.connect(tmp_path / "offers.sqlite")
    run = store.start_run(conn, RECENT)
    sales = [Sale(item_id=i, title=title, price_cents=ask, currency="USD",
                  shipping_cents=0, sold_date=sold, listing_format="fixed",
                  bids=None, best_offer=bo, condition=None, seller="s",
                  url=f"https://www.ebay.com/itm/{i}", image_url=None,
                  query_id="q", sport="football")
             for i, ask, bo, sold, title in rows]
    store.upsert_sales(conn, sales, run)
    from nflcarddb.parse_title import parse_title
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in sales], "t")
    return conn


CARD = "2024 Panini Prizm Caleb Williams #301 RC"


def test_a_confirmed_price_replaces_the_ask_and_keeps_it(tmp_path):
    conn = _db(tmp_path, [("a", 4500, True, RECENT, CARD)])
    before = conn.execute("SELECT updated_at FROM sales").fetchone()[0]

    record(conn, "a", read_offer(ACCEPTED_PAGE, ask_cents=4500))
    row = conn.execute("SELECT price_cents, ask_cents, offer_status, updated_at "
                       "FROM sales WHERE item_id = 'a'").fetchone()
    conn.close()

    assert row["price_cents"] == 3800
    assert row["ask_cents"] == 4500
    assert row["offer_status"] == "found"
    assert row["updated_at"] > before, "the correction has to reach the upload"


def test_a_re_scrape_cannot_put_the_ask_back(tmp_path):
    """The search results only ever show the ask. A routine re-collection of
    the day would otherwise overwrite the one fact a lookup paid a page for."""
    conn = _db(tmp_path, [("a", 4500, True, RECENT, CARD)])
    record(conn, "a", read_offer(ACCEPTED_PAGE, ask_cents=4500))

    again = Sale(item_id="a", title=CARD, price_cents=4500, currency="USD",
                 sold_date=RECENT, best_offer=False, url="u", query_id="q")
    store.upsert_sales(conn, [again], store.start_run(conn, RECENT))
    row = conn.execute("SELECT price_cents, best_offer FROM sales").fetchone()
    conn.close()

    assert row["price_cents"] == 3800
    assert row["best_offer"] == 1


def test_an_unconfirmed_best_offer_still_takes_the_scraped_price(tmp_path):
    """The protection is for confirmed prices only. Everything else refreshes
    as it always has."""
    conn = _db(tmp_path, [("a", 4500, True, RECENT, CARD)])
    record(conn, "a", read_offer(_page("US $45.00"), ask_cents=4500))
    again = Sale(item_id="a", title=CARD, price_cents=4300, currency="USD",
                 sold_date=RECENT, best_offer=True, url="u", query_id="q")
    store.upsert_sales(conn, [again], store.start_run(conn, RECENT))
    row = conn.execute("SELECT price_cents FROM sales").fetchone()
    conn.close()
    assert row["price_cents"] == 4300


def test_a_reading_that_found_nothing_leaves_the_price_alone(tmp_path):
    conn = _db(tmp_path, [("a", 4500, True, RECENT, CARD)])
    record(conn, "a", read_offer(_page("US $45.00 Best offer accepted"), 4500))
    row = conn.execute("SELECT price_cents, ask_cents, offer_status FROM sales").fetchone()
    conn.close()
    assert (row["price_cents"], row["ask_cents"], row["offer_status"]) == \
        (4500, None, "not_shown")


# --- which sales get looked up ------------------------------------------------


def test_the_most_valuable_asks_come_first(tmp_path):
    """A best offer closes a similar fraction under the ask at any price, so
    the same page request corrects far more dollars at the top of the list."""
    conn = _db(tmp_path, [
        ("cheap", 1200, True, RECENT, CARD),
        ("dear", 40000, True, RECENT, "2024 Panini Prizm Jayden Daniels #316"),
        ("mid", 5000, True, RECENT, "2024 Panini Prizm Drake Maye #317"),
    ])
    order = [r["item_id"] for r in pending(conn, 1000, today=TODAY)]
    conn.close()
    assert order == ["dear", "mid", "cheap"]


def test_only_unconfirmed_recent_best_offers_above_the_floor(tmp_path):
    conn = _db(tmp_path, [
        ("bo", 5000, True, RECENT, CARD),
        ("fixed", 5000, False, RECENT, CARD),          # not a best offer
        ("tiny", 300, True, RECENT, CARD),             # under the floor
        ("old", 5000, True, "2026-06-01", CARD),       # page long gone
    ])
    ids = [r["item_id"] for r in pending(conn, 1000, today=TODAY)]
    conn.close()
    assert ids == ["bo"]


def test_a_sale_restored_without_a_url_is_still_reachable(tmp_path):
    """Rows rebuilt from Cloudflare carry no URL -- the upload has no column
    for it. The item id is enough to build one."""
    conn = _db(tmp_path, [("990000000123", 5000, True, RECENT, CARD)])
    conn.execute("UPDATE sales SET url = NULL")
    conn.commit()
    url = pending(conn, 1000, today=TODAY)[0]["url"]
    conn.close()
    assert url == "https://www.ebay.com/itm/990000000123"


def test_a_finished_lookup_is_not_repeated(tmp_path):
    conn = _db(tmp_path, [("a", 5000, True, RECENT, CARD)])
    record(conn, "a", read_offer(_page("US $50.00"), 5000))
    assert pending(conn, 1000, today=TODAY) == []
    assert [r["item_id"] for r in
            pending(conn, 1000, today=TODAY, retry=("not_shown",))] == ["a"]
    conn.close()


# --- the run ------------------------------------------------------------------


class Pages:
    """Serves item pages by item id; can fail or block on chosen ones."""

    def __init__(self, pages, fail=(), block=None):
        self.pages, self.fail, self.block = pages, set(fail), block
        self.calls = []

    def get(self, url, label=None):
        item = url.rsplit("/", 1)[-1]
        self.calls.append(item)
        if self.block:
            raise self.block("refused")
        if item in self.fail:
            raise FetchError(f"giving up on {url}: timed out")
        return self.pages.get(item, "")

    def budget_exhausted(self):
        return False


def _run(tmp_path, conn, fetcher, **kw):
    conn.close()
    return run_offer_lookup(fetcher, str(tmp_path / "offers.sqlite"),
                            minutes=0, min_ask_cents=1000, today=TODAY, **kw)


def test_a_run_confirms_what_it_can_and_reports_the_discount(tmp_path):
    conn = _db(tmp_path, [("a", 4500, True, RECENT, CARD),
                          ("b", 2000, True, RECENT, "2024 Prizm Drake Maye #317")])
    report = _run(tmp_path, conn, Pages({"a": ACCEPTED_PAGE, "b": _page("US $20.00")}))

    assert (report.found, report.not_shown) == (1, 1)
    assert round(report.discount, 3) == round(1 - 3800 / 4500, 3)


def test_one_page_that_will_not_load_does_not_end_the_run(tmp_path):
    conn = _db(tmp_path, [("a", 9000, True, RECENT, CARD),
                          ("b", 4500, True, RECENT, "2024 Prizm Drake Maye #317")])
    report = _run(tmp_path, conn, Pages({"b": ACCEPTED_PAGE}, fail={"a"}))
    assert report.errors == 1
    assert report.found == 1
    assert report.stopped is None


def test_several_failures_in_a_row_stop_the_run(tmp_path):
    rows = [(f"i{n}", 9000 - n, True, RECENT, CARD) for n in range(6)]
    conn = _db(tmp_path, rows)
    fetcher = Pages({}, fail={f"i{n}" for n in range(6)})
    report = _run(tmp_path, conn, fetcher)
    assert report.stopped == "network"
    assert len(fetcher.calls) == 3


@pytest.mark.parametrize("error,why", [(BlockedError, "blocked"),
                                       (SignedOutError, "signed_out")])
def test_a_block_or_sign_out_stops_at_once(tmp_path, error, why):
    conn = _db(tmp_path, [("a", 9000, True, RECENT, CARD),
                          ("b", 8000, True, RECENT, CARD)])
    fetcher = Pages({}, block=error)
    report = _run(tmp_path, conn, fetcher)
    assert report.stopped == why
    assert fetcher.calls == ["a"]


def test_pages_that_never_show_a_price_stop_the_run_early(tmp_path):
    """If the first pages find nothing, the markup is not what the parser
    expects. Better to say so after a minute than to spend the night proving
    it one page at a time."""
    rows = [(f"i{n:03d}", 90000 - n, True, RECENT, CARD) for n in range(60)]
    conn = _db(tmp_path, rows)
    pages = {f"i{n:03d}": _page("US $900.00") for n in range(60)}
    fetcher = Pages(pages)
    report = _run(tmp_path, conn, fetcher)

    assert report.stopped == "markup"
    assert len(fetcher.calls) == MARKUP_GUARD_PAGES
    assert "offer-check.bat" in report.error


def test_the_time_limit_is_respected(tmp_path):
    conn = _db(tmp_path, [(f"i{n}", 9000 - n, True, RECENT, CARD) for n in range(5)])
    conn.close()
    ticks = iter(range(0, 10_000, 30))           # 30 "seconds" per check
    report = run_offer_lookup(Pages({}), str(tmp_path / "offers.sqlite"),
                              minutes=1, min_ask_cents=1000, today=TODAY,
                              clock=lambda: next(ticks))
    assert report.stopped == "time"
    assert report.looked_up < 5


def test_labelling_share_is_reported_by_week(tmp_path):
    conn = _db(tmp_path, [("a", 5000, True, "2026-10-06", CARD),
                          ("b", 5000, False, "2026-10-06", CARD)])
    weeks = labelling_by_week(conn)
    conn.close()
    assert weeks[0]["pct"] == 50.0


# --- the upload ---------------------------------------------------------------


def test_the_upload_says_which_prices_are_confirmed(tmp_path):
    from nflcarddb.api_export import build_sql

    conn = _db(tmp_path, [("a", 4500, True, RECENT, CARD),
                          ("b", 2000, True, RECENT, "2024 Prizm Drake Maye #317")])
    record(conn, "a", read_offer(ACCEPTED_PAGE, ask_cents=4500))
    conn.close()

    sql, _ = build_sql(tmp_path / "offers.sqlite")
    assert "offer_known" in sql
    # Confirmed: price 3800, ask 4500, offer_known 1.
    assert "'a'" in sql and ", 3800, 4500," in sql
    # Not yet looked up: price and ask are both still the ask.
    assert ", 2000, 2000," in sql


def test_the_upload_schema_has_the_column():
    from pathlib import Path

    from nflcarddb.api_export import EXPORT_COLUMNS
    from nflcarddb.d1_http import MIGRATIONS

    assert "offer_known" in EXPORT_COLUMNS
    assert "offer_known" in Path("api/schema.sql").read_text(encoding="utf-8")
    assert any("offer_known" in m for m in MIGRATIONS)


# --- saved pages ----------------------------------------------------------------


def test_a_single_file_save_is_unpacked_before_parsing(tmp_path):
    """Chrome's "Webpage, Single File" is MHTML: quoted-printable, every `=`
    written as `=3D`. Read as plain text the markup stops parsing."""
    from nflcarddb.ingest import read_saved_html

    msg = EmailMessage()
    msg.set_content(ACCEPTED_PAGE, subtype="html", cte="quoted-printable")
    path = tmp_path / "listing.mhtml"
    path.write_bytes(msg.as_bytes())

    html = read_saved_html(path)
    assert "=3D" not in html
    assert read_offer(html, ask_cents=4500).accepted_cents == 3800


def _bare_config(tmp_path):
    """A config naming no vocabulary files. `main` registers whatever the
    config names into the parser's shared state, and the repo's real word
    lists leaking into other tests changes what THEY parse."""
    import yaml

    cfg = tmp_path / "q.yml"
    cfg.write_text(yaml.safe_dump({
        "database": str(tmp_path / "x.db"),
        "queries": [{"id": "a", "keywords": "football"}],
    }))
    return str(cfg)


def test_checking_a_saved_page_writes_nothing(tmp_path, capsys):
    from nflcarddb.cli import main

    page = tmp_path / "listing.html"
    page.write_text(ACCEPTED_PAGE, encoding="utf-8")
    code = main(["offers", "--config", _bare_config(tmp_path), "--file", str(page)])
    out = capsys.readouterr().out
    assert code == 0
    assert "ACCEPTED OFFER: $38.00" in out


def test_lookups_are_off_until_turned_on(tmp_path, capsys):
    from nflcarddb.cli import main

    assert main(["offers", "--config", _bare_config(tmp_path)]) == 0
    assert "off" in capsys.readouterr().out


def test_the_command_runs_end_to_end_through_a_real_fetcher(tmp_path, monkeypatch, capsys):
    """The report printing lives only in the command, and a crash there would
    surface at night with nobody watching. Network replaced, nothing else."""
    import yaml

    from nflcarddb import fetch as fetch_mod
    from nflcarddb.cli import main

    db = tmp_path / "offers.sqlite"
    conn = _db(tmp_path, [("a", 4500, True, RECENT, CARD)])
    conn.close()
    cfg = tmp_path / "q.yml"
    cfg.write_text(yaml.safe_dump({
        "database": str(db),
        "offer_lookup_minutes": 5,
        "fetch": {"delay": 0, "jitter": 0, "max_retries": 0, "engine": "requests"},
        "queries": [{"id": "a", "keywords": "football"}],
    }))
    monkeypatch.setattr(fetch_mod.Fetcher, "get",
                        lambda self, url, label=None: ACCEPTED_PAGE)
    # Restored sales are dated by the real clock in the command; keep the
    # fixture's sale inside the lookup window whatever today is.
    conn = store.connect(db)
    conn.execute("UPDATE sales SET sold_date = date('now', '-2 days')")
    conn.commit()
    conn.close()

    code = main(["offers", "--config", str(cfg)])
    out = capsys.readouterr().out
    print(out)

    assert code == 0
    line = next(l for l in out.splitlines() if "accepted" in l and "->" in l)
    assert "$    45.00 ask" in line and "$    38.00 accepted" in line
    assert "16% under the ask" in out
    conn = store.connect(db)
    assert conn.execute("SELECT price_cents FROM sales").fetchone()[0] == 3800
    conn.close()
