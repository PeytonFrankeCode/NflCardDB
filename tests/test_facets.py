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
