"""eBay's Browse API as a card catalogue.

It gives no sold prices -- that is Marketplace Insights, which was refused
twice, and why the collector still scrapes. What it gives is the thing three
rounds of scraping the sidebar could not: every value of every aspect, as JSON.

These tests run against recorded response shapes. The API cannot be reached
from here, but unlike scraped markup its format is a published contract, so
building against it is a documented risk rather than a guess.
"""

import pytest

from nflcarddb.ebay_api import (aspect_filter_for, aspects_from_payload,
                                load_credentials, save_credentials)

# The shape eBay documents for fieldgroups=ASPECT_REFINEMENTS.
PAYLOAD = {
    "total": 812004,
    "refinement": {
        "aspectDistributions": [
            {
                "localizedAspectName": "Parallel/Variety",
                "aspectValueDistributions": [
                    {"localizedAspectValue": "Silver Prizm", "matchCount": 5001},
                    {"localizedAspectValue": "Genies", "matchCount": 312},
                    {"localizedAspectValue": "Sunday Kings", "matchCount": 208},
                ],
            },
            {
                "localizedAspectName": "Set",
                "aspectValueDistributions": [
                    {"localizedAspectValue": "2025 Topps Chrome Football",
                     "matchCount": 2110},
                ],
            },
            {
                "localizedAspectName": "Player/Athlete",
                "aspectValueDistributions": [
                    {"localizedAspectValue": "Jayden Daniels", "matchCount": 1204},
                ],
            },
        ]
    },
}


def test_the_payload_becomes_the_shape_facets_already_accumulates():
    """The API is a better source for the same store, not a second store --
    so merging, cleaning and bucketing keep working untouched."""
    from nflcarddb.facets import as_vocabulary, merge

    aspects = aspects_from_payload(PAYLOAD)
    assert aspects["Set"]["2025 Topps Chrome Football"] == 2110

    store: dict = {}
    merge(store, aspects)
    vocab = as_vocabulary(store)
    assert "Genies" in vocab["parallels"]
    assert "Jayden Daniels" in vocab["players"]


def test_values_come_back_biggest_first():
    aspects = aspects_from_payload(PAYLOAD)
    assert list(aspects["Parallel/Variety"])[0] == "Silver Prizm"


def test_the_insert_names_added_by_hand_are_in_ebays_own_data():
    """Genies and Sunday Kings were typed in by hand over two rounds."""
    parallels = aspects_from_payload(PAYLOAD)["Parallel/Variety"]
    assert "Genies" in parallels
    assert "Sunday Kings" in parallels


def test_an_empty_or_odd_payload_is_not_a_crash():
    assert aspects_from_payload({}) == {}
    assert aspects_from_payload({"refinement": {}}) == {}
    assert aspects_from_payload({"refinement": {"aspectDistributions": None}}) == {}


def test_a_distribution_missing_its_name_is_skipped():
    assert aspects_from_payload({"refinement": {"aspectDistributions": [
        {"aspectValueDistributions": [{"localizedAspectValue": "x"}]}]}}) == {}


def test_a_missing_count_is_kept_as_unknown_not_zero():
    """A value without a count is still a real value."""
    aspects = aspects_from_payload({"refinement": {"aspectDistributions": [
        {"localizedAspectName": "Set",
         "aspectValueDistributions": [{"localizedAspectValue": "1979 Topps"}]}]}})
    assert aspects["Set"]["1979 Topps"] is None


def test_the_aspect_filter_uses_ebays_brace_syntax():
    """Not a plain query parameter: the category comes first, values are
    braced, clauses are comma-separated."""
    assert aspect_filter_for("261328", [("Season", "2025")]) == \
        "categoryId:261328,Season:{2025}"


def test_several_aspects_combine_into_one_filter():
    """Pinning the sport has to survive being combined with a drill target."""
    assert aspect_filter_for("261328", [("Sport", "Football"),
                                        ("Season", "2025")]) == \
        "categoryId:261328,Sport:{Football},Season:{2025}"


