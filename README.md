# NflCardDB

Scrapes one day of eBay **sold** football-card listings and stores them in SQLite,
with listing titles parsed into structured columns (player, team, year, set,
parallel, card number, grade) so you can actually query the data.

```
eBay sold search  ->  listing parser  ->  title parser  ->  SQLite
   (price bands)      (item id anchor)     (regex+vocab)    (sales + cards)
```

## Start here

**Not comfortable with a terminal? → [WINDOWS.md](WINDOWS.md)** walks you through
it with no typing.

**Automatic collecting is blocked, and the working path is the grabber.** eBay
serves sold listings only to signed-in accounts, refuses automated clients
outright (HTTP 403 on the first request, from any transport), and since Chrome
127 the browser's saved session cannot be reused by another program -- cookies
are bound to Chrome's own process. Every automated route ends at the sign-in
page. So the data comes from a page *you* have open: open `tools/grabber.html`,
drag its button to your bookmarks bar, and click it on any eBay sold-search
page. It reads the listings already on your screen and downloads them; drag
that file onto `import.bat`. One click per page, 240 sales at a time, nothing
for eBay to refuse.

**Run it on your own computer, not on GitHub's servers.** This was tested, not
assumed: a scheduled run on a GitHub Actions runner got a bot-check page and
collected 0 sales in 5 requests. eBay treats datacenter IP ranges as suspicious.
A home connection looks like ordinary browsing and works fine.

The `Collect card sales` workflow in the Actions tab still exists, and is worth
one click to confirm the block for yourself — it saves the exact page eBay
returned as an artifact, which is what you need to debug a wrong category id or
changed markup. Just don't plan on it as the way you gather data.

---

## Quick start (command line)

```bash
pip install -e ".[dev]"
pytest -q                                   # 55 tests, no network needed

nflcarddb url --query football_singles      # see the URLs it would hit
nflcarddb probe --query football_singles    # ONE live request, verify it works
nflcarddb -v scrape --date 2025-07-30       # the real thing
nflcarddb stats --date 2025-07-30
nflcarddb export --date 2025-07-30 --out sales.csv
```

Start with `probe`. It makes a single request and prints the result count plus
sample titles, which tells you whether your category id is right and whether
eBay is serving you results before you commit to hundreds of requests.

## Read this before your first real run

**Verify the category ids in `config/queries.yml`.** eBay reshuffles its
taxonomy and the shipped value (`261328`, Sports Trading Card Singles) is a
starting point, not a guarantee. Browse to the category you want, filter to
Sold, and copy the `_sacat=` value out of the URL. Narrowing to a football-only
child category cuts your page count substantially.

**This scrapes a site that would rather you didn't.** eBay's terms prohibit
automated access, and they run bot detection. The client here is deliberately
slow and single-threaded: ~2.5s between requests with jitter, exponential
backoff on HTTP 429/503, and a hard stop the moment a bot-check page is served.
If you get blocked, the fix is a longer `--delay` and a smaller `--page-budget`
— not more concurrency, not proxy rotation. Keep the volume proportionate to
personal price research and you are unlikely to have trouble.

**Best-offer sales are recorded but not trustworthy as prices.** When a seller
accepts an offer, eBay shows the *asking* price on the search page, not what
the buyer actually paid. Those rows are flagged `best_offer = 1`. Exclude them
from any pricing analysis:

```sql
SELECT * FROM v_sales WHERE best_offer = 0;
```

This is the single biggest quality caveat in the dataset and there is no fix
available from search-page scraping alone.

## The API (Cloudflare Workers + D1)

`api/` is a read-only HTTP API so another site can query your sales — filter by
player, set, grade and date, or ask for price statistics. Keyed, quota'd, and
free to run at this size. Setup and endpoints: **[API.md](API.md)**.

One thing worth knowing before you build against it: **a key placed in browser
JavaScript is public**, because the visitor's browser has to know it to send it.
For real protection the caller must be a server. API.md spells out both cases.

**Handing this to a developer? → [TEAM.md](TEAM.md)** is a one-page brief:
both integration options, the endpoints, and the two data caveats that matter.

