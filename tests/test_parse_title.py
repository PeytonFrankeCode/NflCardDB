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


def test_stacked_parallels_are_collected_in_a_canonical_order():
    """Deliberately not the order the seller wrote them in.

    This field is part of the key, and "Downtown! Oversized" and "OVERSIZED ...
    Downtown!" are one card -- source order gave them two keys and split three
    real Donruss groups. Alphabetical costs a slightly odd display name and buys
    a stable identity.
    """
    a = parse_title("2024 Panini Prizm Caleb Williams RC Orange Lazer #301")
    assert a.parallel == "Lazer Orange"
    assert a.player == "Caleb Williams"  # no parallel word leaks into the name


def test_the_same_parallels_in_any_order_are_one_card():
    a = parse_title("2024 Panini Prizm Caleb Williams #301 Silver Prizm Gold")
    b = parse_title("2024 Panini Prizm Caleb Williams #301 Gold Silver Prizm")
    assert card_key(a) == card_key(b)


def test_an_oversized_insert_is_a_different_card_from_the_base_insert():
    """Three Downtown numbers each held two different players, one seller
    saying Oversized or Horizontal and the other not. If it were one checklist
    the same number would be the same player -- the data proves the split."""
    base = parse_title("2025 Panini Donruss Quinshon Judkins #17 Downtown Rookie")
    over = parse_title("2025 Panini Donruss Downtown! Oversized Shedeur Sanders #17 RC")
    assert card_key(base) != card_key(over)


def test_word_order_does_not_split_an_oversized_insert():
    a = parse_title("2025 Donruss Saquon Barkley OVERSIZED Downtown #7 Philadelphia")
    b = parse_title("2025 Panini Donruss - Downtown! Oversized Saquon Barkley #7")
    assert card_key(a) == card_key(b)


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


def test_a_draft_position_is_not_a_card_number():
    """2024-contenders-n1 collected Mahomes, Williams and Daniels -- entirely
    from "#1 Draft Pick" and "#1 Ranked". Nine sales, one fake card."""
    for title in [
        "2025 Panini Prizm Cam Ward Rookie #1 Overall Pick RC",
        "2024 Panini Contenders Caleb Williams #1 Draft Pick Rookie Ticket",
        "2024 Panini Contenders Patrick Mahomes MVP #1 Ranked",
    ]:
        assert parse_title(title).card_number is None, title


def test_draft_picks_the_set_keeps_its_number():
    """The discriminator is the plural: "Draft Picks" is a Panini product and
    "Draft Pick" after a number is where someone was taken. The set is claimed
    *after* the number, so the plural is still in the text at that point."""
    for title in ["2024 Panini Prizm Draft Picks #25 Caleb Williams",
                  "2024 Panini Prizm #25 Draft Picks Caleb Williams"]:
        assert parse_title(title).card_number == "25", title


def test_a_set_range_is_not_card_number_one():
    """"#1-330" is the span of a whole set on offer."""
    a = parse_title("2025 Panini Prizm #1-330 Base Set Arch Manning")
    assert a.card_number is None


def test_pick_your_card_listings_get_no_identity():
    """The price is real; the card is not knowable. Any name in the title is an
    example rather than what sold, so keying it would drop a $3 sale into a
    genuine card's price history."""
    for title in [
        "2025 Panini Prizm #1-330 Complete Your Set Pick Your Card Arch Manning",
        "2024 Donruss You Pick Your Card Caleb Williams #389",
        "2025 Mosaic Choose Your Card Travis Hunter",
    ]:
        attrs = parse_title(title)
        assert card_key(attrs) is None, title


def test_the_sale_behind_a_pick_your_card_listing_is_still_kept():
    """Suppressing the identity must not suppress the row -- the title and
    price stay, they simply join no card."""
    attrs = parse_title("2025 Panini Prizm Pick Your Card Complete Your Set")
    assert attrs.year == 2025
    assert attrs.set_name == "Prizm"
    assert attrs.player is None


