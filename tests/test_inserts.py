"""Learning insert-set names, which are the mirror image of players.

An insert restarts its numbering at one, so its name is part of a card's
identity -- four Phoenix cards shared #8 without it. Hand-keeping the list does
not scale: every product ships a dozen inserts and next year ships different
ones. So it is learned, the same way the roster is.

The direction of the risk is what shapes these tests. A missed insert *merges*
cards, which is the status quo. A wrongly learned name *splits* a card between
sellers who typed the word and sellers who did not -- breaking cards that
already work. So most of what follows is about what must NOT be learned.
"""

import pytest

from nflcarddb import db as store
from nflcarddb.card_key import card_key
from nflcarddb.models import Sale
from nflcarddb.parse_title import (
    load_inserts,
    parse_title,
    register_inserts,
)
from nflcarddb.roster import build_inserts, write_inserts

ROSTER = {"derrick henry", "chris olave", "saquon barkley", "ja'marr chase",
          "justin herbert", "joe burrow"}


@pytest.fixture(autouse=True)
def _clean_vocabulary():
    """The vocabulary is process-wide, so a test must not leak into the next."""
    register_inserts([])
    yield
    register_inserts([])


def _seed(path, titles):
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-25")
    sales = [Sale(item_id=str(900000 + i), title=t, price_cents=100,
                  sold_date="2026-08-25") for i, t in enumerate(titles)]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in sales], "t")
    conn.close()
    return path


def _insert_titles(name, players=ROSTER, numbers=(11, 12)):
    return [f"2025 Panini Donruss Optic {p.title()} {name} #{n} Insert"
            for p in players for n in numbers]


def test_an_insert_set_is_learned(tmp_path):
    path = _seed(tmp_path / "i.db", _insert_titles("Moonstruck"))
    names = {r["name"] for r in build_inserts(str(path), ROSTER)}
    assert "Moonstruck" in names


def test_a_player_in_only_one_product_is_not_learned(tmp_path):
    """The case that makes context count alone insufficient. A rookie who only
    appears in one product looks exactly like an insert by breadth -- what
    separates them is that nobody else's name stands beside theirs."""
    titles = _insert_titles("Moonstruck") + [
        f"2025 Panini Donruss Optic Tetairoa McMillan #{300 + n}" for n in range(8)
    ]
    path = _seed(tmp_path / "i.db", titles)
    names = {r["name"] for r in build_inserts(str(path), ROSTER)}

    assert "Moonstruck" in names
    assert not any("mcmillan" in n.lower() for n in names)


def test_marketing_noise_is_not_learned(tmp_path):
    """"Case Hit" appears beside many players in one product -- the exact shape
    being looked for. The noise vocabulary is what keeps it out."""
    titles = _insert_titles("Moonstruck") + [
        f"2025 Panini Donruss Optic {p.title()} #{n} Case Hit SSP Rookie"
        for p in ROSTER for n in (7, 8)
    ]
    path = _seed(tmp_path / "i.db", titles)
    names = {n.lower() for n in
             (r["name"] for r in build_inserts(str(path), ROSTER))}

    assert "moonstruck" in names
    assert "case hit" not in names


def test_a_brand_and_set_pair_is_not_learned(tmp_path):
    """"Panini Donruss" is in every title of one product beside every player."""
    path = _seed(tmp_path / "i.db", _insert_titles("Moonstruck"))
    names = {n.lower() for n in
             (r["name"] for r in build_inserts(str(path), ROSTER))}
    assert "panini donruss" not in names


def test_a_word_inside_a_known_phrase_is_not_proposed_alone(tmp_path):
    """"Micro" out of "Micro Mosaic" is a fragment, not a name."""
    titles = [f"2025 Panini Mosaic {p.title()} Micro Mosaic #{n}"
              for p in ROSTER for n in (11, 12)]
    path = _seed(tmp_path / "i.db", titles)
    names = {n.lower() for n in
             (r["name"] for r in build_inserts(str(path), ROSTER))}
    assert "micro" not in names