**If your website is itself on Cloudflare, you probably do not need a key at
all** — bind the D1 database straight to the Pages project and read it directly.
No secret, no quota, no extra hop. See **[CLOUDFLARE.md](CLOUDFLARE.md)**.

Best-offer sales carry `price: null` there rather than eBay's asking price — a
missing number cannot be averaged by mistake, a wrong one can.

## The dashboard (GitHub Pages)

`site/` is a static dashboard — KPI tiles, daily volume and median-price charts,
top players / grades / sets, and a searchable table of the underlying sales. It
has no build step and no dependencies; open `site/index.html` through any local
server and it works.

**Pages cannot run the scraper.** It is static hosting, so there is no Python,
and a browser cannot query eBay directly either — eBay sends no CORS headers, so
a client-side `fetch` to it is blocked. The split is therefore:

```
scrape (wherever you can reach eBay)  ->  nflcarddb publish  ->  site/data/*.json  ->  Pages
```

To put it online:

1. Settings → Pages → Source: **GitHub Actions**.
2. Scrape and publish, then push:

   ```bash
   nflcarddb scrape --date "$(date -d yesterday +%F)"
   nflcarddb publish                 # writes site/data/*.json
   git add site/data && git commit -m "data update" && git push
   ```

`.github/workflows/pages.yml` redeploys on any push touching `site/`. Until you
publish once, the page renders an empty state that tells you these commands —
that is expected, not a failure.

`publish` writes six small JSON files. Price statistics deliberately exclude
best-offer rows and non-USD listings, while volume counts include everything, so
"sales per day" and "median price" are computed over different row sets by
design — the dashboard labels which is which. Those excluded rows still appear
in the table, flagged, rather than being hidden.

To preview locally:

```bash
nflcarddb publish
python3 -m http.server -d site 8000    # then open http://localhost:8000
```

## Where to run this

A daily GitHub Actions workflow is included (`.github/workflows/daily-scrape.yml`),
but be aware that **GitHub-hosted runners use datacenter IPs, which eBay
throttles much harder than residential ones.** Expect intermittent blocks. If
you want a scrape you can depend on, run it on a machine at home — a cron entry,
or a self-hosted Actions runner with `runs-on: self-hosted`:

```cron
15 4 * * *  cd /path/to/NflCardDB && ./venv/bin/nflcarddb scrape >> scrape.log 2>&1
```

The workflow caches the SQLite file between runs and uploads it as an artifact.
Once the database outgrows that comfortably (a few hundred MB), move it to S3/R2
or a small Postgres instance and drop the cache steps.

## How it gets a whole day

Two problems, two solutions:

**eBay caps every search at ~10,000 results**, no matter how many pages you
ask for. So each query is split into price bands (`config/queries.yml`), and
any band that still reports a capped count is automatically subdivided at its
geometric midpoint — geometric, not arithmetic, because card prices are roughly
log-distributed and an arithmetic split leaves everything in the bottom half.
Subdivision recurses to `max_subdivide_depth`.

**There is no "sold on date X" URL parameter.** Instead results are sorted by
most-recently-ended and paged until the listings fall past your target date.
Only listings matching the target date are kept.

Bands overlap their parents after subdivision, so the same listing genuinely
arrives more than once per run. `item_id` is the primary key and every write is
an upsert, which makes re-running a day idempotent — safe to retry after a block.

## Storage

SQLite, one file, no server. Rows are written in batches of 200 so an
interrupted run keeps everything it already paid for.

| table | what's in it |
|---|---|
| `sales` | one row per listing: price, shipping, sold date, format, bids, best-offer flag, seller, URL |
| `cards` | structured attributes parsed from the title, plus a `confidence` score |
| `scrape_runs` | one row per invocation: status, pages fetched, items seen/new |
| `scrape_segments` | per-band progress, including which bands hit the cap |
| `sales_fts` | FTS5 index over titles, kept current by triggers |
| `v_sales` | the join you actually want, with prices in dollars |

