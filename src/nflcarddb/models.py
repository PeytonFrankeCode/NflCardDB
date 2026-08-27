"""Record types shared across the scrape pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass(slots=True)
class Sale:
    """One sold listing as scraped off a search results page."""

    item_id: str
    title: str
    price_cents: Optional[int] = None
    currency: str = "USD"
    shipping_cents: Optional[int] = None
    sold_date: Optional[str] = None  # ISO YYYY-MM-DD
    listing_format: str = "unknown"  # auction | fixed | unknown
    bids: Optional[int] = None
    best_offer: bool = False
    condition: Optional[str] = None
    seller: Optional[str] = None
    url: Optional[str] = None
    image_url: Optional[str] = None
    query_id: Optional[str] = None

    def as_row(self) -> dict:
        row = asdict(self)
        row["best_offer"] = int(self.best_offer)
        return row


@dataclass(slots=True)
class CardAttrs:
    """Structured card attributes recovered from a listing title."""

    player: Optional[str] = None
    team: Optional[str] = None
    year: Optional[int] = None
    brand: Optional[str] = None
    set_name: Optional[str] = None
    subset: Optional[str] = None
    variety: Optional[str] = None
    parallel: Optional[str] = None
    card_number: Optional[str] = None
    serial_number: Optional[int] = None
    print_run: Optional[int] = None
    grader: Optional[str] = None
    grade: Optional[float] = None
    is_graded: bool = False
    is_rookie: bool = False
    is_auto: bool = False
    is_relic: bool = False
    confidence: float = 0.0
    # Words the parser could not account for. Not stored -- it exists so
    # `card_name` knows whether "Base" is a fact or a failure, and so the
    # vocabulary gaps can be ranked rather than guessed at.
    unparsed: Optional[str] = None

    def as_row(self) -> dict:
        row = asdict(self)
        for flag in ("is_graded", "is_rookie", "is_auto", "is_relic"):
            row[flag] = int(getattr(self, flag))
        return row


@dataclass(slots=True)
class PageResult:
    """Everything useful pulled out of a single search results page."""

    sales: list[Sale] = field(default_factory=list)
    total_results: Optional[int] = None
    total_is_capped: bool = False  # eBay rendered "10,000+"
    strategy: str = "unknown"  # which selector cascade matched
