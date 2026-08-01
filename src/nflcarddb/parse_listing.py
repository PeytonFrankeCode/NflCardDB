"""Turn an eBay search results page into Sale records.

Design note: eBay reskins its search markup regularly -- the result container
has been ``li.s-item`` for years and is migrating to ``li.s-card``. Pinning to
either class name guarantees a breakage. Instead we anchor on the one thing
that has never changed: every result links to ``/itm/<numeric id>``. We find
those links, walk up to the enclosing result container, and pull fields with a
selector cascade that falls back to regex over the container's text.

If eBay changes markup anyway, ``nflcarddb calibrate <saved.html>`` reports
exactly which stage stopped matching.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Optional

from bs4 import BeautifulSoup, Tag

from .models import PageResult, Sale

PARSER_VERSION = "listing/1"

ITEM_ID_RE = re.compile(r"/itm/(?:[^/?#]+/)?(\d{9,15})")
MONEY_RE = re.compile(
    r"(?:(?P<code>[A-Z]{1,3})\s*)?(?P<sym>[$£€¥])\s?(?P<amt>\d[\d,]*(?:\.\d{2})?)"
)
BIDS_RE = re.compile(r"(\d+)\s+bids?\b", re.I)
RESULTS_RE = re.compile(r"([\d,]+)(\+?)\s*results?", re.I)
FREE_SHIP_RE = re.compile(r"free\s+(?:shipping|postage|delivery)", re.I)
SHIP_RE = re.compile(
    r"\+\s*(?:[A-Z]{1,3}\s*)?[$£€¥]\s?(\d[\d,]*(?:\.\d{2})?)\s*(?:shipping|postage|delivery)",
    re.I,
)
BEST_OFFER_RE = re.compile(r"best\s+offer(?:\s+accepted)?", re.I)

# "Sold Jul 30, 2025" (US) and "Sold 30 Jul 2025" (UK/AU sites).
SOLD_US_RE = re.compile(r"sold\s+([A-Z][a-z]{2})\s+(\d{1,2}),?\s+(\d{4})", re.I)
SOLD_INTL_RE = re.compile(r"sold\s+(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})", re.I)

SYMBOL_TO_CURRENCY = {"$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY"}
CODE_TO_CURRENCY = {"C": "CAD", "AU": "AUD", "US": "USD", "GBP": "GBP", "EUR": "EUR"}

# eBay injects a non-listing promo tile into results; drop it.
PLACEHOLDER_TITLES = {"shop on ebay", "new listing"}

CONDITION_HINTS = (
    "brand new", "pre-owned", "new (other)", "open box", "used", "graded",
    "ungraded", "like new",
)


def _money_to_cents(text: str) -> tuple[Optional[int], Optional[str]]:
    """Parse the first monetary amount in ``text`` -> (cents, currency)."""
    m = MONEY_RE.search(text or "")
    if not m:
        return (None, None)
    amount = m.group("amt").replace(",", "")
    try:
        cents = int(round(float(amount) * 100))
    except ValueError:
        return (None, None)
    code = (m.group("code") or "").upper()
    currency = CODE_TO_CURRENCY.get(code) or SYMBOL_TO_CURRENCY.get(m.group("sym"), "USD")
    return (cents, currency)


def _parse_sold_date(text: str) -> Optional[str]:
    m = SOLD_US_RE.search(text)
    if m:
        mon, day, year = m.group(1), m.group(2), m.group(3)
        raw, fmt = f"{mon} {day} {year}", "%b %d %Y"
    else:
        m = SOLD_INTL_RE.search(text)
        if not m:
            return None
        day, mon, year = m.group(1), m.group(2), m.group(3)
        raw, fmt = f"{mon} {day} {year}", "%b %d %Y"
    try:
        return datetime.strptime(raw.title(), fmt).date().isoformat()
    except ValueError:
        return None


def _container_for(anchor: Tag) -> Tag:
    """Walk up from an item link to the enclosing result tile."""
    node = anchor
    for _ in range(8):
        parent = node.parent
        if parent is None or not isinstance(parent, Tag):
            break
        node = parent
        if node.name == "li":
            return node
        classes = " ".join(node.get("class") or [])
        if any(k in classes for k in ("s-item__wrapper", "s-card__wrapper", "srp-results")):
            if "srp-results" in classes:
                break
            return node
    return anchor.parent if isinstance(anchor.parent, Tag) else anchor


def _text_of(node: Optional[Tag]) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _first_text(container: Tag, selectors: Iterable[str]) -> str:
    for sel in selectors:
        found = container.select_one(sel)
        if found:
            text = _text_of(found)
            if text:
                return text
    return ""


def _extract_title(container: Tag, anchor: Tag) -> str:
    title = _first_text(container, (
        ".s-item__title span[role=heading]",
        ".s-item__title",
        ".s-card__title span",
        ".s-card__title",
        "[class*=item__title]",
        "[class*=card__title]",
        "h3",
    ))
    if not title:
        title = anchor.get("aria-label") or _text_of(anchor)
    # eBay prefixes some titles with a "New Listing" badge.
    return re.sub(r"^new\s+listing\s*", "", title, flags=re.I).strip()


def _extract_price(container: Tag) -> tuple[Optional[int], Optional[str]]:
    price_text = _first_text(container, (
        ".s-item__price",
        ".s-card__price",
        "[class*=item__price]",
        "[class*=card__price]",
        ".su-styled-text.primary.bold",
    ))
    if not price_text:
        # Last resort: first money-looking token in the tile, skipping shipping lines.
        for line in container.get_text("\n", strip=True).split("\n"):
            if MONEY_RE.search(line) and not re.search(r"shipping|postage|delivery", line, re.I):
                price_text = line
                break
    if not price_text:
        return (None, None)
    # Multi-variant listings render "$10.00 to $25.00"; keep the low end.
    return _money_to_cents(price_text)


def _extract_shipping(container: Tag, blob: str) -> Optional[int]:
    ship_text = _first_text(container, (
        ".s-item__shipping",
        ".s-item__logisticsCost",
        "[class*=logisticsCost]",
        "[class*=item__shipping]",
    )) or blob
    if FREE_SHIP_RE.search(ship_text):
        return 0
    m = SHIP_RE.search(ship_text)
    if m:
        try:
            return int(round(float(m.group(1).replace(",", "")) * 100))
        except ValueError:
            return None
    return None


def _extract_condition(container: Tag, blob: str) -> Optional[str]:
    text = _first_text(container, (".SECONDARY_INFO", ".s-item__subtitle", "[class*=subtitle]"))
    if text:
        return text
    low = blob.lower()
    for hint in CONDITION_HINTS:
        if hint in low:
            return hint.title()
    return None


def _extract_seller(container: Tag) -> Optional[str]:
    text = _first_text(container, (".s-item__seller-info-text", "[class*=seller-info]"))
    if not text:
        return None
    m = re.match(r"([\w.\-*]+)\s*\(", text)
    return m.group(1) if m else text[:64]


def parse_search_page(html: str, query_id: Optional[str] = None) -> PageResult:
    """Parse one search results page into a PageResult."""
    soup = BeautifulSoup(html, "lxml")
    result = PageResult(strategy="itm-anchor")

    # Result count, used to decide whether a price band needs subdividing.
    heading = soup.select_one(".srp-controls__count-heading, [class*=count-heading], h1")
    heading_text = _text_of(heading) or html[:4000]
    m = RESULTS_RE.search(heading_text)
    if m:
        try:
            result.total_results = int(m.group(1).replace(",", ""))
            result.total_is_capped = m.group(2) == "+"
        except ValueError:
            pass

    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        m = ITEM_ID_RE.search(anchor["href"])
        if not m:
            continue
        item_id = m.group(1)
        if item_id in seen:
            continue

        container = _container_for(anchor)
        title = _extract_title(container, anchor)
        if not title or title.strip().lower() in PLACEHOLDER_TITLES:
            continue

        seen.add(item_id)
        blob = container.get_text(" ", strip=True)
        price_cents, currency = _extract_price(container)
        bids_m = BIDS_RE.search(blob)
        bids = int(bids_m.group(1)) if bids_m else None

        result.sales.append(Sale(
            item_id=item_id,
            title=title,
            price_cents=price_cents,
            currency=currency or "USD",
            shipping_cents=_extract_shipping(container, blob),
            sold_date=_parse_sold_date(blob),
            listing_format="auction" if bids is not None else "fixed",
            bids=bids,
            best_offer=bool(BEST_OFFER_RE.search(blob)),
            condition=_extract_condition(container, blob),
            seller=_extract_seller(container),
            url=f"https://www.ebay.com/itm/{item_id}",
            image_url=_extract_image(container),
            query_id=query_id,
        ))

    if not result.sales:
        result.strategy = "no-match"
    return result


def _extract_image(container: Tag) -> Optional[str]:
    img = container.select_one("img[src], img[data-src]")
    if not img:
        return None
    src = img.get("src") or img.get("data-src")
    # eBay lazy-loads with a 1x1 gif placeholder.
    if src and "s-l" in src and not src.startswith("data:"):
        return src
    return src if src and not src.startswith("data:") else None