```sql
-- PSA 10 rookie prices by player, best offers excluded
SELECT player, COUNT(*) n, ROUND(AVG(price), 2) avg_price
FROM v_sales
WHERE grade = 10 AND grader = 'PSA' AND is_rookie = 1 AND best_offer = 0
GROUP BY player HAVING n >= 5 ORDER BY avg_price DESC;

-- full-text search over raw titles
SELECT s.title, s.price_cents / 100.0 AS price
FROM sales_fts f JOIN sales s ON s.rowid = f.rowid
WHERE sales_fts MATCH 'Kaboom NOT reprint';
```

## Title parsing

`2023 Panini Prizm CJ Stroud Silver Prizm RC #339 PSA 10` becomes:

| player | team | year | brand | set | parallel | card # | grader | grade | rookie | confidence |
|---|---|---|---|---|---|---|---|---|---|---|
| CJ Stroud | – | 2023 | Panini | Prizm | Silver Prizm | 339 | PSA | 10.0 | ✓ | 0.90 |

It works by claiming the mechanical parts first (grade, card number, serial,
year, team, set, parallel), blanking each match out of a working copy, and
treating the longest surviving run of name-shaped tokens as the player. Teams
are claimed *before* parallels so that "Green Bay" doesn't leave "Green"
looking like a colour parallel.

**Always filter on `confidence`.** Sellers write titles however they like, and
a listing like `HOT INVEST 2023 Prizm Football Rare SSP Mint` has no player in
it at all. `confidence >= 0.5` is a reasonable floor for analysis.

Two ways to improve it:

- Set `roster:` in the config to a newline-delimited player-name file. Exact
  matches raise confidence and fix ambiguous names.
- Extend the vocabularies at the top of `src/nflcarddb/parse_title.py`, then
  `nflcarddb parse --all` to reparse the whole history in place. The parser is
  pure and offline, so this costs nothing.

For the long tail that regexes will never get, batching low-confidence titles
through a cheap LLM (Haiku) is a sound next step — parse first, escalate only
what scores badly.

## When eBay changes its markup

It will. The listing parser is built to survive it: rather than pinning to CSS
classes like `s-item__title` (which are mid-migration to `s-card__*` right
now), it finds every `<a href>` containing `/itm/<numeric id>`, walks up to the
enclosing tile, and extracts fields with a selector cascade that falls back to
regex over the tile's text. Both markup generations are covered by fixtures in
`tests/fixtures/`.

If it does break, dump a real page and inspect it:

```bash
nflcarddb probe --query football_singles --save-html data/html
nflcarddb calibrate data/html/probe_football_singles.html
```

`calibrate` reports which stage matched, per-field coverage, and sample parses,
so you can see whether the page has no results, a bot check, or renamed
selectors.

## Command reference

| command | does |
|---|---|
| `scrape` | fetch and store one day (`--date`, `--query`, `--delay`, `--page-budget`, `--dry-run`, `--save-html`) |
| `probe` | one live request against a configured query |
| `calibrate` | parse a saved HTML file, report coverage — no network |
| `parse` | (re)parse titles into `cards` (`--all` to redo everything) |
| `stats` | daily counts, recent runs, top players |
| `export` | CSV out (`--date`, `--out`) |
| `import` | load eBay pages you saved by hand — no network, cannot be blocked |
| `publish` | write `site/data/*.json` for the Pages dashboard |
| `api-key` | mint a key for the hosted API (shown once) |
| `export-api` | write SQL that loads the data into Cloudflare D1 |
| `url` | print the URLs a config would hit, fetch nothing |

## Known limitations

- **Best-offer prices are the ask, not the sale.** Flagged, not fixable here.
- **Only what search pages expose.** No per-listing detail fetch, so no
  item-specifics, no full seller history, no bid-by-bid auction data.
- **Non-USD listings are stored in their original currency** with a `currency`
  column. There is no FX conversion; aggregate on `currency = 'USD'` or add a
  rates table.
- **Player extraction is heuristic.** Filter on `confidence`.
- **Sold dates are eBay's display dates**, in the site's timezone, not UTC.
