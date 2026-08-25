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
from .browser import BlockedByWindows, BrowserUnavailable, ProfileLocked
from .config import load_config
from .diagnose import bisect_url, format_bisect, format_report, run_diagnosis
from .fetch import BlockedError, FetchError, SignedOutError, make_fetcher
from .parse_listing import parse_search_page
from .parse_title import load_roster, parse_title
from .ingest import import_files
from .images import DEFAULT_SIZE
from .pipeline import (
    coverage_report,
    find_thin_days,
    image_report,
    mark_for_recollection,
    top_sales,
    top_cards,
    card_history,
    reparse_titles,
    run_backfill,
    run_scrape,
    yesterday,
)
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
        items_per_page_override=args.items_per_page,
        dry_run=args.dry_run,
    )
    print(json.dumps(report.as_dict(), indent=2))

    # Loud, because a silent zero is what this exists to catch: eBay answers a
    # filter it cannot use with no results rather than an error, so a broken
    # query looks exactly like a quiet day.
    if report.empty_queries:
        print(file=sys.stderr)
        print("=" * 62, file=sys.stderr)
        for name in report.empty_queries:
            print(f"  QUERY '{name}' RETURNED NOTHING", file=sys.stderr)
        print("=" * 62, file=sys.stderr)
        print("\nThat is a broken query, not a quiet day. Check it:", file=sys.stderr)
        print(f"  nflcarddb url --query {report.empty_queries[0]}", file=sys.stderr)
        print("and paste that URL into a browser. If eBay shows no results "
              "there\neither, the filters in config/queries.yml are wrong.",
              file=sys.stderr)

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
        max_minutes=args.max_minutes, on_day=announce,
    )

    print()
    print(json.dumps(report.as_dict(), indent=2))

    if report.stopped_early == "time_budget":
        # Not a failure: the run did what it was allowed to, and the next one
        # resumes. Exiting non-zero here would make every scheduled run "fail".
        print(f"\nStopped after {args.max_minutes} minutes, as instructed. "
              "Run again to continue -- collected days are skipped.")
        return 0
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


def cmd_coverage(args) -> int:
    """Report how much of eBay's 90-day window is collected."""
    config = load_config(args.config) if Path(args.config or "").exists() else None
    db_path = args.db or (config.database if config else "data/nflcarddb.sqlite")

    report = coverage_report(db_path, days=args.days)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    done, total = report["collected"], report["window_days"]
    bar_width = 40
    filled = round(bar_width * done / total) if total else 0
    print(f"[{'#' * filled}{'.' * (bar_width - filled)}]  {done}/{total} days")
    print()

    if report["complete"]:
        print("Every day eBay still has is collected. Nothing left to catch up on.")
    else:
        print(f"{report['missing']} day(s) still to collect — roughly "
              f"{report['estimated_hours_left']} hours of collecting.")
        print(f"Next up: {report['next_up']}")
        print("\nThe oldest of these age out of eBay's window as time passes, "
              "so they are the ones worth collecting first.")

    if report["outside_window"]:
        print(f"\nYou also hold {report['outside_window']} day(s) older than "
              f"{args.days} days. eBay no longer serves those, so that data now "
              "exists only here.")
    return 0


def cmd_top(args) -> int:
    """The biggest sales in a window."""
    config = load_config(args.config) if Path(args.config or "").exists() else None
    db_path = args.db or (config.database if config else "data/nflcarddb.sqlite")

    rows = top_sales(db_path, days=args.days, limit=args.limit,
                     include_offers=args.include_offers)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        print("No priced sales in that window.")
        return 1

    window = f"the last {args.days} days" if args.days else "the whole dataset"
    print(f"Biggest sales in {window}:\n")
    for row in rows:
        flag = "  (ASK, sold for less)" if row["is_ask"] else ""
        print(f"  ${row['price']:>12,.2f}  {row['date']}  {(row['grade'] or 'Raw'):<9} "
              f"{row['title'][:64]}{flag}")

    if not args.include_offers:
        print("\nBest offers are excluded: their price is the seller's ask, not "
              "what was paid.\nAdd --include-offers to see them, labelled.")
    return 0


def cmd_card(args) -> int:
    """One card's sales over time, or the cards that are actually trading."""
    config = load_config(args.config) if Path(args.config or "").exists() else None
    db_path = args.db or (config.database if config else "data/nflcarddb.sqlite")

    if not args.key:
        rows = top_cards(db_path, days=args.days, limit=args.limit)
        if args.json:
            print(json.dumps(rows, indent=2))
            return 0
        if not rows:
            print("No cards identified yet. Collect some sales, then run "
                  "`nflcarddb parse --all`.", file=sys.stderr)
            return 1
        window = f"the last {args.days} days" if args.days else "everything"
        print(f"Most-traded cards in {window}:\n")
        for r in rows:
            print(f"  {r['sales']:>4} sales  avg ${r['average']:>9,.2f}  "
                  f"{(r['card_name'] or r['card_key'])[:52]}")
            print(f"        {r['card_key']}")
        print("\nPrice history for one:  nflcarddb card --key <card_key>")
        return 0

    history = card_history(db_path, args.key, grade=args.grade)
    if args.json:
        print(json.dumps(history, indent=2))
        return 0
    if not history["sales"]:
        print(f"No sales found for {args.key}", file=sys.stderr)
        return 1

    print(f"{history['card_name'] or args.key}")
    print(f"{history['card_key']}  --  {history['sales']} sale(s)\n")
    for label, block in history["by_grade"].items():
        print(f"  {label:<10} {block['n']:>4} sales   "
              f"median ${block['median']:>9,.2f}   "
              f"${block['low']:,.2f} - ${block['high']:,.2f}   "
              f"{block['first']} to {block['last']}")
    return 0


