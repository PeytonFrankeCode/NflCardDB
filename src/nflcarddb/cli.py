"""Command line entry point: nflcarddb <command>."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

from . import db as store
from .browser import BrowserUnavailable
from .config import load_config
from .diagnose import format_report, run_diagnosis
from .fetch import BlockedError, FetchError, SignedOutError, make_fetcher
from .parse_listing import parse_search_page
from .parse_title import parse_title
from .ingest import import_files
from .images import DEFAULT_SIZE
from .pipeline import image_report, reparse_titles, run_backfill, run_scrape, yesterday
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
        chrome_profile=args.chrome_profile or None,
        dry_run=args.dry_run,
    )
    print(json.dumps(report.as_dict(), indent=2))
    if report.status == "ok":
        return 0
    # Same codes `probe` uses, so scripts can branch on the cause rather than
    # on a generic failure. run_scrape swallows these errors to keep the rows it
    # already collected, so the reason has to travel back on the report.
    return {"blocked": 4, "network": 5, "signed_out": 8,
            "interrupted": 130}.get(report.reason, 1)


def cmd_backfill(args) -> int:
    """Collect several past days, newest first, skipping ones already done."""
    config = load_config(args.config)

    def announce(day, status, result):
        if status == "skipped":
            print(f"  {day}  already collected, skipping")
        elif status == "ok":
            print(f"  {day}  {result.new:>6} new sales  ({result.pages} pages)")
        else:
            print(f"  {day}  stopped: {status}", file=sys.stderr)

    print(f"Collecting up to {args.days} day(s), newest first.")
    print("eBay keeps sold listings about 90 days, so older days are gone for")
    print("good once they age out. Expect roughly 10 minutes per day.\n")

    report = run_backfill(
        config, days=args.days, db_path=args.db, end_date=args.end_date,
        force=args.force, page_budget_per_day=args.page_budget,
        on_day=announce,
    )

    print()
    print(json.dumps(report.as_dict(), indent=2))

    if report.stopped_early == "signed_out":
        print("\nThe session expired. Run login.bat, then run this again -- "
              "the days already collected are kept.", file=sys.stderr)
        return 8
    if report.stopped_early == "blocked":
        print("\neBay asked for a human check. Everything collected so far is "
              "saved; try again in an hour or two.", file=sys.stderr)
        return 4
    if report.stopped_early:
        return 1
    return 0


def cmd_parse(args) -> int:
    config = load_config(args.config) if Path(args.config or "").exists() else None
    db_path = args.db or (config.database if config else "data/nflcarddb.sqlite")
    roster = args.roster or (config.roster if config else None)
    count = reparse_titles(db_path, roster, all_rows=args.all)
    print(f"parsed {count} title(s)")
    return 0


def cmd_images(args) -> int:
    """Report photo coverage, and resize the URLs already collected."""
    config = load_config(args.config) if Path(args.config or "").exists() else None
    db_path = args.db or (config.database if config else "data/nflcarddb.sqlite")

    report = image_report(db_path, size=args.size, upgrade=args.upgrade)
    print(json.dumps(report, indent=2))

    if report["resizable"] and not args.upgrade:
        print(f"\n{report['resizable']} photo(s) are still stored at a smaller "
              f"size. Run with --upgrade to rewrite them to {args.size}px.")
    elif args.upgrade and report["upgraded"]:
        print(f"\nRewrote {report['upgraded']} photo URL(s) to {args.size}px. "
              "Re-run publish or d1-push to send them out.")
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
        "photo": sum(1 for s in result.sales if not s.image_url),
    }
    print("\nfield coverage (lower missing is better):")
    for name, n in missing.items():
        print(f"  {name:<10} missing on {n}/{len(result.sales)}")

    photo = next((s.image_url for s in result.sales if s.image_url), None)
    if photo:
        print(f"\nexample photo: {photo}")

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
    engine = args.engine or config.fetch.engine
    profile_dir = "data/browser-profile"
    profile_directory = None
    if getattr(args, "chrome_profile", False):
        from .browser import default_chrome_profile
        from .chrome_profiles import pick_ebay_profile

        found = default_chrome_profile()
        if found:
            profile_dir = str(found)
            print(f"using your everyday Chrome profile: {found}")
            print("(Chrome must be closed)")
            chosen = pick_ebay_profile(found)
            if chosen:
                profile_directory = chosen.directory
                print(f"eBay session found in: {chosen.describe()}\n")
            else:
                print("no profile here holds eBay cookies\n")
            # Cookies only reach the browser engine.
            if not args.engine and engine == "auto":
                engine = "browser"

    fetcher = make_fetcher(
        engine=engine,
        delay=0, jitter=0, max_retries=1, save_dir=args.save_html,
        headless=not args.headed, profile_dir=profile_dir,
        profile_directory=profile_directory,
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


def _resolve_profile(args) -> Optional[str]:
    """Pick the browser profile: this project's own, or the real Chrome one."""
    if not getattr(args, "chrome_profile", False):
        return getattr(args, "profile", None) or "data/browser-profile"

    from .browser import default_chrome_profile

    found = default_chrome_profile()
    if not found:
        print("Could not find a Chrome profile on this machine. Pass --profile "
              "with its path, or drop --chrome-profile to use this project's own.",
              file=sys.stderr)
        return None
    print(f"Using your everyday Chrome profile: {found}")
    print("Chrome must be fully closed while this runs.\n")
    return str(found)


