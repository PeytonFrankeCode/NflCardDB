"""Measuring parse quality, and being clear about which measurement is which.

Two numbers get called accuracy. `audit` reports errors the data betrays about
itself -- a floor, never the whole picture. `review` produces a real percentage
from a sample someone checked. Conflating them would let a reassuring floor be
quoted as accuracy, so the tests keep the distinction explicit.
"""

import csv

import pytest

from nflcarddb import db as store
from nflcarddb.audit import audit, contradictory_groups, coverage, wide_spread_groups
from nflcarddb.models import Sale
from nflcarddb.parse_title import parse_title
from nflcarddb.review import draw_sample, score, write_sample


def _seed(path, titles_prices):
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    sales = [
        Sale(item_id=f"{900000000000 + i}", title=t, price_cents=p,
             sold_date="2026-08-03")
        for i, (t, p) in enumerate(titles_prices)
    ]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in sales], "v1")
    conn.close()
    return path


def test_coverage_counts_what_got_identified(tmp_path):
    path = _seed(tmp_path / "cov.db", [
        ("2021 Panini Prizm Ja'Marr Chase #220 PSA 10", 9000),
        ("2021 Panini Prizm Ja'Marr Chase #220 PSA 9", 4000),
        ("nice card lot look", 500),          # nothing identifiable in this
    ])
    stats = coverage(str(path))

    assert stats["cards"] == 3
    assert stats["with_key"] == 2
    assert stats["without_key"] == 1
    assert stats["groups"] == 1               # both Chase sales are one card
    assert 0 < stats["key_rate"] < 1


def test_an_unclear_title_gets_no_key_rather_than_a_guess(tmp_path):
    """The answer to "what happens when the description is not clear": nothing
    is invented. A wrong group is worse than no group."""
    path = _seed(tmp_path / "vague.db", [
        ("HUGE FOOTBALL CARD LOT MUST SEE!!!", 2500),
        ("mystery pack repack hit chase", 1500),
    ])
    stats = coverage(str(path))
    assert stats["with_key"] == 0
    assert stats["without_key"] == 2


def test_a_group_naming_two_players_is_reported(tmp_path):
    """Wrong without checking any catalogue: the group contradicts itself."""
    path = tmp_path / "clash.db"
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    sales = [Sale(item_id=f"90000000000{i}", title=f"card {i}", price_cents=1000,
                  sold_date="2026-08-03") for i in range(4)]
    store.upsert_sales(conn, sales, run)

    from nflcarddb.models import CardAttrs
    # Same key, two different players -- at least one of these is misfiled.
    store.upsert_cards(conn, [
        ("900000000000", CardAttrs(player="Ja'Marr Chase", year=2021,
                                   set_name="Prizm", card_number="220",
                                   confidence=0.9)),
        ("900000000001", CardAttrs(player="JaMarr Chase", year=2021,
                                   set_name="Prizm", card_number="220",
                                   confidence=0.9)),
        ("900000000002", CardAttrs(player="Justin Herbert", year=2021,
                                   set_name="Prizm", card_number="220",
                                   confidence=0.9)),
        ("900000000003", CardAttrs(player="Justin Herbert", year=2021,
                                   set_name="Prizm", card_number="220",
                                   confidence=0.9)),
    ], "v1")
    conn.close()

    flagged = contradictory_groups(str(path))
    assert len(flagged) == 1
    assert flagged[0]["sales"] == 4
    assert set(flagged[0]["players"]) == {"jamarrchase", "justinherbert"}


