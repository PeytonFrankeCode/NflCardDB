"""End-to-end run of the pipeline with eBay replaced by fixture HTML.

Exercises config -> fetch -> parse -> SQLite without touching the network.
"""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from nflcarddb import db as store
from nflcarddb import fetch as fetch_mod
from nflcarddb.config import load_config
from nflcarddb.pipeline import reparse_titles, run_scrape

FIXTURES = Path(__file__).parent / "fixtures"


def _tile(item_id, title, sold, price="10.00"):
    return (
        f'<li class="s-item"><a class="s-item__link" '
        f'href="https://www.ebay.com/itm/{item_id}">'
        f'<div class="s-item__title"><span role="heading">{title}</span></div></a>'
        f'<span class="s-item__price">${price}</span>'
        f'<div class="s-item__caption"><span>Sold  {sold}</span></div></li>'
    )


def _page(tiles, count="120"):
    return (
        f'<h1 class="srp-controls__count-heading">{count} results</h1>'
        f'<ul class="srp-results">{"".join(tiles)}</ul>'
    )


@pytest.fixture
def project(tmp_path):
    """A config + db path pointing entirely inside tmp_path."""
    db_path = tmp_path / "test.db"
    cfg_path = tmp_path / "queries.yml"
    cfg_path.write_text(yaml.safe_dump({
        "database": str(db_path),
        "fetch": {
            "engine": "requests", "delay": 0, "jitter": 0, "items_per_page": 4,
            "max_pages_per_segment": 5, "page_budget": 50,
        },
        "price_bands": [[None, 50], [50, None]],
        "queries": [{"id": "football_singles", "keywords": "football", "category": "261328"}],
    }))
    return cfg_path, db_path


@pytest.fixture
def serve(monkeypatch):
    """Route Fetcher.get to a callable that answers based on URL params."""

    def _install(handler):
        def fake_get(self, url, label=None):
            self.stats.requests += 1
            return handler(parse_qs(urlparse(url).query))

        monkeypatch.setattr(fetch_mod.Fetcher, "get", fake_get)

    return _install


def test_full_run_stores_sales_and_parsed_cards(project, serve):
    cfg_path, db_path = project

    def handler(params):
        # Page 1 of each band has target-date sales; page 2 is older, ending the walk.
        if params["_pgn"] == ["1"]:
            lo = params.get("_udlo", ["0"])[0]
            return _page([
                _tile(f"90000000000{lo}1", "2023 Panini Prizm CJ Stroud Silver RC #339 PSA 10", "Jul 30, 2025"),
                _tile(f"90000000000{lo}2", "2021 Donruss Optic Ja'Marr Chase Holo #201 BGS 9.5", "Jul 30, 2025"),
            ])
        return _page([_tile("800000000001", "old card", "Jul 28, 2025")])

    serve(handler)
    config = load_config(cfg_path)
    report = run_scrape(config, target_date="2025-07-30")

    assert report.status == "ok"
    assert report.seen == 4  # two bands x two listings
    assert report.new == 4

    conn = store.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 4
    assert conn.execute(
        "SELECT COUNT(*) FROM sales WHERE sold_date != '2025-07-30'"
    ).fetchone()[0] == 0

    # Titles were parsed into the cards table during the same run.
    row = conn.execute(
        "SELECT player, set_name, grade FROM cards ORDER BY player LIMIT 1"
    ).fetchone()
    assert row["player"] == "CJ Stroud"
    assert row["set_name"] == "Prizm"
    assert row["grade"] == 10.0

    run = conn.execute("SELECT status, items_new FROM scrape_runs").fetchone()
    assert run["status"] == "ok"
    assert run["items_new"] == 4
    conn.close()


def test_rerunning_a_day_does_not_duplicate(project, serve):
    cfg_path, db_path = project
    serve(lambda params: _page([
        _tile("910000000001", "2023 Prizm Bijan Robinson RC #44", "Jul 30, 2025")
    ]) if params["_pgn"] == ["1"] else _page([]))

    config = load_config(cfg_path)
    first = run_scrape(config, target_date="2025-07-30")
    second = run_scrape(config, target_date="2025-07-30")

    assert first.new == 1
    assert second.seen >= 1
    assert second.new == 0  # already known

    conn = store.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 1
    conn.close()


