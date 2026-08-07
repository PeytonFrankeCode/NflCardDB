"""Orchestration: run the configured queries for one day and store the results."""

from __future__ import annotations

import logging
import statistics
import time
from datetime import date, timedelta
from typing import Optional

from . import db as store
from .config import Config, QuerySpec
from .fetch import (
    DEFAULT_UA,
    BlockedError,
    FetchError,
    SignedOutError,
    make_fetcher,
)
from .images import DEFAULT_SIZE, normalize_image_url
from .models import Sale
from .parse_title import PARSER_VERSION as TITLE_PARSER_VERSION
from .parse_title import load_roster, parse_title
from .search import (
    NEWEST_FIRST,
    OLDEST_FIRST,
    PriceBand,
    plan_bands,
    probe_oldest_first,
    walk_query,
)

log = logging.getLogger(__name__)

# How far back eBay keeps sold listings visible. The window a day must be
# reached inside, and the reason older days are worth collecting first.
WINDOW_DAYS = 90

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
        # Why the run stopped early, so callers (and exit codes) can distinguish
        # "eBay blocked us" from "the network is down".
        self.reason: Optional[str] = None
        self.engine: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "target_date": self.target_date,
            "engine": self.engine,
            "items_seen": self.seen,
            "items_new": self.new,
            "pages_fetched": self.pages,
            "status": self.status,
            "reason": self.reason,
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
    engine_override: Optional[str] = None,
    chrome_profile: Optional[bool] = None,
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

    use_chrome = (
        config.fetch.chrome_profile if chrome_profile is None else chrome_profile
    )
    profile_dir = "data/browser-profile"
    profile_directory = None
    if use_chrome:
        from .browser import default_chrome_profile
        from .chrome_profiles import pick_ebay_profile

        found = default_chrome_profile()
        if found:
            profile_dir = str(found)
            log.info("using your everyday Chrome profile: %s", found)
            log.info("Chrome must stay closed while this runs")
            # Chrome keeps several profiles side by side; the eBay session is in
            # exactly one of them, and it is not always Default.
            chosen = pick_ebay_profile(found)
            if chosen:
                profile_directory = chosen.directory
            else:
                log.warning("no Chrome profile here holds eBay cookies -- "
                            "is the eBay sign-in in a different browser?")
        else:
            log.warning("no Chrome profile found; using this project's own")

    # The signed-in session lives in Chrome's cookies, and only the browser
    # engine can use them -- the TLS client "auto" starts with would ignore the
    # profile entirely and get refused for being logged out. So asking for the
    # Chrome profile implies the browser engine, unless one was named outright.
    engine = engine_override or config.fetch.engine
    if use_chrome and not engine_override and engine == "auto":
        engine = "browser"
        log.info("using the browser engine, so the signed-in session applies")

    fetcher = make_fetcher(
        engine=engine,
        profile_dir=profile_dir,
        profile_directory=profile_directory,
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

    incomplete: list[str] = []

    def on_segment(query_id, band: PriceBand, status, result, note) -> None:
        if getattr(result, "ran_out", False):
            incomplete.append(f"{query_id}:{band.label}")
        if not dry_run:
            store.record_segment(
                conn, run_id, f"{query_id}:{band.label}", query_id,
                band.lo, band.hi, status, result.pages, len(result.sales), note,
            )

    # eBay has no sold-date filter, so a day is reached by paging from one end
    # of its ~90-day window. Approach from whichever end is nearer: day 85 is
    # five days of paging from the old end and eighty-five from the new one.
    direction = NEWEST_FIRST
    days_back = (date.today() - date.fromisoformat(target_date)).days
    if days_back > WINDOW_DAYS:
        log.warning(
            "%s is %d days back, past the ~%d days eBay keeps sold listings. "
            "Expect little or nothing.", target_date, days_back, WINDOW_DAYS,
        )
    elif days_back > WINDOW_DAYS / 2 and config.fetch.try_oldest_first:
        if probe_oldest_first(fetcher, queries[0].keywords, queries[0].category,
                              queries[0].extra or None):
            direction = OLDEST_FIRST
            log.info("%s is %d days back; paging from the older end", target_date,
                     days_back)
        else:
            log.warning(
                "%s is %d days back and eBay will not sort oldest-first, so it "
                "must be reached by paging through everything sold since. That "
                "may exceed the page budget.", target_date, days_back,
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
                direction=direction,
                on_segment=on_segment,
            ):
                buffer.append(sale)
                if len(buffer) >= BATCH_SIZE:
                    flush()
            flush()

    except SignedOutError as exc:
        report.status = "partial"
        report.reason = "signed_out"
        report.error = str(exc)
        log.error("stopping: %s", exc)
    except BlockedError as exc:
        report.status = "partial"
        report.reason = "blocked"
        report.error = str(exc)
        log.error("stopping early: %s", exc)
    except FetchError as exc:
        report.status = "partial"
        report.reason = "network"
        report.error = str(exc)
        log.error("stopping early: %s", exc)
    except KeyboardInterrupt:
        report.status = "partial"
        report.reason = "interrupted"
        report.error = "interrupted"
        log.warning("interrupted; flushing what we have")
    else:
        # Nothing threw, but a walk that never reached the target date collected
        # only part of the day. Recording that as 'ok' is what makes the gap
        # permanent: completed_days would skip it and the backfill never returns.
        if incomplete:
            report.status = "partial"
            report.reason = "incomplete"
            report.error = (
                f"{len(incomplete)} segment(s) never reached {target_date}: "
                + ", ".join(incomplete[:5])
                + ("..." if len(incomplete) > 5 else "")
            )
            log.warning("%s is incomplete -- %s", target_date, report.error)

    finally:
        flush()
        report.pages = fetcher.stats.requests
        if getattr(fetcher, "switched", False):
            report.engine = "browser"
        # Chromium is a real process; it has to be shut down or it outlives the run.
        closer = getattr(fetcher, "close", None)
        if closer:
            closer()
        if not dry_run:
            store.finish_run(
                conn, run_id, report.status, report.pages,
                report.seen, report.new, report.error,
            )
        conn.close()

    return report


class BackfillReport:
    def __init__(self) -> None:
        self.days_done: list[str] = []
        self.days_skipped: list[str] = []
        self.days_failed: list[tuple[str, str]] = []
        self.sales_new = 0
        self.pages = 0
        self.stopped_early: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "days_collected": self.days_done,
            "days_skipped": self.days_skipped,
            "days_failed": self.days_failed,
            "sales_new": self.sales_new,
            "pages_fetched": self.pages,
            "stopped_early": self.stopped_early,
        }