def test_one_odd_spelling_among_many_is_not_flagged(tmp_path):
    """A parser wobble on 1 of 20 is noise; flagging it would bury the real ones."""
    from nflcarddb.models import CardAttrs

    path = tmp_path / "wobble.db"
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    sales = [Sale(item_id=f"9000000000{i:02}", title="x", price_cents=1000,
                  sold_date="2026-08-03") for i in range(20)]
    store.upsert_sales(conn, sales, run)

    parsed = [(s.item_id, CardAttrs(player="Ja'Marr Chase", year=2021,
                                    set_name="Prizm", card_number="220",
                                    confidence=0.9)) for s in sales]
    parsed[0] = (sales[0].item_id, CardAttrs(player="Chase Bengals", year=2021,
                                             set_name="Prizm", card_number="220",
                                             confidence=0.9))
    store.upsert_cards(conn, parsed, "v1")
    conn.close()

    assert contradictory_groups(str(path)) == []


def test_a_wild_price_spread_within_one_grade_is_reported(tmp_path):
    """Same card, same condition, 200x the money: probably two cards sharing a key."""
    titles = [("2021 Panini Prizm Ja'Marr Chase #220 PSA 10", p)
              for p in (9000, 9500, 8800, 9100, 9300, 2_000_000)]
    path = _seed(tmp_path / "spread.db", titles)

    flagged = wide_spread_groups(str(path))
    assert len(flagged) == 1
    assert flagged[0]["ratio"] > 20
    assert flagged[0]["high"] == 20000.0


def test_a_small_group_is_not_judged_on_spread(tmp_path):
    """Two sales can differ wildly for ordinary reasons."""
    path = _seed(tmp_path / "small.db", [
        ("2021 Panini Prizm Ja'Marr Chase #220 PSA 10", 9000),
        ("2021 Panini Prizm Ja'Marr Chase #220 PSA 10", 900000),
    ])
    assert wide_spread_groups(str(path)) == []


def test_audit_reports_a_floor_not_an_accuracy(tmp_path):
    path = _seed(tmp_path / "audit.db", [
        ("2021 Panini Prizm Ja'Marr Chase #220 PSA 10", 9000),
        ("2021 Panini Prizm Ja'Marr Chase #220 PSA 9", 4000),
    ])
    report = audit(str(path))

    assert "known_bad_rate" in report
    assert "accuracy" not in report        # the word is reserved for review
    assert report["known_bad_rate"] == 0.0


def test_a_sample_is_random_and_repeatable(tmp_path):
    """Repeatable so a score can be re-checked; random because the first N rows
    are one day in price order, which would measure the wrong thing."""
    path = _seed(tmp_path / "sample.db", [
        (f"2021 Panini Prizm Player{i} #{i} PSA 10", 1000 + i) for i in range(60)
    ])
    a = draw_sample(str(path), size=10, seed=7)
    b = draw_sample(str(path), size=10, seed=7)
    c = draw_sample(str(path), size=10, seed=8)

    assert [r["item_id"] for r in a] == [r["item_id"] for r in b]
    assert [r["item_id"] for r in a] != [r["item_id"] for r in c]


def test_the_sample_file_carries_what_a_reviewer_needs(tmp_path):
    path = _seed(tmp_path / "cols.db", [
        ("2021 Panini Prizm Ja'Marr Chase #220 PSA 10", 9000),
    ])
    out = write_sample(draw_sample(str(path), size=1, seed=1),
                       tmp_path / "sample.csv")

    with out.open(newline="", encoding="utf-8-sig") as handle:
        row = next(csv.DictReader(handle))

    assert row["correct"] == ""                       # blank, for them to fill
    assert "Chase" in row["title"]                    # what the seller wrote
    assert "Chase" in row["card_name"]                # what we read it as
    assert row["listing"].startswith("https://www.ebay.com/itm/")


def _scored(tmp_path, marks):
    path = tmp_path / "done.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["item_id", "correct", "notes",
                                                    "title", "card_name",
                                                    "confidence"])
        writer.writeheader()
        for i, mark in enumerate(marks):
            writer.writerow({"item_id": str(i), "correct": mark, "notes": "",
                             "title": f"title {i}", "card_name": f"card {i}",
                             "confidence": "0.8"})
    return score(path)


