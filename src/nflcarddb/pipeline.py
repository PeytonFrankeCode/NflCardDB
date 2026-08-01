"""Orchestration: run the configured queries for one day and store the results."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from . import db as store
from .config import Config, QuerySpec
from .fetch import DEFAULT_UA, BlockedError, Fetcher, FetchError
from .models import Sale
from .parse_title import PARSER_VERSION as TITLE_PARSER_VERSION
from .parse_title import load_roster, parse_title
from .search import PriceBand, plan_bands, walk_query

log = logging.getLogger(__name__)

# Flush to SQLite this often so an interrupted run keeps what it already paid for.
BATCH_SIZE = 200


def yesterday() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


class ScrapeReport:
    def __init__(self, run_id: str, target_date: Optional[str]) -> None:
        self.run_id = run_id
        self.target_date = target_date
        self.seen = 0
        self.new = 0
        self.pages = 0
        self.status = "ok"
        self.error: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "target_date": self.target_date,
            "items_seen": self.seen,
            "items_new": self.new,
            "pages_fetched": self.pages,
            "status": self.status,
            "error": self.error,
        }


def run_scrape(
    config: Config,
    target_date: Optional[str] = None,
    only_query: Optional[str] = None,
    db_path: Optional[str] = None,
    save_html_dir: Optional[str] = None,
    delay_override: Optional[float] = None,
    page_budget_override: Optional[int] = None,
    dry_run: bool = False,
) -> ScrapeReport:
    target_date = target_date or yesterday()
    conn = store.connect(db_path or config.database)

    queries = config.queries
    if only_query:
        queries = [q for q in config.queries if q.id == only_query]
        if not queries:
            raise ValueError(f"no query with id {only_query!r} in config")

    run_id = store.start_run(conn, target_date)
    report = ScrapeReport(run_id, target_date)

    fetcher = Fetcher(
        delay=delay_override if delay_override is not None else config.fetch.delay,
        jitter=config.fetch.jitter,
        max_retries=config.fetch.max_retries,
        timeout=config.fetch.timeout,
        user_agent=config.fetch.user_agent or DEFAULT_UA,
        page_budget=(
            page_budget_override if page_budget_override is not None
            else config.fetch.page_budget
        ),
        save_dir=save_html_dir,
    )

    roster = load_roster(config.roster) if config.roster else None
    buffer: list[Sale] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer or dry_run:
            buffer = []
            return
        seen, new = store.upsert_sales(conn, buffer, run_id)
        store.upsert_cards(
            conn,
            [(s.item_id, parse_title(s.title, roster)) for s in buffer],
            TITLE_PARSER_VERSION,
        )
        report.seen += seen
        report.new += new
        buffer = []

    def on_segment(query_id, band: PriceBand, status, result, note) -> None:
        if not dry_run:
            store.record_segment(
                conn, run_id, f"{query_id}:{band.label}", query_id,
                band.lo, band.hi, status, result.pages, len(result.sales), note,
            )

    try:
        for query in queries:
            log.info("query %s -> %s", query.id, target_date)
            bands = plan_bands([tuple(b) for b in config.bands_for(query)])
            for sale in walk_query(
                fetcher=fetcher,
                query_id=query.id,
                keywords=query.keywords,
                category=query.category,
                bands=bands,
                target_date=target_date,
                max_pages=config.fetch.max_pages_per_segment,
                max_depth=config.fetch.max_subdivide_depth,
                items_per_page=config.fetch.items_per_page,
                extra=query.extra or None,
                on_segment=on_segment,
            ):
                buffer.append(sale)
                if len(buffer) >= BATCH_SIZE:
                    flush()
            flush()

    except BlockedError as exc:
        report.status = "partial"
        report.error = str(exc)
        log.error("stopping early: %s", exc)
    except FetchError as exc:
        report.status = "partial"
        report.error = str(exc)
        log.error("stopping early: %s", exc)
    except KeyboardInterrupt:
        report.status = "partial"
        report.error = "interrupted"
        log.warning("interrupted; flushing what we have")
    finally:
        flush()
        report.pages = fetcher.stats.requests
        if not dry_run:
            store.finish_run(
                conn, run_id, report.status, report.pages,
                report.seen, report.new, report.error,
            )
        conn.close()

    return report


def reparse_titles(
    db_path: str, roster_path: Optional[str] = None, all_rows: bool = False
) -> int:
    """Re-run the title parser, e.g. after improving its vocabularies."""
    conn = store.connect(db_path)
    roster = load_roster(roster_path) if roster_path else None

    if all_rows:
        rows = [(r[0], r[1]) for r in conn.execute("SELECT item_id, title FROM sales")]
    else:
        rows = store.unparsed_items(conn)

    count = store.upsert_cards(
        conn,
        [(item_id, parse_title(title, roster)) for item_id, title in rows],
        TITLE_PARSER_VERSION,
    )
    conn.close()
    return count


def query_spec_summary(queries: list[QuerySpec]) -> str:
    return ", ".join(f"{q.id}(cat={q.category or '-'})" for q in queries)