def test_a_value_containing_the_syntax_is_dropped_not_sent():
    """A comma or brace inside a value would silently filter on something
    else, which is worse than not filtering."""
    assert aspect_filter_for("261328", [("Set", "Topps, Chrome")]) is None
    assert aspect_filter_for("261328", [("Sport", "Football"),
                                        ("Set", "a{b}")]) == \
        "categoryId:261328,Sport:{Football}"


def test_no_pairs_means_no_filter_rather_than_a_bare_category():
    assert aspect_filter_for("261328", []) is None


def test_credentials_round_trip(tmp_path):
    path = tmp_path / "keys.txt"
    save_credentials("MyApp-PRD-abc", "PRD-secret", str(path))
    assert load_credentials(str(path)) == ("MyApp-PRD-abc", "PRD-secret")


def test_missing_or_incomplete_credentials_read_as_absent(tmp_path):
    assert load_credentials(str(tmp_path / "nope.txt")) is None
    half = tmp_path / "half.txt"
    half.write_text("app_id=only-this\n", encoding="utf-8")
    assert load_credentials(str(half)) is None


def test_comments_in_the_credentials_file_are_ignored(tmp_path):
    path = tmp_path / "keys.txt"
    path.write_text("# a note\napp_id=a\ncert_id=b\n", encoding="utf-8")
    assert load_credentials(str(path)) == ("a", "b")


def test_the_credentials_file_is_gitignored():
    """It is a credential. This is the test that keeps it out of the repo."""
    from pathlib import Path

    ignored = Path("/home/user/NflCardDB/.gitignore").read_text(encoding="utf-8")
    assert "data/ebay-api.txt" in ignored


def test_only_the_read_scope_is_requested():
    """A token that can do more than read a catalogue is one that can do more
    than read a catalogue by accident."""
    from nflcarddb.ebay_api import SCOPE

    assert SCOPE == "https://api.ebay.com/oauth/api_scope"
    assert "sell" not in SCOPE


# --- turning the harvest into the parser's vocabulary -----------------------

STORE = {
    "Player/Athlete": {"Jayden Daniels": 1204, "Ja'Marr Chase": 892},
    # Moonstruck and Stargazing are not in the built-in list; the other two
    # are already understood as parallels.
    "Parallel/Variety": {"Moonstruck": 312, "Stargazing": 208,
                         "Silver Prizm": 5001, "Refractor": 4000},
}


def test_the_roster_is_written_from_ebays_player_list(tmp_path):
    """Same file the learned roster used, so nothing downstream changes -- the
    config key, the loader and the parser all stay as they are."""
    from nflcarddb.facets import write_roster
    from nflcarddb.parse_title import load_roster

    path = tmp_path / "players.txt"
    assert write_roster(STORE, path) == 2
    assert load_roster(path) == {"jayden daniels", "ja'marr chase"}


def test_a_name_the_built_in_list_already_has_is_not_duplicated(tmp_path):
    """Genies was typed in by hand two rounds ago, and keys correctly. eBay
    has it too; re-writing it as a claim-only word would demote it."""
    from nflcarddb.facets import write_designations

    path = tmp_path / "inserts.txt"
    write_designations({"Parallel/Variety": {"Genies": 9}}, path)
    written = [l for l in path.read_text(encoding="utf-8").splitlines()
               if l and not l.startswith("#")]
    assert written == []


def test_names_already_understood_are_left_out_of_the_file(tmp_path):
    """"Silver Prizm" and "Refractor" are already parallels and already key.
    Rewriting them as claim-only words would strip them out of identities that
    currently work."""
    from nflcarddb.facets import write_designations

    path = tmp_path / "inserts.txt"
    write_designations(STORE, path)
    written = [l for l in path.read_text(encoding="utf-8").splitlines()
               if l and not l.startswith("#")]

    assert "Moonstruck" in written
    assert "Stargazing" in written
    assert "Silver Prizm" not in written
    assert "Refractor" not in written


