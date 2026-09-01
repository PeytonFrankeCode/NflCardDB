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
        self.seconds = 0.0
        self.blocked = 0
        self.challenge_seconds = 0.0
        self.per_query: dict = {}
        self.empty_queries: list = []

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "target_date": self.target_date,
            "engine": self.engine,
            "items_seen": self.seen,
            "items_new": self.new,
            "pages_fetched": self.pages,
            "seconds": self.seconds,
            "seconds_per_page": (round(self.seconds / self.pages, 1)
                                 if self.pages else None),
            "sales_per_query": self.per_query,
            "empty_queries": self.empty_queries,
            "bot_checks": self.blocked,
            "seconds_lost_to_bot_checks": self.challenge_seconds,
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
    items_per_page_override: Optional[int] = None,
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
    started = time.monotonic()

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
        block_media=config.fetch.block_media,
        challenge_retries=config.fetch.challenge_retries,
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
    per_query: dict[str, int] = {}

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
            before = report.seen
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
                items_per_page=(items_per_page_override
                                or config.fetch.items_per_page),
                extra=query.extra or None,
                direction=direction,
                on_segment=on_segment,
            ):
                buffer.append(sale)
                if len(buffer) >= BATCH_SIZE:
                    flush()
            flush()

            # A configured query that returns nothing is a broken query, not a
            # quiet day -- eBay answers an unusable filter with zero results
            # rather than an error, so it fails silently and the run still
            # reports "ok". That is how a query collecting 23,000 sales a day
            # was replaced with an empty one and nobody noticed until the
            # totals looked light.
            per_query[query.id] = report.seen - before
            if per_query[query.id] == 0:
                log.error(
                    "query %s returned NOTHING. Check its URL in a browser: "
                    "nflcarddb url --query %s", query.id, query.id,
                )

    except SignedOutError as exc:
        report.status = "partial"
        report.reason = "signed_out"
        report.error = str(exc)
        log.error("stopping: %s", exc)
    except BlockedError as exc:
        report.status = "partial"
        # A bot check on a deep sold search is the usual *symptom* of having no
        # session -- eBay challenges the request rather than redirecting to
        # sign-in, so it never looks like being signed out. If the warm-up saw
        # a signed-out homepage, say that instead: "wait an hour" is the wrong
        # advice and costs a day.
        if getattr(fetcher, "signed_in", None) is False:
            report.reason = "signed_out"
            report.error = (
                "eBay served bot checks, and this session is not signed in. "
                "That is almost certainly the cause: sold listings need an "
                "account. Run login.bat, then collect again."
            )
            log.error("stopping: signed out (eBay answered with bot checks)")
        else:
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
        report.seconds = round(time.monotonic() - started, 1)
        report.per_query = per_query
        report.empty_queries = [q for q, n in per_query.items() if n == 0]
        report.blocked = fetcher.stats.blocked
        report.challenge_seconds = round(fetcher.stats.challenge_seconds, 1)
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
    db_path: str, roster_path: Optional[str] = None, all_rows: bool = False,
    use_checklist: bool = True,
) -> int:
    """Re-run the title parser, e.g. after improving its vocabularies.

    When a checklist has been loaded, each parse is then looked up against it
    and whatever the title left out is filled in -- the insert set above all,
    which changes the card's identity because an insert restarts numbering at
    one. That step is deliberately after parsing rather than inside it: the
    insert names were tried as parser vocabulary first and measured worse, and
    a card the checklist cannot place is left exactly as parsed.
    """
    from . import checklist as cl

    conn = store.connect(db_path)
    roster = load_roster(roster_path) if roster_path else None

    if all_rows:
        rows = [(r[0], r[1]) for r in conn.execute("SELECT item_id, title FROM sales")]
    else:
        rows = store.unparsed_items(conn)

    known = use_checklist and bool(
        conn.execute("SELECT 1 FROM checklist_sets LIMIT 1").fetchone())

    parsed = []
    for item_id, title in rows:
        attrs = parse_title(title, roster)
        if known:
            cl.enrich(conn, attrs)
        parsed.append((item_id, attrs))

    count = store.upsert_cards(conn, parsed, TITLE_PARSER_VERSION)
    conn.close()
    return count