def run_backfill(
    config: Config,
    days: int,
    db_path: Optional[str] = None,
    end_date: Optional[str] = None,
    force: bool = False,
    page_budget_per_day: Optional[int] = None,
    max_minutes: Optional[float] = None,
    on_day=None,
) -> BackfillReport:
    """Collect the last `days` days, most recent first.

    Newest first on purpose: eBay drops sold listings after about 90 days, so if
    a run is interrupted the days most likely to still be there next time are
    the older ones. Days already collected are skipped, which makes this safe to
    re-run and cheap to resume.

    A block stops the whole thing rather than grinding through the remaining
    days -- once eBay is refusing, further requests only deepen the hole.

    `max_minutes` bounds the run for an unattended overnight slot. The check is
    between days, never mid-day: a day is only counted as collected when its
    whole run finished, so stopping partway would throw away the pages already
    paid for. One more day therefore overruns the budget slightly, which is the
    cheaper of the two mistakes.
    """
    report = BackfillReport()
    last = date.fromisoformat(end_date) if end_date else date.today() - timedelta(days=1)
    deadline = (time.monotonic() + max_minutes * 60) if max_minutes else None

    conn = store.connect(db_path or config.database)
    already = set() if force else store.completed_days(conn)
    conn.close()

    for offset in range(days):
        target = (last - timedelta(days=offset)).isoformat()

        if target in already:
            report.days_skipped.append(target)
            log.info("%s already collected, skipping", target)
            if on_day:
                on_day(target, "skipped", None)
            continue

        if deadline is not None and time.monotonic() >= deadline:
            report.stopped_early = "time_budget"
            log.info("time budget reached; %s and earlier left for next time", target)
            break

        log.info("collecting %s (%d of %d)", target, offset + 1, days)
        result = run_scrape(
            config, target_date=target, db_path=db_path,
            page_budget_override=page_budget_per_day,
        )
        report.pages += result.pages
        report.sales_new += result.new

        if result.status == "ok":
            report.days_done.append(target)
            if on_day:
                on_day(target, "ok", result)
            continue

        report.days_failed.append((target, result.reason or "failed"))
        if on_day:
            on_day(target, result.reason or "failed", result)

        # Blocked or signed out means every later day fails the same way.
        if result.reason in ("blocked", "signed_out", "interrupted"):
            report.stopped_early = result.reason
            log.error("stopping backfill: %s", result.reason)
            break

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


