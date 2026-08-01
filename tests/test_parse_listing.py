from pathlib import Path

import pytest

from nflcarddb.parse_listing import parse_search_page

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def s_item():
    return parse_search_page((FIXTURES / "sold_s_item.html").read_text(), query_id="q1")


@pytest.fixture(scope="module")
def s_card():
    return parse_search_page((FIXTURES / "sold_s_card.html").read_text(), query_id="q2")


def test_classic_markup_yields_real_listings_only(s_item):
    # Four <li> tiles, one of which is the "Shop on eBay" promo.
    assert len(s_item.sales) == 3
    assert all(s.item_id.isdigit() for s in s_item.sales)
    assert "123456789012" not in {s.item_id for s in s_item.sales}


def test_classic_markup_fields(s_item):
    sale = next(s for s in s_item.sales if s.item_id == "226789012345")
    assert sale.title == "2023 Panini Prizm CJ Stroud Silver Prizm RC #339 PSA 10"
    assert sale.price_cents == 14999
    assert sale.currency == "USD"
    assert sale.shipping_cents == 500
    assert sale.sold_date == "2025-07-30"
    assert sale.seller == "cardvault_kc"
    assert sale.condition == "Pre-Owned"
    assert sale.query_id == "q1"
    assert sale.url == "https://www.ebay.com/itm/226789012345"


def test_auction_bids_and_free_shipping(s_item):
    sale = next(s for s in s_item.sales if s.item_id == "335566778899")
    assert sale.bids == 7
    assert sale.listing_format == "auction"
    assert sale.shipping_cents == 0


def test_best_offer_flagged_and_new_listing_badge_stripped(s_item):
    sale = next(s for s in s_item.sales if s.item_id == "144556677788")
    assert sale.best_offer is True
    assert sale.title.startswith("2023 Panini Select")
    assert sale.price_cents == 125000
    assert sale.sold_date == "2025-07-29"


def test_capped_result_count(s_item):
    assert s_item.total_results == 10000
    assert s_item.total_is_capped is True


def test_new_markup_still_parses(s_card):
    assert len(s_card.sales) == 3
    assert s_card.total_results == 3482
    assert s_card.total_is_capped is False

    sale = next(s for s in s_card.sales if s.item_id == "405566778812")
    assert sale.price_cents == 31250
    assert sale.shipping_cents == 425
    assert sale.sold_date == "2025-07-30"
    assert "Marvin Harrison" in sale.title


def test_non_usd_currency_detected(s_card):
    sale = next(s for s in s_card.sales if s.item_id == "405566778814")
    assert sale.currency == "CAD"
    assert sale.price_cents == 6100


def test_international_sold_date_format(s_card):
    # "Sold 30 Jul 2025" rather than "Sold Jul 30, 2025".
    sale = next(s for s in s_card.sales if s.item_id == "405566778814")
    assert sale.sold_date == "2025-07-30"


def test_thousands_separator_in_price(s_card):
    sale = next(s for s in s_card.sales if s.item_id == "405566778813")
    assert sale.price_cents == 240000


def test_empty_page_reports_no_match():
    result = parse_search_page("<html><body><p>nothing here</p></body></html>")
    assert result.sales == []
    assert result.strategy == "no-match"
