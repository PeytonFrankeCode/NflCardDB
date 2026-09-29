"""Keeping three sports apart in one database.

The card key is year + set + number, with the player deliberately left out so
that "Ja'Marr" and "JaMarr" cannot split one card in two. That is correct
while every card is football and silently wrong the moment a second sport
arrives, because Panini and Topps print the same set names in every sport they
licence. Most of these tests are about that collision.
"""

import pytest

from nflcarddb.card_key import DEFAULT_SPORT, card_key
from nflcarddb.parse_title import parse_title
from nflcarddb.sport import resolve_sport, sport_from_title


def _key(title, collected_as=None):
    return card_key(parse_title(title, collected_as=collected_as))


def test_the_same_set_and_number_in_three_sports_are_three_cards():
    """The whole reason this exists. Without the sport these are one key
    holding three different players, averaged into one price history, with
    nothing in the data looking wrong."""
    football = _key("2024 Panini Prizm Caleb Williams #1 RC", "football")
    basketball = _key("2024 Panini Prizm Victor Wembanyama #1 RC", "basketball")
    baseball = _key("2024 Panini Prizm Paul Skenes #1 RC", "baseball")

    assert football and basketball and baseball
    assert len({football, basketball, baseball}) == 3


def test_football_keys_did_not_change_when_the_other_sports_arrived():
    """540,000 rows already carry these keys. Re-keying them would mean a full
    reparse and a full re-upload to say exactly what they already said."""
    assert _key("2024 Panini Prizm Caleb Williams #1 RC", "football") == "2024-prizm-n1"
    assert _key("2024 Panini Prizm Caleb Williams #1 RC", None) == "2024-prizm-n1"


def test_a_title_that_names_no_sport_keys_the_same_as_one_that_does():
    """The failure this design is built to avoid. Sellers are inconsistent --
    one writes "Bears", the next writes nothing -- and if the detected sport
    went into every key, one silent title would split a working card."""
    with_team = _key("2024 Panini Prizm Caleb Williams #1 Bears RC", "football")
    without = _key("2024 PRIZM #1 CALEB WILLIAMS ROOKIE", "football")
    assert with_team == without


def test_a_basketball_card_keys_the_same_whether_or_not_the_title_says_so():
    """Same guarantee for the sports that ARE marked: the search stamps them,
    so the key does not depend on the seller's wording."""
    with_team = _key("2024-25 Panini Prizm Victor Wembanyama #1 Spurs", "basketball")
    without = _key("2024-25 PANINI PRIZM #1 WEMBANYAMA RC", "basketball")
    assert with_team == without
    assert with_team.startswith("basketball-")


# --- reading the sport off a title ------------------------------------------


@pytest.mark.parametrize("title,expected", [
    ("2024 Panini Prizm Caleb Williams Bears RC", "football"),
    ("2023 Topps Chrome Football Bryce Young", "football"),
    ("2024-25 Panini Prizm Victor Wembanyama Spurs", "basketball"),
    ("2023 NBA Hoops Scoot Henderson RC", "basketball"),
    ("2024 Topps Chrome Paul Skenes Pirates RC", "baseball"),
    ("2023 Bowman Draft Baseball Chase Burns", "baseball"),
])
def test_unmistakable_titles_are_read_correctly(title, expected):
    assert sport_from_title(title) == expected


def test_a_title_naming_no_sport_says_so_rather_than_guessing():
    """The ordinary case. Most titles are a year, a set, a player and nothing
    else -- which is exactly why the search that found the sale is what the
    key relies on."""
    assert sport_from_title("2024 Prizm Silver #301 RC PSA 10") is None
    assert sport_from_title("") is None


def test_a_nickname_two_leagues_share_is_not_evidence():
    """"Giants" is New York and San Francisco. "Cardinals" is Arizona and
    St. Louis. Matching one would file a card under the wrong sport with
    complete confidence, which is worse than having no rule."""
    for title in ("2024 Prizm Giants Rookie #12",
                  "2024 Topps Cardinals Star #40",
                  "2023 Select Panthers RC #7"):
        assert sport_from_title(title) is None, title


def test_the_city_makes_an_ambiguous_nickname_usable_again():
    assert sport_from_title("2024 Topps San Francisco Giants Star") == "baseball"
    assert sport_from_title("2024 Prizm New York Giants Rookie",
                            football_teams=("New York Giants",)) == "football"


def test_weight_of_evidence_beats_first_match():
    """A mis-typed listing: the word "Football" in a title otherwise entirely
    about a basketball card. Two basketball signals outvote one football
    word."""
    assert sport_from_title(
        "2024 Prizm Football Victor Wembanyama Spurs NBA RC") == "basketball"


def test_evenly_split_evidence_is_not_an_answer():
    assert sport_from_title("2024 Topps NFL NBA Multi Sport Card") is None


# --- how the two sources combine --------------------------------------------


def test_the_title_corrects_the_search_that_found_it():
    """eBay's keyword matching is loose, so a football search does return the
    odd basketball card. When the title says so plainly, it wins."""
    assert resolve_sport("2024-25 Prizm Wembanyama Spurs NBA",
                         collected_as="football") == "basketball"


def test_a_silent_title_defers_to_the_search():
    assert resolve_sport("2024 Prizm #301 Silver RC",
                         collected_as="basketball") == "basketball"


def test_neither_source_falls_back_to_the_default():
    assert resolve_sport("2024 Prizm #301", None, default="football") == "football"
    assert resolve_sport("2024 Prizm #301", None, default=None) is None


def test_the_unmarked_sport_is_the_one_the_key_omits():
    """Two halves of one decision, in two files. If they drift, every key in
    the database is wrong in a way nothing would report."""
    from nflcarddb.config import Config

    assert Config().default_sport == DEFAULT_SPORT


def test_the_worker_agrees_about_which_sport_is_unmarked():
    """The API has to match the unmarked rows when asked for the default
    sport, or the entire back catalogue vanishes from the site."""
    from pathlib import Path

    worker = Path("api/worker.js").read_text(encoding="utf-8")
    assert f'const DEFAULT_SPORT = "{DEFAULT_SPORT}"' in worker