def find_thin_days(db_path: str, ratio: float = 0.5) -> list[dict]:
    """Days whose sale count is far below the busiest days: probably truncated.

    A day cut short by the page budget still recorded status 'ok' before this
    was fixed, so it looks collected and the backfill skips it forever. eBay's
    daily volume is steady enough that a day holding a fraction of the best
    day's total is a collection failure rather than a quiet Tuesday.

    Compared against the median day rather than the busiest: one unusually big
    day -- a card show weekend, a rookie debut -- would otherwise drag the bar
    above every ordinary day and flag the lot.
    """
    conn = store.connect(db_path)
    try:
        rows = [
            (r[0], r[1]) for r in conn.execute(
                "SELECT sold_date, COUNT(*) FROM sales WHERE sold_date IS NOT NULL "
                "GROUP BY sold_date ORDER BY sold_date"
            )
        ]
    finally:
        conn.close()

    if len(rows) < 3:
        return []

    counts = sorted(n for _, n in rows)
    reference = int(statistics.median(counts))
    floor = reference * ratio

    return [
        {"day": day, "sales": n, "expected": reference,
         "fraction": round(n / reference, 2)}
        for day, n in rows if n < floor
    ]


def mark_for_recollection(db_path: str, days: list[str]) -> int:
    """Clear the 'ok' runs for these days so the backfill collects them again.

    The sales already stored are left alone -- item_id is the primary key, so
    re-collecting merges rather than duplicates, and a day that turns out fine
    loses nothing.
    """
    if not days:
        return 0
    conn = store.connect(db_path)
    try:
        placeholders = ",".join("?" * len(days))
        cursor = conn.execute(
            f"UPDATE scrape_runs SET status = 'partial', "
            f"error = COALESCE(error, 'marked incomplete: too few sales') "
            f"WHERE status = 'ok' AND target_date IN ({placeholders})",
            days,
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def coverage_report(db_path: str, days: int = 90, minutes_per_day: float = 10.0) -> dict:
    """Which of the last `days` are collected, and what is left to do.

    The window is 90 days because that is roughly how long eBay keeps sold
    listings visible. Days older than that are not "missing" in any actionable
    sense -- they are gone, and no amount of running the backfill brings them
    back -- so they are reported separately from days still worth collecting.
    """
    conn = store.connect(db_path)
    try:
        done = store.completed_days(conn)
    finally:
        conn.close()

    yesterday = date.today() - timedelta(days=1)
    window = [(yesterday - timedelta(days=n)).isoformat() for n in range(days)]

    have = [d for d in window if d in done]
    missing = [d for d in window if d not in done]

    return {
        "window_days": days,
        "collected": len(have),
        "missing": len(missing),
        "complete": not missing,
        "oldest_collected": min(done) if done else None,
        "newest_collected": max(done) if done else None,
        "next_up": missing[0] if missing else None,
        "missing_days": missing,
        "estimated_hours_left": round(len(missing) * minutes_per_day / 60, 1),
        # Collected days that have aged out of the window: kept, but eBay would
        # no longer serve them, so they can never be re-collected if lost.
        "outside_window": len([d for d in done if d < window[-1]]),
    }


def image_report(db_path: str, size: int = DEFAULT_SIZE, upgrade: bool = False) -> dict:
    """Report photo coverage, and optionally resize the URLs already stored.

    Resizing needs no re-scrape: the size lives in the filename, so the 140px
    thumbnail captured off a results page rewrites to a 500px photo of the same
    listing. Rows collected before that rewrite existed keep their thumbnails
    until this runs.
    """
    conn = store.connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        with_url = conn.execute(
            "SELECT COUNT(*) FROM sales WHERE image_url IS NOT NULL AND image_url != ''"
        ).fetchone()[0]

        rows = conn.execute(
            "SELECT item_id, image_url FROM sales "
            "WHERE image_url IS NOT NULL AND image_url != ''"
        ).fetchall()

        changed = [
            (new, item_id)
            for item_id, url in rows
            if (new := normalize_image_url(url, size=size)) and new != url
        ]

        if upgrade and changed:
            conn.executemany(
                "UPDATE sales SET image_url = ?, updated_at = ? WHERE item_id = ?",
                [(new, store.utcnow(), item_id) for new, item_id in changed],
            )
            conn.commit()

        sample = conn.execute(
            "SELECT image_url FROM sales WHERE image_url IS NOT NULL "
            "AND image_url != '' LIMIT 1"
        ).fetchone()

        return {
            "sales": total,
            "with_photo": with_url,
            "coverage": round(with_url / total, 3) if total else 0.0,
            "resizable": len(changed),
            "upgraded": len(changed) if upgrade else 0,
            "size": size,
            "example": sample[0] if sample else None,
        }
    finally:
        conn.close()


def query_spec_summary(queries: list[QuerySpec]) -> str:
    return ", ".join(f"{q.id}(cat={q.category or '-'})" for q in queries)
