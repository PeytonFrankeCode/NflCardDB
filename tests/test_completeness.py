"""Knowing when a day was only partly collected.

eBay has no sold-date filter, so a day is reached by paging from one end of its
~90-day window. Run out of pages first and you get some of the day, not none --
which is the dangerous case, because it looks like success. A day recorded as
'ok' is never revisited, so a truncated day recorded as 'ok' is a permanent
hole. These tests are about refusing to record that.
"""

from datetime import date, timedelta

from nflcarddb import db as store
from nflcarddb.models import Sale
from nflcarddb.pipeline import find_thin_days, mark_for_recollection
from nflcarddb.search import (
    NEWEST_FIRST,
    OLDEST_FIRST,
    PriceBand,
    build_url,
    probe_oldest_first,
    walk_segment,
)
from tests.test_search import FakeFetcher, _page_html


def test_the_sort_flips_with_the_direction():
    assert "_sop=13" in build_url("football", direction=NEWEST_FIRST)
    assert "_sop=1&" in build_url("football", direction=OLDEST_FIRST) + "&"


def test_a_walk_that_passes_the_target_is_complete():
    fetcher = FakeFetcher({
        1: _page_html([("100000000001", "a", "Jul 30, 2025")], count="10"),
        2: _page_html([("100000000002", "b", "Jul 29, 2025")], count="10"),
    })
    result = walk_segment(fetcher, "q", "kw", None, PriceBand(1, 5),
                          "2025-07-30", max_pages=5, items_per_page=1)
    assert result.stopped_on_date is True
    assert result.reached_target is True
    assert len(result.sales) == 1


def test_a_walk_that_runs_out_of_pages_is_incomplete():
    """The sales are real; the claim that the day is finished would not be."""
    pages = {n: _page_html([(f"10000000000{n}", "x", "Aug 5, 2025")], count="9999")
             for n in range(1, 6)}
    fetcher = FakeFetcher(pages)

    result = walk_segment(fetcher, "q", "kw", None, PriceBand(1, 5),
                          "2025-07-30", max_pages=3, items_per_page=1)

    assert result.stopped_on_date is False
    assert result.ran_out is True
    assert result.reached_target is False


def test_running_out_of_ebay_results_counts_as_complete():
    """Nothing left to fetch is a finished segment, not a truncated one."""
    fetcher = FakeFetcher({
        1: _page_html([("100000000001", "a", "Jul 30, 2025")], count="3"),
        2: _page_html([]),
    })
    result = walk_segment(fetcher, "q", "kw", None, PriceBand(1, 5),
                          "2025-07-30", max_pages=5, items_per_page=1)
    assert result.reached_target is True


def test_oldest_first_stops_on_the_other_side_of_the_target():
    """Sorted ascending, "past the target" means newer, not older."""
    fetcher = FakeFetcher({
        1: _page_html([("100000000001", "a", "Jul 30, 2025")], count="10"),
        2: _page_html([("100000000002", "b", "Jul 31, 2025")], count="10"),
    })
    result = walk_segment(fetcher, "q", "kw", None, PriceBand(1, 5),
                          "2025-07-30", max_pages=5, items_per_page=1,
                          direction=OLDEST_FIRST)

    assert result.stopped_on_date is True
    assert [s.item_id for s in result.sales] == ["100000000001"]


def test_no_target_date_is_never_incomplete():
    fetcher = FakeFetcher({1: _page_html([("100000000001", "a", "Jul 30, 2025")], count="5")})
    result = walk_segment(fetcher, "q", "kw", None, PriceBand(1, 5), None,
                          max_pages=1, items_per_page=1)
    assert result.ran_out is False


def _old(days: int) -> str:
    return (date.today() - timedelta(days=days)).strftime("%b %d, %Y")


def test_the_probe_accepts_a_sort_that_really_returns_old_listings():
    fetcher = FakeFetcher({1: _page_html(
        [(f"10000000000{i}", "x", _old(80)) for i in range(6)], count="5000")})
    assert probe_oldest_first(fetcher) is True


def test_the_probe_rejects_a_sort_ebay_quietly_ignored():
    """Recent dates back means the sort was dropped -- collecting the wrong end."""
    fetcher = FakeFetcher({1: _page_html(
        [(f"10000000000{i}", "x", _old(1)) for i in range(6)], count="5000")})
    assert probe_oldest_first(fetcher) is False


def test_the_probe_never_ends_a_run():
    class Broken:
        def get(self, url, label=None):
            raise RuntimeError("eBay said no")

        def budget_exhausted(self):
            return False

    assert probe_oldest_first(Broken()) is False


def _seed(path, per_day: dict[str, int]):
    conn = store.connect(path)
    for day, n in per_day.items():
        run = store.start_run(conn, day)
        store.upsert_sales(
            conn, [Sale(item_id=f"{day}-{i}", title="t", sold_date=day)
                   for i in range(n)], run)
        store.finish_run(conn, run, status="ok", pages=1, seen=n, new=n)
    conn.close()


def test_thin_days_are_spotted_against_a_normal_day(tmp_path):
    path = tmp_path / "thin.db"
    _seed(path, {"2026-08-03": 200, "2026-08-02": 190, "2026-08-01": 210,
                 "2026-07-20": 12})

    thin = find_thin_days(str(path))
    assert [r["day"] for r in thin] == ["2026-07-20"]
    assert thin[0]["sales"] == 12


def test_an_ordinary_quiet_day_is_not_flagged(tmp_path):
    path = tmp_path / "quiet.db"
    _seed(path, {"2026-08-03": 200, "2026-08-02": 190, "2026-08-01": 160})

    assert find_thin_days(str(path)) == []


def test_one_huge_day_does_not_condemn_the_rest(tmp_path):
    """Comparing against the maximum would flag every normal day after a spike."""
    path = tmp_path / "spike.db"
    _seed(path, {"2026-08-03": 5000, "2026-08-02": 200, "2026-08-01": 195,
                 "2026-07-31": 205, "2026-07-30": 190})

    assert find_thin_days(str(path)) == []


def test_marking_a_day_makes_the_backfill_collect_it_again(tmp_path):
    path = tmp_path / "mark.db"
    _seed(path, {"2026-08-03": 200, "2026-08-02": 190, "2026-07-20": 5})

    conn = store.connect(path)
    assert "2026-07-20" in store.completed_days(conn)
    conn.close()

    assert mark_for_recollection(str(path), ["2026-07-20"]) == 1

    conn = store.connect(path)
    done = store.completed_days(conn)
    # No longer counted as done, so the backfill returns to it...
    assert "2026-07-20" not in done
    # ...and the sales already collected are untouched, since re-collecting
    # upserts on item_id rather than starting over.
    assert conn.execute(
        "SELECT COUNT(*) FROM sales WHERE sold_date = '2026-07-20'"
    ).fetchone()[0] == 5
    assert "2026-08-03" in done
    conn.close()


def test_marking_nothing_is_harmless(tmp_path):
    path = tmp_path / "none.db"
    _seed(path, {"2026-08-03": 10})
    assert mark_for_recollection(str(path), []) == 0
