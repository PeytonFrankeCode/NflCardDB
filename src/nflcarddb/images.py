"""Listing photo URLs.

eBay serves every listing photo off one CDN with the wanted size baked into the
filename -- `.../s-l140.jpg` is a 140px thumbnail of exactly the same image as
`.../s-l500.jpg`. Search results embed the small one because that is all a
results page needs, so the URL captured while scraping is a 140px thumbnail
unless it is rewritten. A card is unreadable at 140px, hence this module.

Nothing here downloads anything. Only the URL is stored, and the image itself
stays on eBay's CDN -- 20,000 photos a day is a storage problem nobody asked
for, and the URL is what a website needs in an <img> tag anyway.
"""

from __future__ import annotations

import re
from typing import Optional

# `.../s-l140.jpg`, `.../s-l1600.webp`. The number is the longest edge.
SIZE_RE = re.compile(r"/s-l\d+(\.\w{3,4})(?=$|[?#])", re.I)

# eBay resizes on demand, but only to sizes it recognises; an unknown one 404s.
ALLOWED_SIZES = (64, 96, 140, 225, 300, 400, 500, 640, 800, 960, 1200, 1600)

# 500px reads clearly on a card grid and is ~40KB. 1600 is four times the bytes
# for detail nobody sees in a list.
DEFAULT_SIZE = 500

IMAGE_HOSTS = ("i.ebayimg.com", "ir.ebaystatic.com", "pics.ebaystatic.com")

# Site furniture, not listing photos: eBay ships its badges and spacer GIFs from
# the static hosts, and lazy-loading leaves a base64 pixel in `src` until scroll.
NOT_A_PHOTO = re.compile(
    r"^data:|ebaystatic\.com|/1x1|spacer|blank\.gif|sprite|logo", re.I
)


def is_placeholder(url: Optional[str]) -> bool:
    """True when a URL is site furniture or a lazy-load stand-in, not a photo."""
    if not url or not url.strip():
        return True
    return bool(NOT_A_PHOTO.search(url.strip()))


def normalize_image_url(url: Optional[str], size: int = DEFAULT_SIZE) -> Optional[str]:
    """Rewrite an eBay photo URL to a usable size.

    Two rewrites, both needed:

    * `/thumbs/` is a separate small-image path -- asking it for s-l500 returns
      the thumbnail anyway, so the segment is dropped to reach the full image.
    * `s-l<n>` is replaced with the requested size.

    Anything that is not an eBay CDN URL is returned unchanged but for the query
    string; it may still be a real photo, just not one whose size we can pick.
    Placeholders return None so a caller stores NULL rather than a grey pixel.
    """
    if is_placeholder(url):
        return None

    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    url = url.replace("http://", "https://", 1)

    # Tracking parameters change per page view and would defeat deduplication.
    url = url.split("?", 1)[0].split("#", 1)[0]

    if "i.ebayimg.com" not in url:
        return url

    url = url.replace("/thumbs/", "/", 1)

    if size not in ALLOWED_SIZES:
        size = min(ALLOWED_SIZES, key=lambda s: (abs(s - size), s))

    return SIZE_RE.sub(rf"/s-l{size}\1", url)


def best_from_srcset(srcset: Optional[str]) -> Optional[str]:
    """Pick the highest-resolution candidate out of a srcset attribute.

    Format is `url 1x, url 2x` or `url 140w, url 280w`; either way the numeric
    prefix of the descriptor orders them.
    """
    if not srcset:
        return None
    best: Optional[str] = None
    best_score = -1.0
    for candidate in srcset.split(","):
        parts = candidate.split()
        if not parts:
            continue
        url = parts[0]
        score = 1.0
        if len(parts) > 1:
            m = re.match(r"([\d.]+)", parts[1])
            if m:
                try:
                    score = float(m.group(1))
                except ValueError:
                    score = 1.0
        if score > best_score:
            best, best_score = url, score
    return best