def test_scoring_counts_only_what_was_judged(tmp_path):
    result = _scored(tmp_path, ["y"] * 8 + ["n"] * 2 + ["?", ""])

    assert result["reviewed"] == 10       # the ? and the blank are not counted
    assert result["correct"] == 8
    assert result["accuracy"] == 0.8
    assert result["unsure"] == 1
    assert result["not_reviewed"] == 1


def test_scoring_accepts_the_words_people_actually_type(tmp_path):
    result = _scored(tmp_path, ["Y", "yes", "1", "N", "no", "WRONG"])
    assert result["correct"] == 3
    assert result["wrong"] == 3


def test_a_small_sample_reports_a_wide_margin(tmp_path):
    """The number must not be quotable more precisely than it deserves."""
    small = _scored(tmp_path, ["y"] * 9 + ["n"])
    assert small["accuracy"] == 0.9
    assert small["margin_of_error"] > 0.15          # 10 rows says very little

    big = _scored(tmp_path, ["y"] * 360 + ["n"] * 40)
    assert big["accuracy"] == 0.9
    assert big["margin_of_error"] < 0.04            # 400 rows says quite a lot


def test_scoring_an_unmarked_file_explains_itself(tmp_path):
    with pytest.raises(ValueError, match="Nothing was marked"):
        _scored(tmp_path, ["", "", ""])


def test_scoring_the_wrong_file_explains_itself(tmp_path):
    path = tmp_path / "wrong.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no 'correct' column"):
        score(path)


def test_wrong_rows_come_back_so_they_can_be_fixed(tmp_path):
    result = _scored(tmp_path, ["y", "n", "y"])
    assert len(result["wrong_examples"]) == 1
    assert result["wrong_examples"][0]["title"] == "title 1"


def test_a_name_wearing_extra_words_is_not_a_grouping_error(tmp_path):
    """Peyton's real data flagged 454 groups this way. Every one was grouped
    correctly -- the parser had swept a subset name into the player field, so
    "Caleb Williams" and "Caleb Williams Future Stars" looked like two people."""
    from nflcarddb.audit import messy_named_groups
    from nflcarddb.models import CardAttrs

    path = tmp_path / "messy.db"
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    sales = [Sale(item_id=f"90000000000{i}", title="x", price_cents=1000,
                  sold_date="2026-08-03") for i in range(4)]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [
        (s.item_id, CardAttrs(player=name, year=2025, set_name="Topps Chrome",
                              card_number="NFS-1", confidence=0.9))
        for s, name in zip(sales, ["Caleb Williams", "Caleb Williams Future Stars",
                                   "Future Stars Caleb Williams", "Caleb Williams"])
    ], "v1")
    conn.close()

    # Not a contradiction: same player, messy field.
    assert contradictory_groups(str(path)) == []
    # Reported separately, because a page showing that name looks broken.
    messy = messy_named_groups(str(path))
    assert len(messy) == 1
    assert messy[0]["sales"] == 4


def test_two_genuinely_different_players_are_still_caught(tmp_path):
    """The containment rule must not swallow real errors."""
    from nflcarddb.models import CardAttrs

    path = tmp_path / "real.db"
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    sales = [Sale(item_id=f"90000000000{i}", title="x", price_cents=1000,
                  sold_date="2026-08-03") for i in range(4)]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [
        (s.item_id, CardAttrs(player=name, year=2021, set_name="Prizm",
                              card_number="220", confidence=0.9))
        for s, name in zip(sales, ["Ja'Marr Chase", "Ja'Marr Chase",
                                   "Justin Herbert", "Justin Herbert"])
    ], "v1")
    conn.close()

    assert len(contradictory_groups(str(path))) == 1


