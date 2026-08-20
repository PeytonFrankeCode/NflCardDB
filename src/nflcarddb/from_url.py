"""Turn an eBay search URL into a config query.

Widening coverage means answering "which eBay searches should we collect?", and
the reliable way to answer it is in a browser: apply the filters, look at the
results, and see for yourself what comes back. This converts the URL that
produces into a query block, so nobody has to know that `_sacat` is the category
or guess an id that eBay may have retired.

Anything the collector manages itself -- sort order, page number, results per
page, the price band -- is dropped, because those are set per request while
walking. Everything else is preserved verbatim, which is what makes an aspect
filter like `Sport=Football` or `Graded=Yes` work without this file knowing such
filters exist.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import parse_qs, urlparse

# Set per request by the walker, so carrying them over from a browser URL would
# either be ignored or fight it.
MANAGED = {
    "_nkw", "_sacat",          # captured explicitly below
    "_sop", "_pgn", "_ipg",    # sort and pagination
    "_udlo", "_udhi",          # price band
    "LH_Sold", "LH_Complete",  # the whole point; always applied
    "rt", "_from", "_trksid", "_odkw", "_osacat", "_fsrp",  # tracking noise
    "_oac", "_dmd", "_ipg_", "_blrs", "_sadis", "_stpos",
}


class NotAnEbaySearch(ValueError):
    """The URL is not an eBay search results page."""


def parse_search_url(url: str, query_id: Optional[str] = None) -> dict:
    """Return a QuerySpec-shaped dict for an eBay search URL."""
    parsed = urlparse(url.strip())
    if not parsed.netloc or "ebay." not in parsed.netloc.lower():
        raise NotAnEbaySearch(
            f"{url!r} is not an eBay URL.\n"
            "Search eBay in your browser, tick Sold items, then copy the "
            "address bar."
        )
    if "/sch/" not in parsed.path:
        raise NotAnEbaySearch(
            "That is not a search results page.\n"
            "Run the search first, then copy the address -- an item page or a "
            "category landing page has no filters to read."
        )

    params = parse_qs(parsed.query, keep_blank_values=False)
    first = {k: v[0] for k, v in params.items() if v}

    keywords = first.get("_nkw", "").strip()
    category = first.get("_sacat", "").strip() or None
    if category in ("0", ""):
        category = None

    extra = {k: v for k, v in first.items() if k not in MANAGED}

    if not keywords and not category and not extra:
        raise NotAnEbaySearch(
            "That URL carries no keywords, category or filters, so it would "
            "collect the whole of eBay. Narrow the search first."
        )

    return {
        "id": query_id or _suggest_id(keywords, category, extra),
        "keywords": keywords,
        "category": category,
        "extra": extra,
    }


def _suggest_id(keywords: str, category: Optional[str], extra: dict) -> str:
    parts = [w for w in keywords.lower().split() if w.isalnum()][:3]
    for key in ("Sport", "Graded", "Season"):
        value = extra.get(key)
        if value:
            parts.append(str(value).lower().replace(" ", "_"))
    if not parts:
        parts = [f"cat{category}" if category else "query"]
    return "_".join(parts)[:40]


def to_yaml(spec: dict) -> str:
    """Render a query block ready to paste under `queries:`."""
    lines = [f"  - id: {spec['id']}"]
    if spec.get("keywords"):
        lines.append(f"    keywords: {spec['keywords']}")
    if spec.get("category"):
        lines.append(f'    category: "{spec["category"]}"')
    if spec.get("extra"):
        lines.append("    extra:")
        for key, value in sorted(spec["extra"].items()):
            lines.append(f'      {key}: "{value}"')
    return "\n".join(lines)
