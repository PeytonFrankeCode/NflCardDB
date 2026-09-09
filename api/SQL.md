# Querying the card database directly from your site

This is for a site with its **own D1 binding** — it queries the database with
SQL and does not go through the Worker or an API key. Everything below is
copy-paste SQL against the tables `d1-push.bat` uploads.

The sorting is already done. It is not computed when your site asks; it is
computed on your PC during the upload and stored as columns. Your site's job is
`SELECT ... ORDER BY <column>`, nothing more.

---

## The three tables

| Table | One row per | What it's for |
|---|---|---|
| `cards` | **card** | the catalogue your site browses and sorts |
| `card_grades` | card × grade | "PSA 10 vs raw" side by side |
| `sales` | eBay listing | one card's price history, the raw evidence |

`card_key` joins all three.

Browse `cards`. Only touch `sales` when someone opens a single card — it is
600,000+ rows and D1 bills by rows read.

---

## `quality` — which cards are safe to show

Every row in `cards` carries one of four values:

| `quality` | Means | Roughly |
|---|---|---|
| `clean` | numbered, and its prices agree within each grade | 7,000 |
| `unproven` | numbered, but too few sales yet to judge | most of them |
| `suspect` | numbered, but prices scatter inside one grade — probably two cards sharing a key | some |
| `bucket` | no card number was ever read, so the row is *every* card of that player in that set at once | some |

**Default your site to `quality = 'clean'`.** Nothing is deleted — the other
piles are still there to page through if you want them — but `clean` is the set
where the price on the card is a price for *that* card.

`unproven` is not "bad", it is "new". A card needs about four sales in one grade
before the spread means anything. That pile drains into `clean` on its own as
you keep collecting.

Never show `bucket` rows as if they were cards. A bucket row's median is the
median of a player's whole run in a set, which is a real number about nothing.

---

## The browse query

This is the one your site runs most. Swap the `ORDER BY` for each sort tab.

```sql
SELECT card_key, card_name, player, team, year, brand, set_name, subset,
       parallel, card_number, print_run, image_url,
       sales, median_cents, low_cents, high_cents,
       raw_sales, raw_median_cents, trend_pct, trend_sales,
       first_sold, last_sold
FROM cards
WHERE quality = 'clean'
ORDER BY sales DESC, median_cents DESC
LIMIT 50;
```

### The sort tabs

| Tab | `ORDER BY` | Extra `WHERE` |
|---|---|---|
| Most traded | `sales DESC, median_cents DESC` | — |
| Highest value | `median_cents DESC, sales DESC` | — |
| Cheapest | `median_cents ASC, sales DESC` | — |
| Biggest riser | `trend_pct DESC, sales DESC` | `AND trend_pct IS NOT NULL AND trend_sales >= 10` |
| Biggest faller | `trend_pct ASC, sales DESC` | `AND trend_pct IS NOT NULL AND trend_sales >= 10` |
| Sold most recently | `last_sold DESC, sales DESC` | — |
| Newest cards | `year DESC, sales DESC` | — |
| Oldest cards | `year ASC, sales DESC` | — |
| A–Z | `card_name ASC` | — |

**Neither filter on the trend rows is optional.**

`trend_pct IS NOT NULL` — a card with fewer than four sales in a single grade
has no trend, because two points draw a line through anything. Without it,
every card with no history lands at one end of the list.

`trend_sales >= 10` — **not** `sales >= 10`. The trend is measured inside the
card's largest single grade, because a raw copy and a PSA 10 are two markets
and a card that moved from one to the other is not a card whose price moved.
`trend_sales` is how many sales that grade contributed; `sales` counts every
grade. A card with 200 sales can carry a trend drawn from four of them, and
filtering on `sales` reads like evidence without being any.

Sort by `trend_pct` with no floor and the top of the page is cards up 900% on
four sales — arithmetically true, worth nothing. Ten is a reasonable floor;
raise it for a front page, lower it for a "recently moving" feed.

Each of those sorts has an index behind it with `quality` leading, so the
database jumps straight to the rows rather than reading the catalogue and
throwing most of it away.

---

## Filters to bolt on

Add these to the `WHERE`. All of them stack.

```sql
AND player = 'Patrick Mahomes'      -- indexed
AND year = 2025
AND set_name = 'Prizm'
AND subset IS NULL                  -- base cards only, no inserts
AND subset = 'Kaboom'               -- one insert
AND parallel IS NULL                -- the plain version
AND is_rookie = 1
AND is_auto = 1
AND is_relic = 1
AND print_run <= 25                 -- short prints
AND median_cents BETWEEN 1000 AND 10000
AND sales >= 5                      -- only cards with a real market
AND trend_sales >= 10               -- only trends with evidence behind them
```

