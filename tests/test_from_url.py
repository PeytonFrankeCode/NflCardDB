"""Turning a browser URL into a config query.

Widening coverage means choosing which eBay searches to collect, and the only
reliable way to choose is to run them in a browser and look. This is the bridge
from "that search looks right" to a config entry, so nobody has to know an
eBay category id or guess whether one has been retired.
"""

import pytest

from nflcarddb.from_url import NotAnEbaySearch, parse_search_url, to_yaml

SOLD = "https://www.ebay.com/sch/i.html?_nkw=football&_sacat=261328&LH_Sold=1"


def test_keywords_and_category_are_read():
    spec = parse_search_url(SOLD)
    assert spec["keywords"] == "football"
    assert spec["category"] == "261328"


def test_ebay_filters_are_carried_over_untouched():
    """The point of `extra`: a filter this project has never heard of still
    works, because it is copied rather than interpreted."""
    spec = parse_search_url(SOLD + "&Sport=Football&Graded=Yes&Season=2024")
    assert spec["extra"] == {"Sport": "Football", "Graded": "Yes",
                             "Season": "2024"}


def test_things_the_walker_sets_per_request_are_dropped():
    """Carrying a page number or sort order over from a browser would either be
    ignored or fight the walk."""
    spec = parse_search_url(
        SOLD + "&_sop=13&_pgn=7&_ipg=240&_udlo=10&_udhi=25&LH_Complete=1")
    assert spec["extra"] == {}


def test_tracking_noise_is_dropped():
    spec = parse_search_url(SOLD + "&rt=nc&_from=R40&_trksid=p123&_odkw=old")
    assert spec["extra"] == {}


def test_a_category_of_zero_means_all_categories():
    spec = parse_search_url("https://www.ebay.com/sch/i.html?_nkw=prizm&_sacat=0")
    assert spec["category"] is None


def test_an_id_is_suggested_from_what_the_search_is():
    assert parse_search_url(SOLD)["id"] == "football"
    assert parse_search_url(SOLD + "&Graded=Yes")["id"] == "football_yes"
    assert parse_search_url(
        "https://www.ebay.com/sch/i.html?_sacat=261328&Sport=Football"
    )["id"] == "football"


def test_an_explicit_id_wins():
    assert parse_search_url(SOLD, "my_query")["id"] == "my_query"


def test_a_non_ebay_url_is_refused():
    with pytest.raises(NotAnEbaySearch, match="not an eBay URL"):
        parse_search_url("https://www.google.com/search?q=football+cards")


def test_an_item_page_is_refused_with_the_reason():
    """An item page has no filters on it, so there is nothing to convert."""
    with pytest.raises(NotAnEbaySearch, match="not a search results page"):
        parse_search_url("https://www.ebay.com/itm/123456789012")


def test_a_search_with_no_filters_at_all_is_refused():
    """It would collect the whole of eBay, which is not what anyone means."""
    with pytest.raises(NotAnEbaySearch, match="whole of eBay"):
        parse_search_url("https://www.ebay.com/sch/i.html?_sop=13&_pgn=1")


def test_the_yaml_pastes_straight_into_the_config():
    import yaml

    block = to_yaml(parse_search_url(SOLD + "&Sport=Football"))
    parsed = yaml.safe_load("queries:\n" + block)["queries"][0]

    assert parsed["category"] == "261328"        # quoted, so not an int
    assert parsed["extra"]["Sport"] == "Football"
    assert parsed["keywords"] == "football"


def test_the_yaml_omits_what_is_absent():
    block = to_yaml(parse_search_url(
        "https://www.ebay.com/sch/i.html?_sacat=261328"))
    assert "keywords" not in block
    assert "extra" not in block


def test_a_generated_query_survives_config_loading(tmp_path):
    """The end-to-end claim: paste it in and the collector uses it."""
    from nflcarddb.config import load_config

    block = to_yaml(parse_search_url(SOLD + "&Sport=Football", "wide"))
    path = tmp_path / "q.yml"
    path.write_text("database: x.sqlite\nqueries:\n" + block + "\n")

    query = load_config(path).queries[0]
    assert query.id == "wide"
    assert query.category == "261328"
    assert query.extra == {"Sport": "Football"}


def test_the_generated_query_reaches_the_url_it_came_from():
    """A round trip: the filters that were chosen in the browser are the
    filters that get requested."""
    from nflcarddb.search import build_url

    spec = parse_search_url(SOLD + "&Sport=Football&Graded=Yes")
    url = build_url(spec["keywords"], spec["category"], page=1,
                    extra=spec["extra"])

    assert "Sport=Football" in url
    assert "Graded=Yes" in url
    assert "_sacat=261328" in url
    assert "LH_Sold=1" in url      # applied by the builder, always