def test_block_mid_run_keeps_partial_data(project, serve, monkeypatch):
    cfg_path, db_path = project
    calls = {"n": 0}

    def handler(params):
        calls["n"] += 1
        if calls["n"] > 1:
            raise fetch_mod.BlockedError("bot check")
        return _page([_tile("920000000001", "2023 Prizm Puka Nacua RC #364", "Jul 30, 2025")])

    serve(handler)
    config = load_config(cfg_path)
    report = run_scrape(config, target_date="2025-07-30")

    assert report.status == "partial"
    assert "bot check" in (report.error or "")

    # The listing fetched before the block must survive.
    conn = store.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 1
    assert conn.execute("SELECT status FROM scrape_runs").fetchone()[0] == "partial"
    conn.close()


def test_dry_run_writes_nothing(project, serve):
    cfg_path, db_path = project
    serve(lambda params: _page([
        _tile("930000000001", "2023 Prizm Anthony Richardson RC #384", "Jul 30, 2025")
    ]) if params["_pgn"] == ["1"] else _page([]))

    config = load_config(cfg_path)
    report = run_scrape(config, target_date="2025-07-30", dry_run=True)

    assert report.seen == 0
    conn = store.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 0
    conn.close()


def test_page_budget_caps_requests(project, serve):
    cfg_path, _ = project
    # Always return a full page of target-date sales so nothing else stops the walk.
    serve(lambda params: _page([
        _tile(f"9400000000{params['_pgn'][0]}{i}", f"2023 Prizm Player{i} #1", "Jul 30, 2025")
        for i in range(4)
    ]))

    config = load_config(cfg_path)
    report = run_scrape(config, target_date="2025-07-30", page_budget_override=3)
    assert report.pages <= 3


def test_reparse_after_vocabulary_change(project, serve):
    cfg_path, db_path = project
    serve(lambda params: _page([
        _tile("950000000001", "2023 Panini Mosaic Rashee Rice Genesis RC #301", "Jul 30, 2025")
    ]) if params["_pgn"] == ["1"] else _page([]))

    run_scrape(load_config(cfg_path), target_date="2025-07-30")

    conn = store.connect(db_path)
    conn.execute("DELETE FROM cards")
    conn.commit()
    conn.close()

    assert reparse_titles(str(db_path)) == 1

    conn = store.connect(db_path)
    assert conn.execute("SELECT player FROM cards").fetchone()[0] == "Rashee Rice"
    conn.close()


def test_a_query_that_returns_nothing_is_reported(project, serve):
    """eBay answers a filter it cannot use with zero results rather than an
    error, so a broken query is indistinguishable from a quiet day unless the
    run says so. This is how a working query was replaced with an empty one."""
    import yaml

    cfg_path, db_path = project
    raw = yaml.safe_load(Path(cfg_path).read_text())
    raw["queries"] = [
        {"id": "works", "keywords": "football", "category": "261328"},
        {"id": "broken", "keywords": "football", "category": "999999"},
    ]
    Path(cfg_path).write_text(yaml.safe_dump(raw))

    def handler(params):
        if params.get("_sacat") == ["999999"]:
            return _page([])                       # eBay's empty answer
        if params["_pgn"] == ["1"]:
            return _page([_tile("940000000001",
                                "2023 Prizm CJ Stroud RC #339", "Jul 30, 2025")])
        return _page([])

    serve(handler)
    report = run_scrape(load_config(cfg_path), target_date="2025-07-30")

    assert report.empty_queries == ["broken"]
    assert report.per_query["works"] > 0
    assert report.as_dict()["empty_queries"] == ["broken"]


def test_every_query_producing_rows_reports_no_empties(project, serve):
    serve(lambda params: _page([
        _tile("950000000001", "2023 Prizm Bijan Robinson RC #301", "Jul 30, 2025")
    ]) if params["_pgn"] == ["1"] else _page([]))

    report = run_scrape(load_config(project[0]), target_date="2025-07-30")
    assert report.empty_queries == []