def _looks_signed_in(fetcher, navigate: bool = True) -> bool:
    """Check every open tab, not just the one we drive.

    With a real Chrome profile the user may well sign in on a different tab
    from the one this command opened, so looking only at ours reports a false
    negative. Never raises: this is a convenience check, and a wrong answer
    here must not fail the command.
    """
    def _check(html: str) -> bool:
        low = html.lower()
        return ("my ebay" in low) and ("sign in" not in low[:200000])

    context = getattr(fetcher, "_context", None)
    pages = list(getattr(context, "pages", []) or []) if context else []
    for page in pages:
        try:
            if "ebay." in (page.url or "") and _check(page.content()):
                return True
        except Exception:
            continue

    if not navigate:
        return False
    try:
        fetcher._page.goto("https://www.ebay.com/", timeout=45000,
                           wait_until="domcontentloaded")
        return _check(fetcher._page.content())
    except Exception:
        return False


def cmd_login(args) -> int:
    """Sign in once, by hand, in the browser the collector owns.

    This is what makes unattended collecting possible. Reusing the everyday
    Chrome profile cannot work: since Chrome 127 cookies are bound to the Chrome
    process that wrote them, so a Chrome launched from here cannot decrypt a
    session created by a Chrome launched from the desktop. When the same
    launcher writes *and* reads the profile that problem disappears -- which is
    exactly this profile.

    Any human verification is solved by the person sitting here, in a normal
    visible window. Nothing is bypassed; the session is simply established once
    and then reused.
    """
    from .browser import BrowserFetcher, BrowserUnavailable

    profile = _resolve_profile(args)
    if profile is None:
        return 2

    f = BrowserFetcher(delay=0, jitter=0, headless=False, warm_up=False,
                       profile_dir=profile)
    try:
        f._ensure_browser()
    except ProfileLocked as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 7
    except BrowserUnavailable as exc:
        print(f"browser engine unavailable: {exc}", file=sys.stderr)
        return 6

    try:
        landed = True
        try:
            f._page.goto("https://www.ebay.com/", timeout=60000,
                         wait_until="domcontentloaded")
        except Exception as exc:
            landed = False
            log_detail = str(exc).splitlines()[0]

        print("=" * 58)
        print("  A browser window has opened.")
        print("=" * 58)
        print()
        if landed:
            print("  1. Sign in to eBay in that window, as you normally would.")
            print("     (If you are already signed in, there is nothing to do.)")
            print("  2. Then come back here and press Enter.")
        else:
            # Navigation can fail while the window itself is perfectly usable --
            # the point is only that the profile ends up holding a session.
            print(f"  It could not open eBay for you ({log_detail}).")
            print("  That is fine -- do it by hand in that window:")
            print()
            print("  1. Click the address bar, type   ebay.com   and press Enter.")
            print("  2. Sign in if you are not already.")
            print("  3. Then come back here and press Enter.")
        print()
        print("  Your sign-in goes to eBay directly. It is not seen or")
        print("  stored by this project -- only the browser profile in")
        print(f"  {profile} keeps the session, on this PC.")
        print()
        # Poll rather than demanding a keypress: signing in can take a while
        # when eBay asks for a code or a puzzle, and people forget to come back.
        print("  Waiting for you to sign in (up to 10 minutes)...")
        print("  Tick 'Stay signed in' if eBay offers it -- it makes the")
        print("  session last far longer.")
        print()
        deadline = time.monotonic() + 600
        detected = False
        while time.monotonic() < deadline:
            if _looks_signed_in(f, navigate=False):
                detected = True
                break
            time.sleep(5)

        if not detected:
            try:
                input("  Not detected yet. Press Enter once you are signed in... ")
            except EOFError:
                pass

        print()
        if detected or _looks_signed_in(f):
            print("  Signed in. The collector will reuse this session.")
            print("  Next: run doctor.bat to see whether sold listings open up.")
            return 0
        print("  Could not confirm you are signed in.")
        print("  Run doctor.bat anyway -- this check only reads the page and can")
        print("  be wrong. The session is saved either way.")
        return 1
    finally:
        f.close()