def cmd_roster(args) -> int:
    """Learn player names from the titles already collected."""
    from .roster import build, write

    config = load_config(args.config) if Path(args.config or "").exists() else None
    db_path = args.db or (config.database if config else "data/nflcarddb.sqlite")

    names = build(db_path, min_contexts=args.min_contexts,
                  min_sightings=args.min_sightings)
    if not names:
        print("Not enough collected data to learn names from yet.", file=sys.stderr)
        return 1

    path = write(names, args.out)
    print(f"Learned {len(names)} player names -> {path}\n")
    print("Most confident (seen across the most different sets):")
    for name, sightings, contexts in names[:12]:
        print(f"  {name:<28} {sightings:>6,} listings, {contexts:>3} sets")
    print(f"\n  ...and {max(0, len(names) - 12)} more")

    if args.no_apply:
        print(f"\nNot applied. To use it, add this line to {args.config}:")
        print(f"  roster: {path}")
        print("then run:  nflcarddb parse --all")
        return 0

    if enable_roster(args.config, path):
        print(f"\nTurned on in {args.config}.")
    else:
        print(f"\nCould not edit {args.config}. Add this line yourself:")
        print(f"  roster: {path}")
        return 1

    from .audit import coverage

    before = coverage(db_path)
    print("\nRe-reading every title with the roster. This takes a minute.")
    reparse_titles(db_path, str(path), all_rows=True)
    after = coverage(db_path)

    # The point of the roster is fewer, bigger groups over the same sales: a
    # name that stopped varying stops splitting one card into several. Printing
    # both ends is the difference between a measured improvement and a claimed
    # one.
    print("\nWhat changed")
    print("=" * 58)
    _delta("sales matched to a card", before.get("with_key", 0),
           after.get("with_key", 0))
    _delta("cards seen more than once",
           before.get("groups", 0) - before.get("singleton_groups", 0),
           after.get("groups", 0) - after.get("singleton_groups", 0))
    _delta("sales sharing a card", before.get("grouped_sales", 0),
           after.get("grouped_sales", 0))
    _rate_delta("cards seen only once",
                before.get("singleton_groups", 0), before.get("groups", 0),
                after.get("singleton_groups", 0), after.get("groups", 0))

    # Raw group count is NOT reported as better or worse, and that is deliberate.
    # A run that matches previously-unmatched sales has to put them somewhere, so
    # the count rises for a good reason and falls for a good reason, and the two
    # are indistinguishable without knowing whether coverage moved. Labelling it
    # "worse" once hid a genuine improvement behind a red number.
    print(f"\n  (distinct cards {before.get('groups', 0):,} -> "
          f"{after.get('groups', 0):,} -- neither good nor bad on its own, "
          f"since\n   newly-matched sales create groups of their own)")
    print("\nThe rate is the honest one: a smaller share of cards seen only once")
    print("means sales that were split apart are now one price history.")
    return 0


def _delta(label: str, before: int, after: int, lower_is_better: bool = False) -> None:
    change = after - before
    if change == 0:
        note = "no change"
    else:
        good = (change < 0) if lower_is_better else (change > 0)
        note = f"{change:+,}  {'better' if good else 'worse'}"
    print(f"  {label:<28} {before:>9,} -> {after:>9,}   {note}")


def _rate_delta(label: str, before_n: int, before_d: int,
                after_n: int, after_d: int) -> None:
    """A share rather than a count, for anything the population size distorts."""
    if not before_d or not after_d:
        return
    before, after = before_n / before_d, after_n / after_d
    change = after - before
    note = "no change" if abs(change) < 0.0005 else \
        f"{change * 100:+.1f} pts  {'better' if change < 0 else 'worse'}"
    print(f"  {label:<28} {before:>8.1%} -> {after:>9.1%}   {note}")


def enable_roster(config_path: Optional[str], roster_path) -> bool:
    """Point the config at the roster just built."""
    return enable_setting(config_path, "roster", roster_path)


