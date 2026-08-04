"""Bounded catch-up runs, and the report of what is left to collect.

The backfill walks backwards through eBay's ~90-day window. Unattended it needs
a wall-clock bound so it ends before the PC is wanted for something else, and
that bound has to behave like an instruction rather than a failure -- a nightly
run that reports failure every night trains you to ignore it.
"""

from datetime import date, timedelta

from nflcarddb import db as store
from nflcarddb.config import Config, QuerySpec
from nflcarddb.models import Sale
from nflcarddb.pipeline import coverage_report, run_backfill


def _config(tmp_path):
    config = Config(database=str(tmp_path / "b.db"),
                    queries=[QuerySpec(id="q", category="261328")])
    config.fetch.engine = "requests"
    return config


class _Result:
    def __init__(self, status="ok", new=10, pages=2, reason=None):
        self.status, self.new, self.pages, self.reason = status, new, pages, reason


def test_the_budget_stops_the_run_between_days(monkeypatch, tmp_path):
    clock = {"t": 0.0}
    collected = []

    def fake_scrape(config, target_date=None, **kw):
        collected.append(target_date)
        clock["t"] += 600          # ten minutes a day, as observed
        return _Result()

    monkeypatch.setattr("nflcarddb.pipeline.run_scrape", fake_scrape)
    monkeypatch.setattr("nflcarddb.pipeline.time.monotonic", lambda: clock["t"])

    report = run_backfill(_config(tmp_path), days=30, max_minutes=30)

    # The budget is checked before each day starts, so the fourth day -- which
    # would begin at exactly 30 minutes elapsed -- is left for the next run.
    assert len(collected) == 3
    assert report.stopped_early == "time_budget"


def test_a_day_already_running_is_never_cut_off_partway(monkeypatch, tmp_path):
    """Days count as collected only when whole; stopping mid-day wastes pages."""
    clock = {"t": 0.0}

    def slow_scrape(config, target_date=None, **kw):
        clock["t"] += 3600         # one day overruns the whole budget
        return _Result()

    monkeypatch.setattr("nflcarddb.pipeline.run_scrape", slow_scrape)
    monkeypatch.setattr("nflcarddb.pipeline.time.monotonic", lambda: clock["t"])

    report = run_backfill(_config(tmp_path), days=30, max_minutes=10)

    assert len(report.days_done) == 1        # it finished, over budget
    assert report.stopped_early == "time_budget"


def test_no_budget_means_no_limit(monkeypatch, tmp_path):
    monkeypatch.setattr("nflcarddb.pipeline.run_scrape",
                        lambda config, target_date=None, **kw: _Result())
    report = run_backfill(_config(tmp_path), days=5)
    assert len(report.days_done) == 5
    assert report.stopped_early is None


def test_skipped_days_do_not_consume_the_budget(monkeypatch, tmp_path):
    """Otherwise a nearly-complete window would time out before reaching a gap."""
    clock = {"t": 0.0}
    yesterday = date.today() - timedelta(days=1)
    done_days = [(yesterday - timedelta(days=n)).isoformat() for n in range(5)]

    conn = store.connect(tmp_path / "b.db")
    for day in done_days:
        run = store.start_run(conn, day)
        store.upsert_sales(conn, [Sale(item_id=f"i{day}", title="t", sold_date=day)], run)
        store.finish_run(conn, run, status="ok", pages=1, seen=1, new=1)
    conn.close()

    collected = []

    def fake_scrape(config, target_date=None, **kw):
        collected.append(target_date)
        clock["t"] += 600
        return _Result()

    monkeypatch.setattr("nflcarddb.pipeline.run_scrape", fake_scrape)
    monkeypatch.setattr("nflcarddb.pipeline.time.monotonic", lambda: clock["t"])

    report = run_backfill(_config(tmp_path), days=30, max_minutes=20)

    assert len(report.days_skipped) == 5      # skipping is free
    assert collected == [(yesterday - timedelta(days=n)).isoformat() for n in (5, 6)]


def test_a_block_still_wins_over_the_budget(monkeypatch, tmp_path):
    monkeypatch.setattr("nflcarddb.pipeline.time.monotonic", lambda: 0.0)
    monkeypatch.setattr(
        "nflcarddb.pipeline.run_scrape",
        lambda config, target_date=None, **kw: _Result(status="blocked",
                                                       reason="blocked"),
    )
    report = run_backfill(_config(tmp_path), days=30, max_minutes=600)
    assert report.stopped_early == "blocked"


def _seed(path, days_ago: list[int]):
    conn = store.connect(path)
    yesterday = date.today() - timedelta(days=1)
    for n in days_ago:
        day = (yesterday - timedelta(days=n)).isoformat()
        run = store.start_run(conn, day)
        store.upsert_sales(conn, [Sale(item_id=f"x{n}", title="t", sold_date=day)], run)
        store.finish_run(conn, run, status="ok", pages=1, seen=1, new=1)
    conn.close()


def test_coverage_counts_the_window_and_names_the_next_day(tmp_path):
    path = tmp_path / "c.db"
    _seed(path, [0, 1, 2])

    report = coverage_report(str(path), days=90)
    assert report["collected"] == 3
    assert report["missing"] == 87
    assert report["complete"] is False
    yesterday = date.today() - timedelta(days=1)
    assert report["next_up"] == (yesterday - timedelta(days=3)).isoformat()
    assert report["estimated_hours_left"] == round(87 * 10 / 60, 1)


def test_coverage_reports_a_finished_window(tmp_path):
    path = tmp_path / "c.db"
    _seed(path, list(range(7)))

    report = coverage_report(str(path), days=7)
    assert report["complete"] is True
    assert report["missing"] == 0
    assert report["next_up"] is None
    assert report["estimated_hours_left"] == 0.0


def test_coverage_counts_days_ebay_can_no_longer_serve_separately(tmp_path):
    """Data older than the window is not missing -- it is irreplaceable."""
    path = tmp_path / "c.db"
    _seed(path, [0, 120])

    report = coverage_report(str(path), days=90)
    assert report["collected"] == 1        # only the in-window day
    assert report["outside_window"] == 1
    assert report["missing"] == 89


def test_coverage_on_an_empty_database(tmp_path):
    report = coverage_report(str(tmp_path / "empty.db"), days=90)
    assert report["collected"] == 0
    assert report["missing"] == 90
    assert report["oldest_collected"] is None
    assert report["complete"] is False
