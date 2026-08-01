"""Loading eBay pages saved by hand.

This path exists because automated fetching is blocked: eBay 403s the HTTP
client and bot-checks a headless browser. Nothing here touches the network, so
it keeps working regardless of what eBay does to scrapers.
"""

import json

from nflcarddb import db as store
from nflcarddb.ingest import collect_html_files, import_files

FIXTURE = "tests/fixtures/sold_s_item.html"
FIXTURE_2 = "tests/fixtures/sold_s_card.html"


def test_import_a_single_saved_page(tmp_path):
    db = tmp_path / "i.db"
    report = import_files([FIXTURE], db)

    assert report.files == 1
    assert report.parsed == 1
    assert report.sales_seen == 3
    assert report.sales_new == 3
    assert report.dates == {"2025-07-30", "2025-07-29"}

    conn = store.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 3
    # Titles are parsed on import, same as a live scrape.
    assert conn.execute(
        "SELECT player FROM cards WHERE item_id = '226789012345'"
    ).fetchone()[0] == "CJ Stroud"
    conn.close()


def test_importing_a_folder_reads_every_page(tmp_path):
    folder = tmp_path / "saved"
    folder.mkdir()
    for i, src in enumerate((FIXTURE, FIXTURE_2)):
        (folder / f"page{i}.html").write_text(open(src, encoding="utf-8").read())

    report = import_files([folder], tmp_path / "f.db")
    assert report.files == 2
    assert report.parsed == 2
    assert report.sales_seen == 6


def test_reimporting_the_same_page_adds_nothing(tmp_path):
    db = tmp_path / "dupe.db"
    first = import_files([FIXTURE], db)
    second = import_files([FIXTURE], db)

    assert first.sales_new == 3
    assert second.sales_seen == 3
    assert second.sales_new == 0  # same listings, already stored

    conn = store.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 3
    conn.close()


def test_a_bot_check_page_is_named_as_such(tmp_path):
    page = tmp_path / "blocked.html"
    page.write_text("<html><body><h1>Pardon Our Interruption</h1></body></html>")

    report = import_files([page], tmp_path / "b.db")
    assert report.parsed == 0
    assert len(report.skipped) == 1
    assert "bot-check" in report.skipped[0][1]


def test_an_unrelated_page_is_skipped_not_crashed_on(tmp_path):
    page = tmp_path / "random.html"
    page.write_text("<html><body><p>my holiday photos</p></body></html>")

    report = import_files([page], tmp_path / "r.db")
    assert report.files == 1
    assert report.parsed == 0
    assert "no listings" in report.skipped[0][1]


def test_missing_paths_report_nothing_rather_than_raising(tmp_path):
    report = import_files([tmp_path / "nope.html"], tmp_path / "n.db")
    assert report.files == 0
    assert report.parsed == 0


def test_collect_html_files_deduplicates(tmp_path):
    folder = tmp_path / "d"
    folder.mkdir()
    f = folder / "a.html"
    f.write_text("<html/>")
    # Same file named directly and via its folder.
    assert len(collect_html_files([folder, f])) == 1


def test_import_records_a_run_so_it_shows_in_stats(tmp_path):
    db = tmp_path / "run.db"
    import_files([FIXTURE], db)

    conn = store.connect(db)
    row = conn.execute("SELECT status, items_new FROM scrape_runs").fetchone()
    assert row["status"] == "ok"
    assert row["items_new"] == 3
    conn.close()


def test_report_serialises_for_the_cli(tmp_path):
    report = import_files([FIXTURE], tmp_path / "s.db")
    payload = json.loads(json.dumps(report.as_dict()))
    assert payload["sales_new"] == 3
    assert payload["dates"] == ["2025-07-29", "2025-07-30"]