def test_subset_names_no_longer_reach_the_player_field():
    """The parse bug behind those 454 groups, fixed at the source."""
    for title, expected in [
        ("2025 Topps Chrome Caleb Williams Future Stars #NFS-1 Refractor",
         "Caleb Williams"),
        ("2025 Topps Chrome Future Stars Caleb Williams #NFS-1 Refractor",
         "Caleb Williams"),
        ("2025 Bowman University Chrome Fernando Mendoza #109",
         "Fernando Mendoza"),
        ("2024 Donruss Optic Caleb Williams Rated Rookie #201 Aqua",
         "Caleb Williams"),
        ("2025 Topps Chrome Jaxson Dart #306 Refractor Leather", "Jaxson Dart"),
        ("1989 Score Deion Sanders #246 NM MINT", "Deion Sanders"),
    ]:
        assert parse_title(title).player == expected, title


def test_rated_rookie_still_sets_the_rookie_flag():
    """Claiming the phrase consumes the word before the flag pass sees it."""
    a = parse_title("2024 Donruss Optic Caleb Williams Rated Rookie #201")
    assert a.player == "Caleb Williams"
    assert a.is_rookie is True


def test_a_missing_card_number_is_measured_as_a_split(tmp_path):
    """One physical card owns two keys -- numbered and un-numbered -- so sales
    scatter between them by how much the seller bothered to type."""
    from nflcarddb.audit import number_split_groups
    from nflcarddb.models import CardAttrs

    path = tmp_path / "split.db"
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    sales = [Sale(item_id=f"90000000000{i}", title="x", price_cents=1000,
                  sold_date="2026-08-03") for i in range(5)]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [
        (s.item_id, CardAttrs(player="Jayden Daniels", year=2024,
                              set_name="Prizm", card_number=num, confidence=0.9))
        for s, num in zip(sales, ["316", "316", "316", None, None])
    ], "v1")
    conn.close()

    report = number_split_groups(str(path))
    assert report["recoverable_sales"] == 2      # only one number ever seen
    assert report["ambiguous_sales"] == 0
    assert report["examples"][0]["number"] == "316"
    assert report["examples"][0]["joined"] == 3


def test_two_numbers_for_one_player_stays_ambiguous(tmp_path):
    """A base card and an insert of the same player in the same set. An
    un-numbered title does not say which, and guessing would invent a fact."""
    from nflcarddb.audit import number_split_groups
    from nflcarddb.models import CardAttrs

    path = tmp_path / "ambig.db"
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    sales = [Sale(item_id=f"90000000000{i}", title="x", price_cents=1000,
                  sold_date="2026-08-03") for i in range(4)]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [
        (s.item_id, CardAttrs(player="Jayden Daniels", year=2024,
                              set_name="Prizm", card_number=num, confidence=0.9))
        for s, num in zip(sales, ["316", "12", None, None])
    ], "v1")
    conn.close()

    report = number_split_groups(str(path))
    assert report["recoverable_sales"] == 0
    assert report["ambiguous_sales"] == 2


def test_a_parallel_keeps_its_own_split_accounting(tmp_path):
    """A Silver Prizm is a different card from the base, so its un-numbered
    sales must not be rejoined to the base card's number."""
    from nflcarddb.audit import number_split_groups
    from nflcarddb.models import CardAttrs

    path = tmp_path / "par.db"
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    sales = [Sale(item_id=f"90000000000{i}", title="x", price_cents=1000,
                  sold_date="2026-08-03") for i in range(3)]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [
        (sales[0].item_id, CardAttrs(player="Jayden Daniels", year=2024,
                                     set_name="Prizm", card_number="316",
                                     confidence=0.9)),
        (sales[1].item_id, CardAttrs(player="Jayden Daniels", year=2024,
                                     set_name="Prizm", parallel="Silver Prizm",
                                     card_number="316", confidence=0.9)),
        (sales[2].item_id, CardAttrs(player="Jayden Daniels", year=2024,
                                     set_name="Prizm", parallel="Silver Prizm",
                                     confidence=0.9)),
    ], "v1")
    conn.close()

    report = number_split_groups(str(path))
    assert report["recoverable_sales"] == 1
    assert report["examples"][0]["joined"] == 1     # the Silver, not the base