def card_history(
    db_path: str,
    key: str,
    grade: Optional[str] = None,
    include_offers: bool = True,
) -> dict:
    """Every sale of one card, oldest first -- the shape a trend line needs.

    Split by grade rather than pooled: a PSA 10 and a raw copy of the same card
    trade at different prices, so one line through both would describe neither.
    """
    where = ["c.card_key = ?", "s.sold_date IS NOT NULL", "s.price_cents IS NOT NULL"]
    params: list = [key]
    if not include_offers:
        where.append("s.best_offer = 0")

    conn = store.connect(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT s.sold_date, s.price_cents, s.best_offer, s.item_id, s.title,
                   s.image_url, c.card_name, c.grader, c.grade
            FROM sales s JOIN cards c USING (item_id)
            WHERE {' AND '.join(where)}
            ORDER BY s.sold_date
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    series: dict[str, list[dict]] = {}
    name = None
    image = None
    for r in rows:
        label = (f"{r['grader']} {r['grade']:g}" if r["grader"] and r["grade"] is not None
                 else (r["grader"] or "Raw"))
        if grade and label != grade:
            continue
        name = name or r["card_name"]
        image = image or r["image_url"]
        series.setdefault(label, []).append({
            "date": r["sold_date"],
            "price": round(r["price_cents"] / 100.0, 2),
            "is_ask": bool(r["best_offer"]),
            "id": r["item_id"],
        })

    return {
        "card_key": key,
        "card_name": name,
        "image": image,
        "sales": sum(len(v) for v in series.values()),
        "by_grade": {
            label: {
                "n": len(points),
                "first": points[0]["date"],
                "last": points[-1]["date"],
                "low": min(p["price"] for p in points),
                "high": max(p["price"] for p in points),
                "median": round(statistics.median(p["price"] for p in points), 2),
                "points": points,
            }
            for label, points in sorted(series.items(),
                                        key=lambda kv: -len(kv[1]))
        },
    }


def top_cards(db_path: str, days: Optional[int] = 30, limit: int = 25) -> list[dict]:
    """Cards with the most sales in a window -- what is actually trading."""
    where = ["c.card_key IS NOT NULL", "s.sold_date IS NOT NULL",
             "s.price_cents IS NOT NULL"]
    params: list = []
    if days:
        where.append("s.sold_date >= ?")
        params.append((date.today() - timedelta(days=days)).isoformat())

    conn = store.connect(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT c.card_key, COUNT(*) AS n,
                   -- The name sellers agree on most often, not whichever row
                   -- happens to sort first.
                   (SELECT c2.card_name FROM cards c2
                     WHERE c2.card_key = c.card_key AND c2.card_name IS NOT NULL
                     GROUP BY c2.card_name ORDER BY COUNT(*) DESC LIMIT 1) AS name,
                   CAST(AVG(s.price_cents) AS INTEGER) AS avg_cents,
                   MAX(s.price_cents) AS high_cents
            FROM sales s JOIN cards c USING (item_id)
            WHERE {' AND '.join(where)}
            GROUP BY c.card_key ORDER BY n DESC, high_cents DESC LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    finally:
        conn.close()

    return [
        {"card_key": r["card_key"], "card_name": r["name"], "sales": r["n"],
         "average": round(r["avg_cents"] / 100.0, 2),
         "high": round(r["high_cents"] / 100.0, 2)}
        for r in rows
    ]


def top_sales(
    db_path: str,
    days: Optional[int] = 30,
    limit: int = 20,
    include_offers: bool = True,
) -> list[dict]:
    """The biggest sales in a window, highest first.

    Best offers are included. Their price is the seller's ask rather than what
    was paid, so they place higher than they earned; `is_ask` marks them, and
    `include_offers=False` leaves them out for anyone who wants only confirmed
    amounts.
    """
    where = ["s.sold_date IS NOT NULL", "s.price_cents IS NOT NULL"]
    params: list = []
    if not include_offers:
        where.append("s.best_offer = 0")
    if days:
        where.append("s.sold_date >= ?")
        params.append((date.today() - timedelta(days=days)).isoformat())

    conn = store.connect(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT s.item_id, s.sold_date, s.title, s.price_cents, s.currency,
                   s.best_offer, s.image_url, c.player, c.year, c.set_name,
                   c.grader, c.grade
            FROM sales s LEFT JOIN cards c USING (item_id)
            WHERE {' AND '.join(where)}
            ORDER BY s.price_cents DESC LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": r["item_id"],
            "date": r["sold_date"],
            "title": r["title"],
            "price": round(r["price_cents"] / 100.0, 2),
            "currency": r["currency"],
            "is_ask": bool(r["best_offer"]),
            "player": r["player"],
            "year": r["year"],
            "set": r["set_name"],
            "grade": (f"{r['grader']} {r['grade']:g}"
                      if r["grader"] and r["grade"] is not None
                      else (r["grader"] or None)),
            "image": r["image_url"],
            "url": f"https://www.ebay.com/itm/{r['item_id']}",
        }
        for r in rows
    ]


def find_thin_days(db_path: str, ratio: float = 0.5) -> list[dict]:
    """Days whose sale count is far below the busiest days: probably truncated.

    A day cut short by the page budget still recorded status 'ok' before this
    was fixed, so it looks collected and the backfill skips it forever. eBay's
    daily volume is steady enough that a day holding a fraction of the best
    day's total is a collection failure rather than a quiet Tuesday.

    The reference is the 90th-percentile day, not the median. That matters when
    most days are truncated -- which is exactly the situation this exists to
    clean up -- because then the median IS a truncated day and nothing gets
    flagged. It leans on eBay's daily volume being steady, which it is: real
    complete days here sit within about 15% of each other.
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
    reference = counts[min(len(counts) - 1, int(round((len(counts) - 1) * 0.9)))]
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
