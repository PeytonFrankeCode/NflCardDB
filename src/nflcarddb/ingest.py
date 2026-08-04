"""Load saved eBay search pages into the database.

The collector's automated fetching is blocked: eBay refuses the HTTP client with
403 and serves a bot-check page to a real headless browser. This path sidesteps
the question entirely. You browse eBay yourself, in your own browser, exactly
like anyone else -- then save the page and hand the file to this module.

It is slower and it is manual, but it cannot be blocked, because nothing here
talks to eBay at all. The parser does not care where the HTML came from.
"""

from __future__ import annotations

import glob
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import db as store
from .models import Sale
from .parse_listing import _money_to_cents, _parse_sold_date, parse_search_page
from .parse_title import PARSER_VERSION as TITLE_PARSER_VERSION
from .parse_title import load_roster, parse_title

log = logging.getLogger(__name__)

HTML_SUFFIXES = {".html", ".htm", ".mhtml", ".xhtml"}
JSON_SUFFIXES = {".json"}
READABLE_SUFFIXES = HTML_SUFFIXES | JSON_SUFFIXES


def _sale_from_bookmarklet(row: dict, query_id: str) -> Optional[Sale]:
    """Turn one bookmarklet record into a Sale.

    The bookmarklet reads the live page, so it captures rendered text rather
    than markup -- the same values a person sees. Parsing happens here so that
    the browser side stays as simple as possible.
    """
    item_id = str(row.get("id") or "").strip()
    title = (row.get("title") or "").strip()
    if not item_id.isdigit() or not title:
        return None

    price_cents, currency = _money_to_cents(row.get("price_text") or "")
    shipping_cents = None
    ship_text = row.get("shipping_text") or ""
    if re.search(r"free", ship_text, re.I):
        shipping_cents = 0
    elif ship_text:
        shipping_cents = _money_to_cents(ship_text)[0]

    bids = row.get("bids")
    return Sale(
        item_id=item_id,
        title=title,
        price_cents=price_cents,
        currency=currency or "USD",
        shipping_cents=shipping_cents,
        sold_date=_parse_sold_date(f"sold {row.get('sold_text') or ''}"),
        listing_format="auction" if bids else "fixed",
        bids=bids,
        best_offer=bool(row.get("best_offer")),
        url=f"https://www.ebay.com/itm/{item_id}",
        query_id=query_id,
    )


def _read_bookmarklet(path: Path, query_id: str) -> tuple[list[Sale], Optional[str]]:
    """Parse a bookmarklet capture. Returns (sales, reason-it-was-skipped)."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        return ([], f"not readable JSON: {exc}")

    if not isinstance(payload, dict) or "sales" not in payload:
        return ([], "JSON, but not a capture from the bookmarklet")

    sales = []
    for row in payload.get("sales") or []:
        if isinstance(row, dict):
            sale = _sale_from_bookmarklet(row, query_id)
            if sale:
                sales.append(sale)
    if not sales:
        return ([], "capture contained no usable listings")
    return (sales, None)


@dataclass
class ImportReport:
    files: int = 0
    parsed: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    sales_seen: int = 0
    sales_new: int = 0
    dates: set[str] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {
            "files_read": self.files,
            "files_parsed": self.parsed,
            "files_skipped": len(self.skipped),
            "sales_seen": self.sales_seen,
            "sales_new": self.sales_new,
            "dates": sorted(self.dates),
            "skipped": self.skipped[:20],
        }


def collect_html_files(paths: Iterable[str | Path]) -> list[Path]:
    """Expand files, directories and globs into a sorted list of HTML files."""
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found.extend(
                f for f in sorted(p.rglob("*"))
                if f.is_file() and f.suffix.lower() in READABLE_SUFFIXES
            )
        elif p.is_file():
            found.append(p)
        else:
            # Let a pattern through, e.g. data/html/*.html. glob.glob is used
            # rather than Path.glob because dropped paths are absolute, and
            # Path().glob raises NotImplementedError on an absolute pattern.
            found.extend(Path(m) for m in sorted(glob.glob(str(raw))))
    # Deduplicate while preserving order.
    seen: set[Path] = set()
    unique = []
    for f in found:
        resolved = f.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(f)
    return unique


def import_files(
    paths: Iterable[str | Path],
    db_path: str | Path,
    roster_path: Optional[str] = None,
    query_id: str = "imported",
) -> ImportReport:
    """Parse saved search pages and store whatever sales they contain."""
    report = ImportReport()
    files = collect_html_files(paths)
    if not files:
        return report

    conn = store.connect(db_path)
    roster = load_roster(roster_path) if roster_path else None
    run_id = store.start_run(conn, None)

    try:
        for path in files:
            report.files += 1

            if path.suffix.lower() in JSON_SUFFIXES:
                sales, why = _read_bookmarklet(path, query_id)
                if why:
                    report.skipped.append((path.name, why))
                    continue
            else:
                try:
                    html = path.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    report.skipped.append((path.name, f"could not read: {exc}"))
                    continue

                result = parse_search_page(html, query_id=query_id)
                if not result.sales:
                    low = html[:6000].lower()
                    if "sign in or register" in low:
                        reason = ("this is eBay's sign-in page -- the page was saved "
                                  "while signed out")
                    elif any(m in low for m in ("pardon our interruption", "captcha")):
                        reason = "this is a bot-check page, not search results"
                    else:
                        reason = "no listings found -- is this a sold-listings search page?"
                    report.skipped.append((path.name, reason))
                    continue
                sales = result.sales

            report.parsed += 1
            report.dates.update(s.sold_date for s in sales if s.sold_date)

            seen, new = store.upsert_sales(conn, sales, run_id)
            store.upsert_cards(
                conn,
                [(s.item_id, parse_title(s.title, roster)) for s in sales],
                TITLE_PARSER_VERSION,
            )
            report.sales_seen += seen
            report.sales_new += new
            log.info("%s -> %d sale(s), %d new", path.name, seen, new)

        store.finish_run(
            conn, run_id,
            "ok" if report.parsed else "failed",
            report.files, report.sales_seen, report.sales_new,
            None if report.parsed else "no parsable pages",
        )
    finally:
        conn.close()

    return report
