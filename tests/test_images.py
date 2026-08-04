"""Listing photo URLs.

The size lives in the filename, so getting a usable photo is a string rewrite
rather than a second request. These tests pin that rewrite, and pin the
rejection of the things that look like images but are not: badges, spacer GIFs,
and the base64 pixel eBay leaves behind while lazy-loading.
"""

from bs4 import BeautifulSoup

from nflcarddb.images import (
    best_from_srcset,
    is_placeholder,
    normalize_image_url,
)
from nflcarddb.parse_listing import _extract_image, parse_search_page


def test_thumbnail_is_rewritten_to_a_usable_size():
    """140px is what a results page embeds; a card is unreadable at 140px."""
    assert normalize_image_url(
        "https://i.ebayimg.com/images/g/AbC/s-l140.jpg"
    ) == "https://i.ebayimg.com/images/g/AbC/s-l500.jpg"


def test_the_thumbs_path_is_dropped():
    """/thumbs/ serves small images whatever size is asked for."""
    assert normalize_image_url(
        "https://i.ebayimg.com/thumbs/images/g/AbC/s-l140.jpg"
    ) == "https://i.ebayimg.com/images/g/AbC/s-l500.jpg"


def test_an_explicit_size_is_honoured():
    assert normalize_image_url(
        "https://i.ebayimg.com/images/g/AbC/s-l140.jpg", size=1600
    ).endswith("s-l1600.jpg")


def test_an_unrecognised_size_snaps_to_one_ebay_serves():
    """eBay 404s a size it does not know, which would look like a dead photo."""
    url = normalize_image_url("https://i.ebayimg.com/images/g/AbC/s-l140.jpg", size=517)
    assert url.endswith("s-l500.jpg")


def test_webp_keeps_its_extension():
    assert normalize_image_url(
        "https://i.ebayimg.com/images/g/AbC/s-l225.webp"
    ).endswith("s-l500.webp")


def test_tracking_parameters_are_stripped():
    """They vary per page view, so they would defeat deduplication."""
    assert normalize_image_url(
        "https://i.ebayimg.com/images/g/AbC/s-l140.jpg?set_id=8800005007"
    ) == "https://i.ebayimg.com/images/g/AbC/s-l500.jpg"


def test_protocol_relative_and_http_urls_become_https():
    assert normalize_image_url("//i.ebayimg.com/images/g/A/s-l140.jpg").startswith("https://")
    assert normalize_image_url("http://i.ebayimg.com/images/g/A/s-l140.jpg").startswith("https://")


def test_a_non_ebay_url_survives_unchanged():
    assert normalize_image_url("https://cdn.example.com/photo.jpg") == \
        "https://cdn.example.com/photo.jpg"


def test_placeholders_return_none_rather_than_a_grey_pixel():
    for junk in (
        "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",
        "https://ir.ebaystatic.com/rs/v/badge.png",
        "https://pics.ebaystatic.com/aw/pics/s_1x2.gif",
        "https://i.ebayimg.com/spacer.gif",
        "",
        None,
    ):
        assert is_placeholder(junk), junk
        assert normalize_image_url(junk) is None


def test_srcset_picks_the_largest_candidate():
    assert best_from_srcset(
        "https://i.ebayimg.com/a/s-l140.jpg 1x, https://i.ebayimg.com/a/s-l280.jpg 2x"
    ) == "https://i.ebayimg.com/a/s-l280.jpg"

    assert best_from_srcset(
        "https://x/a.jpg 140w, https://x/b.jpg 500w, https://x/c.jpg 300w"
    ) == "https://x/b.jpg"


def test_srcset_handles_a_bare_url_and_empty_input():
    assert best_from_srcset("https://x/a.jpg") == "https://x/a.jpg"
    assert best_from_srcset("") is None
    assert best_from_srcset(None) is None


def _tile(inner: str):
    html = f"""<li class="s-item"><div class="s-item__wrapper">
      <a href="https://www.ebay.com/itm/123456789012">{inner}</a>
      <div class="s-item__title"><span role="heading">2021 Prizm Ja'Marr Chase RC</span></div>
      <span class="s-item__price">$88.00</span>
    </div></li>"""
    return BeautifulSoup(html, "lxml").select_one("li")


def test_a_lazy_loaded_photo_is_found_in_data_src():
    """Below the fold, `src` is a base64 pixel and the photo is in data-src."""
    tile = _tile(
        '<img src="data:image/gif;base64,R0lGODlh" '
        'data-src="https://i.ebayimg.com/thumbs/images/g/XYZ/s-l140.jpg">'
    )
    assert _extract_image(tile) == "https://i.ebayimg.com/images/g/XYZ/s-l500.jpg"


def test_a_seller_badge_is_not_mistaken_for_the_photo():
    """Taking the first <img> in the tile picks the badge on listings with one."""
    tile = _tile(
        '<img src="https://ir.ebaystatic.com/rs/v/top-rated.png">'
        '<div class="s-item__image-wrapper">'
        '<img src="https://i.ebayimg.com/images/g/REAL/s-l225.jpg"></div>'
    )
    assert _extract_image(tile) == "https://i.ebayimg.com/images/g/REAL/s-l500.jpg"


def test_srcset_is_preferred_over_a_smaller_src():
    tile = _tile(
        '<img src="https://i.ebayimg.com/images/g/A/s-l140.jpg" '
        'srcset="https://i.ebayimg.com/images/g/A/s-l140.jpg 1x, '
        'https://i.ebayimg.com/images/g/A/s-l280.jpg 2x">'
    )
    assert _extract_image(tile) == "https://i.ebayimg.com/images/g/A/s-l500.jpg"


def test_a_tile_with_no_photo_yields_none_not_a_badge():
    tile = _tile('<img src="https://ir.ebaystatic.com/rs/v/logo.png">')
    assert _extract_image(tile) is None


def test_photos_come_through_the_full_parse():
    tile = _tile('<img src="https://i.ebayimg.com/thumbs/images/g/Q/s-l140.jpg">')
    page = parse_search_page(str(tile))

    assert len(page.sales) == 1
    assert page.sales[0].image_url == "https://i.ebayimg.com/images/g/Q/s-l500.jpg"


def test_image_report_counts_and_upgrades(tmp_path):
    """Rows collected before the rewrite existed keep thumbnails until asked."""
    from nflcarddb import db as store
    from nflcarddb.models import Sale
    from nflcarddb.pipeline import image_report

    path = tmp_path / "img.db"
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    store.upsert_sales(conn, [
        Sale(item_id="1", title="a", sold_date="2026-08-03",
             image_url="https://i.ebayimg.com/thumbs/images/g/A/s-l140.jpg"),
        Sale(item_id="2", title="b", sold_date="2026-08-03",
             image_url="https://i.ebayimg.com/images/g/B/s-l500.jpg"),
        Sale(item_id="3", title="c", sold_date="2026-08-03", image_url=None),
    ], run)
    conn.close()

    report = image_report(str(path))
    assert report["sales"] == 3
    assert report["with_photo"] == 2
    assert report["resizable"] == 1        # only the 140px one needs rewriting
    assert report["upgraded"] == 0         # reporting alone changes nothing

    report = image_report(str(path), upgrade=True)
    assert report["upgraded"] == 1

    conn = store.connect(path)
    stored = conn.execute("SELECT image_url FROM sales WHERE item_id='1'").fetchone()[0]
    conn.close()
    assert stored == "https://i.ebayimg.com/images/g/A/s-l500.jpg"

    # Upgrading is idempotent: a second pass finds nothing left to do.
    assert image_report(str(path), upgrade=True)["upgraded"] == 0
