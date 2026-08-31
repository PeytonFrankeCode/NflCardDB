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


def test_verifying_a_numberless_parse_asks_the_weaker_question(conn):
    """Deliberate, and worth stating: with no number to check, the strongest
    honest claim is that this player has *some* card in this product. It is a
    weak yes, not a card-level one -- so callers must not read True here as
    "identified"."""
    vague = parse_title("2026 Topps Josh Allen Rookie Bills")
    assert vague.card_number is None
    assert cl.verify(conn, vague) is True
    assert cl.resolve_number(conn, vague) is None      # still not identified

    nobody = parse_title("2026 Topps Tom Brady Buccaneers")
    assert cl.verify(conn, nobody) is False


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