def _one_page_scrape(tmp_path, monkeypatch, titles, skip_lots=True):
    """Run a scrape against a single fabricated page of results."""
    import yaml

    from nflcarddb import pipeline
    from nflcarddb.config import load_config
    from nflcarddb.models import Sale

    cfg = tmp_path / "q.yml"
    cfg.write_text(yaml.safe_dump({
        "database": str(tmp_path / "s.db"),
        "skip_lots": skip_lots,
        "fetch": {"delay": 0, "jitter": 0, "max_retries": 0, "engine": "requests"},
        "price_bands": [[None, None]],
        "queries": [{"id": "q", "keywords": "football", "category": "261328"}],
    }))

    sales = [Sale(item_id=str(9_100_000_000 + i), title=t, price_cents=1000,
                  shipping_cents=0, sold_date="2026-01-01", currency="USD",
                  best_offer=False, query_id="q")
             for i, t in enumerate(titles)]

    def fake_walk(*a, **k):
        """walk_query yields sales and reports each segment as it finishes."""
        yield from sales
        on_segment = k.get("on_segment")
        if on_segment:
            result = type("R", (), {"pages": 1, "sales": sales,
                                    "capped": False, "ran_out": False})()
            on_segment(k.get("query_id", "q"),
                       type("B", (), {"label": "all", "lo": None, "hi": None})(),
                       "ok", result, None)

    monkeypatch.setattr(pipeline, "walk_query", fake_walk)
    class FakeFetcher:
        stats = type("S", (), {"requests": 1, "blocked": 0,
                               "challenge_seconds": 0.0})()
        engine = "requests"

        def close(self):
            pass

    monkeypatch.setattr(pipeline, "make_fetcher", lambda **k: FakeFetcher())
    # A recent date, so the run is not warned off as outside eBay's window.
    from datetime import date, timedelta
    day = (date.today() - timedelta(days=2)).isoformat()
    for sale in sales:
        sale.sold_date = day
    return pipeline.run_scrape(load_config(cfg), target_date=day)


def test_a_lot_is_not_collected_at_all(tmp_path, monkeypatch):
    """Sellers file lots in the singles categories too, so dropping eBay's Lots
    category does not stop them arriving. A lot's price belongs to no single
    card, so storing it costs a row, a parse and an upload for nothing."""
    from nflcarddb import db as store

    report = _one_page_scrape(tmp_path, monkeypatch, [
        "2024 Panini Prizm Caleb Williams #301 Silver Prizm RC",
        "2026 Topps Fernando Mendoza 7 Card Rookie Lot",
        "Josh Allen 5 Card Lot Prizm 2024",
    ])
    assert report.skipped_lots == 2
    conn = store.connect(tmp_path / "s.db")
    titles = [r[0] for r in conn.execute("SELECT title FROM sales")]
    conn.close()
    assert len(titles) == 1 and "Caleb Williams" in titles[0]


def test_lots_can_still_be_kept(tmp_path, monkeypatch):
    """Throwing away a collected sale is not something to do without a way
    back, so the behaviour is a setting rather than a rule."""
    from nflcarddb import db as store

    report = _one_page_scrape(tmp_path, monkeypatch, [
        "2024 Panini Prizm Caleb Williams #301 RC",
        "Josh Allen 5 Card Lot Prizm 2024",
    ], skip_lots=False)
    assert report.skipped_lots == 0
    conn = store.connect(tmp_path / "s.db")
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 2
    conn.close()


def test_a_newly_collected_sale_is_grouped_like_a_reparsed_one(tmp_path,
                                                               monkeypatch):
    """Otherwise a day collected today groups differently from the same card
    collected last month, until someone remembers to re-read everything."""
    from nflcarddb import checklist as cl
    from nflcarddb import db as store

    conn = store.connect(tmp_path / "s.db")
    cl.import_rows(conn, [{"year": 2026, "set_name": "Topps",
                           "subset": "Touchdown", "card_number": "TD-16",
                           "player": "Josh Allen"}], source="test")
    conn.close()

    _one_page_scrape(tmp_path, monkeypatch,
                     ["2026 Topps Josh Allen #TD-16 Bills"])

    conn = store.connect(tmp_path / "s.db")
    subset, key = conn.execute("SELECT subset, card_key FROM cards").fetchone()
    conn.close()
    assert subset == "Touchdown"
    assert "touchdown" in key
