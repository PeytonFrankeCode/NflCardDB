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


def test_aspects_are_grouped_into_the_vocabularies_they_feed():
    grouped = harvest(HTML)
    assert "Jayden Daniels" in dict(grouped["players"])
    assert "Genies" in dict(grouped["parallels"])
    assert "Panini Phoenix" in dict(grouped["sets"])
    assert "2025" in dict(grouped["seasons"])


def test_the_insert_names_added_by_hand_are_in_ebays_own_list():
    """The point of the whole exercise: Genies and Sunday Kings were typed in
    by hand over two rounds. eBay had them all along, under Parallel/Variety."""
    parallels = dict(harvest(HTML)["parallels"])
    assert "Genies" in parallels
    assert "Sunday Kings" in parallels


def test_harvests_accumulate_across_pages():
    """One search renders only the facets eBay chose for it, so the vocabulary
    is built from many searches rather than one."""
    store: dict = {}
    merge(store, {"parallels": {"Genies": 10}})
    merge(store, {"parallels": {"Kaboom": 4}, "sets": {"Prizm": 99}})

    assert set(store["parallels"]) == {"Genies", "Kaboom"}
    assert set(store["sets"]) == {"Prizm"}


def test_a_bigger_count_wins_when_a_value_is_seen_again():
    store: dict = {}
    merge(store, {"parallels": {"Genies": 10}})
    merge(store, {"parallels": {"Genies": 300}})
    assert store["parallels"]["Genies"] == 300


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
