"""CLI-level behaviour, especially how expected failures are reported.

A network outage or a bot check is a normal thing to hit when scraping; it must
read as a clear message and a distinct exit code, not a Python traceback.
"""

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
        "fetch": {"delay": 0, "jitter": 0, "max_retries": 0},
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