def enable_setting(config_path: Optional[str], key: str, value) -> bool:
    """Set a top-level config key to a path, editing the file in place.

    A file the user has to remember to edit is a file that stays unedited, and
    then the list exists while nothing reads it -- which looks exactly like the
    list not working. The line is written as one comment-free assignment so
    re-running this is idempotent rather than additive.
    """
    path = Path(config_path or "")
    if not path.exists():
        return False

    line = f"{key}: {Path(value).as_posix()}"
    try:
        original = path.read_text(encoding="utf-8")
        out, replaced = [], False
        for raw in original.splitlines():
            stripped = raw.lstrip("# ").rstrip()
            # Both the shipped commented example and a previous run's line.
            if stripped.startswith(f"{key}:") and not replaced:
                out.append(line)
                replaced = True
            else:
                out.append(raw)
        if not replaced:
            # No line to rewrite, commented or otherwise: append one rather
            # than reporting failure, so a config predating the setting still
            # gets it.
            out.append(line)
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def cmd_facets(args) -> int:
    """Harvest eBay's own card taxonomy from its search sidebar.

    Every vocabulary in the title parser is a list somebody maintains, and it is
    wrong the day a product ships. eBay classifies the same listings itself and
    exposes the result as search facets, so this reads them instead of guessing.
    """
    from .facets import (HARVESTER_VERSION, as_vocabulary, bucket_of,
                         drillable, harvest, load_store, merge, save_store)

    config = load_config(args.config)
    queries = ([q for q in config.queries if q.id == args.query] if args.query
               else config.queries)
    if not queries:
        print(f"no query {args.query!r}; have: {[q.id for q in config.queries]}",
              file=sys.stderr)
        return 2

    store_path = Path(args.out)
    # Facets accumulate: one search shows only what eBay chose to render for
    # it, so the vocabulary is built up across queries and days.
    accumulated = {} if args.fresh else load_store(store_path)
    if store_path.exists() and not accumulated and not args.fresh:
        print("(existing file was an older format; starting over)")

    fetcher = make_fetcher(
        engine=args.engine or config.fetch.engine,
        delay=config.fetch.delay, jitter=config.fetch.jitter, max_retries=2,
        save_dir=args.save_html, headless=not args.headed,
        profile_dir="data/browser-profile",
    )
    pages = 0
    baselines: dict[str, Optional[int]] = {}
    try:
        for query in queries:
            url = build_url(query.keywords, query.category, page=1,
                            items_per_page=60)
            print(f"GET {url}")
            html = fetcher.get(url, label=f"facets_{query.id}")
            baselines[query.id] = parse_search_page(
                html, query_id=query.id).total_results
            found = harvest(html)
            if not found:
                print("  no facets found on this page")
                continue
            pages += 1
            merge(accumulated, found)
            print(f"  {baselines[query.id] or '?'} results; "
                  + ", ".join(f"{bucket_of(k)} {len(v)}"
                              for k, v in sorted(found.items())
                              if not bucket_of(k).startswith("other:")))
        # A results page renders only its top handful of values per aspect, so
        # one pass gives the eight most-listed sets rather than the sets. The
        # way to a full list is to narrow the search and ask again: inside
        # Season=2025 the Set facet lists 2025's sets.
        # Stages run in order and each recomputes its targets from everything
        # harvested so far, so "seasons,sets" means: narrow by each season to
        # discover that season's sets, then narrow by each of those sets to
        # discover its parallels. One stage alone cannot reach the parallels,
        # because a set has to be known before it can be searched within.
        base = queries[0]
        stopped = False
        # The unfiltered count for the query being drilled. Every narrowed
        # search should return fewer than this; one that does not was not
        # narrowed at all.
        baseline = baselines.get(base.id)
        unfiltered = 0
        checked = 0
        for stage in [s.strip() for s in (args.drill or "").split(",") if s.strip()]:
            if stopped:
                break
            targets = drillable(accumulated, stage, limit=args.drill_limit)
            if not targets:
                print(f"\nNothing to drill for {stage!r} yet.")
                continue
            print(f"\nNarrowing by {len(targets)} {stage} value(s)...")
            for aspect, value in targets:
                if pages >= args.budget:
                    print(f"  stopped at the {args.budget}-request budget; "
                          f"run again to go further")
                    stopped = True
                    break
                # `_dcat` alongside `_sacat`: eBay's own facet links carry it,
                # and without it an aspect filter is ignored rather than
                # rejected -- the page comes back unfiltered and identical, so
                # sixty drill requests harvested nothing new and looked like a
                # thin category rather than a broken URL.
                extra = {aspect: value}
                if base.category:
                    extra["_dcat"] = base.category
                url = build_url(base.keywords, base.category, page=1,
                                items_per_page=60, extra=extra)
                try:
                    html = fetcher.get(url, label=f"facets_{aspect}_{value}"[:60])
                except (BlockedError, FetchError) as exc:
                    print(f"  {value}: {type(exc).__name__}, stopping here")
                    stopped = True
                    break
                pages += 1

                # Whether the filter actually applied. An ignored filter is
                # silent, so it is checked rather than assumed.
                narrowed = parse_search_page(html, query_id=base.id).total_results
                if baseline and narrowed:
                    checked += 1
                    if narrowed >= baseline:
                        unfiltered += 1

                before = sum(len(v) for v in accumulated.values())
                merge(accumulated, harvest(html))
                gained = sum(len(v) for v in accumulated.values()) - before
                if gained:
                    print(f"  {aspect}={value}: +{gained} new")
        if checked:
            print(f"\n  filter check: {checked - unfiltered} of {checked} narrowed "
                  f"searches came back smaller")
        if unfiltered:
            print(f"\n  WARNING: {unfiltered} narrowed search(es) came back no "
                  f"smaller than the\n  unfiltered one, so eBay ignored the "
                  f"filter. Drilling cannot deepen\n  the vocabulary until "
                  f"that is fixed -- send this to Claude.")
    finally:
        closer = getattr(fetcher, "close", None)
        if closer:
            closer()

    if not accumulated:
        print("\nNothing harvested. eBay's sidebar markup or URL format has "
              "moved.\nRe-run with --save-html and send the saved page to Claude.",
              file=sys.stderr)
        return 1

    save_store(accumulated, store_path)

    vocab = as_vocabulary(accumulated, min_count=args.min_count)
    print(f"\nHarvested by {HARVESTER_VERSION} from {pages} page(s) -> {store_path}")
    print("=" * 58)
    for bucket, names in sorted(vocab.items()):
        print(f"  {bucket:<16} {len(names):>6,}   e.g. {', '.join(names[:4])}")

    print()
    print("This is eBay's classification of the same listings, not a guess.")
    print("Run it again after each product release and it stays current.")
    return 0