def cmd_profiles(args) -> int:
    """List Chrome profiles and say which one holds an eBay session."""
    from .browser import default_chrome_profile
    from .chrome_profiles import list_profiles

    root = Path(args.path) if args.path else default_chrome_profile()
    if not root:
        print("No Chrome installation found on this machine.", file=sys.stderr)
        return 2

    print(f"Chrome profiles under {root}\n")
    profiles = list_profiles(root)
    if not profiles:
        print("  (none found)")
        return 1
    for p in profiles:
        mark = "  <-- signed in to eBay" if p.ebay_cookies else ""
        print(f"  {p.describe()}{mark}")

    if not any(p.ebay_cookies for p in profiles):
        print("\nNone of these hold eBay cookies. If you are signed in to eBay")
        print("in a different browser (Edge, Firefox), that session cannot be")
        print("used here -- sign in once in Chrome, then try again.")
        return 1
    return 0


def cmd_doctor(args) -> int:
    """Try every fetch method once and report exactly what eBay returns to each."""
    config = load_config(args.config)
    query = next((q for q in config.queries if q.id == args.query), None)
    if query is None:
        print(f"no query {args.query!r}; have: {[q.id for q in config.queries]}",
              file=sys.stderr)
        return 2

    profile = _resolve_profile(args)
    if profile is None:
        return 2

    url = build_url(query.keywords, query.category, page=1, items_per_page=60)
    diag = run_diagnosis(url, save_dir=args.save_html, headed=args.headed,
                         profile_dir=profile)
    print(format_report(diag))
    return 0 if diag.any_working else 1


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


def _local_sale_count(db_path) -> Optional[int]:
    """How many sales the export would send, for comparing against D1.

    Mirrors the export's own WHERE clause -- a sale with no date is not
    uploaded, so counting it here would report a mismatch that isn't one.
    """
    import sqlite3

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM sales WHERE sold_date IS NOT NULL"
            ).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def cmd_d1_push(args) -> int:
    """Upload schema and data to D1 over HTTPS. No Node, no wrangler."""
    import os

    from .api_export import export_api_sql
    from .d1_http import D1Error, apply_migrations, push_sql, verify

    token = args.token or os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token:
        print("No API token.\n", file=sys.stderr)
        print("Create one at https://dash.cloudflare.com/profile/api-tokens", file=sys.stderr)
        print("  Create Token -> Custom token -> Account | D1 | Edit\n", file=sys.stderr)
        print("Then either pass --token, or set it first:", file=sys.stderr)
        print("  set CLOUDFLARE_API_TOKEN=your_token_here", file=sys.stderr)
        return 2

    def progress(index, total, count):
        print(f"  batch {index}/{total} ({count} statements)", flush=True)

    if args.verify_only:
        try:
            state = verify(args.account_id, args.database_id, token)
        except D1Error as exc:
            print(f"\nCould not read the database: {exc}", file=sys.stderr)
            return 3
        print(json.dumps(state, indent=2))
        if not state.get("sales"):
            print("\nD1 has no sales yet -- run this without --verify-only.",
                  file=sys.stderr)
            return 1
        return 0

    try:
        if args.schema:
            print(f"Applying schema from {args.schema}...")
            sql = Path(args.schema).read_text(encoding="utf-8")
            push_sql(args.account_id, args.database_id, token, sql,
                     dry_run=args.dry_run, on_progress=progress)
            print("Schema applied.\n")

        # A database created before a column existed needs it added; the schema
        # above cannot do that, because the table is already there.
        if not args.dry_run:
            for statement in apply_migrations(args.account_id, args.database_id, token):
                print(f"Migrated: {statement}")

        if not args.schema_only:
            print("Building the upload from your local database...")
            stats = export_api_sql(args.db, args.out, since=args.since)
            print(f"  {stats['rows']} rows, {stats['bytes'] // 1024} KB\n")
            if not stats["rows"]:
                print("Nothing to upload -- collect some sales first.", file=sys.stderr)
                return 1

            print("Uploading...")
            sql = Path(args.out).read_text(encoding="utf-8")
            result = push_sql(args.account_id, args.database_id, token, sql,
                              dry_run=args.dry_run, on_progress=progress)
            print()
            print(json.dumps(result.as_dict(), indent=2))

        if args.dry_run:
            print("\nDry run -- nothing was sent.")
            return 0

        print("\nChecking what D1 now holds...")
        state = verify(args.account_id, args.database_id, token)
        print(json.dumps(state, indent=2))

        # Compare against the local database rather than trusting the upload:
        # a partial upload still exits 0 on every batch it did send.
        if not args.schema_only and not args.since:
            local = _local_sale_count(args.db)
            remote = state.get("sales")
            if local is not None and remote is not None and local != remote:
                print(f"\nHeads up: {local} sales here, {remote} in D1.",
                      file=sys.stderr)
                print("Re-run this -- every write is an upsert, so it is safe.",
                      file=sys.stderr)
                return 1
        return 0

    except D1Error as exc:
        print(f"\nUpload failed: {exc}", file=sys.stderr)
        return 3


