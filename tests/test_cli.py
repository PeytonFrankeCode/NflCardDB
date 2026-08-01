"""CLI-level behaviour, especially how expected failures are reported.

A network outage or a bot check is a normal thing to hit when scraping; it must
read as a clear message and a distinct exit code, not a Python traceback.
"""

import json
from pathlib import Path

import pytest
import yaml

from nflcarddb import fetch as fetch_mod
from nflcarddb.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "queries.yml"
    path.write_text(yaml.safe_dump({
        "database": str(tmp_path / "t.db"),
        "fetch": {"delay": 0, "jitter": 0, "max_retries": 0, "engine": "requests"},
        "price_bands": [[None, None]],
        "queries": [{"id": "football_singles", "keywords": "football", "category": "261328"}],
    }))
    return str(path)


def test_network_failure_reports_cleanly(cfg, monkeypatch, capsys):
    def boom(self, url, label=None):
        raise fetch_mod.FetchError("giving up on https://www.ebay.com/...: proxy 403")

    monkeypatch.setattr(fetch_mod.Fetcher, "get", boom)

    code = main(["probe", "--config", cfg, "--query", "football_singles"])
    err = capsys.readouterr().err

    assert code == 5
    assert "network error" in err
    assert "Traceback" not in err
    assert "Check your connection" in err


def test_bot_check_reports_cleanly(cfg, monkeypatch, capsys):
    def blocked(self, url, label=None):
        raise fetch_mod.BlockedError("eBay served a bot-check page.")

    monkeypatch.setattr(fetch_mod.Fetcher, "get", blocked)

    code = main(["probe", "--config", cfg, "--query", "football_singles"])
    err = capsys.readouterr().err

    assert code == 4
    assert "blocked" in err
    assert "Traceback" not in err
    assert "--delay" in err  # tells the user what to actually change


def test_missing_config_is_not_a_traceback(tmp_path, capsys):
    code = main(["probe", "--config", str(tmp_path / "nope.yml"), "--query", "x"])
    assert code == 2
    assert "error" in capsys.readouterr().err


def test_unknown_query_id_is_rejected(cfg, capsys):
    code = main(["probe", "--config", cfg, "--query", "not_a_query"])
    assert code == 2
    assert "football_singles" in capsys.readouterr().err  # shows valid ids


def test_scrape_exit_code_matches_the_cause(cfg, monkeypatch, capsys):
    """setup.bat / collect.bat branch on these codes, so they must be stable.

    run_scrape deliberately swallows BlockedError to keep the rows already
    collected, which means the exit code cannot come from an exception -- it has
    to be carried back on the report.
    """
    def blocked(self, url, label=None):
        raise fetch_mod.BlockedError("eBay served a bot-check page.")

    monkeypatch.setattr(fetch_mod.Fetcher, "get", blocked)
    code = main(["scrape", "--config", cfg, "--date", "2025-07-30"])
    assert code == 4

    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "partial"
    assert report["reason"] == "blocked"


def test_scrape_exit_code_for_network_failure(cfg, monkeypatch, capsys):
    def dead(self, url, label=None):
        raise fetch_mod.FetchError("giving up: proxy 403")

    monkeypatch.setattr(fetch_mod.Fetcher, "get", dead)
    assert main(["scrape", "--config", cfg, "--date", "2025-07-30"]) == 5
    assert json.loads(capsys.readouterr().out)["reason"] == "network"


def test_successful_scrape_exits_zero(cfg, monkeypatch, capsys):
    page = (
        '<h1 class="srp-controls__count-heading">5 results</h1><ul class="srp-results">'
        '<li class="s-item"><a class="s-item__link" href="https://www.ebay.com/itm/990000000001">'
        '<div class="s-item__title"><span role="heading">2023 Prizm CJ Stroud RC #339</span></div></a>'
        '<span class="s-item__price">$10.00</span>'
        '<div class="s-item__caption"><span>Sold  Jul 30, 2025</span></div></li></ul>'
    )
    monkeypatch.setattr(fetch_mod.Fetcher, "get", lambda self, url, label=None: page)
    assert main(["scrape", "--config", cfg, "--date", "2025-07-30"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "ok"
    assert report["reason"] is None
    assert report["items_new"] == 1


def test_calibrate_works_offline(capsys):
    code = main(["calibrate", str(FIXTURES / "sold_s_item.html"), "--limit", "2"])
    out = capsys.readouterr().out
    assert code == 0
    assert "listings found: 3" in out
    assert "CJ Stroud" in out


def test_calibrate_flags_a_page_with_no_results(tmp_path, capsys):
    page = tmp_path / "blocked.html"
    page.write_text("<html><body><h1>Pardon Our Interruption</h1></body></html>")
    code = main(["calibrate", str(page)])
    assert code == 1
    assert "Nothing matched" in capsys.readouterr().out


def test_url_command_needs_no_network(cfg, capsys):
    assert main(["url", "--config", cfg]) == 0
    out = capsys.readouterr().out
    assert "LH_Sold=1" in out and "LH_Complete=1" in out