Search by name:

```sql
WHERE quality = 'clean' AND card_name LIKE '%mahomes%'
ORDER BY sales DESC LIMIT 50
```

`LIKE '%…%'` cannot use an index, so it reads the catalogue. Fine for a search
box a person types in; do not put it on a page that loads automatically.

---

## One card's page

Three queries. Run them together — D1 batches them in one round trip.

**The card:**

```sql
SELECT * FROM cards WHERE card_key = ?;
```

**Its markets** (PSA 10, PSA 9, Raw, BGS 9.5 …):

```sql
SELECT grade_label, sales, median_cents, low_cents, high_cents, last_sold
FROM card_grades
WHERE card_key = ?
ORDER BY sales DESC;
```

**Its price history** — the chart:

```sql
SELECT sold_date, price_cents, grader, grade, best_offer, title, image_url
FROM sales
WHERE card_key = ? AND price_cents IS NOT NULL
ORDER BY sold_date;
```

That last one uses `idx_sales_card`, so it reads that card's sales and nothing
else, however large `sales` grows.

To plot one line per grade rather than a scatter of everything mixed together,
add `AND grader = 'PSA' AND grade = 10`. A PSA 10 and a raw copy are the same
cardboard and two completely different markets; drawn on one line they look
like wild volatility that is really just two price levels.

---

## Counting for pagination

```sql
SELECT COUNT(*) FROM cards WHERE quality = 'clean';
```

Cache that. It changes once a day, when you upload — running it beside every
page of results doubles the work for a number that did not move.

**Do not page with large `OFFSET`s.** `LIMIT 50 OFFSET 20000` makes the database
walk 20,050 rows to hand you 50, and D1 charges for all of them. Past the first
few pages, remember the last row and ask for what comes after it:

```sql
-- page 2 onward, sorted by sales
WHERE quality = 'clean' AND (sales, card_key) < (?, ?)
ORDER BY sales DESC, card_key DESC LIMIT 50
```

Reading 543,935 rows this way once cost 74 million row-reads the other way.

---

## Some ready-made pages

**Today's movers** — cards with a real market that moved:

```sql
SELECT card_name, player, year, set_name, median_cents, trend_pct, trend_sales
FROM cards
WHERE quality = 'clean' AND trend_pct IS NOT NULL AND trend_sales >= 10
ORDER BY trend_pct DESC LIMIT 25;
```

**Rookie cards under $20 that actually trade:**

```sql
SELECT card_name, player, year, set_name, median_cents, sales
FROM cards
WHERE quality = 'clean' AND is_rookie = 1
  AND median_cents <= 2000 AND sales >= 5
ORDER BY sales DESC LIMIT 50;
```

**One player, most valuable first:**

```sql
SELECT card_name, year, set_name, subset, parallel, card_number,
       median_cents, sales, trend_pct
FROM cards
WHERE player = ? AND quality IN ('clean', 'unproven')
ORDER BY median_cents DESC LIMIT 50;
```

**The sets you have the most of** — for a set-browsing menu:

```sql
SELECT year, set_name, COUNT(*) AS cards, SUM(sales) AS sales
FROM cards
WHERE quality = 'clean'
GROUP BY year, set_name
ORDER BY sales DESC LIMIT 100;
```

**The market, day by day** — already rolled up, one row per day:

```sql
SELECT sold_date, sales, priced, median_cents, p90_cents, total_cents
FROM daily ORDER BY sold_date DESC LIMIT 90;
```

---

## Two things to know about the prices

**Best offers carry the seller's ask, not what the buyer paid.** eBay does not
publish the accepted amount. Those rows are marked `best_offer = 1`, and
`ask_cents` holds the same number, so you can tell them apart — but they are
counted in every median in `cards` and `card_grades`. Those medians therefore
sit slightly high. If you want a page free of that, filter it yourself:

```sql
SELECT sold_date, price_cents FROM sales
WHERE card_key = ? AND best_offer = 0 AND price_cents IS NOT NULL
ORDER BY sold_date;
```

**Prices are cents.** `median_cents = 1250` is $12.50. Divide by 100 for
display; never store the divided number.

---

## When the numbers look stale

The catalogue is rebuilt and uploaded by `d1-push.bat`. If your site's names or
sorting look out of date, run `d1-check.bat` — the `cards` line is the
catalogue's size. If it disagrees with what you expect, `resend-all.bat` sends
the whole database again rather than only what changed.
