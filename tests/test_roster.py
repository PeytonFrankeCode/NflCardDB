"""Learning player names from the titles already collected.

The name scan takes the longest run of name-shaped words, which breaks when an
insert name sits beside the player: "Bomb Squad Jayden Daniels" is one run. No
positional rule fixes it -- that one wants the last two words, "Jayden Daniels
Preview" the first two -- so the parser needs a roster, and a shipped roster
goes stale every draft. Hence learning one.
"""

from nflcarddb import db as store
from nflcarddb.card_key import card_key
from nflcarddb.models import Sale
from nflcarddb.parse_title import load_roster, parse_title
from nflcarddb.roster import build, write

# Real titles from a collected sample, chosen for the failure they caused.
TITLES = [
    "2024 PANINI DONRUSS BOMB SQUAD #29 JAYDEN DANIELS ROOKIE RC PSA 9",
    "2024 PANINI DONRUSS #389 JAYDEN DANIELS ROOKIE RC PSA 9",
    "2024 Jayden Daniels Donruss Optic Preview Emoji Prizm Rookie PSA 9 #389",
    "2024 Panini Totally Certified Jayden Daniels Mirror Red /99 RC PSA 8",
    "2025 Panini Mosaic Jayden Daniels #12 Green",
    "1997 Topps Chrome Barry Sanders Season's Best #6 PSA 8",
    "1998 Topps Chrome Barry Sanders #40 Refractor",
    "2000 Panini Prizm Barry Sanders #10 Silver",
    "2020 Score Barry Sanders #77 Red",
]


def _seeded(path):
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    sales = [Sale(item_id=f"{900000000000 + i}", title=t, price_cents=1000,
                  sold_date="2026-08-03") for i, t in enumerate(TITLES)]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in sales], "v1")
    conn.close()
    return path


def test_real_players_are_learned(tmp_path):
    names = {n for n, _, _ in build(str(_seeded(tmp_path / "r.db")),
                                    min_contexts=2, min_sightings=2)}
    assert "jayden daniels" in names
    assert "barry sanders" in names


def test_insert_names_are_not_learned(tmp_path):
    """The whole discriminator: an insert repeats as often as a player but
    lives in one set of one year, because that is what an insert is."""
    names = {n for n, _, _ in build(str(_seeded(tmp_path / "r.db")),
                                    min_contexts=2, min_sightings=2)}
    for junk in ("bomb squad", "optic preview", "totally certified",
                 "season's best", "panini donruss"):
        assert junk not in names, junk


def test_a_learned_roster_rescues_the_player(tmp_path):
    """End to end: the titles that produced 'Bomb Squad Jayden Daniels'."""
    path = _seeded(tmp_path / "r.db")
    roster_file = write(build(str(path), min_contexts=2, min_sightings=2),
                        tmp_path / "players.txt")
    roster = load_roster(roster_file)

    for title in TITLES[:5]:
        assert parse_title(title, roster).player == "Jayden Daniels", title


def test_the_roster_pulls_scattered_sales_into_one_card(tmp_path):
    """The point of fixing the name: without it, a polluted name becomes the
    key whenever a title has no card number, and one card splits many ways."""
    path = _seeded(tmp_path / "r.db")
    roster = load_roster(write(build(str(path), min_contexts=2, min_sightings=2),
                               tmp_path / "players.txt"))

    without = {card_key(parse_title(t)) for t in TITLES[:2]}
    with_roster = {card_key(parse_title(t, roster)) for t in TITLES[:2]}
    # #29 and #389 are genuinely different cards either way...
    assert len(with_roster) == 2
    # ...but the names now agree, which is what stops a group contradicting
    # itself and what makes the displayed name consistent.
    assert {parse_title(t, roster).player for t in TITLES[:2]} == {"Jayden Daniels"}


def test_a_shouted_name_is_normalised(tmp_path):
    """Sellers SHOUT. One card should not display two names."""
    path = _seeded(tmp_path / "r.db")
    roster = load_roster(write(build(str(path), min_contexts=2, min_sightings=2),
                               tmp_path / "players.txt"))

    shouted = parse_title(TITLES[1], roster).player
    normal = parse_title(TITLES[2], roster).player
    assert shouted == normal == "Jayden Daniels"


def test_frequency_alone_does_not_make_a_name(tmp_path):
    """The claim the whole module rests on, tested against itself.

    A phrase seen three times a *lot* still loses to a phrase seen a few times
    across many sets. If frequency were the signal this test would fail, and so
    would the module -- inserts are the most repeated phrases in the data.
    """
    from nflcarddb.models import CardAttrs

    path = tmp_path / "breadth.db"
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")

    # An insert: printed once, listed constantly.
    narrow = [("Deep Threat Dummy", 2024, "Donruss")] * 12
    # A player: listed less, but wherever cards are made.
    wide = [("Travis Kelce", 2024, "Donruss"),
            ("Travis Kelce", 2023, "Prizm"),
            ("Travis Kelce", 2022, "Mosaic"),
            ("Travis Kelce", 2021, "Select")]
    rows = narrow + wide

    sales = [Sale(item_id=f"{910000000000 + i}", title="x", price_cents=1000,
                  sold_date="2026-08-03") for i in range(len(rows))]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [
        (s.item_id, CardAttrs(player=player, year=year, set_name=set_name,
                              card_number=str(i), confidence=0.9))
        for i, (s, (player, year, set_name)) in enumerate(zip(sales, rows))
    ], "v1")
    conn.close()

    names = {n for n, _, _ in build(str(path), min_contexts=3, min_sightings=4)}
    assert "travis kelce" in names        # 4 sightings, 4 sets
    assert "deep threat" not in names     # 12 sightings, 1 set


