"""Giving one physical card one identity across differently-worded sales.

Without this there is no such thing as "this card's price over time": every
sale is an unrelated row whose only link is free text a seller typed.

The dangerous failure is not a missing group, it is a wrong one -- two
different cards sharing a key silently average into a single price history that
describes neither. So most of these are about refusing to guess.
"""

from nflcarddb.card_key import (
    MIN_CONFIDENCE,
    card_key,
    card_name,
    grade_label,
    normalize_player,
)
from nflcarddb.models import CardAttrs
from nflcarddb.parse_title import parse_title


def test_the_same_card_written_three_ways_gets_one_key():
    """The whole point, and taken from real collected titles."""
    keys = {
        card_key(parse_title(t))
        for t in [
            "2021 Panini Prizm Ja'Marr Chase RC #220 PSA 10",
            "Ja'Marr Chase 2021 Prizm Rookie Card #220 PSA 10 GEM MINT Bengals",
            "2021 PRIZM #220 JAMARR CHASE ROOKIE PSA 10",
        ]
    }
    assert len(keys) == 1
    assert None not in keys


def test_grade_is_not_part_of_the_card():
    """A PSA 10 and a PSA 9 of #220 are one card in two conditions. Baking the
    grade in would make "how many of this card sold" unanswerable."""
    ten = parse_title("2021 Panini Prizm Ja'Marr Chase #220 PSA 10")
    nine = parse_title("2021 Panini Prizm Ja'Marr Chase #220 PSA 9")

    assert card_key(ten) == card_key(nine)
    assert grade_label(ten) != grade_label(nine)


def test_a_parallel_is_a_different_card():
    base = parse_title("2021 Panini Prizm Ja'Marr Chase #220 PSA 10")
    silver = parse_title("2021 Panini Prizm Ja'Marr Chase #220 Silver Prizm PSA 10")

    assert card_key(base) != card_key(silver)


def test_different_years_of_the_same_number_do_not_merge():
    a = parse_title("2020 Panini Prizm Justin Herbert #325")
    b = parse_title("2021 Panini Prizm Justin Herbert #325")
    assert card_key(a) != card_key(b)


def test_different_sets_do_not_merge():
    a = parse_title("2021 Panini Prizm Ja'Marr Chase #220")
    b = parse_title("2021 Panini Select Ja'Marr Chase #220")
    assert card_key(a) != card_key(b)


def test_player_spellings_fold_together():
    # A comparison token, not a display name: the variants differ in whether
    # there is a separator at all, so every separator has to go.
    for variant in ("Ja'Marr Chase", "JaMarr Chase", "Ja Marr Chase", "JAMARR CHASE"):
        assert normalize_player(variant) == "jamarrchase"


def test_generational_suffixes_fold_too():
    assert normalize_player("Odell Beckham Jr") == normalize_player("Odell Beckham")
    assert normalize_player("Marvin Harrison Jr.") == "marvinharrison"


def test_accents_fold():
    assert normalize_player("José Ramírez") == "joseramirez"


def test_an_empty_name_is_handled():
    assert normalize_player(None) == ""
    assert normalize_player("") == ""


def test_no_key_without_a_year():
    assert card_key(CardAttrs(set_name="Prizm", card_number="220",
                              confidence=0.9)) is None


def test_no_key_without_a_set():
    assert card_key(CardAttrs(year=2021, card_number="220",
                              confidence=0.9)) is None


def test_no_key_from_year_and_set_alone():
    """Those describe thousands of cards, not one."""
    assert card_key(CardAttrs(year=2021, set_name="Prizm", confidence=0.9)) is None


def test_no_key_from_a_low_confidence_parse():
    """A key built from a guess is a wrong grouping presented as a fact."""
    attrs = CardAttrs(year=2021, set_name="Prizm", card_number="220",
                      confidence=MIN_CONFIDENCE - 0.01)
    assert card_key(attrs) is None
    attrs.confidence = MIN_CONFIDENCE
    assert card_key(attrs) is not None


def test_the_player_identifies_a_card_that_has_no_number():
    """Plenty of titles omit it; falling back keeps those groupable."""
    a = card_key(CardAttrs(year=2021, set_name="Prizm", player="Ja'Marr Chase",
                           confidence=0.8))
    b = card_key(CardAttrs(year=2021, set_name="Prizm", player="JaMarr Chase",
                           confidence=0.8))
    assert a is not None and a == b