def test_a_claimed_word_is_kept_out_of_the_name_without_changing_the_key(tmp_path):
    """The correction that cost a regression. Keyed, these 760 names dropped
    grouped cards from 5,238 to 4,187 -- because eBay's Parallel/Variety is
    ticked on a form and need not appear in the title at all. Claimed, they
    still stop the word being read as part of the player's name."""
    from nflcarddb.card_key import card_key
    from nflcarddb.facets import write_designations
    from nflcarddb.parse_title import (load_inserts, parse_title,
                                       register_designations)

    path = tmp_path / "words.txt"
    write_designations({"Parallel/Variety": {"Moonstruck": 50}}, path)
    try:
        register_designations(load_inserts(path))
        with_word = parse_title(
            "2025 Panini Donruss Optic Derrick Henry Moonstruck #11")
        without = parse_title("2025 Panini Donruss Optic Derrick Henry #11")

        # Same card either way -- that is the whole point.
        assert card_key(with_word) == card_key(without)
        # ...and the word did not end up in the player's name.
        assert with_word.player == "Derrick Henry"
    finally:
        register_designations([])


def test_placeholders_never_reach_the_vocabulary_files(tmp_path):
    """"Not Specified" as a player name would be a card belonging to nobody."""
    from nflcarddb.facets import write_designations, write_roster

    dirty = {"Player/Athlete": {"Not Specified": 90000, "Tom Brady": 5},
             "Parallel/Variety": {"[Base]": 900, "Moonstruck": 5}}

    roster = tmp_path / "p.txt"
    assert write_roster(dirty, roster) == 1
    assert "Not Specified" not in roster.read_text(encoding="utf-8")

    inserts = tmp_path / "i.txt"
    write_designations(dirty, inserts)
    assert "[Base]" not in inserts.read_text(encoding="utf-8")


def test_a_learned_word_can_never_become_part_of_a_key():
    """The guard the regression bought. Whatever arrives in the claim-only
    list, it must not reach card_key -- 760 of them cost 1,051 grouped cards."""
    from nflcarddb.card_key import card_key
    from nflcarddb.parse_title import parse_title, register_designations

    try:
        register_designations(["Moonstruck", "Stargazing", "Holo Foil"])
        keys = {card_key(parse_title(t)) for t in [
            "2025 Panini Donruss Optic Derrick Henry #11",
            "2025 Panini Donruss Optic Derrick Henry Moonstruck #11",
            "2025 Panini Donruss Optic Derrick Henry Holo Foil #11",
        ]}
        assert len(keys) == 1
    finally:
        register_designations([])


def test_a_claimed_word_does_not_override_a_built_in_insert():
    """Genies keys, and a claim-only list must not take that away."""
    from nflcarddb.parse_title import parse_title, register_designations

    try:
        register_designations(["Genies"])
        assert parse_title(
            "2025 Panini Phoenix Bo Nix Genies #8").subset == "Genies"
    finally:
        register_designations([])


def test_the_ebay_roster_does_not_overwrite_the_learned_one(tmp_path):
    """Writing over the learned roster destroyed the only thing the new list
    could be compared against, which is how a 1,051-card regression became
    impossible to attribute to either change that caused it."""
    from nflcarddb import cli
    import inspect

    source = inspect.getsource(cli.cmd_catalog)
    assert "config/nfl_players_ebay.txt" in source
    assert 'Path("config/nfl_players.txt")' not in source


def test_a_stale_inserts_setting_pointing_at_ebay_data_is_recognised(tmp_path):
    """The bug that made a fix measure as doing nothing. eBay's parallels were
    written over the insert file and keyed; moving them to a claim-only file
    left the old setting pointing at the overwritten file, so they went on
    being keyed."""
    from nflcarddb.cli import _catalog_written_inserts
    from nflcarddb.facets import write_designations

    class Cfg:
        pass

    written = tmp_path / "nfl_inserts.txt"
    write_designations({"Parallel/Variety": {"Moonstruck": 5}}, written)
    cfg = Cfg()
    cfg.inserts = str(written)
    assert _catalog_written_inserts(cfg) == str(written)