def test_building_a_roster_switches_it_on(tmp_path):
    """Built-but-not-enabled looks identical to built-and-broken."""
    from nflcarddb.cli import enable_roster
    from nflcarddb.config import load_config

    config = tmp_path / "queries.yml"
    config.write_text(
        "database: data/x.sqlite\n"
        "# roster: config/nfl_players.txt\n"
        "queries:\n  - id: a\n    keywords: football\n",
        encoding="utf-8")

    assert enable_roster(str(config), tmp_path / "learned.txt") is True
    assert load_config(str(config)).roster == (tmp_path / "learned.txt").as_posix()


def test_switching_it_on_twice_does_not_stack_lines(tmp_path):
    from nflcarddb.cli import enable_roster

    config = tmp_path / "queries.yml"
    config.write_text(
        "database: data/x.sqlite\n"
        "# roster: config/nfl_players.txt\n"
        "queries:\n  - id: a\n    keywords: football\n",
        encoding="utf-8")

    enable_roster(str(config), tmp_path / "one.txt")
    enable_roster(str(config), tmp_path / "two.txt")

    lines = [l for l in config.read_text(encoding="utf-8").splitlines()
             if "roster" in l]
    assert len(lines) == 1
    assert "two.txt" in lines[0]


def test_a_missing_config_is_reported_not_created(tmp_path):
    """Writing a config the user never had would hide the real problem."""
    from nflcarddb.cli import enable_roster

    missing = tmp_path / "nope.yml"
    assert enable_roster(str(missing), tmp_path / "learned.txt") is False
    assert not missing.exists()


def test_the_command_learns_enables_and_reparses(tmp_path, capsys):
    """One double-click has to do the whole job: a roster that is built but
    not switched on, or switched on but never applied to existing rows, looks
    exactly like a roster that does not work."""
    from nflcarddb.cli import main
    from nflcarddb.config import load_config

    db = _seeded(tmp_path / "r.db")
    config = tmp_path / "queries.yml"
    config.write_text(
        f"database: {db.as_posix()}\n"
        "# roster: config/nfl_players.txt\n"
        "queries:\n  - id: a\n    keywords: football\n",
        encoding="utf-8")
    out = tmp_path / "players.txt"

    code = main(["roster", "--config", str(config), "--out", str(out),
                 "--min-contexts", "2", "--min-sightings", "2"])
    assert code == 0
    assert load_config(str(config)).roster == out.as_posix()

    # The existing rows were re-read, not just future ones.
    conn = store.connect(db)
    names = {r[0] for r in conn.execute(
        "SELECT DISTINCT player FROM cards WHERE player IS NOT NULL")}
    conn.close()
    assert "Jayden Daniels" in names
    assert not any("Bomb Squad" in n for n in names)

    printed = capsys.readouterr().out
    assert "distinct cards" in printed        # the before/after was reported


def test_no_apply_leaves_the_database_alone(tmp_path):
    from nflcarddb.cli import main
    from nflcarddb.config import load_config

    db = _seeded(tmp_path / "r.db")
    config = tmp_path / "queries.yml"
    config.write_text(
        f"database: {db.as_posix()}\n"
        "# roster: config/nfl_players.txt\n"
        "queries:\n  - id: a\n    keywords: football\n",
        encoding="utf-8")

    before = _players(db)
    assert main(["roster", "--config", str(config),
                 "--out", str(tmp_path / "p.txt"),
                 "--min-contexts", "2", "--min-sightings", "2",
                 "--no-apply"]) == 0

    assert load_config(str(config)).roster is None
    assert _players(db) == before


def _players(db):
    conn = store.connect(db)
    try:
        return {r[0] for r in conn.execute("SELECT player FROM cards")}
    finally:
        conn.close()


def test_an_empty_database_learns_nothing(tmp_path):
    conn = store.connect(tmp_path / "empty.db")
    conn.close()
    assert build(str(tmp_path / "empty.db")) == []


def test_a_generational_suffix_is_not_a_surname(tmp_path):
    """"Patrick Mahomes II" yields the window "Mahomes II", which travels with
    the player across every set and so passes breadth on his coattails. It is
    not a name, and a title matching it would display "Mahomes Ii"."""
    from nflcarddb.models import CardAttrs

    path = tmp_path / "suffix.db"
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    sets = [(2024, "Prizm"), (2023, "Mosaic"), (2022, "Select"), (2021, "Optic")]
    sales = [Sale(item_id=f"{920000000000 + i}", title="x", price_cents=1000,
                  sold_date="2026-08-03") for i in range(len(sets))]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [
        (s.item_id, CardAttrs(player="Patrick Mahomes II", year=y, set_name=n,
                              card_number=str(i), confidence=0.9))
        for i, (s, (y, n)) in enumerate(zip(sales, sets))
    ], "v1")
    conn.close()

    names = {n for n, _, _ in build(str(path), min_contexts=3, min_sightings=3)}
    assert "patrick mahomes" in names
    assert "mahomes ii" not in names