def cmd_setup_api(args) -> int:
    """Create the database, upload the data, deploy the API -- in one go."""
    from .cloud_setup import SetupError, setup

    try:
        result = setup(args.db, label=args.label, skip_login=args.skip_login)
    except SetupError as exc:
        print(f"\nSetup stopped: {exc}", file=sys.stderr)
        return 2

    print()
    print("=" * 64)
    print("  Your API is live")
    print("=" * 64)
    print()
    for step in result.steps:
        print(f"  done: {step}")
    print()
    print("  PASTE THESE TWO INTO YOUR WEBSITE")
    print("  (Cloudflare -> your website's Pages project -> Settings ->")
    print("   Environment variables -> Add -> tick Encrypt)")
    print()
    print("  Name:  NFLCARDDB_API")
    print(f"  Value: {result.worker_url}")
    print()
    print("  Name:  NFLCARDDB_KEY")
    print(f"  Value: {result.api_key}")
    print()
    print("  Then redeploy your website -- variables only attach on a deploy.")
    print()
    print("  The key is not stored anywhere and cannot be shown again.")
    print("  Losing it just means running this again.")
    print()
    print(f"  Try it:  curl -H \"Authorization: Bearer {result.api_key}\" \\")
    print(f"             {result.worker_url}/v1/summary")
    print()

    Path("api-details.txt").write_text(
        "NFLCARDDB_API=" + (result.worker_url or "") + "\n"
        "NFLCARDDB_KEY=" + (result.api_key or "") + "\n"
        "\nPaste these into your WEBSITE's Cloudflare Pages project as\n"
        "encrypted environment variables, then redeploy it.\n"
        "\nDelete this file once you have.\n",
        encoding="utf-8",
    )
    print("  Also written to api-details.txt -- delete it once you have copied them.")
    return 0


def cmd_api_key(args) -> int:
    """Mint a key. Shown once here and never stored -- only its hash is."""
    from .api_export import new_api_key

    key, key_hash = new_api_key()
    print("=" * 62)
    print("  New API key -- copy it now, it is not recoverable")
    print("=" * 62)
    print()
    print(f"  {key}")
    print()
    print(f"  label: {args.label}")
    print(f"  hash:  {key_hash}")
    print()
    print("  Only the hash is stored, so this key cannot be recovered from")
    print("  the database. Losing it means minting another.")
    print()
    print("  To activate it, include it in the next export:")
    print(f"    nflcarddb export-api --add-key {key_hash}:{args.label}")
    print()
    print("  Use it as:  Authorization: Bearer <key>")
    print()
    print("  A key placed in website JavaScript is PUBLIC -- anyone can read")
    print("  the page source. For that, give it a small quota and treat it as")
    print("  identification, not protection. Keep real keys on a server.")
    return 0