def test_a_hand_built_insert_list_is_left_alone(tmp_path):
    """An insert list someone built themselves is theirs."""
    from nflcarddb.cli import _catalog_written_inserts
    from nflcarddb.roster import write_inserts

    class Cfg:
        pass

    mine = tmp_path / "mine.txt"
    write_inserts([{"name": "Moonstruck", "sightings": 9, "contexts": 1,
                    "players": 5, "where": "2025 Donruss",
                    "example": "x"}], mine)
    cfg = Cfg()
    cfg.inserts = str(mine)
    assert _catalog_written_inserts(cfg) is None


def test_a_missing_or_unset_inserts_path_is_not_a_crash():
    from nflcarddb.cli import _catalog_written_inserts

    class Cfg:
        inserts = None

    assert _catalog_written_inserts(Cfg()) is None
    assert _catalog_written_inserts(None) is None


def test_blanking_a_setting_removes_it_rather_than_writing_a_dot(tmp_path):
    """`Path("").as_posix()` is ".", so blanking a setting wrote `inserts: .`
    and every command died opening the current directory as a word list."""
    from nflcarddb.cli import disable_setting
    from nflcarddb.config import load_config

    config = tmp_path / "q.yml"
    config.write_text("database: x.sqlite\ninserts: config/old.txt\n"
                      "queries:\n  - id: a\n    keywords: football\n",
                      encoding="utf-8")

    assert disable_setting(str(config), "inserts") is True
    assert "inserts" not in config.read_text(encoding="utf-8")
    assert load_config(str(config)).inserts is None


def test_an_unusable_word_list_warns_instead_of_crashing(tmp_path, capsys):
    """A directory exists and then raises on open. That took down every
    command in the project before it had started."""
    from nflcarddb.cli import _register_learned_vocabulary

    config = tmp_path / "q.yml"
    config.write_text(f"database: x.sqlite\ninserts: {tmp_path.as_posix()}\n"
                      "queries:\n  - id: a\n    keywords: football\n",
                      encoding="utf-8")

    class Args:
        pass

    args = Args()
    args.config = str(config)
    _register_learned_vocabulary(args)          # must not raise
    assert "not usable" in capsys.readouterr().err


def test_the_catalogue_does_not_switch_the_roster_over():
    """It measured worse: 4,395 grouped cards with the learned list against
    4,187 with eBay's. The file is written; turning it on is a decision."""
    import inspect

    from nflcarddb import cli

    source = inspect.getsource(cli.cmd_catalog)
    assert '("designations", words_path),' in source
    assert '("roster", roster_path)' not in source


def test_an_unusable_inserts_path_is_cleared_too(tmp_path):
    """`inserts: .` was written by the blanking bug and then survived the run
    meant to clear it, because the detector only recognised files it could
    read. An unusable path is exactly what needs clearing."""
    from nflcarddb.cli import _catalog_written_inserts

    class Cfg:
        pass

    cfg = Cfg()
    cfg.inserts = "."
    assert _catalog_written_inserts(cfg) == "."

    cfg.inserts = str(tmp_path / "gone.txt")
    assert _catalog_written_inserts(cfg) == str(tmp_path / "gone.txt")


def test_an_unreadable_word_list_is_an_absent_one_everywhere():
    """Guarding this at each call site is what let the same crash happen
    twice: fixed in the startup path, then taking the audit down from a second
    call site added the same day."""
    from nflcarddb.parse_title import load_inserts

    assert load_inserts(".") == []
    assert load_inserts("") == []
    assert load_inserts("/nope/nope.txt") == []