def test_the_number_is_preferred_over_the_player():
    """Including the name when a number exists would split one card in two when
    the name parses differently between listings."""
    with_name = CardAttrs(year=2021, set_name="Prizm", card_number="220",
                          player="Ja'Marr Chase", confidence=0.9)
    misparsed = CardAttrs(year=2021, set_name="Prizm", card_number="220",
                          player="Chase Bengals", confidence=0.9)
    assert card_key(with_name) == card_key(misparsed)


def test_the_key_is_url_safe():
    key = card_key(CardAttrs(year=2021, set_name="Topps Chrome",
                             card_number="RA-JC", parallel="Gold /50",
                             confidence=0.9))
    assert key == key.lower()
    assert all(c.isalnum() or c == "-" for c in key)


def test_the_name_reads_the_same_however_the_seller_wrote_it():
    attrs = parse_title("2021 Panini Prizm Ja'Marr Chase RC #220 PSA 10")
    assert card_name(attrs) == "2021 Prizm Ja'Marr Chase #220"


def test_no_name_from_almost_nothing():
    assert card_name(CardAttrs(year=2021)) is None


def test_grade_label_covers_raw_and_partial_grades():
    assert grade_label(CardAttrs(grader="PSA", grade=10.0)) == "PSA 10"
    assert grade_label(CardAttrs(grader="BGS", grade=9.5)) == "BGS 9.5"
    assert grade_label(CardAttrs()) == "Raw"
    assert grade_label(CardAttrs(grader="SGC")) == "SGC"


def test_history_groups_a_card_and_splits_it_by_grade(tmp_path):
    """The end-to-end claim: differently-titled sales become one trend line
    per grade, which is the thing that did not exist before."""
    from nflcarddb import db as store
    from nflcarddb.models import Sale
    from nflcarddb.pipeline import card_history, top_cards

    path = tmp_path / "hist.db"
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    rows = [
        ("2021 Panini Prizm Ja'Marr Chase RC #220 PSA 10", 9000, "2026-08-01"),
        ("Ja'Marr Chase 2021 Prizm Rookie #220 PSA 10 GEM", 9500, "2026-08-02"),
        ("2021 PRIZM #220 JAMARR CHASE ROOKIE PSA 10", 11000, "2026-08-03"),
        ("2021 Panini Prizm Ja'Marr Chase #220 PSA 9", 4000, "2026-08-02"),
        ("2020 Panini Prizm Justin Herbert #325 PSA 10", 30000, "2026-08-02"),
    ]
    sales = [Sale(item_id=f"10000000000{i}", title=t, price_cents=p, sold_date=d)
             for i, (t, p, d) in enumerate(rows)]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in sales], "v1")
    key = conn.execute(
        "SELECT card_key FROM cards WHERE item_id = '100000000000'").fetchone()[0]
    conn.close()

    history = card_history(str(path), key)

    assert history["sales"] == 4                      # three PSA 10s and a PSA 9
    assert set(history["by_grade"]) == {"PSA 10", "PSA 9"}
    assert history["by_grade"]["PSA 10"]["n"] == 3
    assert history["by_grade"]["PSA 10"]["median"] == 95.0
    # Ordered oldest first, which is what a trend line needs.
    dates = [p["date"] for p in history["by_grade"]["PSA 10"]["points"]]
    assert dates == sorted(dates)
    # The Herbert card is a different card and must not be in here.
    assert history["by_grade"]["PSA 10"]["high"] == 110.0

    trading = top_cards(str(path), days=None)
    assert trading[0]["card_key"] == key
    assert trading[0]["sales"] == 4
    assert "Chase" in trading[0]["card_name"]


def test_a_named_grade_narrows_the_history(tmp_path):
    from nflcarddb import db as store
    from nflcarddb.models import Sale
    from nflcarddb.pipeline import card_history

    path = tmp_path / "grade.db"
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    sales = [
        Sale(item_id="100000000001",
             title="2021 Panini Prizm Ja'Marr Chase #220 PSA 10",
             price_cents=9000, sold_date="2026-08-01"),
        Sale(item_id="100000000002",
             title="2021 Panini Prizm Ja'Marr Chase #220 PSA 9",
             price_cents=4000, sold_date="2026-08-02"),
    ]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in sales], "v1")
    key = conn.execute("SELECT card_key FROM cards LIMIT 1").fetchone()[0]
    conn.close()

    only_tens = card_history(str(path), key, grade="PSA 10")
    assert set(only_tens["by_grade"]) == {"PSA 10"}
    assert only_tens["sales"] == 1
