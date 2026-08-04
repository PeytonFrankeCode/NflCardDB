"""Collecting several past days at once.

The properties that matter: already-collected days are skipped so a restart is
cheap, and a block stops the walk rather than burning through the remaining days
against a site that is already refusing.
"""

import pytest
import yaml

from nflcarddb import db as store
from nflcarddb.config import load_config
from nflcarddb.pipeline import run_backfill


@pytest.fixture
def project(tmp_path):
    db_path = tmp_path / "b.db"
    cfg = tmp_path / "q.yml"
    cfg.write_text(yaml.safe_dump({
        "database": str(db_path),
        "fetch": {"engine": "requests", "delay": 0, "jitter": 0},
        "price_bands": [[None, None]],
        "queries": [{"id": "football_singles", "keywords": "f", "category": "1"}],
    }))
    return load_config(cfg), db_path


def fake_run(results):
    """Stand in for run_scrape, answering per target date from `results`."""
    calls = []

    class Result:
        def __init__(self, status, reason=None, new=5, pages=3):
            self.status, self.reason = status, reason
            self.new, self.pages, self.seen = new, pages, new

    def _run(config, target_date=None, db_path=None, page_budget_override=None, **kw):
        calls.append(target_date)
        spec = results.get(target_date, ("ok", None))
        return Result(*spec)

    return _run, calls


def test_walks_backwards_from_yesterday(project, monkeypatch):
    config, db = project
    run, calls = fake_run({})
    monkeypatch.setattr("nflcarddb.pipeline.run_scrape", run)

    report = run_backfill(config, days=3, db_path=str(db), end_date="2026-08-03")

    # Newest first: if it is interrupted, the days most likely to still exist
    # next time are the older ones.
    assert calls == ["2026-08-03", "2026-08-02", "2026-08-01"]
    assert report.days_done == calls
    assert report.sales_new == 15


def test_days_already_collected_are_skipped(project, monkeypatch):
    config, db = project

    # Mark 2026-08-02 as genuinely done: an ok run *and* rows.
    conn = store.connect(db)
    run_id = store.start_run(conn, "2026-08-02")
    from nflcarddb.models import Sale
    store.upsert_sales(conn, [Sale(item_id="1" * 12, title="t", price_cents=100,
                                   sold_date="2026-08-02")], run_id)
    store.finish_run(conn, run_id, "ok", 1, 1, 1)
    conn.close()

    run, calls = fake_run({})
    monkeypatch.setattr("nflcarddb.pipeline.run_scrape", run)
    report = run_backfill(config, days=3, db_path=str(db), end_date="2026-08-03")

    assert "2026-08-02" not in calls
    assert report.days_skipped == ["2026-08-02"]
    assert calls == ["2026-08-03", "2026-08-01"]


def test_force_recollects_everything(project, monkeypatch):
    config, db = project
    conn = store.connect(db)
    run_id = store.start_run(conn, "2026-08-02")
    from nflcarddb.models import Sale
    store.upsert_sales(conn, [Sale(item_id="2" * 12, title="t", price_cents=100,
                                   sold_date="2026-08-02")], run_id)
    store.finish_run(conn, run_id, "ok", 1, 1, 1)
    conn.close()

    run, calls = fake_run({})
    monkeypatch.setattr("nflcarddb.pipeline.run_scrape", run)
    run_backfill(config, days=3, db_path=str(db), end_date="2026-08-03", force=True)
    assert "2026-08-02" in calls


def test_a_block_stops_the_whole_walk(project, monkeypatch):
    """Once eBay is refusing, the remaining days would only deepen the hole."""
    config, db = project
    run, calls = fake_run({"2026-08-02": ("partial", "blocked", 0, 1)})
    monkeypatch.setattr("nflcarddb.pipeline.run_scrape", run)

    report = run_backfill(config, days=5, db_path=str(db), end_date="2026-08-03")

    assert calls == ["2026-08-03", "2026-08-02"]      # stopped, did not continue
    assert report.stopped_early == "blocked"
    assert report.days_done == ["2026-08-03"]          # the good day is kept
    assert report.days_failed == [("2026-08-02", "blocked")]


def test_a_signed_out_session_stops_the_walk(project, monkeypatch):
    config, db = project
    run, calls = fake_run({"2026-08-03": ("partial", "signed_out", 0, 1)})
    monkeypatch.setattr("nflcarddb.pipeline.run_scrape", run)

    report = run_backfill(config, days=5, db_path=str(db), end_date="2026-08-03")
    assert calls == ["2026-08-03"]
    assert report.stopped_early == "signed_out"


def test_a_network_blip_does_not_stop_later_days(project, monkeypatch):
    """One flaky day is worth skipping past; a block is not."""
    config, db = project
    run, calls = fake_run({"2026-08-02": ("partial", "network", 0, 1)})
    monkeypatch.setattr("nflcarddb.pipeline.run_scrape", run)

    report = run_backfill(config, days=3, db_path=str(db), end_date="2026-08-03")
    assert calls == ["2026-08-03", "2026-08-02", "2026-08-01"]
    assert report.stopped_early is None
    assert report.days_failed == [("2026-08-02", "network")]
    assert report.days_done == ["2026-08-03", "2026-08-01"]


def test_completed_days_needs_both_a_run_and_rows(tmp_path):
    """An 'ok' run that collected nothing must not count as done."""
    db = tmp_path / "c.db"
    conn = store.connect(db)

    empty = store.start_run(conn, "2026-08-05")
    store.finish_run(conn, empty, "ok", 3, 0, 0)      # ok, but no rows

    from nflcarddb.models import Sale
    good = store.start_run(conn, "2026-08-06")
    store.upsert_sales(conn, [Sale(item_id="3" * 12, title="t", price_cents=100,
                                   sold_date="2026-08-06")], good)
    store.finish_run(conn, good, "ok", 3, 1, 1)

    done = store.completed_days(conn)
    assert done == {"2026-08-06"}
    conn.close()


def test_report_serialises(project, monkeypatch):
    import json

    config, db = project
    run, _ = fake_run({})
    monkeypatch.setattr("nflcarddb.pipeline.run_scrape", run)
    report = run_backfill(config, days=2, db_path=str(db), end_date="2026-08-03")
    payload = json.loads(json.dumps(report.as_dict()))
    assert payload["days_collected"] == ["2026-08-03", "2026-08-02"]
    assert payload["stopped_early"] is None
