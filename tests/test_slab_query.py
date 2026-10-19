"""The graded pass asks eBay for slabs by its own Graded item specific."""
from urllib.parse import parse_qs, urlparse

from nflcarddb.config import load_config
from nflcarddb.search import build_url


def _slabs():
    config = load_config("config/queries.yml")
    found = [q for q in config.queries if q.id == "football_slabs"]
    assert found, "football_slabs is the query the Market's slab sales come from"
    return found[0]


def test_slabs_are_asked_for_by_eBays_graded_filter_not_a_grader_name():
    q = _slabs()
    assert q.extra.get("Graded") == "Yes"
    # Without _dcat eBay ignores an aspect filter and returns every listing.
    assert q.extra.get("_dcat") == q.category == "261328"
    # Not "psa": that keyword missed every BGS, SGC and CGC slab.
    assert "psa" not in q.keywords.lower()


def test_the_filter_reaches_the_url_as_eBay_spells_it():
    q = _slabs()
    params = parse_qs(urlparse(build_url(q.keywords, q.category, extra=q.extra)).query)
    assert params["Graded"] == ["Yes"]
    assert params["_dcat"] == ["261328"]


def test_an_unquoted_yaml_yes_is_still_sent_as_Yes(tmp_path):
    cfg = tmp_path / "q.yml"
    cfg.write_text("queries:\n  - id: s\n    keywords: football\n    category: '261328'\n"
                   "    extra:\n      Graded: Yes\n      Autographed: no\n")
    q = load_config(str(cfg)).queries[0]
    assert q.extra == {"Graded": "Yes", "Autographed": "No"}
