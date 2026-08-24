import pytest

from nflcarddb.card_key import card_key
from nflcarddb.parse_title import parse_title


def test_modern_graded_rookie():
    a = parse_title("2023 Panini Prizm CJ Stroud Silver Prizm RC #339 PSA 10")
    assert a.year == 2023
    assert a.brand == "Panini"
    assert a.set_name == "Prizm"
    assert a.card_number == "339"
    assert a.grader == "PSA"
    assert a.grade == 10.0
    assert a.is_graded is True
    assert a.is_rookie is True
    assert a.player == "CJ Stroud"
    assert a.confidence >= 0.8


def test_half_grade_and_optic():
    a = parse_title("2021 Donruss Optic Ja'Marr Chase Rated Rookie Holo #201 BGS 9.5")
    assert a.grader == "BGS"
    assert a.grade == 9.5
    assert a.set_name == "Donruss Optic"
    assert a.brand == "Panini"
    assert a.parallel == "Holo"
    assert a.is_rookie is True
    assert a.player == "Ja'Marr Chase"


def test_serial_numbering():
    a = parse_title("2023 Panini Select Bijan Robinson Zebra Prizm RC #44 12/99")
    assert a.serial_number == 12
    assert a.print_run == 99
    assert a.card_number == "44"
    assert a.parallel == "Zebra"
    assert a.player == "Bijan Robinson"


def test_print_run_without_serial():
    a = parse_title("2022 National Treasures Brock Purdy Rookie Patch Auto /99")
    assert a.print_run == 99
    assert a.serial_number is None
    assert a.is_auto is True
    assert a.is_relic is True
    assert a.is_rookie is True
    assert a.set_name == "National Treasures"


def test_vintage_refractor():
    a = parse_title("1998 Topps Chrome Peyton Manning Refractor Rookie #165 SGC 8.5")
    assert a.year == 1998
    assert a.set_name == "Topps Chrome"
    assert a.brand == "Topps"
    assert a.parallel == "Refractor"
    assert a.grade == 8.5
    assert a.grader == "SGC"
    assert a.player == "Peyton Manning"


def test_hyphenated_and_apostrophe_names():
    a = parse_title("2024 Panini Prizm Amon-Ra St. Brown Blue Wave #12")
    assert "Amon-Ra" in (a.player or "")

    b = parse_title("2020 Prizm Ja'Marr Chase Base #325")
    assert b.player == "Ja'Marr Chase"


def test_suffix_retained():
    a = parse_title("2024 Panini Mosaic Marvin Harrison Jr Genesis Prizm RC #301")
    assert a.player is not None
    assert a.player.startswith("Marvin Harrison")
    assert a.set_name == "Mosaic"


def test_all_caps_title_normalised():
    a = parse_title("2023 PANINI PRIZM JORDAN ADDISON SILVER RC #301 PSA 10")
    assert a.player == "Jordan Addison"
    assert a.grade == 10.0


def test_ungraded_card_has_no_grader():
    a = parse_title("2023 Panini Donruss Puka Nacua Rated Rookie #364")
    assert a.is_graded is False
    assert a.grader is None
    assert a.grade is None
    assert a.player == "Puka Nacua"


def test_grader_without_number():
    a = parse_title("2019 Prizm Kyler Murray Rookie PSA")
    assert a.grader == "PSA"
    assert a.is_graded is True
    assert a.grade is None


def test_seller_noise_does_not_become_player():
    a = parse_title("HOT INVEST 2023 Panini Prizm Football Card Rare SSP Mint Free Shipping")
    # No real name present; anything found should not be marketing noise.
    if a.player:
        assert a.player.lower() not in {"hot invest", "free shipping", "rare ssp"}
    assert a.confidence < 0.8


def test_empty_title_is_safe():
    a = parse_title("")
    assert a.player is None
    assert a.confidence == 0.0