def test_a_set_name_ending_in_a_subset_name_survives_intact():
    """"Prizm Draft Picks" is a college product and "Prizm" is the NFL one.
    Claiming the subset first left the set matching bare "Prizm", so a college
    card and an NFL card with the same number merged into one price history."""
    assert parse_title(
        "2024 Panini Prizm Draft Picks #25 Caleb Williams").set_name == "Prizm Draft Picks"
    assert parse_title(
        "2024 Panini Prizm #25 Caleb Williams").set_name == "Prizm"
    assert parse_title(
        "2025 Panini Select Draft Picks #12 Travis Hunter").set_name == "Select Draft Picks"


def test_two_products_sharing_a_number_no_longer_share_a_key():
    college = parse_title("2024 Panini Prizm Draft Picks #25 Caleb Williams")
    nfl = parse_title("2024 Panini Prizm #25 Caleb Williams")
    assert card_key(college) != card_key(nfl)


def test_an_insert_set_is_part_of_the_card():
    """Peyton's 2025-phoenix-n8 held fourteen sales and four players. Contours,
    Phoenician, Genies and Archetype are four insert sets inside Phoenix, and
    each restarts its numbering at one -- so four different cards were sharing
    a key, and a price history."""
    titles = [
        "Panini Phoenix Contours Myles Garrett #8 Cleveland Browns 2025 Football",
        "2025 Panini Phoenix Travis Hunter Phoenician SSP Case Hit #8 Jaguars",
        "2025 Panini Phoenix Bo Nix GENIES Denver Broncos #8 CASE HIT SSP",
        "2025 Panini Phoenix Omarion Hampton Archetype Case Hit RC Chargers #8",
    ]
    keys = {card_key(parse_title(t)) for t in titles}
    assert len(keys) == 4


def test_the_insert_name_is_read_not_mistaken_for_the_player():
    """"Micro Mosaic" was being stored as a player's name."""
    a = parse_title("Panini 2025 Mosaic Football Micro Mosaic Omarion Hampton RC #11")
    assert a.subset == "Micro Mosaic"
    assert a.player == "Omarion Hampton"


def test_a_shouted_insert_name_is_one_card_not_two():
    """The subset is in the key now, so two spellings would be two cards."""
    a = parse_title("2025 Panini Phoenix Bo Nix GENIES #8")
    b = parse_title("2025 Panini Phoenix Bo Nix Genies #8")
    assert a.subset == b.subset == "Genies"
    assert card_key(a) == card_key(b)


def test_boilerplate_beside_the_player_never_reaches_the_key():
    """"Rated Rookie" is what Donruss calls its base rookie cards. Keying it
    would split a real card between sellers who type it and sellers who don't --
    the opposite failure, and worse, because it breaks cards that work."""
    for typed, plain in [
        ("2024 Donruss Optic Caleb Williams Rated Rookie #201 Aqua",
         "2024 Donruss Optic Caleb Williams #201 Aqua"),
        ("2025 Topps Chrome Caleb Williams Future Stars #NFS-1 Refractor",
         "2025 Topps Chrome Caleb Williams #NFS-1 Refractor"),
    ]:
        assert card_key(parse_title(typed)) == card_key(parse_title(plain))
        assert parse_title(typed).subset is None


def test_rated_rookie_still_sets_the_flag_without_entering_the_key():
    a = parse_title("2024 Donruss Optic Caleb Williams Rated Rookie #201")
    assert a.is_rookie is True
    assert a.subset is None


def test_the_donruss_optic_inserts_no_longer_share_a_number():
    """The second collision wave: one Optic number held Uptown, Rookie Recruits
    and Sunday Kings; another held Uptowns, Sunday Kings and Rookie Kings."""
    keys = {card_key(parse_title(t)) for t in [
        "2025 Donruss Optic Football Shedeur Sanders Uptown #18 (RC)",
        "Panini 2025 Donruss Optic Pat Bryant Rookie Recruits Auto RC /199 #18",
        "2025 Panini Donruss Optic Derrick Henry Sunday Kings #18",
    ]}
    assert len(keys) == 3