def test_a_name_spanning_many_products_is_not_an_insert(tmp_path):
    """An insert belongs to a product. A phrase everywhere is describing cards."""
    titles = []
    for year, set_name in [(2025, "Prizm"), (2024, "Mosaic"), (2023, "Select"),
                           (2022, "Donruss")]:
        titles += [f"{year} Panini {set_name} {p.title()} Wildcard #{n}"
                   for p in ROSTER for n in (5, 6)]
    path = _seed(tmp_path / "i.db", titles)
    names = {n.lower() for n in
             (r["name"] for r in build_inserts(str(path), ROSTER))}
    assert "wildcard" not in names


def test_the_proposal_carries_its_evidence(tmp_path):
    """It is a proposal, so it has to be arguable with."""
    path = _seed(tmp_path / "i.db", _insert_titles("Moonstruck"))
    row = next(r for r in build_inserts(str(path), ROSTER)
               if r["name"] == "Moonstruck")

    assert row["sightings"] == 12
    assert row["players"] == 6
    assert row["contexts"] == 1
    assert "Moonstruck" in row["example"]


def test_the_written_file_explains_the_risk(tmp_path):
    path = _seed(tmp_path / "i.db", _insert_titles("Moonstruck"))
    out = write_inserts(build_inserts(str(path), ROSTER), tmp_path / "ins.txt")
    text = out.read_text(encoding="utf-8")

    assert "SPLITS" in text                  # the direction of the danger
    assert "Moonstruck" in text
    assert "12 listings" in text             # evidence beside the name


def test_comments_and_evidence_do_not_become_names(tmp_path):
    out = tmp_path / "ins.txt"
    out.write_text("# a comment\n\nMoonstruck  # 12 listings, 6 players\n",
                   encoding="utf-8")
    assert load_inserts(out) == ["Moonstruck"]


def test_a_learned_name_separates_the_cards(tmp_path):
    """The whole point: before, the insert and the base share a number."""
    title = "2025 Panini Donruss Optic Derrick Henry Moonstruck #11 Insert"
    base = "2025 Panini Donruss Optic Derrick Henry #11"

    assert card_key(parse_title(title, ROSTER)) == card_key(parse_title(base, ROSTER))
    register_inserts(["Moonstruck"])
    assert card_key(parse_title(title, ROSTER)) != card_key(parse_title(base, ROSTER))


def test_registering_replaces_rather_than_accumulates():
    """So calling it twice is the same as calling it once."""
    register_inserts(["Moonstruck"])
    register_inserts(["Starlight"])
    title = "2025 Panini Donruss Optic Derrick Henry Moonstruck #11"
    assert parse_title(title, ROSTER).subset is None

    register_inserts(["Moonstruck"])
    assert parse_title(title, ROSTER).subset == "Moonstruck"


def test_an_empty_registration_clears_the_learned_names():
    register_inserts(["Moonstruck"])
    register_inserts([])
    title = "2025 Panini Donruss Optic Derrick Henry Moonstruck #11"
    assert parse_title(title, ROSTER).subset is None


def test_a_shouted_learned_name_folds_to_one_spelling():
    """It is in the key now, so two spellings would be two cards."""
    register_inserts(["Moonstruck"])
    a = parse_title("2025 Panini Donruss Optic Derrick Henry MOONSTRUCK #11")
    b = parse_title("2025 Panini Donruss Optic Derrick Henry Moonstruck #11")
    assert a.subset == b.subset == "Moonstruck"
    assert card_key(a) == card_key(b)


def test_a_learned_name_never_overrides_a_built_in_designation():
    """Boilerplate must stay out of the key even if the learner proposes it."""
    register_inserts(["Rated Rookie"])
    typed = parse_title("2024 Donruss Optic Ja'Marr Chase Rated Rookie #201")
    plain = parse_title("2024 Donruss Optic Ja'Marr Chase #201")
    assert card_key(typed) == card_key(plain)
