"""Matching listings against what was actually printed.

The whole point of a checklist is to stop guessing -- so most of these are
about the cases where it must REFUSE to answer. A checklist that resolves an
ambiguous card is worse than no checklist, because it turns "we do not know"
into a confident wrong answer that nothing downstream can question.
"""

from nflcarddb import checklist as cl
from nflcarddb import db as store
from nflcarddb.parse_title import parse_title

import pytest


# 2026 Topps: Josh Allen is on the base card and in four different inserts.
# This is the real shape of the problem, taken from Peyton's own catalogue.
TOPPS_2026 = [
    {"year": 2026, "set_name": "Topps", "card_number": "97",
     "player": "Patrick Mahomes"},
    {"year": 2026, "set_name": "Topps", "card_number": "301",
     "player": "Fernando Mendoza"},
    {"year": 2026, "set_name": "Topps", "card_number": "301",
     "player": "Fernando Mendoza", "parallel": "Pink Prizm"},
    {"year": 2026, "set_name": "Topps", "card_number": "44",
     "player": "Josh Allen"},
    {"year": 2026, "set_name": "Topps", "subset": "Touchdown",
     "card_number": "TD-16", "player": "Josh Allen"},
    {"year": 2026, "set_name": "Topps", "subset": "Wild Card",
     "card_number": "WC-1", "player": "Josh Allen"},
    {"year": 2026, "set_name": "Topps", "subset": "Prizmania",
     "card_number": "PC-7", "player": "Josh Allen", "print_run": 25},
    {"year": 2026, "set_name": "Topps", "subset": "Kaiju",
     "card_number": "S-5", "player": "Josh Allen"},
]


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "cl.db")
    cl.import_rows(c, TOPPS_2026, source="test")
    yield c
    c.close()


def test_a_checklist_row_keys_the_same_way_a_sale_does(conn):
    """They must meet on one column or the table is useless."""
    from nflcarddb.card_key import card_key
    sale = parse_title("2026 Topps Fernando Mendoza #301 Rookie RC")
    row = conn.execute(
        "SELECT card_key FROM checklist WHERE card_number = '301' "
        "AND parallel IS NULL").fetchone()
    assert row["card_key"] == card_key(sale)


def test_the_number_is_filled_in_when_it_is_not_a_choice(conn):
    """Mendoza has one base card, so a title without a number is determined."""
    attrs = parse_title("2026 Topps Fernando Mendoza Rookie RC Indiana")
    assert attrs.card_number is None
    assert cl.resolve_number(conn, attrs) == "301"


def test_the_number_is_refused_when_it_would_be_a_guess(conn):
    """Josh Allen is five different cards in this set.

    This is the case that killed inferring numbers from sibling sales, and a
    checklist does not make it go away -- it makes it visible.
    """
    attrs = parse_title("2026 Topps Josh Allen Rookie Bills")
    assert cl.resolve_number(conn, attrs) is None
    assert len(cl.candidates(conn, attrs)) == 5


def test_naming_the_insert_resolves_what_the_number_could_not(conn):
    """The gain in practice: the title says Touchdown but no number."""
    attrs = parse_title("2026 Topps Josh Allen Rookie Bills")
    attrs.subset = "Touchdown"
    assert cl.resolve_number(conn, attrs) == "TD-16"


def test_an_unloaded_product_is_unknown_rather_than_wrong(conn):
    """The distinction that stops a coverage gap reading as a parse failure.

    Without it, every card in a product nobody has loaded would be reported as
    naming a card that does not exist.
    """
    absent = parse_title("2019 Panini Prizm Kyler Murray #301")
    assert cl.covers(conn, 2019, "Prizm") is False
    assert cl.verify(conn, absent) is None


def test_a_card_that_was_never_printed_is_reported_as_such(conn):
    """A real finding, and only sayable inside a loaded product."""
    fake = parse_title("2026 Topps Fernando Mendoza #9999")
    assert fake.card_number == "9999"
    assert cl.covers(conn, 2026, "Topps") is True
    assert cl.verify(conn, fake) is False


def test_verifying_a_numberless_parse_never_condemns_the_card(conn):
    """With no number there is nothing solid enough to call a card fake.

    Saying False here looked reasonable and was measured against 1,500 real
    titles: it fired 246 times, and the cause was nearly always a misread
    player name rather than a card that was never printed. So it accused the
    checklist of a gap that belonged to the parser. True still means "this
    player has some card in this product" -- a weak yes, not a card-level one.
    """
    vague = parse_title("2026 Topps Josh Allen Rookie Bills")
    assert vague.card_number is None
    assert cl.verify(conn, vague) is True
    assert cl.resolve_number(conn, vague) is None      # still not identified

    nobody = parse_title("2026 Topps Tom Brady Buccaneers")
    assert cl.verify(conn, nobody) is None             # unknown, not false


def test_a_number_written_without_its_hyphen_still_matches(conn):
    """Sellers type "#AK20" where the checklist says "AK-20"."""
    conn.execute("INSERT INTO checklist (card_key, year, set_name, card_number, "
                 "number_fold, player, updated_at) VALUES ('ak', 2026, 'Topps', "
                 "'AK-20', 'AK20', 'Bo Nix', '2026-01-01')")
    attrs = parse_title("2026 Topps Bo Nix All Kings #AK20 Broncos")
    assert attrs.card_number == "AK20"
    assert [r["card_number"] for r in cl.candidates(conn, attrs)] == ["AK-20"]


def test_a_printed_card_verifies(conn):
    assert cl.verify(conn, parse_title("2026 Topps Patrick Mahomes #97")) is True