def cmd_inserts(args) -> int:
    """Propose insert-set names learned from collected titles."""
    from .parse_title import load_inserts, register_inserts
    from .roster import build_inserts, write_inserts

    config = load_config(args.config) if Path(args.config or "").exists() else None
    db_path = args.db or (config.database if config else "data/nflcarddb.sqlite")
    roster_path = args.roster or (config.roster if config else None)

    if not roster_path or not Path(roster_path).exists():
        print("A roster is needed first -- the test for an insert is how many "
              "different\nplayers it appears beside, and that needs to know who "
              "the players are.\nRun `nflcarddb roster` (or names.bat).\n",
              file=sys.stderr)
        return 1

    roster = load_roster(roster_path)
    rows = build_inserts(db_path, roster, max_contexts=args.max_contexts,
                         min_sightings=args.min_sightings,
                         min_players=args.min_players)
    if not rows:
        print("No insert names met the evidence bar. Collect more and retry.")
        return 0

    path = write_inserts(rows, args.out)
    print(f"Proposed {len(rows)} insert names -> {path}\n")
    print("Strongest first, by how many different players they appear beside:")
    for row in rows[:15]:
        print(f"  {row['name']:<26} {row['sightings']:>5,} listings, "
              f"{row['players']:>3} players, {row['where']}")
    if len(rows) > 15:
        print(f"\n  ...and {len(rows) - 15} more in the file.")

    print()
    print("=" * 58)
    print("READ THE FILE BEFORE TURNING IT ON. A name that is not really an")
    print("insert SPLITS a card between sellers who typed the word and sellers")
    print("who did not -- worse than the merging this fixes, because it breaks")
    print("cards that already group correctly. Delete any line you doubt.")

    if args.apply:
        if enable_setting(args.config, "inserts", path):
            print(f"\nTurned on in {args.config}. Re-reading every title.")
            register_inserts(load_inserts(path))
            reparse_titles(db_path, roster_path, all_rows=True)
            print("Done.")
        else:
            print(f"\nCould not edit {args.config}. Add this line yourself:")
            print(f"  inserts: {Path(path).as_posix()}")
            return 1
    else:
        print("\nWhen you are happy with it:  nflcarddb inserts --apply")
    return 0


def cmd_audit(args) -> int:
    """What can be measured about parsing quality without labelling anything."""
    from .audit import audit

    config = load_config(args.config) if Path(args.config or "").exists() else None
    db_path = args.db or (config.database if config else "data/nflcarddb.sqlite")

    report = audit(db_path)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    if not report.get("cards"):
        print("No parsed cards yet.", file=sys.stderr)
        return 1

    if not report["with_key"]:
        # Sales collected before card identity existed have no key until the
        # parser is re-run over them. Say which command, rather than reporting
        # 0% and letting it read as "the matching does not work".
        print("None of your sales have been matched to a card yet.\n",
              file=sys.stderr)
        print("That is a one-off backfill, not a fault -- these were collected "
              "before\ncard matching existed. Run:\n", file=sys.stderr)
        print("  nflcarddb parse --all\n", file=sys.stderr)
        print("or just double-click accuracy.bat, which does it for you.",
              file=sys.stderr)
        return 1

    print("How much of the data is identified")
    print("=" * 58)
    versions = report.get("parser_versions") or []
    if versions:
        print(f"  read by parser        {', '.join(versions):>9}")
    print(f"  sales parsed          {report['cards']:>9,}")
    print(f"  given a card_key      {report['with_key']:>9,}  "
          f"({report['key_rate']:.1%})")
    print(f"  no key (too unclear)  {report['without_key']:>9,}")
    print(f"  distinct cards        {report['groups']:>9,}")
    print(f"  seen more than once   {report['groups'] - report['singleton_groups']:>9,}")
    print()
    print("  confidence spread")
    for bucket, n in report["confidence_buckets"].items():
        bar = "#" * round(40 * n / report["cards"])
        print(f"    {bucket}  {n:>8,}  {bar}")

    print()
    print("Errors the data admits to")
    print("=" * 58)
    print(f"  groups naming different players   {report['contradictory_groups']:>6,}")
    print(f"  groups with 20x+ price spread     {report['wide_spread_groups']:>6,}")
    print(f"  -> at least {report['known_bad_rate']:.2%} of keyed sales are grouped wrong")
    print()
    print(f"  grouped right, name spelled several ways  {report['messy_name_groups']:>6,}")
    print("  (cosmetic: the price history is correct, the displayed name varies)")

    split = report.get("number_split") or {}
    if split.get("recoverable_sales") or split.get("ambiguous_sales"):
        print()
        print("Sales split apart by a missing card number")
        print("=" * 58)
        print(f"  could be rejoined safely          {split['recoverable_sales']:>6,}")
        print(f"  genuinely ambiguous               {split['ambiguous_sales']:>6,}")
        print("  (one card owns two keys: numbered and un-numbered. Where only")
        print("   one number ever appears for that player in that set, an")
        print("   un-numbered sale can only be that card.)")
        for row in split["examples"][:5]:
            print(f"\n  {row['year']} {row['set_name']} {row['player']} #{row['number']}")
            print(f"    {row['joined']} sales grouped, {row['stranded']} stranded "
                  f"for want of the number")

    for row in report["examples"]["contradictory"]:
        print(f"\n  {row['card_key']}  ({row['sales']} sales)")
        print(f"    names: {', '.join(row['players'])}")
        # The titles, because a key alone cannot say what went wrong: `n1` looks
        # identical whether it came from "#1/1" or "#1 OVERALL PICK".
        for title in row.get("titles", []):
            print(f"      | {title[:72]}")
    for row in report["examples"]["messy_names"]:
        print(f"\n  {row['card_key']}  ({row['sales']} sales, grouped correctly)")
        print(f"    name read as: {', '.join(row['variants'])}")
    for row in report["examples"]["wide_spread"]:
        print(f"\n  {row['card_key']}  {row['grade']}  ({row['sales']} sales)")
        print(f"    median ${row['median']:,.2f} but one at ${row['high']:,.2f} "
              f"({row['ratio']}x)")

    print()
    print("=" * 58)
    print("That is a FLOOR, not an accuracy figure. It counts only groups that")
    print("contradict themselves; a group can be wrong and look perfectly")
    print("consistent. For a real percentage:  nflcarddb review")
    return 0


