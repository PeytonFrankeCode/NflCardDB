"""Command line entry point: nflcarddb <command>."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

from . import db as store
from .browser import BrowserUnavailable
from .config import load_config
from .fetch import BlockedError, FetchError, make_fetcher
from .parse_listing import parse_search_page
from .parse_title import parse_title
from .ingest import import_files
from .pipeline import reparse_titles, run_scrape, yesterday
from .publish import publish
from .search import PriceBand, build_url


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_scrape(args) -> int:
    config = load_config(args.config)
    report = run_scrape(
        config=config,
        target_date=args.date,
        only_query=args.query,
        db_path=args.db,
        save_html_dir=args.save_html,
        delay_override=args.delay,
        page_budget_override=args.page_budget,
        engine_override=args.engine,
        dry_run=args.dry_run,
    )
    print(json.dumps(report.as_dict(), indent=2))
    if report.status == "ok":
        return 0
    # Same codes `probe` uses, so scripts can branch on the cause rather than
    # on a generic failure. run_scrape swallows these errors to keep the rows it
    # already collected, so the reason has to travel back on the report.
    return {"blocked": 4, "network": 5, "interrupted": 130}.get(report.reason, 1)


def cmd_parse(args) -> int:
    config = load_config(args.config) if Path(args.config or "").exists() else None
    db_path = args.db or (config.database if config else "data/nflcarddb.sqlite")
    roster = args.roster or (config.roster if config else None)
    count = reparse_titles(db_path, roster, all_rows=args.all)
    print(f"parsed {count} title(s)")
    return 0


def cmd_calibrate(args) -> int:
    """Parse a saved HTML page and report what the selectors found.

    This is the tool to reach for when eBay changes its markup: save a real
    sold-search page (`nflcarddb scrape --save-html data/html` or just File >
    Save in a browser) and run it through here.
    """
    html = Path(args.html).read_text(encoding="utf-8", errors="replace")
    result = parse_search_page(html)

    print(f"strategy:      {result.strategy}")
    print(f"result count:  {result.total_results}{'+' if result.total_is_capped else ''}")
    print(f"listings found: {len(result.sales)}")
    if not result.sales:
        print(
            "\nNothing matched. The parser anchors on <a href> containing "
            "/itm/<numeric id>; check whether the saved page actually contains "
            "results (a bot-check page will not) before editing selectors."
        )
        return 1

    missing = {
        "price": sum(1 for s in result.sales if s.price_cents is None),
        "sold_date": sum(1 for s in result.sales if not s.sold_date),
        "shipping": sum(1 for s in result.sales if s.shipping_cents is None),
        "condition": sum(1 for s in result.sales if not s.condition),
    }
    print("\nfield coverage (lower missing is better):")
    for name, n in missing.items():
        print(f"  {name:<10} missing on {n}/{len(result.sales)}")

    print("\nsample:")
    for sale in result.sales[: args.limit]:
        attrs = parse_title(sale.title)
        price = f"${sale.price_cents / 100:.2f}" if sale.price_cents else "?"
        print(f"  [{sale.item_id}] {price:>10}  {sale.sold_date or '?':<10} {sale.title[:70]}")
        print(
            f"      -> player={attrs.player!r} year={attrs.year} set={attrs.set_name!r} "
            f"parallel={attrs.parallel!r} #{attrs.card_number} "
            f"{attrs.grader or ''}{attrs.grade or ''} conf={attrs.confidence}"
        )
    return 0


def cmd_probe(args) -> int:
    """Fetch a single live page and report what came back.

    One request. Use it to verify a category id or check whether you are being
    served results before committing to a full run.
    """
    config = load_config(args.config)
    query = next((q for q in config.queries if q.id == args.query), None)
    if query is None:
        print(f"no query {args.query!r}; have: {[q.id for q in config.queries]}", file=sys.stderr)
        return 2

    url = build_url(query.keywords, query.category, page=1, items_per_page=60)
    print(f"GET {url}\n")

    # One retry only: probe is a diagnostic, so it should report a dead network
    # in seconds rather than working through the full backoff ladder.
    # One retry only: probe is a diagnostic, so it should report a dead network
    # in seconds rather than working through the full backoff ladder.
    fetcher = make_fetcher(
        engine=args.engine or config.fetch.engine,
        delay=0, jitter=0, max_retries=1, save_dir=args.save_html,
        headless=not args.headed,
    )
    try:
        html = fetcher.get(url, label=f"probe_{query.id}")
    finally:
        closer = getattr(fetcher, "close", None)
        if closer:
            closer()

    engine_used = "browser" if getattr(fetcher, "switched", False) else (
        args.engine or config.fetch.engine
    )
    result = parse_search_page(html, query_id=query.id)

    print(f"engine used:    {engine_used}")
    print(f"result count:   {result.total_results}{'+' if result.total_is_capped else ''}")
    print(f"listings parsed: {len(result.sales)}\n")
    for sale in result.sales[: args.limit]:
        price = f"${sale.price_cents / 100:.2f}" if sale.price_cents else "?"
        print(f"  {price:>10}  {sale.sold_date or '?':<12} {sale.title[:80]}")
    if not result.sales:
        print("No listings parsed -- re-run with --save-html and inspect the page.")
        return 1
    return 0


def cmd_stats(args) -> int:
    conn = store.connect(args.db)
    target = args.date or yesterday()

    summary = store.daily_summary(conn, target)
    print(f"=== {target} ===")
    print(f"  sales:           {summary.get('n', 0)}")
    print(f"  best-offer rows: {summary.get('best_offers') or 0}  (price is the ask, not the sale)")
    print(f"  avg price:       ${summary.get('avg_price') or 0:,.2f}")
    print(f"  max price:       ${summary.get('max_price') or 0:,.2f}")

    total = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    parsed = conn.execute("SELECT COUNT(*) FROM cards WHERE confidence >= 0.5").fetchone()[0]
    print(f"\n  rows in db:      {total}")
    print(f"  confident parses: {parsed}")

    print("\n  recent runs:")
    for row in conn.execute(
        "SELECT run_id, target_date, status, pages_fetched, items_seen, items_new "
        "FROM scrape_runs ORDER BY started_at DESC LIMIT 5"
    ):
        print(
            f"    {row['run_id']}  {row['target_date']}  {row['status']:<8} "
            f"pages={row['pages_fetched']:<5} seen={row['items_seen']:<6} new={row['items_new']}"
        )

    print("\n  top players (all time, confident parses):")
    for row in conn.execute(
        "SELECT player, COUNT(*) n, ROUND(AVG(s.price_cents)/100.0, 2) avg_price "
        "FROM cards c JOIN sales s USING (item_id) "
        "WHERE c.player IS NOT NULL AND c.confidence >= 0.5 "
        "GROUP BY player ORDER BY n DESC LIMIT 10"
    ):
        print(f"    {row['player']:<28} {row['n']:>6} sales  avg ${row['avg_price'] or 0:,.2f}")

    conn.close()
    return 0


def cmd_export(args) -> int:
    conn = store.connect(args.db)
    sql = "SELECT * FROM v_sales"
    params: tuple = ()
    if args.date:
        sql += " WHERE sold_date = ?"
        params = (args.date,)
    sql += " ORDER BY sold_date DESC, total DESC"

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("no rows matched", file=sys.stderr)
        conn.close()
        return 1

    out = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    try:
        writer = csv.writer(out)
        writer.writerow(rows[0].keys())
        writer.writerows(tuple(r) for r in rows)
    finally:
        if args.out:
            out.close()
            print(f"wrote {len(rows)} row(s) to {args.out}", file=sys.stderr)
    conn.close()
    return 0


def cmd_import(args) -> int:
    """Load eBay pages you saved yourself, instead of fetching them."""
    config = load_config(args.config) if Path(args.config or "").exists() else None
    db_path = args.db or (config.database if config else "data/nflcarddb.sqlite")
    roster = args.roster or (config.roster if config else None)

    report = import_files(args.paths, db_path, roster)
    if not report.files:
        print("No .html files found in what you gave me.", file=sys.stderr)
        print(
            "Save an eBay sold-listings page in your browser (Ctrl+S), then pass "
            "the file or its folder to this command.",
            file=sys.stderr,
        )
        return 2

    print(json.dumps(report.as_dict(), indent=2))
    for name, reason in report.skipped[:10]:
        print(f"  skipped {name}: {reason}", file=sys.stderr)

    if not report.parsed:
        print(
            "\nNothing could be read from those files. Make sure you saved a "
            "SOLD listings search page -- the one with 'Sold' next to each "
            "price -- as 'Webpage, Complete' or 'Webpage, Single File'.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_publish(args) -> int:
    """Flatten the database into the static JSON the Pages dashboard reads."""
    meta = publish(args.db, args.out)
    print(json.dumps(meta, indent=2))
    if not meta["total_sales"]:
        print(
            "\nNote: the database is empty, so the dashboard will render its "
            "empty state. Run a scrape first.",
            file=sys.stderr,
        )
    return 0


def cmd_url(args) -> int:
    """Print the URL a query/band would hit, without fetching it."""
    config = load_config(args.config)
    for query in config.queries:
        if args.query and query.id != args.query:
            continue
        print(f"# {query.id}")
        for lo, hi in config.bands_for(query):
            print(f"  {build_url(query.keywords, query.category, 1, PriceBand(lo, hi))}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nflcarddb", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scrape", help="scrape one day of sold listings")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db", help="override the database path from config")
    p.add_argument("--date", help="ISO date to collect (default: yesterday)")
    p.add_argument("--query", help="run only this query id")
    p.add_argument("--delay", type=float, help="override seconds between requests")
    p.add_argument("--page-budget", type=int, help="override max requests for this run")
    p.add_argument("--save-html", help="directory to dump fetched pages into")
    p.add_argument("--engine", choices=["auto", "requests", "browser"],
                   help="how to fetch pages (default: from config, normally auto)")
    p.add_argument("--dry-run", action="store_true", help="fetch and parse but do not write")
    p.set_defaults(func=cmd_scrape)

    p = sub.add_parser("parse", help="(re)parse titles into the cards table")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--roster")
    p.add_argument("--all", action="store_true", help="reparse every row, not just new ones")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("calibrate", help="parse a saved HTML page and report coverage")
    p.add_argument("html")
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("probe", help="fetch one live page to verify a query")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--query", required=True)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--save-html")
    p.add_argument("--engine", choices=["auto", "requests", "browser"],
                   help="how to fetch pages (default: from config, normally auto)")
    p.add_argument("--headed", action="store_true",
                   help="show the browser window, so you can see what eBay serves")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("stats", help="summarise what is in the database")
    p.add_argument("--db", default="data/nflcarddb.sqlite")
    p.add_argument("--date")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("export", help="export sales to CSV")
    p.add_argument("--db", default="data/nflcarddb.sqlite")
    p.add_argument("--date")
    p.add_argument("--out")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="load eBay pages you saved yourself")
    p.add_argument("paths", nargs="+", help="HTML files, folders, or a glob")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--roster")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("publish", help="export static JSON for the Pages dashboard")
    p.add_argument("--db", default="data/nflcarddb.sqlite")
    p.add_argument("--out", default="site/data", help="output directory for the JSON")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("url", help="print the search URLs a config would hit")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--query")
    p.set_defaults(func=cmd_url)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BrowserUnavailable as exc:
        print(f"browser engine unavailable: {exc}", file=sys.stderr)
        return 6
    except BlockedError as exc:
        # Expected failure mode, not a crash: eBay served an interstitial.
        print(f"blocked: {exc}", file=sys.stderr)
        print(
            "\nWhat to do: if this was the plain HTTP client, the fix is the\n"
            "browser engine (--engine browser), not waiting -- eBay refused the\n"
            "request because it could tell no real browser sent it.\n"
            "If a browser was already in use, wait a couple of hours and retry\n"
            "with a longer --delay (try 5) and a smaller --page-budget.\n"
            "Already-collected rows were saved; re-running the same --date\n"
            "resumes safely.",
            file=sys.stderr,
        )
        return 4
    except FetchError as exc:
        print(f"network error: {exc}", file=sys.stderr)
        print(
            "\nCould not reach eBay after retrying. Check your connection, any\n"
            "proxy or firewall in front of it, and that ebay.com resolves. Run\n"
            "`nflcarddb url` to see the exact URL being requested.",
            file=sys.stderr,
        )
        return 5
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