def test_player_spelling_does_not_break_the_match(conn):
    """A checklist writes Ja'Marr and a seller writes JaMarr. Both fold."""
    conn.execute("INSERT INTO checklist (card_key, year, set_name, card_number, "
                 "player, updated_at) VALUES ('k', 2026, 'Topps', '12', "
                 "'Ja''Marr Chase', '2026-01-01')")
    attrs = parse_title("2026 Topps JaMarr Chase Bengals")
    assert {r["card_number"] for r in cl.candidates(conn, attrs)} == {"12"}


def test_the_vocabulary_is_read_off_rather_than_guessed(conn):
    """Prizmania and Kaiju carry no colour word and behave like nothing else,
    which is exactly why guessing missed them."""
    vocab = cl.vocabulary(conn)
    assert {"Touchdown", "Wild Card", "Prizmania", "Kaiju"} <= set(vocab["inserts"])
    assert "Pink Prizm" in vocab["parallels"]


def test_importing_twice_does_not_double_the_checklist(conn):
    before = cl.stats(conn)["cards"]
    cl.import_rows(conn, TOPPS_2026, source="test")
    assert cl.stats(conn)["cards"] == before


def test_a_reimport_corrects_a_row_rather_than_leaving_both(conn):
    cl.import_rows(conn, [{"year": 2026, "set_name": "Topps", "subset": "Kaiju",
                           "card_number": "S-5", "player": "Josh Allen",
                           "print_run": 99}], source="fix")
    row = conn.execute("SELECT print_run FROM checklist WHERE card_number='S-5'"
                       ).fetchone()
    assert row["print_run"] == 99


def test_rows_too_thin_to_identify_a_card_are_dropped(tmp_path):
    c = store.connect(tmp_path / "thin.db")
    stats = cl.import_rows(c, [
        {"year": 2026, "set_name": "Topps"},              # no number, no player
        {"set_name": "Topps", "card_number": "1"},         # no year
        {"year": 2026, "card_number": "1"},                # no set
        {"year": 2026, "set_name": "Topps", "card_number": "1"},   # keeps
    ])
    assert stats["loaded"] == 1
    c.close()


def test_coverage_is_recorded_from_what_landed(conn):
    """Not from what the caller meant to load."""
    row = conn.execute("SELECT cards FROM checklist_sets WHERE year = 2026 "
                       "AND set_name = 'Topps'").fetchone()
    assert row["cards"] == cl.stats(conn)["cards"]


# ------------------------------------------------------------------- enrich
#
# The checklist's real job, found the hard way. Teaching the parser its 4,355
# insert names made parsing WORSE on 1,500 real titles -- 435 verified cards
# down to 377 -- because an insert name belongs to one product and a global
# vocabulary poisons every title containing the word. Looking the card up
# afterwards and reading the insert off the checklist costs nothing.


def test_the_insert_is_read_off_the_checklist_not_out_of_the_title(conn):
    """The exact case from the data: "2025 Topps Chrome Kaiju #10 Jaxson Dart"
    parsed as base #10, which the checklist says is a different player."""
    attrs = parse_title("2026 Topps Josh Allen #TD-16 Bills")
    assert attrs.subset is None
    assert cl.enrich(conn, attrs) == ["subset"]
    assert attrs.subset == "Touchdown"


def test_enriching_changes_the_card_it_groups_under(conn):
    """The point of all of it: the insert reaches the key."""
    from nflcarddb.card_key import card_key
    plain = parse_title("2026 Topps Josh Allen #TD-16 Bills")
    enriched = parse_title("2026 Topps Josh Allen #TD-16 Bills")
    cl.enrich(conn, enriched)
    assert card_key(plain) != card_key(enriched)
    assert "touchdown" in card_key(enriched)


def test_a_title_the_checklist_cannot_place_is_left_alone(conn):
    """The safety property. A parse that matches nothing must come back
    untouched, or the checklist could damage cards it knows nothing about."""
    attrs = parse_title("2011 Topps Chrome Cam Newton #14 Panthers")
    before = (attrs.subset, attrs.card_number, attrs.print_run)
    assert cl.enrich(conn, attrs) == []
    assert (attrs.subset, attrs.card_number, attrs.print_run) == before


def test_the_checklists_spelling_of_the_number_wins(conn):
    """So "#AK20" and "#AK-20" stop being two cards."""
    conn.execute("INSERT INTO checklist (card_key, year, set_name, card_number, "
                 "number_fold, player, subset, updated_at) VALUES "
                 "('ak2', 2026, 'Topps', 'AK-20', 'AK20', 'Bo Nix', "
                 "'All Kings', '2026-01-01')")
    attrs = parse_title("2026 Topps Bo Nix #AK20 Broncos")
    assert "card_number" in cl.enrich(conn, attrs)
    assert attrs.card_number == "AK-20"


def test_an_ambiguous_insert_is_not_filled_in(conn):
    """Two inserts sharing a number and a player is a real choice, and picking
    one would be the invention this module exists to avoid."""
    conn.executemany(
        "INSERT INTO checklist (card_key, year, set_name, card_number, "
        "number_fold, player, subset, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        [("d1", 2026, "Topps", "5", "5", "Bo Nix", "Duos", "2026-01-01"),
         ("d2", 2026, "Topps", "5", "5", "Bo Nix", "Manga", "2026-01-01")])
    attrs = parse_title("2026 Topps Bo Nix #5 Broncos")
    assert cl.identify(conn, attrs) is None
    assert cl.enrich(conn, attrs) == []


def test_parallels_of_one_card_still_count_as_identified(conn):
    """Rows differing only by colour are the same card, so they must not read
    as ambiguity -- otherwise every card with parallels would be unenrichable."""
    attrs = parse_title("2026 Topps Fernando Mendoza #301 Rookie RC")
    row = cl.identify(conn, attrs)
    assert row is not None and row["card_number"] == "301"
