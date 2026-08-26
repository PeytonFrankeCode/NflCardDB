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
    """Genies was typed in by hand two rounds ago. eBay has it too, and it
    should be written once, not twice."""
    from nflcarddb.facets import write_inserts

    path = tmp_path / "inserts.txt"
    write_inserts({"Parallel/Variety": {"Genies": 9}}, path)
    written = [l for l in path.read_text(encoding="utf-8").splitlines()
               if l and not l.startswith("#")]
    assert written == []


def test_names_already_understood_are_left_out_of_the_insert_file(tmp_path):
    """"Silver Prizm" and "Refractor" are already parallels. Subsets are
    claimed before parallels, so moving them across would rewrite working keys
    for no gain."""
    from nflcarddb.facets import write_inserts

    path = tmp_path / "inserts.txt"
    write_inserts(STORE, path)
    written = [l for l in path.read_text(encoding="utf-8").splitlines()
               if l and not l.startswith("#")]

    assert "Moonstruck" in written
    assert "Stargazing" in written
    assert "Silver Prizm" not in written
    assert "Refractor" not in written


def test_the_insert_file_feeds_straight_back_into_the_parser(tmp_path):
    """End to end: a name eBay knows and the built-in list does not becomes
    part of the card's identity."""
    from nflcarddb.card_key import card_key
    from nflcarddb.facets import write_inserts
    from nflcarddb.parse_title import (load_inserts, parse_title,
                                       register_inserts)

    path = tmp_path / "inserts.txt"
    write_inserts({"Parallel/Variety": {"Moonstruck": 50}}, path)
    try:
        base = parse_title("2025 Panini Donruss Optic Derrick Henry #11")
        register_inserts(load_inserts(path))
        insert = parse_title("2025 Panini Donruss Optic Derrick Henry Moonstruck #11")
        assert card_key(base) != card_key(insert)
    finally:
        register_inserts([])


def test_placeholders_never_reach_the_vocabulary_files(tmp_path):
    """"Not Specified" as a player name would be a card belonging to nobody."""
    from nflcarddb.facets import write_inserts, write_roster

    dirty = {"Player/Athlete": {"Not Specified": 90000, "Tom Brady": 5},
             "Parallel/Variety": {"[Base]": 900, "Moonstruck": 5}}

    roster = tmp_path / "p.txt"
    assert write_roster(dirty, roster) == 1
    assert "Not Specified" not in roster.read_text(encoding="utf-8")

    inserts = tmp_path / "i.txt"
    write_inserts(dirty, inserts)
    assert "[Base]" not in inserts.read_text(encoding="utf-8")
