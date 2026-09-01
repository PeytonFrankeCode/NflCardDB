"""Reading thecardhuddle.com's checklist JSON.

The mapping in checklist_fetch was written without ever seeing the data -- the
site is blocked from the environment this was built in. So the thing under test
is not "does it parse the real shape", which cannot be known here, but "does it
survive the shapes it plausibly has, and does it say so when it does not".

An import that silently produces nothing is the outcome to design against: it
looks like a site with no data rather than a mapping that missed.
"""

import json

from nflcarddb import checklist_fetch as fetch


# Three layouts a hand-built checklist site might use. All three describe the
# same two cards, so any of them must produce the same rows.
FLAT_LIST = [
    {"number": "301", "player": "Fernando Mendoza", "insert": None},
    {"number": "TD-16", "player": "Josh Allen", "insert": "Touchdown"},
]

WRAPPED = {
    "year": 2026, "set": "Topps",
    "cards": [
        {"cardNumber": "301", "playerName": "Fernando Mendoza"},
        {"cardNumber": "TD-16", "playerName": "Josh Allen",
         "insertName": "Touchdown"},
    ],
}

ODDLY_NAMED = {
    "meta": {"whatever": 1},
    "checklist": [
        {"no": "301", "athlete": "Fernando Mendoza"},
        {"no": "TD-16", "athlete": "Josh Allen", "subset_name": "Touchdown"},
    ],
}


def _numbers(rows):
    return sorted(r["card_number"] for r in rows)


def test_a_bare_list_of_cards_is_read():
    rows = list(fetch.rows_from_product(FLAT_LIST, {"year": 2026, "set_name": "Topps"}))
    assert _numbers(rows) == ["301", "TD-16"]
    assert rows[0]["year"] == 2026 and rows[0]["set_name"] == "Topps"


def test_a_wrapped_payload_is_read_and_its_own_header_wins():
    """A product file that states its own year and set is closer to the data
    than the index entry, so it overrides it."""
    rows = list(fetch.rows_from_product(WRAPPED, {"year": 1999, "set_name": "Wrong"}))
    assert _numbers(rows) == ["301", "TD-16"]
    assert {r["year"] for r in rows} == {2026}
    assert {r["set_name"] for r in rows} == {"Topps"}


def test_unexpected_field_names_still_map():
    rows = list(fetch.rows_from_product(ODDLY_NAMED, {"year": 2026, "set_name": "Topps"}))
    assert _numbers(rows) == ["301", "TD-16"]
    assert [r["subset"] for r in rows] == [None, "Touchdown"]


def test_the_card_list_is_found_even_when_nothing_is_named_as_expected():
    """Fall back to the longest list of objects, which is always the checklist."""
    payload = {"nav": [{"a": 1}], "blob": [{"number": str(i), "player": "X"}
                                           for i in range(20)]}
    assert len(fetch.find_cards(payload)) == 20


def test_a_year_inside_the_product_name_is_used_and_removed():
    """"2024 Panini Prizm" must become year 2024 and set "Panini Prizm", or the
    set will never match what a parsed title produces."""
    meta = fetch.product_meta({"id": "x", "name": "2024 Panini Prizm"})
    assert meta["year"] == 2024
    assert meta["set_name"] == "Panini Prizm"


def test_a_serial_in_the_number_field_is_split_not_swallowed():
    """"12/99" is a serial. Keeping it as the card number would invent a card
    number per copy -- the /1 bug, arriving from a different direction."""
    rows = list(fetch.rows_from_product(
        [{"number": "44/99", "player": "Bijan Robinson"}], {"year": 2023, "set_name": "Select"}))
    assert rows[0]["card_number"] == "44"
    assert rows[0]["print_run"] == 99


def test_print_run_and_flags_are_read_in_the_forms_they_come_in():
    rows = list(fetch.rows_from_product(
        [{"number": "1", "player": "A", "printRun": "/25", "auto": "yes",
          "memorabilia": True}], {"year": 2024, "set_name": "Prizm"}))
    assert rows[0]["print_run"] == 25
    assert rows[0]["is_auto"] is True
    assert rows[0]["is_relic"] is True


def test_index_entries_are_found_in_any_of_the_usual_wrappers():
    listed = [{"id": "a"}, {"id": "b"}]
    assert len(fetch.index_entries(listed)) == 2
    assert len(fetch.index_entries({"products": listed})) == 2
    mapped = fetch.index_entries({"a": {"name": "2024 Prizm"}, "b": {"name": "2023 Select"}})
    assert {e["id"] for e in mapped} == {"a", "b"}


def test_describe_reports_how_well_the_mapping_did():
    """The guard against a silent empty import.

    `filled` counts the fields that actually received a value, so a mapping
    that matched nothing reads as zeros rather than as a site with no cards.
    """
    report = fetch.describe(WRAPPED)
    assert report["cards_found"] == 2
    assert "cardNumber" in report["card_keys"]
    assert report["filled"]["card_number"] == 2
    assert report["filled"]["player"] == 2

    nothing = fetch.describe({"cards": [{"totally": "unexpected"}]})
    assert nothing["cards_found"] == 1
    assert nothing["filled"]["card_number"] == 0


def test_the_rows_are_what_the_importer_takes(tmp_path):
    """End to end: fetched shape -> checklist table, no adapter in between."""
    from nflcarddb import checklist as cl
    from nflcarddb import db as store

    conn = store.connect(tmp_path / "c.db")
    rows = fetch.rows_from_product(WRAPPED, {})
    stats = cl.import_rows(conn, rows, source="thecardhuddle.com")
    assert stats["loaded"] == 2
    assert cl.covers(conn, 2026, "Topps") is True
    assert "Touchdown" in cl.vocabulary(conn)["inserts"]
    conn.close()


