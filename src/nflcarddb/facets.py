"""Read eBay's own card taxonomy out of a search page.

Every vocabulary in `parse_title` -- set names, parallels, insert names -- is a
list somebody has to keep. That list is wrong the day a new product ships, and
keeping it current by hand has been a losing race: roughly forty insert names
added by hand across three rounds, with a dozen more arriving in every release.

eBay already has the answer. Trading-card listings carry structured item
specifics, and the search sidebar exposes them as facets: Player/Athlete, Set,
Season, Parallel/Variety, Manufacturer, Grade. Those lists are eBay's own
classification of the same listings being scraped, they are complete for the
category, and they update themselves when a product ships.

**How they are found matters.** Not by class name -- eBay reskins constantly and
`parse_listing` already carries the scars. A facet link's *href* contains the
aspect name and value as query parameters:

    /sch/i.html?_nkw=football&_sacat=261328&Parallel%2FVariety=Silver+Prizm

So every link on the page is decoded, the known search parameters are
subtracted, and whatever remains is an aspect and its value. That works
regardless of how the sidebar is styled, which is the same reasoning behind
anchoring result parsing on `/itm/<id>`.

Caveat worth stating plainly: this is written against eBay's URL format rather
than a captured page, because nothing here can reach eBay -- data-centre traffic
gets the human check. `nflcarddb facets` saves the HTML it read, so one run
against a real page confirms or corrects it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable, Optional
from urllib.parse import parse_qsl, unquote, urlparse

from bs4 import BeautifulSoup

# eBay's own search plumbing. Everything else in a query string is an aspect,
# so this list is the whole filter -- it is written to over-exclude rather than
# under-exclude, since a stray control parameter appearing as a "vocabulary"
# would be obvious noise while a missed aspect is silently lost.
CONTROL_PARAMS = {
    "_nkw", "_sacat", "_dcat", "_from", "_trksid", "_sop", "_ipg", "_pgn",
    "_udlo", "_udhi", "_odkw", "_osacat", "_oac", "_blrs", "_fsrp", "_sadis",
    "_stpos", "_mPrRngCbx", "_in_kw", "rt", "hash", "epid", "_skc", "LH_Sold",
    "LH_Complete", "LH_ItemCondition", "LH_BIN", "LH_Auction", "LH_PrefLoc",
    "LH_FS", "LH_TitleDesc", "LH_SpecificSeller", "_saslop", "_sasl", "_ssn",
    "_fss", "_fsradio", "_samilow", "_samihi", "_sabdlo", "_sabdhi", "_dmd",
    "_ex_kw", "_adv", "_sop2", "store_name", "_nkwusc", "_sspcats",
}

# The aspects worth harvesting, and what each feeds. Names are eBay's, matched
# case-insensitively and with punctuation ignored, because the exact spelling
# has varied ("Player/Athlete" vs "Player"). Anything not listed is still
# reported, just not treated as vocabulary.
WANTED = {
    "playerathlete": "players",
    "player": "players",
    "set": "sets",
    "parallelvariety": "parallels",
    "parallel": "parallels",
    "insertset": "parallels",
    "manufacturer": "brands",
    "season": "seasons",
    "year": "seasons",
    "cardnumber": "card_numbers",
    "grade": "grades",
    "professionalgrader": "graders",
    "features": "features",
    "league": "leagues",
    "team": "teams",
}

# "Jayden Daniels (1,234)" -- the count eBay puts beside each facet.
_COUNT_RE = re.compile(r"\(\s*([\d,]+)\s*\)\s*$")

# A facet value that is really a control word rather than a card attribute.
_NOT_A_VALUE = re.compile(r"^(see all|show more|more|less|any|all)$", re.I)


def _normalise_aspect(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def aspect_links(html: str) -> list[tuple[str, str, Optional[int]]]:
    """Every (aspect, value, count) an eBay search page links to.

    Reads only hrefs and link text. No class names, no DOM structure -- a
    sidebar redesign does not touch the query strings the links point at.
    """
    soup = BeautifulSoup(html, "lxml")
    out: list[tuple[str, str, Optional[int]]] = []
    seen: set[tuple[str, str]] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "?" not in href:
            continue
        query = urlparse(href).query
        if not query:
            continue

        text = " ".join(anchor.get_text(" ", strip=True).split())
        found = _COUNT_RE.search(text)
        count = int(found.group(1).replace(",", "")) if found else None

        for raw_name, raw_value in parse_qsl(query, keep_blank_values=False):
            name = unquote(raw_name)
            if name in CONTROL_PARAMS or name.startswith("_"):
                continue
            value = unquote(raw_value).strip()
            if not value or _NOT_A_VALUE.match(value):
                continue
            # eBay joins multi-select facet values with a pipe.
            for one in (v.strip() for v in value.split("|")):
                if not one or _NOT_A_VALUE.match(one):
                    continue
                key = (name, one)
                if key in seen:
                    continue
                seen.add(key)
                out.append((name, one, count))

    return out


def harvest(html: str) -> dict[str, dict[str, Optional[int]]]:
    """Group the aspects a page exposes into the vocabularies they feed.

    Returns the same {bucket: {value: count}} shape that `merge` accumulates
    into and `as_vocabulary` reads. One shape throughout, deliberately: an
    earlier version returned sorted lists here and dicts elsewhere, which type
    checks nothing and fails only when the two meet.

    Insertion order is biggest-count-first, so callers that just want to show
    the top few can iterate without sorting again.
    """
    grouped: dict[str, dict[str, Optional[int]]] = defaultdict(dict)
    for aspect, value, count in aspect_links(html):
        bucket = WANTED.get(_normalise_aspect(aspect), f"other:{aspect}")
        # Keep the largest count seen for a value; the same facet can be linked
        # from more than one place with and without its count.
        if value not in grouped[bucket] or (count or 0) > (grouped[bucket][value] or 0):
            grouped[bucket][value] = count

    return {
        name: dict(sorted(values.items(), key=lambda kv: (-(kv[1] or 0), kv[0])))
        for name, values in grouped.items()
    }


def merge(into: dict[str, dict[str, Optional[int]]],
          harvested: dict[str, dict[str, Optional[int]]]) -> dict:
    """Accumulate harvests across pages and queries.

    One page shows only the facets eBay chose to render for that search, so the
    vocabulary is built from many searches rather than one -- and a value seen
    again with a bigger count keeps the bigger one.
    """
    for bucket, values in harvested.items():
        target = into.setdefault(bucket, {})
        for value, count in values.items():
            if value not in target or (count or 0) > (target[value] or 0):
                target[value] = count
    return into


def as_vocabulary(store: dict[str, dict[str, Optional[int]]],
                  min_count: int = 0) -> dict[str, list[str]]:
    """The harvested values as plain name lists, biggest first."""
    out = {}
    for bucket, values in store.items():
        names = [v for v, c in sorted(values.items(),
                                      key=lambda kv: (-(kv[1] or 0), kv[0]))
                 if (c or 0) >= min_count]
        if names:
            out[bucket] = names
    return out
