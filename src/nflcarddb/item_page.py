"""Read the accepted Best Offer amount off a sold listing's own page.

The search results show a best-offer sale at the seller's ASK. The buyer paid
less, by an amount the results never say -- so roughly half the sales in the
database have carried a price that is too high, and every median built on them
has sat high with them. eBay has started printing the accepted amount on the
item page itself. This reads it.

**Why it is conservative to the point of saying nothing.** A wrong price is
worse than a known-high one: the ask is at least labelled as an ask, while a
mis-read price would be indistinguishable from a real sale. So a reading is
only accepted when all of these hold:

* The amount sits next to wording that says it is the accepted offer. Any
  other dollar figure on the page -- shipping, an import charge, the "similar
  items" carousels that fill the bottom of every listing -- is not evidence.
* It is LOWER than the ask. An offer cannot be accepted above the price the
  seller asked, so an amount at or over it means the page showed nothing new.
  This also makes the whole lookup one-directional: it can only ever pull an
  inflated price down toward the truth, never push one up.
* It is not implausibly low. Below a fifth of the ask is far more likely to be
  a misread figure than an accepted offer.
* Only ONE amount qualifies. Two different candidates is a page this does not
  understand, and guessing between them is how a wrong price gets in.

Anything short of that is recorded as what it is -- not shown, ambiguous,
gone -- and the price is left exactly as it was.

The markup this expects is a reasoned guess until it has been checked against
a real saved page (`offer-check.bat`), and the lookup stops itself if its
first pages find nothing, rather than spend a night confirming the guess was
wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup

# Wording that marks an amount as the price actually paid on a best offer.
# Matched in the text immediately BEFORE the amount, or immediately after it.
ACCEPTED_RE = re.compile(
    r"(?:best\s*offer\s*accepted|offer\s*accepted|accepted\s*(?:best\s*)?offer"
    r"|accepted\s*(?:price|amount|for)|sold\s*for|sold\s*price"
    r"|final\s*(?:sale\s*)?price)",
    re.I,
)

# Wording that makes an amount something other than the item's price.
NOT_THE_PRICE_RE = re.compile(
    r"(?:shipping|postage|delivery|import|customs|duties|tax|fee|handling"
    r"|save\s|you\s*save|off\b|coupon|discount|per\s*month|/mo\b|financ)",
    re.I,
)

# Parts of an item page that are about OTHER items. Their prices are real
# prices of real listings, which is exactly what makes them dangerous here.
OTHER_ITEMS_RE = re.compile(
    r"carousel|similar|sponsored|recommend|merch|also-viewed|alsoviewed"
    r"|related|explore|vi-sims|x-sims|plugin-sims",
    re.I,
)

GONE_RE = re.compile(
    r"looked\s+everywhere|no\s+longer\s+available|could\s*n[o']t\s+be\s+found"
    r"|does\s*n[o']t\s+exist|item\s+(?:was\s+)?removed|page\s+(?:is\s+)?missing",
    re.I,
)

# "US $1,234.56" / "$38" -- and NOT "C $", "AU $" and the other dollars.
MONEY_RE = re.compile(r"(?<![A-Za-z])(US\s*)?\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?")
FOREIGN_DOLLAR_RE = re.compile(r"(?:AU|A|C|CA|CAD|NZ|HK|SG|NT)\s*$")

# An unconfirmed lead from collector forums: the accepted amount is said to
# sit in the page's embedded data under this name. Reported for calibration,
# never acted on alone -- see `read_offer`.
JSON_HINT_RE = re.compile(
    r'"taxExclusivePrice"\s*:\s*(?:\{[^{}]{0,200}?"value"\s*:\s*)?"?(\d+(?:\.\d{1,2})?)',
)

CONTEXT_CHARS = 70
LOWEST_PLAUSIBLE = 0.2


@dataclass
class Candidate:
    cents: int
    kind: str          # accepted | struck | other | ignored
    context: str       # the words around it, for calibration output


@dataclass
class OfferReading:
    status: str                         # found | not_shown | ambiguous | gone
    accepted_cents: Optional[int] = None
    reason: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    hint_cents: list[int] = field(default_factory=list)


def _cents(whole: str, frac: Optional[str]) -> int:
    return int(whole.replace(",", "")) * 100 + int(frac or 0)


def _scope(soup: BeautifulSoup):
    """The part of the page about THIS item, with other items cut out."""
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.attrs is None:      # already removed along with a parent
            continue
        marker = " ".join([
            " ".join(tag.get("class") or []),
            tag.get("id") or "",
            tag.get("data-testid") or "",
        ])
        if marker.strip() and OTHER_ITEMS_RE.search(marker):
            tag.decompose()
    for selector in ("#LeftSummaryPanel", "#mainContent", "#CenterPanel",
                     "[class*=x-price-section]", "main"):
        found = soup.select_one(selector)
        if found is not None and found.get_text(strip=True):
            return found
    return soup.body or soup


def _struck_amounts(scope) -> set[int]:
    """Amounts drawn with a line through them: the price before the offer."""
    out: set[int] = set()
    for tag in scope.find_all(True):
        cls = " ".join(tag.get("class") or []).lower()
        if tag.name in ("s", "del", "strike") or "strikethrough" in cls \
                or "strike-through" in cls or "original-price" in cls:
            for m in MONEY_RE.finditer(tag.get_text(" ", strip=True)):
                out.add(_cents(m.group(2), m.group(3)))
    return out


def find_candidates(html: str) -> tuple[list[Candidate], list[int], bool]:
    """Every dollar amount on the item's own part of the page, classified.

    Returns (candidates, json hints, looks_gone).
    """
    hints = [round(float(m.group(1)) * 100) for m in JSON_HINT_RE.finditer(html)]

    soup = BeautifulSoup(html, "lxml")
    whole_text = soup.get_text(" ", strip=True)
    scope = _scope(soup)
    struck = _struck_amounts(scope)
    text = scope.get_text(" ", strip=True)
    looks_gone = bool(GONE_RE.search(whole_text[:6000])) and not MONEY_RE.search(text)

    matches = list(MONEY_RE.finditer(text))
    out: list[Candidate] = []
    for i, m in enumerate(matches):
        # The words since the previous amount, so one label cannot be credited
        # to two figures that follow it.
        prev_end = matches[i - 1].end() if i else 0
        before = text[max(prev_end, m.start() - CONTEXT_CHARS):m.start()]
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        after = text[m.end():min(next_start, m.end() + 30)]
        cents = _cents(m.group(2), m.group(3))
        context = f"{before.strip()} [{m.group(0).strip()}] {after.strip()}".strip()

        if not m.group(1) and FOREIGN_DOLLAR_RE.search(text[max(0, m.start() - 4):m.start()]):
            out.append(Candidate(cents, "ignored", context))
            continue
        if NOT_THE_PRICE_RE.search(before[-25:]) or NOT_THE_PRICE_RE.match(after.strip()):
            out.append(Candidate(cents, "ignored", context))
            continue
        # Struck first: a price drawn with a line through it is by definition
        # not what was paid, even when "Best offer accepted" happens to be
        # printed right after it -- which is exactly where eBay puts that note.
        if cents in struck:
            kind = "struck"
        elif ACCEPTED_RE.search(before) or ACCEPTED_RE.match(after.strip()):
            kind = "accepted"
        else:
            kind = "other"
        out.append(Candidate(cents, kind, context))
    return out, hints, looks_gone


def read_offer(html: str, ask_cents: Optional[int] = None) -> OfferReading:
    """The accepted amount, or a plain statement of why there is none."""
    if not html:
        return OfferReading("gone", reason="empty page")

    candidates, hints, looks_gone = find_candidates(html)
    if looks_gone:
        return OfferReading("gone", reason="eBay no longer has this listing",
                            candidates=candidates, hint_cents=hints)

    accepted = sorted({c.cents for c in candidates if c.kind == "accepted"})
    if ask_cents:
        # An offer cannot be accepted above the ask, and at the ask it tells us
        # nothing the search results did not -- the page may simply be showing
        # the asking price next to a "best offer accepted" note.
        below = [a for a in accepted if a < ask_cents]
        plausible = [a for a in below if a >= ask_cents * LOWEST_PLAUSIBLE]
    else:
        below = plausible = accepted

    reading = OfferReading("not_shown", candidates=candidates, hint_cents=hints)
    if len(plausible) == 1:
        reading.status = "found"
        reading.accepted_cents = plausible[0]
        reading.reason = "an amount labelled as the accepted offer"
    elif len(plausible) > 1:
        reading.status = "ambiguous"
        reading.reason = (f"{len(plausible)} different amounts look like the "
                          f"accepted offer; writing none of them")
    elif below and not plausible:
        reading.reason = "the only labelled amount is implausibly far below the ask"
    elif accepted:
        reading.reason = "the labelled amount is the asking price itself"
    else:
        reading.reason = "no amount on the page is labelled as the accepted offer"
    return reading
