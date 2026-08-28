"""CLI-level behaviour, especially how expected failures are reported.

A network outage or a bot check is a normal thing to hit when scraping; it must
read as a clear message and a distinct exit code, not a Python traceback.
"""

import argparse
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


def test_every_command_can_render_its_help():
    """A literal % in a help string is a %-format placeholder to argparse.

    Python 3.14 rejects it when the parser is built; 3.11 only fails when the
    help is actually rendered -- so building the parser is not enough to catch
    it, and a check that merely built one passed here while failing on the
    user's machine. Rendering every subcommand's help is the version-proof
    equivalent.
    """
    from nflcarddb.cli import build_parser

    parser = build_parser()
    parser.format_help()

    subparsers = [action for action in parser._actions
                  if isinstance(action, argparse._SubParsersAction)]
    assert subparsers, "no subcommands found -- this test would prove nothing"

    names = []
    for action in subparsers:
        for name, sub in action.choices.items():
            names.append(name)
            sub.format_help()          # raises on a badly formed help string

    # Guard against the loop silently covering nothing.
    assert "review" in names and "scrape" in names


def test_cards_lists_the_grouping(tmp_path, capsys):
    """`cards` is the only place the grouping is visible without a browser.

    Two listings that are the same card must come back as one line, or the
    command is reporting on titles rather than on cards.
    """
    from nflcarddb import db as store
    from nflcarddb.models import Sale
    from nflcarddb.parse_title import parse_title

    db_path = tmp_path / "cards.db"
    conn = store.connect(db_path)
    run = store.start_run(conn, "2025-07-30")
    titles = [
        # Same card, two sellers, different wording and different grade.
        "2023 Panini Prizm CJ Stroud Silver Prizm RC #339 PSA 10",
        "2023 Prizm CJ Stroud #339 Silver Prizm Rookie",
        # Sold once: a price, not a history.
        "1998 Topps Chrome Peyton Manning Refractor #165",
    ]
    sales = [
        Sale(item_id=f"40000000000{i}", title=t, price_cents=5000 + i * 1000,
             shipping_cents=0, sold_date=f"2025-07-2{i + 1}", currency="USD",
             best_offer=False, query_id="q1")
        for i, t in enumerate(titles)
    ]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in sales], "title/1")
    store.finish_run(conn, run, "ok", 3, 3, 3)
    conn.close()

    assert main(["cards", "--db", str(db_path)]) == 0
    out = capsys.readouterr().out
    assert "CJ Stroud" in out
    assert "Manning" not in out          # one sale is not a history
    assert "1 card(s) shown" in out


def test_cards_says_so_when_nothing_has_a_history(tmp_path, capsys):
    from nflcarddb import db as store

    db_path = tmp_path / "empty.db"
    store.connect(db_path).close()

    assert main(["cards", "--db", str(db_path)]) == 0
    assert "No card has sold more than once" in capsys.readouterr().out


# ------------------------------------------------------- how Windows runs it
#
# Installing the package writes an `nflcarddb.exe` launcher into the venv.
# Nobody signs that file -- pip generates it -- so a machine running Device
# Guard refuses to start it, and every double-click in the project died with
# "blocked by your organization's Device Guard policy". Running the module
# through the interpreter avoids the question entirely: python.exe is signed by
# the people who publish Python, and a venv copies that signature with the file.


def test_the_module_can_be_run_by_the_interpreter():
    """`python -m nflcarddb` is what every .bat calls, so it must work."""
    import subprocess
    import sys

    done = subprocess.run([sys.executable, "-m", "nflcarddb", "--help"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert "usage: nflcarddb" in done.stdout


def test_no_batch_file_calls_the_unsigned_launcher():
    """The regression this guards is a whole afternoon of dead double-clicks.

    whatswrong.bat is exempt: it runs the launcher on purpose to record whether
    the policy is what is blocking it, which is the diagnosis nobody had.
    """
    root = Path(__file__).resolve().parent.parent
    offenders = []
    for bat in sorted(root.glob("*.bat")):
        if bat.name == "whatswrong.bat":
            continue
        for lineno, line in enumerate(bat.read_text().splitlines(), 1):
            if line.lstrip().startswith("nflcarddb "):
                offenders.append(f"{bat.name}:{lineno}")
    assert not offenders, (
        "these call the unsigned launcher instead of `python -m nflcarddb`: "
        + ", ".join(offenders))