def cmd_vision(args) -> int:
    """Read listing photos and compare what they say with what the titles said.

    Report-only on purpose. Photo reading is unproven on real eBay photos --
    angled slabs, glare, a label 80 pixels tall -- and it is slow enough that
    running it over a day's collection is a decision, not a default. So it
    measures itself first: agreement, blanks filled, and disagreements, on a
    sample. Wiring it into collection is worth doing once those numbers say it
    is worth doing.
    """
    from .vision import OcrUnavailable, attrs_from_lines, fetch_image, \
        rapidocr_reader, reconcile

    config = load_config(args.config) if Path(args.config or "").exists() else None
    db_path = args.db or (config.database if config else "data/nflcarddb.sqlite")
    roster_path = args.roster or (config.roster if config else None)
    roster = load_roster(roster_path) if roster_path and Path(roster_path).exists() else None

    if roster is None:
        print("No roster, so names cannot be read off a label -- OCR returns "
              "one\nunbroken run of letters and the roster is what splits it.\n"
              "Run `nflcarddb roster` (or names.bat) first.\n", file=sys.stderr)

    conn = store.connect(db_path)
    try:
        clause = "c.card_key IS NULL" if args.unclear else "c.card_key IS NOT NULL"
        rows = conn.execute(
            f"""
            SELECT s.item_id, s.title, s.image_url
            FROM sales s JOIN cards c USING (item_id)
            WHERE s.image_url IS NOT NULL AND {clause}
            ORDER BY RANDOM() LIMIT ?
            """,
            (args.limit,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("No sales with a photo to read.", file=sys.stderr)
        return 1

    try:
        read = rapidocr_reader()
    except OcrUnavailable as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2

    stats = {"read": 0, "no_label": 0, "failed": 0,
             "agreed": 0, "filled": 0, "conflicted": 0}
    examples: list[tuple[str, object]] = []

    print(f"Reading {len(rows)} photos. Roughly a second each.\n")
    for i, row in enumerate(rows, 1):
        try:
            image = fetch_image(row["image_url"], args.cache)
            photo = attrs_from_lines(read(image), roster)
        except Exception as exc:      # a dead photo URL is ordinary, not fatal
            stats["failed"] += 1
            logging.debug("photo %s: %s", row["item_id"], exc)
            continue

        if not photo.confidence:
            # eBay drops listing photos after ~90 days, and an ungraded card
            # has no label to read in the first place.
            stats["no_label"] += 1
            continue

        stats["read"] += 1
        reading = reconcile(parse_title(row["title"], roster), photo)
        if reading.agreed:
            stats["agreed"] += 1
        if reading.filled:
            stats["filled"] += 1
        if reading.conflicts:
            stats["conflicted"] += 1
            if len(examples) < 8:
                examples.append((row["title"], reading))
        elif args.unclear and reading.filled and len(examples) < 8:
            examples.append((row["title"], reading))

        if i % 25 == 0:
            print(f"  {i}/{len(rows)}...")

    print()
    print("What the photos said")
    print("=" * 58)
    print(f"  photos tried            {len(rows):>6,}")
    print(f"  label read              {stats['read']:>6,}")
    print(f"  no readable label       {stats['no_label']:>6,}   "
          f"(ungraded, or the photo is gone)")
    print(f"  photo could not load    {stats['failed']:>6,}")
    if stats["read"]:
        print()
        print(f"  agreed with the title   {stats['agreed']:>6,}   "
              f"({stats['agreed'] / stats['read']:.0%} of labels read)")
        print(f"  filled in a blank       {stats['filled']:>6,}")
        print(f"  contradicted the title  {stats['conflicted']:>6,}   "
              f"({stats['conflicted'] / stats['read']:.0%})")

    for title, reading in examples:
        print(f"\n  {title[:64]}")
        for name, ours, theirs in reading.conflicts:
            print(f"    {name}: title said {ours!r}, card says {theirs!r}")
        if reading.filled:
            print(f"    photo supplied: {', '.join(reading.filled)}")

    print()
    print("=" * 58)
    print("Nothing was saved. This measures whether reading photos is worth")
    print("doing before it is wired into collection.")
    return 0


def cmd_review(args) -> int:
    """Draw a sample to check by hand, or score one that has been checked."""
    from .review import draw_sample, score, write_sample

    if args.score:
        try:
            result = score(args.score)
        except ValueError as exc:
            print(f"\n{exc}", file=sys.stderr)
            return 2

        print(json.dumps(result, indent=2))
        low, high = result["range"]
        print()
        print("=" * 58)
        print(f"  ACCURACY: {result['accuracy']:.1%}  "
              f"(somewhere between {low:.1%} and {high:.1%})")
        print("=" * 58)
        print(f"\n  {result['correct']} right, {result['wrong']} wrong, "
              f"out of {result['reviewed']} judged")
        if result["not_reviewed"]:
            print(f"  {result['not_reviewed']} row(s) left blank and not counted")
        if result["margin_of_error"] > 0.05:
            print(f"\n  That range is wide because the sample is small. Review "
                  f"more rows\n  to narrow it -- 400 gets you to about +/-5%.")
        if result["wrong_examples"]:
            print("\n  Ones marked wrong:")
            for row in result["wrong_examples"]:
                print(f"    {row['title']}")
                print(f"      read as: {row['card_name']}  "
                      f"(confidence {row['confidence']})")
                if row["notes"]:
                    print(f"      note: {row['notes']}")
        return 0

    config = load_config(args.config) if Path(args.config or "").exists() else None
    db_path = args.db or (config.database if config else "data/nflcarddb.sqlite")

    rows = draw_sample(db_path, size=args.sample, seed=args.seed,
                       min_confidence=args.min_confidence,
                       keyed_only=not args.include_unkeyed)
    if not rows:
        print("Nothing to review. Collect some sales, then `nflcarddb parse --all`.",
              file=sys.stderr)
        return 1

    path = write_sample(rows, args.out)
    print(f"Wrote {len(rows)} sales to {path}\n")
    print("Open it in Excel. For each row, look at the `title` (and the")
    print("`listing` link if you need the photo), then put y or n in the")
    print("`correct` column:")
    print()
    print("  y  the card_name and card_key describe this listing")
    print("  n  they do not")
    print("  ?  you cannot tell -- not counted either way")
    print()
    print("Save it, then:")
    print(f"  nflcarddb review --score {path}")
    return 0


def cmd_recheck(args) -> int:
    """Find days that look truncated, and queue them for re-collection."""
    config = load_config(args.config) if Path(args.config or "").exists() else None
    db_path = args.db or (config.database if config else "data/nflcarddb.sqlite")

    thin = find_thin_days(db_path, ratio=args.ratio)
    if not thin:
        print("Every collected day looks complete.")
        return 0

    print(f"{len(thin)} day(s) hold far fewer sales than a normal day:\n")
    for row in thin:
        print(f"  {row['day']}  {row['sales']:>7,} sales  "
              f"({row['fraction']:.0%} of a typical {row['expected']:,})")

    if not args.fix:
        print("\nThese were probably cut short by the page budget. Re-run with "
              "--fix to collect them again (existing sales are kept).")
        return 0

    marked = mark_for_recollection(db_path, [r["day"] for r in thin])
    print(f"\nMarked {marked} day(s) for re-collection. Run catchup.bat and they "
          "will be picked up.")
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


def cmd_schedule(args) -> int:
    """Install, inspect or remove the daily Windows scheduled task."""
    from .scheduling import (
        ScheduleError,
        install,
        remove,
        run_now,
        status,
    )

    try:
        if args.remove:
            existed = remove()
            print("Daily collection removed." if existed else "Nothing was scheduled.")
            return 0

        if args.status:
            state = status()
            if not state.installed:
                print("Not scheduled. Run `nflcarddb schedule --at 07:00` to set it up.")
                return 1
            print("Scheduled.\n")
            print(state.detail)
            return 0

        if args.run_now:
            run_now()
            print("Started. It runs in the background; watch logs\\ for progress.")
            return 0

        when = install(Path(args.command).resolve(), args.at)
        print(f"Scheduled: {args.command} runs every day at {when}.\n")
        print("It needs this PC switched on and you signed in to Windows. If the")
        print("PC is off at that time, the run happens once it is back on.")
        print("\nCheck on it any time with:  nflcarddb schedule --status")
        return 0

    except ScheduleError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2


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


def cmd_from_url(args) -> int:
    """Turn an eBay search URL into a query block for the config."""
    from .from_url import NotAnEbaySearch, parse_search_url, to_yaml

    try:
        spec = parse_search_url(args.url, args.id)
    except NotAnEbaySearch as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2

    block = to_yaml(spec)
    print("Add this under `queries:` in config/queries.yml:\n")
    print(block)
    print()
    if spec["extra"]:
        print("The `extra` entries are eBay's own filters, carried over as-is --")
        print("that is how a filter this project has never heard of still works.")
    print(f"\nThen check what it holds:  nflcarddb survey --query {spec['id']}")
    return 0


def cmd_survey(args) -> int:
    """One request per query: how much does each actually cover?"""
    from .search import build_url

    config = load_config(args.config)
    queries = config.queries
    if args.query:
        queries = [q for q in queries if q.id in args.query]

    candidates = [(q.id, build_url(q.keywords, q.category, page=1,
                                   items_per_page=60, extra=q.extra or None))
                  for q in queries]
    for url in args.url or []:
        candidates.append((f"url:{len(candidates)}", url))

    if not candidates:
        print("Nothing to survey.", file=sys.stderr)
        return 2

    fetcher = make_fetcher(
        engine=args.engine or config.fetch.engine,
        profile_dir="data/browser-profile",
        delay=config.fetch.delay,
        jitter=config.fetch.jitter,
        max_retries=1,
        timeout=config.fetch.timeout,
        user_agent=config.fetch.user_agent or DEFAULT_UA,
    )

    print(f"Asking eBay how big each search is. {len(candidates)} request(s).\n")
    rows = []
    try:
        for name, url in candidates:
            try:
                page = parse_search_page(fetcher.get(url, label=f"survey_{name}"))
                total = page.total_results
                rows.append((name, total, page.total_is_capped, len(page.sales)))
                cap = "+" if page.total_is_capped else " "
                print(f"  {name:<28} {total if total is not None else '?':>9}{cap}  "
                      f"({len(page.sales)} on page 1)")
            except (BlockedError, SignedOutError, FetchError) as exc:
                rows.append((name, None, False, 0))
                print(f"  {name:<28} {'failed':>9}   {str(exc).splitlines()[0][:40]}")
    finally:
        closer = getattr(fetcher, "close", None)
        if closer:
            closer()

    print("\nThat count is every sold listing eBay still holds for the search --")
    print("roughly 90 days' worth, not one day. Use it to compare searches:")
    print("a candidate showing twice the total is covering twice as much.")
    known = [r for r in rows if r[1]]
    if len(known) > 1:
        biggest = max(known, key=lambda r: r[1])
        print(f"\nWidest here: {biggest[0]} ({biggest[1]:,}).")
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
    from .browser import BrowserFetcher

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
    except BlockedByWindows as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 9
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


def cmd_bisect(args) -> int:
    """Add one search parameter at a time until eBay refuses one."""
    config = load_config(args.config)
    query = next((q for q in config.queries if q.id == args.query), None)
    if query is None:
        print(f"no query {args.query!r}; have: {[q.id for q in config.queries]}",
              file=sys.stderr)
        return 2

    profile = _resolve_profile(args)
    if profile is None:
        return 2

    print("Adding one search parameter at a time. Seven requests, ~30 seconds.\n")
    results = bisect_url(headless=not args.headed, profile_dir=profile,
                         category=query.category or "261328",
                         keywords=query.keywords or "football")
    report = format_bisect(results)
    print(report)
    Path("bisect-report.txt").write_text(report, encoding="utf-8")
    print("\nSaved to bisect-report.txt")
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
            # Only what changed since the last successful push to THIS database.
            # Re-sending 150,000 rows to deliver one new day is the thing that
            # stops working as the dataset grows.
            conn = store.connect(args.db)
            try:
                mark = None if args.full else store.sync_watermark(
                    conn, args.database_id)
            finally:
                conn.close()

            if mark:
                print(f"Sending only what changed since {mark}.")
                print("  (use --full to re-send everything)\n")
            else:
                print("First upload to this database -- sending everything.\n")

            print("Building the upload from your local database...")
            stats = export_api_sql(args.db, args.out, since=args.since,
                                   changed_since=mark)
            print(f"  {stats['rows']} rows, {stats['bytes'] // 1024} KB\n")
            if not stats["rows"]:
                print("Nothing new to upload -- Cloudflare already has "
                      "everything collected.")
                return 0

            print("Uploading...")
            sql = Path(args.out).read_text(encoding="utf-8")
            result = push_sql(args.account_id, args.database_id, token, sql,
                              dry_run=args.dry_run, on_progress=progress)
            print()
            print(json.dumps(result.as_dict(), indent=2))

            # Recorded only after the upload returned without raising, so a
            # failed push is retried in full rather than silently skipped.
            if not args.dry_run and stats.get("watermark"):
                conn = store.connect(args.db)
                try:
                    store.record_sync(conn, args.database_id,
                                      stats["watermark"], stats["rows"])
                finally:
                    conn.close()

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


def cmd_d1_pull(args) -> int:
    """Rebuild the local database from Cloudflare D1."""
    import os

    from .d1_http import D1Error
    from .d1_restore import count_rows, restore

    token = args.token or os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token:
        print("No API token.\n", file=sys.stderr)
        print("Create one at https://dash.cloudflare.com/profile/api-tokens", file=sys.stderr)
        print("  Create Token -> Custom token -> Account | D1 | Edit\n", file=sys.stderr)
        print("Then pass --token, or set it first:", file=sys.stderr)
        print("  set CLOUDFLARE_API_TOKEN=your_token_here", file=sys.stderr)
        return 2

    config = load_config(args.config) if Path(args.config or "").exists() else None
    db_path = args.db or (config.database if config else "data/nflcarddb.sqlite")
    roster = args.roster or (config.roster if config else None)

    try:
        total = count_rows(args.account_id, args.database_id, token)
        if not total:
            print("Cloudflare has no sales to restore.", file=sys.stderr)
            return 1
        print(f"Cloudflare holds {total:,} sales. Downloading...\n")

        def progress(done):
            print(f"  {done:,} / {total:,}", flush=True)

        result = restore(args.account_id, args.database_id, token, db_path,
                         roster_path=roster, since=args.since,
                         on_progress=progress)
    except D1Error as exc:
        print(f"\nRestore failed: {exc}", file=sys.stderr)
        return 3

    print()
    print(json.dumps(result, indent=2))
    print(f"\nRebuilt {db_path}.")
    print("Run  nflcarddb recheck  to find any day that was incomplete before,")
    print("and  nflcarddb publish  to refresh the dashboard.")
    return 0


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
    p.add_argument("--items-per-page", type=int, choices=[60, 120, 240],
                   help="results per request; 60 is what a browser asks for by "
                        "default and draws less attention than 240")
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
    p.add_argument("--max-minutes", type=float,
                   help="stop starting new days after this long (for scheduled runs)")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("coverage", help="which of the last 90 days you have")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--days", type=int, default=90,
                   help="window to report on (default 90, eBay's retention)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("parse", help="(re)parse titles into the cards table")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--roster")
    p.add_argument("--all", action="store_true", help="reparse every row, not just new ones")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("top", help="the biggest sales in a window")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--days", type=int, default=30,
                   help="window in days; 0 for everything (default 30)")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--include-offers", action="store_true",
                   help="include best offers, whose price is the ask")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_top)

    p = sub.add_parser("card", help="one card's price history, or what is trading")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--key", help="card_key; omit to list the most-traded cards")
    p.add_argument("--grade", help="limit to one grade, e.g. 'PSA 10'")
    p.add_argument("--days", type=int, default=30, help="window when listing (0 = all)")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_card)

    p = sub.add_parser("roster", help="learn player names from collected titles")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--out", default="config/nfl_players.txt")
    p.add_argument("--min-contexts", type=int, default=3,
                   help="different set-and-year combinations a name must span")
    p.add_argument("--min-sightings", type=int, default=8)
    p.add_argument("--no-apply", action="store_true",
                   help="write the list but do not switch it on or reparse")
    p.set_defaults(func=cmd_roster)

    p = sub.add_parser("facets",
                       help="harvest eBay's own set/parallel/player taxonomy")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--query", help="one query id; default is every query")
    p.add_argument("--out", default="data/ebay-facets.json")
    p.add_argument("--fresh", action="store_true",
                   help="start over rather than adding to what is stored")
    p.add_argument("--min-count", type=int, default=0,
                   help="ignore facet values eBay reports fewer listings for")
    p.add_argument("--drill", metavar="BUCKET",
                   help="narrow the search by each value of this vocabulary "
                        "(e.g. seasons) so the other facets list more")
    p.add_argument("--drill-limit", type=int, default=50,
                   help="how many values of that vocabulary to drill into")
    p.add_argument("--budget", type=int, default=60,
                   help="stop after this many requests")
    p.add_argument("--engine")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--save-html", help="keep the fetched page for inspection")
    p.set_defaults(func=cmd_facets)

    p = sub.add_parser("inserts",
                       help="learn insert-set names from collected titles")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--roster")
    p.add_argument("--out", default="config/nfl_inserts.txt")
    p.add_argument("--max-contexts", type=int, default=2,
                   help="products a name may appear in and still be an insert")
    p.add_argument("--min-sightings", type=int, default=6)
    p.add_argument("--min-players", type=int, default=4,
                   help="different players it must appear beside")
    p.add_argument("--apply", action="store_true",
                   help="switch the list on and re-read every title")
    p.set_defaults(func=cmd_inserts)

    p = sub.add_parser("vision",
                       help="read listing photos and compare them with the titles")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--roster")
    p.add_argument("--limit", type=int, default=50,
                   help="how many photos to read (default 50)")
    p.add_argument("--unclear", action="store_true",
                   help="sample sales the title could not identify at all")
    p.add_argument("--cache", default="data/photo-cache",
                   help="where downloaded photos are kept; safe to delete")
    p.set_defaults(func=cmd_vision)

    p = sub.add_parser("audit", help="parsing quality that needs no human review")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("review", help="check a sample by hand for a real accuracy percentage")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--sample", type=int, default=100, help="rows to draw (default 100)")
    p.add_argument("--seed", type=int, help="repeat an earlier sample exactly")
    p.add_argument("--min-confidence", type=float,
                   help="only sample rows at or above this confidence")
    p.add_argument("--include-unkeyed", action="store_true",
                   help="also sample sales that got no card_key")
    p.add_argument("--out", default="review-sample.csv")
    p.add_argument("--score", help="score a filled-in sample instead of drawing one")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("recheck", help="find days that were cut short and re-collect them")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--ratio", type=float, default=0.5,
                   help="flag days below this fraction of a typical day (default 0.5)")
    p.add_argument("--fix", action="store_true", help="queue the flagged days for re-collection")
    p.set_defaults(func=cmd_recheck)

    p = sub.add_parser("images", help="report photo coverage, resize stored URLs")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--size", type=int, default=DEFAULT_SIZE,
                   help=f"longest edge in pixels (default {DEFAULT_SIZE})")
    p.add_argument("--upgrade", action="store_true",
                   help="rewrite stored URLs to --size (no re-scrape needed)")
    p.set_defaults(func=cmd_images)

    p = sub.add_parser("schedule", help="run the collector daily (Windows Task Scheduler)")
    p.add_argument("--at", default="07:00", metavar="HH:MM",
                   help="24-hour local time to run daily (default 07:00)")
    p.add_argument("--command", default="daily.bat",
                   help="what the task runs (default daily.bat)")
    p.add_argument("--status", action="store_true", help="report whether it is scheduled")
    p.add_argument("--remove", action="store_true", help="unschedule it")
    p.add_argument("--run-now", action="store_true", help="run the task immediately")
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser("calibrate", help="parse a saved HTML page and report coverage")
    p.add_argument("html")
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("from-url", help="turn an eBay search URL into a config query")
    p.add_argument("url", help="the address bar from an eBay search, in quotes")
    p.add_argument("--id", help="name for the query (default: guessed)")
    p.set_defaults(func=cmd_from_url)

    p = sub.add_parser("survey", help="how much each query covers (one request each)")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--query", action="append", help="limit to these query ids")
    p.add_argument("--url", action="append", help="also test this raw eBay URL")
    p.add_argument("--engine", choices=["auto", "requests", "browser", "impersonate"])
    p.set_defaults(func=cmd_survey)

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

    p = sub.add_parser("bisect", help="find which search parameter eBay refuses")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--query", default="football_singles")
    p.add_argument("--headed", action="store_true", help="show the browser window")
    p.add_argument("--profile", default="data/browser-profile")
    p.add_argument("--chrome-profile", action="store_true",
                   help="use your everyday Chrome profile (close Chrome first)")
    p.set_defaults(func=cmd_bisect)

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
    p.add_argument("--full", action="store_true",
                   help="re-send every row, ignoring what was already uploaded")
    p.add_argument("--dry-run", action="store_true", help="show what would be sent")
    p.add_argument("--verify-only", action="store_true",
                   help="just report what D1 already holds, upload nothing")
    p.set_defaults(func=cmd_d1_push)

    p = sub.add_parser("d1-pull", help="rebuild the local database from Cloudflare D1")
    p.add_argument("--account-id", required=True)
    p.add_argument("--database-id", required=True)
    p.add_argument("--token", help="or set CLOUDFLARE_API_TOKEN")
    p.add_argument("--config", default="config/queries.yml")
    p.add_argument("--db")
    p.add_argument("--roster")
    p.add_argument("--since", help="only sales on/after this date (YYYY-MM-DD)")
    p.set_defaults(func=cmd_d1_pull)

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


def _register_learned_vocabulary(args) -> None:
    """Load learned insert names into the parser before anything parses.

    Done once here rather than passed as an argument, because every path that
    parses a title would otherwise have to carry it: the collector, the
    importer, the reparser and the D1 restore all say the same thing. A silent
    failure here would show up as inserts quietly not being recognised, so a
    broken path is reported rather than swallowed.
    """
    from .parse_title import load_inserts, register_inserts

    config_path = getattr(args, "config", None)
    if not config_path or not Path(config_path).exists():
        return
    try:
        config = load_config(config_path)
    except (ValueError, OSError):
        return          # the command itself will report a bad config
    if not config.inserts:
        return
    if not Path(config.inserts).exists():
        print(f"warning: inserts file not found: {config.inserts}", file=sys.stderr)
        return
    register_inserts(load_inserts(config.inserts))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    _register_learned_vocabulary(args)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SignedOutError as exc:
        print(f"not signed in: {exc}", file=sys.stderr)
        return 8
    except BlockedByWindows as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 9
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