def test_roster_match_boosts_confidence():
    roster = {"cj stroud", "bijan robinson"}
    with_roster = parse_title("2023 Prizm CJ Stroud #339", roster=roster)
    without = parse_title("2023 Prizm CJ Stroud #339")
    assert with_roster.player == "CJ Stroud"
    assert with_roster.confidence > without.confidence


def test_card_number_wins_over_serial_reading():
    # "#301 /249" must be card 301 out of a 249 run, not "serial 301 of 249".
    a = parse_title("2024 Panini Prizm Caleb Williams RC Orange Lazer #301 /249 BGS 9.5")
    assert a.card_number == "301"
    assert a.print_run == 249
    assert a.serial_number is None


def test_stacked_parallels_collected_in_source_order():
    a = parse_title("2024 Panini Prizm Caleb Williams RC Orange Lazer #301")
    assert a.parallel == "Orange Lazer"
    assert a.player == "Caleb Williams"  # no parallel word leaks into the name


def test_team_extracted_and_kept_out_of_player():
    a = parse_title("JAYDEN DANIELS 2024 SELECT CONCOURSE ROOKIE #52 PSA 10 COMMANDERS")
    assert a.player == "Jayden Daniels"
    assert a.team == "Washington Commanders"


def test_team_nickname_normalised_to_full_name():
    assert parse_title("Christian McCaffrey 2017 Prizm #201 49ers").team == "San Francisco 49ers"
    assert parse_title("2023 Prizm Baker Mayfield Bucs #44").team == "Tampa Bay Buccaneers"


def test_city_colour_not_mistaken_for_parallel():
    # "Green Bay" must be consumed as a team, leaving "Silver" as the parallel.
    a = parse_title("2023 Prizm Jordan Love Green Bay Packers Silver #12")
    assert a.team == "Green Bay Packers"
    assert a.parallel == "Silver"
    assert a.player == "Jordan Love"


@pytest.mark.parametrize(
    "title,expected_year",
    [
        ("2023 Panini Prizm Anthony Richardson #301", 2023),
        ("1986 Topps Jerry Rice Rookie #161", 1986),
        ("2023-24 Panini Prizm Draft Picks Caleb Williams", 2023),
    ],
)
def test_year_variants(title, expected_year):
    assert parse_title(title).year == expected_year


def test_a_one_of_one_is_not_card_number_one():
    """From Peyton's audit: 2025-prizm-n1 held four different players, and
    2024-contenders-n1 three. Sellers write a one-of-one as "#1/1", and the
    numerator was being claimed as the card number -- so every 1/1 in a set
    collapsed onto one key whoever was on the card."""
    a = parse_title("2025 Panini Prizm Cam Ward Gold Vinyl #1/1 Rookie")
    assert a.card_number is None
    assert (a.serial_number, a.print_run) == (1, 1)

    b = parse_title("2024 Panini Contenders Patrick Mahomes Auto #1/1")
    assert card_key(a) != card_key(b)


def test_an_attached_slash_is_serial_numbering():
    a = parse_title("2025 Panini Phoenix Travis Hunter Orange #8/10 RC")
    assert a.card_number is None
    assert (a.serial_number, a.print_run) == (8, 10)


def test_a_detached_slash_is_still_a_print_run():
    """The case the original ordering existed to protect: "#301 /249" is card
    301 from a 249-card run, not serial 301 of 249."""
    a = parse_title("2024 Panini Prizm Caleb Williams #301 /249 Gold")
    assert a.card_number == "301"
    assert a.print_run == 249
    assert a.serial_number is None


def test_a_numerator_bigger_than_the_run_is_not_a_serial():
    """"#202/99" is card 202 from a /99 parallel, written without the space
    that would have made it obvious. Nobody owns copy 202 of 99."""
    a = parse_title("2024 Panini Prizm Caleb Williams #202/99 Gold")
    assert a.card_number == "202"
    assert a.print_run == 99
    assert a.serial_number is None


def test_a_card_number_and_a_separate_serial_both_survive():
    a = parse_title("2024 Donruss Jayden Daniels #389 12/99")
    assert a.card_number == "389"
    assert (a.serial_number, a.print_run) == (12, 99)
