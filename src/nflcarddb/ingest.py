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
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import db as store
from .models import Sale
from .parse_listing import parse_search_page
from .parse_title import PARSER_VERSION as TITLE_PARSER_VERSION
from .parse_title import load_roster, parse_title

log = logging.getLogger(__name__)

HTML_SUFFIXES = {".html", ".htm", ".mhtml", ".xhtml"}


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
                if f.is_file() and f.suffix.lower() in HTML_SUFFIXES
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
            try:
                html = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                report.skipped.append((path.name, f"could not read: {exc}"))
                continue

            result = parse_search_page(html, query_id=query_id)
            if not result.sales:
                low = html[:6000].lower()
                if any(m in low for m in ("pardon our interruption", "captcha", "verify")):
                    reason = "this is a bot-check page, not search results"
                else:
                    reason = "no listings found -- is this a sold-listings search page?"
                report.skipped.append((path.name, reason))
                continue

            report.parsed += 1
            sales: list[Sale] = result.sales
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
