"""Reading eBay's own taxonomy instead of maintaining our own.

Every vocabulary in the title parser is a list somebody keeps current, and it
is wrong the day a product ships -- about forty insert names were added by hand
across three rounds, and a dozen more arrive with every release. eBay already
classifies these listings and publishes the result as search facets.

The tests pin the one decision that makes this durable: facets are found by
their *href*, not by markup. eBay reskins its sidebar; it does not change what
its own filter URLs mean.
"""

from pathlib import Path

from nflcarddb.facets import aspect_links, as_vocabulary, harvest, merge

FIXTURE = Path(__file__).parent / "fixtures" / "sold_facets.html"
HTML = FIXTURE.read_text(encoding="utf-8")


def test_the_aspects_come_out_of_the_link_urls():
    """No class names involved, so a sidebar redesign does not break this."""
    found = {(a, v) for a, v, _ in aspect_links(HTML)}
    assert ("Player/Athlete", "Jayden Daniels") in found
    assert ("Parallel/Variety", "Genies") in found
    assert ("Set", "Panini Phoenix") in found


def test_search_plumbing_is_not_mistaken_for_a_card_attribute():
    """Page numbers and price bands are links with query strings too."""
    names = {a for a, _, _ in aspect_links(HTML)}
    assert "_pgn" not in names
    assert "_sop" not in names
    assert "_udlo" not in names
    assert "_sacat" not in names


def test_url_encoding_is_undone():
    values = {v for _, v, _ in aspect_links(HTML)}
    assert "Ja'Marr Chase" in values          # %27
    assert "Panini Prizm Draft Picks" in values


def test_a_multi_select_facet_becomes_separate_values():
    """eBay joins them with a pipe: Downtown|Oversized is two names."""
    values = {v for _, v, _ in aspect_links(HTML)}
    assert "Downtown" in values
    assert "Oversized" in values
    assert "Downtown|Oversized" not in values


def test_navigation_words_are_not_harvested():
    values = {v.lower() for _, v, _ in aspect_links(HTML)}
    assert "see all" not in values


def test_counts_are_read_from_the_link_text():
    counts = {v: c for _, v, c in aspect_links(HTML)}
    assert counts["Jayden Daniels"] == 1204
    assert counts["Silver Prizm"] == 5001


def test_harvest_keeps_ebays_own_aspect_names():
    """Keyed by the raw name, because that name is also the query parameter
    needed to search within the facet -- which is how drilling works."""
    grouped = harvest(HTML)
    assert "Jayden Daniels" in grouped["Player/Athlete"]
    assert "Genies" in grouped["Parallel/Variety"]
    assert "Panini Phoenix" in grouped["Set"]


def test_aspects_are_grouped_into_the_vocabularies_they_feed():
    vocab = as_vocabulary(harvest(HTML))
    assert "Jayden Daniels" in vocab["players"]
    assert "Genies" in vocab["parallels"]
    assert "Panini Phoenix" in vocab["sets"]
    assert "2025" in vocab["seasons"]


def test_the_insert_names_added_by_hand_are_in_ebays_own_list():
    """The point of the whole exercise: Genies and Sunday Kings were typed in
    by hand over two rounds. eBay had them all along, under Parallel/Variety."""
    parallels = as_vocabulary(harvest(HTML))["parallels"]
    assert "Genies" in parallels
    assert "Sunday Kings" in parallels


def test_harvests_accumulate_across_pages():
    """One search renders only the facets eBay chose for it, so the vocabulary
    is built from many searches rather than one."""
    store: dict = {}
    merge(store, {"Parallel/Variety": {"Genies": 10}})
    merge(store, {"Parallel/Variety": {"Kaboom": 4}, "Set": {"Prizm": 99}})

    assert set(store["Parallel/Variety"]) == {"Genies", "Kaboom"}
    assert set(store["Set"]) == {"Prizm"}


def test_a_bigger_count_wins_when_a_value_is_seen_again():
    store: dict = {}
    merge(store, {"Parallel/Variety": {"Genies": 10}})
    merge(store, {"Parallel/Variety": {"Genies": 300}})
    assert store["Parallel/Variety"]["Genies"] == 300