def test_one_unreadable_product_does_not_end_the_run(monkeypatch):
    """360 good products are worth having when one 404s."""
    calls = []

    def fake(url, timeout=30.0):
        calls.append(url)
        if url.endswith("index.json"):
            return [{"id": "good", "name": "2026 Topps"},
                    {"id": "bad", "name": "2025 Prizm"}]
        if url.endswith("bad.json"):
            raise OSError("404")
        return FLAT_LIST

    monkeypatch.setattr(fetch, "fetch_json", fake)
    rows = list(fetch.fetch_all(delay=0))
    assert len(rows) == 2                      # the good product still arrived
    assert any("bad.json" in c for c in calls)  # and the bad one was attempted


# ------------------------------------------------------- the CSV export
#
# thecardhuddle.com also exports the checklist as CSV, and its columns do not
# line up with this parser's fields by name. The two disagreements below are
# the whole mapping, and both would be silent if they were wrong.


def _csv(tmp_path, rows, header=None):
    import csv as _csv_mod
    header = header or ("product_id,product,year,brand,sport,set_id,set,category,"
                        "card_number,player,team,rookie,parallel,print_run")
    path = tmp_path / "x.csv"
    path.write_text(header + "\n" + "\n".join(rows) + "\n")
    return path


def test_brand_is_the_set_and_set_is_the_subset(tmp_path):
    """Their `brand` is what a title calls the product; their `set` is the
    insert. Reading them the other way round would key every card by a section
    heading no seller types."""
    from nflcarddb import checklist_csv as ccsv
    path = _csv(tmp_path, [
        "p,2026 Topps,2026,Topps,Football,s,Kaiju,insert,KAI-2,Patrick Mahomes,,,Base,",
    ])
    row = next(ccsv.rows_from_csv(path))
    assert row["set_name"] == "Topps"
    assert row["subset"] == "Kaiju"


def test_base_set_sections_never_become_inserts(tmp_path):
    """"Rookies" and "Veterans" sit in the same column an insert name does,
    but they are sections of the base set. Keying them would split every base
    card between sellers who mentioned the section -- which is none of them."""
    from nflcarddb import checklist_csv as ccsv
    path = _csv(tmp_path, [
        "p,2024 Prizm,2024,Prizm,Football,s,Base Set,base,1,A Player,,,Base,",
        "p,2024 Prizm,2024,Prizm,Football,s,Rookies,base,2,B Player,,,Base,",
        "p,2024 Prizm,2024,Prizm,Football,s,Veterans,base,3,C Player,,,Base,",
    ])
    assert [r["subset"] for r in ccsv.rows_from_csv(path)] == [None, None, None]


def test_the_word_base_in_the_parallel_column_is_not_a_parallel(tmp_path):
    from nflcarddb import checklist_csv as ccsv
    path = _csv(tmp_path, [
        "p,2024 Prizm,2024,Prizm,Football,s,Base Set,base,1,A Player,,,Base,",
        "p,2024 Prizm,2024,Prizm,Football,s,Base Set,base,1,A Player,,,Pink,25",
    ])
    rows = list(ccsv.rows_from_csv(path))
    assert rows[0]["parallel"] is None
    assert rows[1]["parallel"] == "Pink" and rows[1]["print_run"] == 25


def test_category_carries_the_auto_and_relic_flags(tmp_path):
    from nflcarddb import checklist_csv as ccsv
    path = _csv(tmp_path, [
        "p,2024 Prizm,2024,Prizm,Football,s,Signatures,autograph,1,A,,,Base,",
        "p,2024 Prizm,2024,Prizm,Football,s,Patches,memorabilia,2,B,,,Base,",
    ])
    rows = list(ccsv.rows_from_csv(path))
    assert (rows[0]["is_auto"], rows[0]["is_relic"]) == (True, False)
    assert (rows[1]["is_auto"], rows[1]["is_relic"]) == (False, True)


def test_other_sports_are_left_out(tmp_path):
    from nflcarddb import checklist_csv as ccsv
    path = _csv(tmp_path, [
        "p,2024 Prizm,2024,Prizm,Basketball,s,Base Set,base,1,A Player,,,Base,",
        "p,2024 Prizm,2024,Prizm,Football,s,Base Set,base,2,B Player,,,Base,",
    ])
    assert [r["card_number"] for r in ccsv.rows_from_csv(path)] == ["2"]


def test_the_export_is_found_without_being_named(tmp_path):
    """Saving the file somewhere sensible has to be enough.

    "Drag this onto that icon" cannot be scheduled and has to be remembered
    every time, which is exactly the kind of step that quietly stops happening.
    """
    from nflcarddb import checklist_csv as ccsv

    (tmp_path / "data" / "checklists").mkdir(parents=True)
    target = tmp_path / "data" / "checklists" / "checklists-variants.csv"
    target.write_text("year,brand\n")
    assert ccsv.find_export(tmp_path) == target


def test_the_variants_export_wins_over_the_flattened_one(tmp_path):
    """The cards export flattens parallels into a sentence and loses the print
    runs, so picking it when both are present would silently cost the columns
    the whole thing is for."""
    from nflcarddb import checklist_csv as ccsv

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "checklistscards.csv").write_text("year,brand\n")
    variants = tmp_path / "data" / "checklistsvariants.csv"
    variants.write_text("year,brand\n")
    assert ccsv.find_export(tmp_path) == variants


def test_nothing_to_find_is_not_an_error(tmp_path):
    from nflcarddb import checklist_csv as ccsv
    assert ccsv.find_export(tmp_path) is None