def test_the_same_insert_in_either_word_order_is_one_card():
    a = parse_title("2025 Panini Donruss Optic Derrick Henry Sunday Kings #18")
    b = parse_title("2025 Panini Donruss Optic - Sunday Kings Derrick Henry #18 SSP")
    assert card_key(a) == card_key(b)
    assert a.player == b.player == "Derrick Henry"


def test_the_contenders_inserts_no_longer_share_a_number():
    """Power Players, Rookie Stallions and Round Numbers, all at #1."""
    keys = {card_key(parse_title(t)) for t in [
        "2024 Panini Contenders - Power Players Patrick Mahomes II, Joe Burrow #1",
        "2024 Panini Contenders #1 Caleb Williams Rookie Stallions",
        "Panini Contenders 2024 Round Numbers #1 Jayden Daniels/Drake Maye RC",
    ]}
    assert len(keys) == 3


def test_a_comment_in_the_roster_is_not_a_player(tmp_path):
    """Every roster this project writes carries a `#` header, and each of
    those lines was being loaded as a name."""
    from nflcarddb.parse_title import load_roster

    path = tmp_path / "players.txt"
    path.write_text("# Player names learned from collected titles.\n"
                    "#\n"
                    "\n"
                    "Tom Brady\n"
                    "Ja'Marr Chase\n", encoding="utf-8")

    assert load_roster(path) == {"tom brady", "ja'marr chase"}


def test_a_word_no_vocabulary_knows_is_reported_rather_than_swallowed():
    """An unknown word gets absorbed into the player's name by the run scan.
    Surfacing it is what turned guessing at insert names into a ranked list of
    what is actually missing -- and that list is where "Dragonscale" came from,
    which is why the example here has to be a word still nobody knows."""
    a = parse_title("2024 Panini Select Ja'Marr Chase Wyvernhide #12 /81 Auto")
    assert a.unparsed == "Wyvernhide"
    assert a.print_run == 81
    assert a.is_auto is True


def test_the_gap_reports_findings_are_now_recognised():
    """The words the report put at the top, no longer unaccounted for."""
    a = parse_title("2024 Panini Select Ja'Marr Chase Dragonscale #12 /81")
    assert a.subset == "Dragonscale"
    assert a.unparsed is None

    b = parse_title("2026 Topps Flagship Fernando Mendoza #25")
    assert b.set_name == "Topps Flagship"

    c = parse_title("2025 Topps Resurgence Bo Nix Voltaic #53")
    assert c.set_name == "Topps Resurgence"
    assert c.subset == "Voltaic"


def test_vintage_condition_shorthand_is_not_a_players_name():
    """"EX-EXMINT", "NR-MINT" and "VG-VGEX" were being read as names on more
    than 1,200 sales between them."""
    for title in ["1975 Topps #416 Joe Theismann (EX-EXMINT)",
                  "1968 Topps #100 John Unitas (VG-VGEX)",
                  "2001 Upper Deck #DU-GS Deuce McAllister NR-MINT"]:
        player = (parse_title(title).player or "").lower()
        assert "mint" not in player and "vgex" not in player, title


def test_a_game_worn_card_is_a_relic():
    """"GAME-WORN" describes a relic and was not flagged as one, so those
    cards keyed as ordinary base cards."""
    assert parse_title(
        "2007 Absolute Michael Robinson GAME-WORN Jumbo #12").is_relic is True
    assert parse_title(
        "BAKER MAYFIELD 2024 DONRUSS THREADS #TA-BM").is_relic is True


def test_a_fully_understood_title_leaves_nothing_over():
    assert parse_title(
        "2021 Panini Prizm Ja'Marr Chase #220 PSA 10").unparsed is None


