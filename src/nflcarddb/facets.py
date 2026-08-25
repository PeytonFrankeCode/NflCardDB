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
    # Per-listing tracking and promotion payloads, confirmed against a live
    # page: these are the bulk of what a results page links, and without them
    # 261 encrypted `itmprp` blobs arrive looking like a vocabulary.
    "itemId", "itmmeta", "itmprp", "iid", "ctx", "promoted_items",
    "ssPageName", "mode", "notionalTypeId", "period", "source", "title", "id",
    "CurrentPage", "amdata", "var", "hash", "campid", "customid", "toolid",
    "mkevt", "mkcid", "mkrid", "siteid", "brand", "norover", "pageci",
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
    # Confirmed present on a live page, so named rather than left in "other".
    "sport": "sports",
    "yearmanufactured": "seasons",
    "graded": "graded",
    "autographed": "autographed",
    "countryoforigin": "countries",
}

# eBay's marker for "not a parallel at all". Harvesting it as one would put
# "[Base]" into a card key.
_BRACKETED = re.compile(r"^\[.*\]$")

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
                if _BRACKETED.match(one):
                    continue
                # Encrypted tracking blobs and id lists: long, or nothing a
                # person would ever write on a card.
                if len(one) > 60 or one.startswith("enc:") or "/" in one[:1]:
                    continue
                key = (name, one)
                if key in seen:
                    continue
                seen.add(key)
                out.append((name, one, count))

    return out


# The stored file's shape. Version 1 was keyed by vocabulary bucket; version 2
# is keyed by eBay's own aspect name, which drilling needs. Bumped rather than
# migrated because re-harvesting costs minutes and a mis-read old file is worse
# than an empty one: bucketing an already-bucketed key produced "other:parallels"
# and "other:other:mode" in a live run.
FILE_VERSION = 2


def bucket_of(aspect: str) -> str:
    """Which vocabulary an eBay aspect feeds, or `other:<name>` if unknown."""
    return WANTED.get(_normalise_aspect(aspect), f"other:{aspect}")


def harvest(html: str) -> dict[str, dict[str, Optional[int]]]:
    """The aspects a page exposes, keyed by eBay's own aspect name.

    Keyed by the raw name -- "Season", "Parallel/Variety" -- rather than by the
    vocabulary bucket, because the name is also the query parameter needed to
    search *within* that facet. A page renders only its top handful of values
    per aspect, so getting a full list means drilling, and drilling needs the
    parameter eBay expects.

    Returns the same {aspect: {value: count}} shape that `merge` accumulates
    into and `as_vocabulary` reads. One shape throughout, deliberately: an
    earlier cut returned sorted lists here and dicts elsewhere, which type
    checks nothing and fails only where the two meet.

    Insertion order is biggest-count-first, so callers wanting the top few can
    iterate without sorting again.
    """
    grouped: dict[str, dict[str, Optional[int]]] = defaultdict(dict)
    for aspect, value, count in aspect_links(html):
        # Keep the largest count seen for a value; the same facet can be linked
        # from more than one place with and without its count.
        if value not in grouped[aspect] or (count or 0) > (grouped[aspect][value] or 0):
            grouped[aspect][value] = count

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
    """The harvested values grouped into vocabularies, biggest first.

    Bucketing happens here rather than at harvest time so the stored file stays
    eBay's taxonomy verbatim, which is what drilling needs.
    """
    merged: dict[str, dict[str, Optional[int]]] = defaultdict(dict)
    for aspect, values in store.items():
        bucket = bucket_of(aspect)
        for value, count in values.items():
            if value not in merged[bucket] or (count or 0) > (merged[bucket][value] or 0):
                merged[bucket][value] = count

    out = {}
    for bucket, values in merged.items():
        names = [v for v, c in sorted(values.items(),
                                      key=lambda kv: (-(kv[1] or 0), kv[0]))
                 if (c or 0) >= min_count]
        if names:
            out[bucket] = names
    return out


def load_store(path) -> dict[str, dict[str, Optional[int]]]:
    """Read an accumulated harvest, or start empty if it is an older shape.

    Silently reading a version-1 file would double-bucket every key. Starting
    over costs one re-harvest; carrying corrupt vocabulary forward costs
    confidence in everything downstream of it.
    """
    import json
    from pathlib import Path

    file = Path(path)
    if not file.exists():
        return {}
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or raw.get("version") != FILE_VERSION:
        return {}
    return {a: dict(v) for a, v in raw.get("aspects", {}).items()}


def save_store(store: dict[str, dict[str, Optional[int]]], path) -> None:
    import json
    from pathlib import Path

    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        json.dumps({"version": FILE_VERSION, "aspects": store},
                   indent=1, sort_keys=True),
        encoding="utf-8",
    )


def drillable(store: dict[str, dict[str, Optional[int]]], bucket: str,
              limit: int = 50) -> list[tuple[str, str]]:
    """(aspect, value) pairs to search within, for one vocabulary bucket.

    A results page shows only its top few values per aspect, so the way to a
    complete list is to narrow the search and ask again: filtering by Season
    makes the Set facet list that season's sets rather than the eight most
    listed sets overall.
    """
    out: list[tuple[str, str]] = []
    for aspect, values in store.items():
        if bucket_of(aspect) != bucket:
            continue
        for value in list(values)[:limit]:
            out.append((aspect, value))
    return out