def cmd_export_api(args) -> int:
    """Write the SQL that loads this data into Cloudflare D1."""
    from .api_export import export_api_sql

    keys = []
    for spec in args.add_key or []:
        if ":" not in spec:
            print(f"--add-key wants hash:label, got {spec!r}", file=sys.stderr)
            return 2
        key_hash, label = spec.split(":", 1)
        keys.append((key_hash.strip(), label.strip()))

    stats = export_api_sql(args.db, args.out, since=args.since, key_hashes=keys)
    print(json.dumps(stats, indent=2))
    if not stats["rows"]:
        print("\nNo rows exported -- collect some sales first.", file=sys.stderr)
        return 1
    print(f"\nNext: wrangler d1 execute nflcarddb --remote --file={stats['file']}")
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
    p.add_argument("--chrome-profile", action="store_true",
                   help="use your everyday, already-signed-in Chrome (close Chrome first)")
    p.add_argument("--dry-run", action="store_true", help="fetch and parse but do not write")
    p.set_defaults(func=cmd_scrape)

    p = sub.add_parser("backfill", help="collect several past days at once")
    p.add_argument("--days", type=int, default=30, help="how many days back (default 30)")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--end-date", help="newest day to collect (default: yesterday)")
    p.add_argument("--force", action="store_true", help="re-collect days already done")
    p.add_argument("--page-budget", type=int, help="max requests per day")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("parse", help="(re)parse titles into the cards table")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--roster")
    p.add_argument("--all", action="store_true", help="reparse every row, not just new ones")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("images", help="report photo coverage, resize stored URLs")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--size", type=int, default=DEFAULT_SIZE,
                   help=f"longest edge in pixels (default {DEFAULT_SIZE})")
    p.add_argument("--upgrade", action="store_true",
                   help="rewrite stored URLs to --size (no re-scrape needed)")
    p.set_defaults(func=cmd_images)

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
    p.add_argument("--chrome-profile", action="store_true",
                   help="use your everyday, already-signed-in Chrome (close Chrome first)")
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

    p = sub.add_parser("login", help="sign in to eBay once, in a real browser window")
    p.add_argument("--profile", default="data/browser-profile")
    p.add_argument("--chrome-profile", action="store_true",
                   help="use your everyday Chrome profile (close Chrome first)")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("profiles", help="show Chrome profiles and which is signed in to eBay")
    p.add_argument("--path", help="a Chrome User Data directory (default: auto-detect)")
    p.set_defaults(func=cmd_profiles)

    p = sub.add_parser("doctor", help="test every fetch method and report what eBay returns")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--query", default="football_singles")
    p.add_argument("--save-html", default="data/html")
    p.add_argument("--headed", action="store_true", help="show the browser window")
    p.add_argument("--profile", default="data/browser-profile")
    p.add_argument("--chrome-profile", action="store_true",
                   help="use your everyday Chrome profile (close Chrome first)")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("import", help="load eBay pages you saved yourself")
    p.add_argument("paths", nargs="+", help="HTML files, folders, or a glob")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--roster")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("d1-push", help="upload to Cloudflare D1 over HTTPS (no Node needed)")
    p.add_argument("--account-id", required=True)
    p.add_argument("--database-id", required=True)
    p.add_argument("--token", help="or set CLOUDFLARE_API_TOKEN")
    p.add_argument("--db", default="data/nflcarddb.sqlite")
    p.add_argument("--out", default="api/import.sql")
    p.add_argument("--schema", help="path to schema.sql, to create the tables first")
    p.add_argument("--schema-only", action="store_true", help="tables only, no data")
    p.add_argument("--since", help="only sales on/after this date (YYYY-MM-DD)")
    p.add_argument("--dry-run", action="store_true", help="show what would be sent")
    p.add_argument("--verify-only", action="store_true",
                   help="just report what D1 already holds, upload nothing")
    p.set_defaults(func=cmd_d1_push)

    p = sub.add_parser("setup-api", help="create, upload and deploy the API in one step")
    p.add_argument("--db", default="data/nflcarddb.sqlite")
    p.add_argument("--label", default="website")
    p.add_argument("--skip-login", action="store_true",
                   help="already signed in to Cloudflare")
    p.set_defaults(func=cmd_setup_api)

    p = sub.add_parser("api-key", help="mint an API key for the hosted API")
    p.add_argument("--label", default="website", help="what this key is for")
    p.set_defaults(func=cmd_api_key)

    p = sub.add_parser("export-api", help="write SQL to load this data into Cloudflare D1")
    p.add_argument("--db", default="data/nflcarddb.sqlite")
    p.add_argument("--out", default="api/import.sql")
    p.add_argument("--since", help="only sales on/after this date (YYYY-MM-DD)")
    p.add_argument("--add-key", action="append", metavar="HASH:LABEL",
                   help="activate a key (repeatable)")
    p.set_defaults(func=cmd_export_api)

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
    except SignedOutError as exc:
        print(f"not signed in: {exc}", file=sys.stderr)
        return 8
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