def test_a_roster_name_is_not_reported_as_unrecognised():
    """The whole name is accounted for when it came from the roster."""
    a = parse_title("2024 Panini Legacy Wyvernhide Tom Brady Silver #12",
                    {"tom brady"})
    assert a.player == "Tom Brady"
    assert a.unparsed == "Wyvernhide"


def test_a_vintage_card_belongs_to_the_makers_set():
    """"1975 Topps #416" had no set at all, which is most of the pre-2000 data."""
    a = parse_title("1975 Topps Set-Break #416 Joe Theismann (EX-EXMINT)")
    assert a.set_name == "Topps"
    assert a.year == 1975
    assert a.card_number == "416"


def test_the_makers_name_never_beats_its_own_product():
    """Matching is longest-first by term, so adding "Panini" to the set list
    made it beat "Prizm" and every modern Panini card lost its real set."""
    assert parse_title(
        "2021 Panini Prizm Ja'Marr Chase #220 PSA 10").set_name == "Prizm"
    assert parse_title(
        "1998 Topps Chrome Peyton Manning Refractor #165").set_name == "Topps Chrome"


def test_a_card_number_with_letters_after_the_digits():
    """Topps Flagship numbers like "25GH-LB" were dropped whole: the pattern
    only allowed letters before the digits."""
    assert parse_title(
        "2026 Topps Flagship #25GH-LB Luther Burden").card_number == "25GH-LB"
    # ...without costing the shape that already worked.
    assert parse_title(
        "2025 Topps Chrome Caleb Williams #NFS-1 Refractor").card_number == "NFS-1"
    assert parse_title("2021 Prizm Ja'Marr Chase #220").card_number == "220"


DUAL_ROSTER = {"cj stroud", "cam ward", "bo nix", "courtland sutton",
               "patrick mahomes", "caleb williams"}


def test_a_card_with_two_players_keeps_both():
    """Surnames dominated the second gap report because the name scan took one
    player and dropped the other: "C.J. Stroud Cam Ward" is one card."""
    a = parse_title("C.J. Stroud Cam Ward 2025 Panini Mosaic #12", DUAL_ROSTER)
    assert "Stroud" in a.player and "Ward" in a.player
    assert a.unparsed is None


def test_two_players_in_either_order_are_one_card():
    """Different sellers write the pair either way round, so the names are
    sorted -- the same reasoning that put the parallels in a canonical order."""
    a = parse_title("C.J. Stroud Cam Ward 2025 Panini Mosaic #12", DUAL_ROSTER)
    b = parse_title("Cam Ward CJ Stroud 2025 Panini Mosaic #12", DUAL_ROSTER)
    assert a.player == b.player
    assert card_key(a) == card_key(b)


def test_punctuation_does_not_hide_the_second_player():
    """"C.J. Stroud" and "cj stroud" are one name written two ways, and a plain
    substring search matches neither against the other."""
    a = parse_title("C.J. Stroud Cam Ward 2025 Panini Mosaic #12", DUAL_ROSTER)
    assert a.player.count("/") == 1


def test_a_single_player_card_is_left_alone():
    a = parse_title("2025 Panini Mosaic Cam Ward #12", DUAL_ROSTER)
    assert "/" not in a.player


def test_the_second_gap_reports_words_are_recognised():
    """Color Match arrived as two unrecognised words, 789 and 546 sales."""
    assert parse_title(
        "2025 Panini Mosaic Emeka Egbuka Color Match #12").parallel == "Color Match"
    assert parse_title("1967 Philadelphia - Gale Sayers #35").set_name == "Philadelphia"
    assert parse_title(
        "1994 Pinnacle Barry Sanders Detroit Lions #100").set_name == "Pinnacle"
    assert parse_title(
        "Von Miller 2021 Panini Mosaic Super Bowl #12").subset == "Super Bowl"