def test_every_combination_is_measured_and_nothing_is_left_changed(tmp_path,
                                                                   capsys):
    """Two choices kept being changed together and then argued about. Four
    passes settle both at once, and the database is left as the config says."""
    from nflcarddb import db as store
    from nflcarddb.cli import main
    from nflcarddb.models import Sale
    from nflcarddb.parse_title import parse_title

    db = tmp_path / "v.db"
    conn = store.connect(db)
    run = store.start_run(conn, "2026-08-26")
    titles = [f"2025 Panini Mosaic Josh Allen Moonstruck #{n} Insert"
              for n in (9, 10)] + [
              f"2025 Panini Mosaic Josh Allen #{n}" for n in (9, 10)]
    sales = [Sale(item_id=str(9000 + i), title=t, price_cents=100,
                  sold_date="2026-08-26") for i, t in enumerate(titles)]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in sales], "t")
    conn.close()

    roster = tmp_path / "players.txt"
    roster.write_text("Josh Allen\n", encoding="utf-8")
    words = tmp_path / "words.txt"
    words.write_text("Moonstruck\n", encoding="utf-8")
    config = tmp_path / "q.yml"
    config.write_text(f"database: {db.as_posix()}\nroster: {roster.as_posix()}\n"
                      "queries:\n  - id: a\n    keywords: football\n",
                      encoding="utf-8")

    assert main(["try-vocab", str(roster), "--words", str(words),
                 "--config", str(config)]) == 0

    out = capsys.readouterr().out
    assert "words keyed" in out
    assert "words claimed only" in out
    assert "Leaving the database read with" in out

    # Keying the word splits the two spellings of one card apart; claim-only
    # keeps them together. Whichever wins, the state afterwards is the
    # config's, not the last combination tried.
    from nflcarddb.parse_title import _LEARNED_INSERTS
    assert _LEARNED_INSERTS == ()


def _vocab_fixture(tmp_path):
    from nflcarddb import db as store
    from nflcarddb.models import Sale
    from nflcarddb.parse_title import parse_title

    db = tmp_path / "v.db"
    conn = store.connect(db)
    run = store.start_run(conn, "2026-08-26")
    # Two sales of one card, one seller naming the insert and one not. Keying
    # the word splits them; claiming it keeps them together.
    titles = ["2025 Panini Mosaic Josh Allen Moonstruck #9 Insert",
              "2025 Panini Mosaic Josh Allen #9"]
    sales = [Sale(item_id=str(9000 + i), title=t, price_cents=100,
                  sold_date="2026-08-26") for i, t in enumerate(titles)]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in sales], "t")
    conn.close()

    roster = tmp_path / "nfl_players.txt"
    roster.write_text("Josh Allen\n", encoding="utf-8")
    words = tmp_path / "words.txt"
    words.write_text("Moonstruck\n", encoding="utf-8")
    config = tmp_path / "q.yml"
    config.write_text(
        f"database: {db.as_posix()}\nroster: {roster.as_posix()}\n"
        f"inserts: {words.as_posix()}\n"
        "queries:\n  - id: a\n    keywords: football\n", encoding="utf-8")
    return db, roster, words, config


def test_applying_writes_the_winner_into_the_config(tmp_path):
    """Measuring and deciding are the same act. Splitting them is how the
    config ended up on the third-best row while a report named the first."""
    from nflcarddb.cli import main
    from nflcarddb.config import load_config
    from nflcarddb.parse_title import register_designations, register_inserts

    db, roster, words, config = _vocab_fixture(tmp_path)
    try:
        assert main(["try-vocab", str(roster), "--words", str(words),
                     "--config", str(config), "--apply"]) == 0

        settled = load_config(str(config))
        # Claim-only wins here: it keeps the two sales on one card.
        assert settled.designations == words.as_posix()
        assert settled.inserts is None
        assert settled.roster == roster.as_posix()
    finally:
        register_inserts([])
        register_designations([])


def test_without_apply_the_config_is_left_alone(tmp_path):
    from nflcarddb.cli import main
    from nflcarddb.config import load_config
    from nflcarddb.parse_title import register_designations, register_inserts

    db, roster, words, config = _vocab_fixture(tmp_path)
    before = config.read_text(encoding="utf-8")
    try:
        assert main(["try-vocab", str(roster), "--words", str(words),
                     "--config", str(config)]) == 0
        assert config.read_text(encoding="utf-8") == before
    finally:
        register_inserts([])
        register_designations([])