def test_the_vocabulary_comes_back_biggest_first():
    vocab = as_vocabulary(harvest(HTML))
    assert vocab["parallels"][0] == "Silver Prizm"     # 5,001 beats the rest


def test_rare_values_can_be_filtered_out():
    vocab = as_vocabulary(harvest(HTML), min_count=500)
    assert "Silver Prizm" in vocab["parallels"]
    assert "Downtown" not in vocab["parallels"]        # only 44


def test_a_page_with_no_facets_harvests_nothing():
    assert harvest("<html><body><p>nothing here</p></body></html>") == {}


def test_an_unrecognised_aspect_is_kept_rather_than_dropped():
    """A new aspect should be visible in the output, not silently lost."""
    html = ('<a href="/sch/i.html?_nkw=x&Autograph%20Format=Sticker">'
            'Sticker (12)</a>')
    grouped = harvest(html)
    assert any("Autograph Format" in bucket for bucket in grouped)


def test_tracking_payloads_are_not_a_vocabulary():
    """Confirmed against a live page: 261 encrypted `itmprp` blobs and 227
    `itemId` values arrived looking exactly like harvested facet values."""
    html = (
        '<a href="/sch/i.html?_nkw=x&itmprp=enc:AQALAAAA0GfYFPkwiKCW4ZNSs2u11xA">a</a>'
        '<a href="/sch/i.html?_nkw=x&itemId=117359217764">b</a>'
        '<a href="/sch/i.html?_nkw=x&itmmeta=012DEW30YG0MEEKND7NH">c</a>'
        '<a href="/sch/i.html?_nkw=x&promoted_items=127675991379,117029055330">d</a>'
        '<a href="/sch/i.html?_nkw=x&ssPageName=STRK:ME:LNLK:MESX">e</a>'
    )
    assert harvest(html) == {}


def test_ebays_base_marker_is_not_a_parallel():
    """"[Base]" means "not a parallel". Harvesting it would put it in a key."""
    html = '<a href="/sch/i.html?_nkw=x&Parallel%2FVariety=%5BBase%5D">[Base] (9)</a>'
    assert harvest(html) == {}


def test_aspects_seen_on_the_live_page_are_named_not_dumped_in_other():
    html = (
        '<a href="/sch/i.html?_nkw=x&Sport=Football">Football (1)</a>'
        '<a href="/sch/i.html?_nkw=x&Year%20Manufactured=2025">2025 (1)</a>'
    )
    vocab = as_vocabulary(harvest(html))
    assert vocab["sports"] == ["Football"]
    assert vocab["seasons"] == ["2025"]


def test_drilling_targets_carry_the_parameter_ebay_expects():
    """Narrowing the search needs eBay's aspect name, not our bucket name."""
    from nflcarddb.facets import drillable

    store = harvest(HTML)
    targets = drillable(store, "seasons")
    assert ("Season", "2025") in targets

    players = drillable(store, "players")
    assert ("Player/Athlete", "Jayden Daniels") in players


def test_drilling_respects_a_limit():
    from nflcarddb.facets import drillable

    assert len(drillable(harvest(HTML), "players", limit=1)) == 1


def test_an_older_stored_file_is_discarded_rather_than_misread(tmp_path):
    """Version 1 was keyed by bucket. Reading it with version-2 code bucketed
    the bucket names, and a live run produced "other:parallels" and
    "other:other:mode". Re-harvesting costs minutes; corrupt vocabulary costs
    confidence in everything downstream."""
    import json

    from nflcarddb.facets import load_store

    old = tmp_path / "facets.json"
    old.write_text(json.dumps({"parallels": {"Genies": 10}}), encoding="utf-8")
    assert load_store(old) == {}


def test_a_current_file_round_trips(tmp_path):
    from nflcarddb.facets import load_store, save_store

    path = tmp_path / "facets.json"
    save_store({"Parallel/Variety": {"Genies": 10}}, path)
    assert load_store(path) == {"Parallel/Variety": {"Genies": 10}}


def test_a_missing_or_unreadable_file_starts_empty(tmp_path):
    from nflcarddb.facets import load_store

    assert load_store(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")
    assert load_store(bad) == {}


def test_bucketing_is_never_applied_twice():
    """The shape of the bug: a bucket name must not survive a second pass."""
    from nflcarddb.facets import bucket_of

    assert bucket_of("Parallel/Variety") == "parallels"
    assert bucket_of("parallels").startswith("other:")   # not "parallels" again


def test_ebays_show_only_toggles_are_not_a_vocabulary():
    """LH_AS, LH_BO and friends are boolean switches. Each produced a bucket
    holding the single value "1" on a live page."""
    html = ('<a href="/sch/i.html?_nkw=x&LH_BO=1">Best offer</a>'
            '<a href="/sch/i.html?_nkw=x&LH_FAST=1">Fast N Free</a>'
            '<a href="/sch/i.html?_nkw=x&imm=1">x</a>')
    assert harvest(html) == {}


def test_ebays_not_specified_marker_is_not_a_value():
    """"!" appeared under Autographed, Graded, Material and Card Condition."""
    html = '<a href="/sch/i.html?_nkw=x&Autographed=%21">! (5)</a>'
    assert harvest(html) == {}


def test_improving_a_filter_also_cleans_what_was_already_stored():
    """Filtering only at harvest time made junk permanent: LH_AS and eBay's
    "!" marker survived two rounds of being filtered, because they were already
    in the accumulated file and nothing re-examined it."""
    from nflcarddb.facets import clean

    dirty = {
        "LH_AS": {"1": None},
        "imm": {"1": None},
        "Autographed": {"!": 5, "Yes": 12},
        "Parallel/Variety": {"[Base]": 9, "Genies": 3},
    }
    assert clean(dirty) == {"Autographed": {"Yes": 12},
                            "Parallel/Variety": {"Genies": 3}}


def test_a_stored_file_is_cleaned_when_it_is_loaded(tmp_path):
    from nflcarddb.facets import FILE_VERSION, load_store
    import json

    path = tmp_path / "f.json"
    path.write_text(json.dumps({
        "version": FILE_VERSION,
        "aspects": {"LH_BO": {"1": None}, "Set": {"1979 Topps": 20}},
    }), encoding="utf-8")

    assert load_store(path) == {"Set": {"1979 Topps": 20}}


def test_the_apis_not_specified_placeholder_is_not_a_value():
    """The API's version of "!". It arrived under every single aspect on a
    live run -- including as a set name and as a player."""
    from nflcarddb.facets import clean

    dirty = {
        "Set": {"Not Specified": 90000, "2024 Panini Donruss": 2110},
        "Player/Athlete": {"Not Specified": 5, "Tom Brady": 900},
        "Parallel/Variety": {"[Base]": 40, "Not Specified": 3, "Prizm": 700},
    }
    assert clean(dirty) == {
        "Set": {"2024 Panini Donruss": 2110},
        "Player/Athlete": {"Tom Brady": 900},
        "Parallel/Variety": {"Prizm": 700},
    }


def test_saving_cleans_too(tmp_path):
    """Cleaning only on load let anything from a new source through: values
    harvested from the API were written unfiltered, so "[Base]" reached a
    report as a parallel after it had supposedly been filtered."""
    from nflcarddb.facets import load_store, save_store

    path = tmp_path / "f.json"
    save_store({"Parallel/Variety": {"[Base]": 9, "Prizm": 3},
                "LH_BO": {"1": None}}, path)
    assert load_store(path) == {"Parallel/Variety": {"Prizm": 3}}


def test_a_vocabulary_is_cleaned_however_the_values_arrived():
    from nflcarddb.facets import as_vocabulary

    vocab = as_vocabulary({"Set": {"Not Specified": 9, "1979 Topps": 3}})
    assert vocab["sets"] == ["1979 Topps"]
