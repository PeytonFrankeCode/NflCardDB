# The sold-data API

A read-only HTTP API over your sales, hosted on Cloudflare Workers + D1. Free
tier covers roughly 100,000 requests a day, which is far more than a card site
needs.

Your collector on your PC stays the only writer. The API can only read, so a
leaked key cannot damage the data — worst case, somebody reads prices.

---

## Read this first: where a key can live

**A key inside website JavaScript is public.** Anyone can open View Source and
copy it. There is no way around that — the browser has to know the key to send
it, so the visitor knows it too.

So:

| Your site calls the API from… | What a key gives you |
|---|---|
| **Its server** (PHP, Node, Python, Next.js API route, etc.) | Real protection. Keep the key on the server; it is never sent to visitors. |
| **Browser JavaScript** | Identification and rate-limiting only. Give that key a small quota and assume it is public. |

Mint separate keys for each, so a leaked public one can be revoked without
breaking anything else.

---

## One-time setup (about 15 minutes)

You need a free Cloudflare account and Node.js installed (https://nodejs.org).

**1. Sign in to Cloudflare**

```bash
cd api
npx wrangler login
```

**2. Create the database**

```bash
npx wrangler d1 create nflcarddb
```

It prints a `database_id`. Paste that into `api/wrangler.toml`, replacing
`PASTE_YOUR_DATABASE_ID_HERE`.

**3. Create the tables**

```bash
npx wrangler d1 execute nflcarddb --remote --file=schema.sql
```

**4. Mint a key**

```bash
nflcarddb api-key --label website
```

It prints the key **once** — copy it somewhere safe. Only its hash is stored, so
nobody, including you, can recover it later. Losing it just means minting
another.

**5. Upload your data, activating that key**

```bash
nflcarddb export-api --add-key <THE_HASH>:website
cd api && npx wrangler d1 execute nflcarddb --remote --file=import.sql
```

**6. Deploy**

```bash
cd api && npx wrangler deploy
```

It prints your URL, something like
`https://nflcarddb-api.<your-name>.workers.dev`.

**Test it:**

```bash
curl -H "Authorization: Bearer YOUR_KEY" https://YOUR-URL/v1/summary
```

---

## Keeping it fresh

After each `collect.bat`, run **`api-deploy.bat`**. It exports and uploads in one
step. Re-imports update existing rows rather than duplicating them, so running it
twice is harmless.

---

## Endpoints

Every request needs `Authorization: Bearer YOUR_KEY`. `/` and `/health` do not,
so you can tell "down" apart from "unauthorised".

### `GET /v1/summary`
Totals and freshness — row counts, date range, when it was last updated.

### `GET /v1/sales`
The sale rows.

| Parameter | Meaning |
|---|---|
| `player`, `set` | partial match, case-insensitive |
| `team`, `grader`, `card_number` | exact match |
| `year`, `grade` | numbers, e.g. `year=2023&grade=10` |
| `from`, `to` | `YYYY-MM-DD` |
| `rookie=true`, `auto=true` | flags |
| `min_confidence` | `0`–`1`; how sure the title parser was |
| `exclude_offers=true` | drop best-offer rows, leaving confirmed prices only |
| `limit`, `offset` | up to 500 per page |

```bash
curl -H "Authorization: Bearer KEY" \
  "https://YOUR-URL/v1/sales?player=Stroud&grader=PSA&grade=10&limit=20"
```

**`price` vs `ask`.** Every row with a published price has `price` set:

| | `price` | `ask` | `best_offer` |
|---|---|---|---|
| Ordinary sale | what it sold for | `null` | `false` |
| Best offer accepted | the seller's **ask** | same number | `true` |

The card sold either way. On a best offer eBay publishes only the asking price —
the buyer paid some unpublished amount below it — so those rows read **above**
what was actually paid, and they are included in every price figure here.

That is a deliberate choice, and a reversible one: pass `exclude_offers=true` to
`/v1/sales`, or filter `ask IS NULL` anywhere else, for confirmed prices only.
Roughly 46% of rows are asks, so the difference is not small.

Each row carries an `image` field — the front photo of the listing, as a URL on
eBay's own CDN, ready to drop into an `<img>`:

```html
<img src="${sale.image}" alt="${sale.title}" loading="lazy">
```

Two things to know about it. It is `null` when the listing had no usable photo,
so guard before rendering. And eBay purges images for old listings — roughly 90
days after the sale — so an `<img>` for an old row will 404; handle `onerror` if
you show history. Only the URL is stored; the image itself is never copied.

### `GET /v1/prices?player=Name`
Median, mean, p10/p90, low and high for one player. Optional `grader` and
`grade` to narrow it — `?player=CJ Stroud&grader=PSA&grade=10` is the common one.

The top-level figures cover every priced row, asks included. Alongside them,
`asking` reports the best-offer subset on its own, so you can see how much of
the number they account for:

```json
{
  "player": "CJ Stroud",
  "matched": 412,
  "median": 24.99,
  "asking": { "n": 355, "median": 39.99, "p90": 180.0 }
}
```

`matched` counts everything; `asking.n` is how many of those were asks. If
`asking.n` is most of `matched`, the headline is mostly list prices — worth
knowing before quoting it as what a card is worth.

### `GET /v1/cards`
**The catalogue — one row per physical card. This is the endpoint a browsing
site is built on.**

Every listing that is the same card is already gathered under one `card_key`, so
this returns cards rather than sales. `total` comes back with every response, so
a site can page and show "1–50 of 6,214" without a second call.

```json
{
  "total": 6214, "limit": 50, "offset": 0, "sort": "traded",
  "cards": [
    {"card_key": "2024-prizm-n301-silver-prizm",
     "card_name": "2024 Prizm Caleb Williams #301 Silver Prizm",
     "player": "Caleb Williams", "year": 2024, "set": "Prizm",
     "subset": null, "parallel": "Silver Prizm", "card_number": "301",
     "print_run": null, "rookie": true, "auto": false, "relic": false,
     "numberless": false,
     "image_url": "https://i.ebayimg.com/images/g/.../s-l500.jpg",
     "sales": 26, "median": 54.08, "low": 24.72, "high": 176.86,
     "raw_sales": 14, "raw_median": 33.19,
     "first_sold": "2026-08-01", "last_sold": "2026-08-24", "trend": 14.5}
  ]
}
```

**Sort** — `?sort=` takes one of:

| | |
|---|---|
| `traded` (default) | most sales first |
| `value` / `cheapest` | by median price |
| `rising` / `falling` | by trend; cards with no trend are excluded |
| `recent` | most recently sold |
| `newest` / `oldest` | by card year |
| `name` | alphabetical |

Anything else is a `400`, not a silent fallback — the value goes into an
`ORDER BY`, so an allow-list is the only safe way to accept it.

**Filter** — `q` (card name), `player`, `set`, `subset`, `parallel`, `team`,
`year`, `year_from`, `year_to`, `card_number`, `rookie`, `auto`, `relic`,
`min_sales`, `min_price`, `max_price`, `numbered_only`, `quality`. Paging is
`limit` (max 500) and `offset`.

### Browsing only the cards that are known good

Grouping is not perfect, and pretending otherwise would put wrong cards at the
top of exactly the lists people look at first — a merged card's price swings
hardest, so it ranks highest on "biggest riser". Every card therefore carries a
`quality`:

| | |
|---|---|
| `clean` | Numbered, and its prices agree within each grade. |
| `unproven` | Numbered, but too few sales in any one grade to check. Nothing is known against it. |
| `suspect` | Numbered, but prices scatter inside a **single grade** far enough that it is probably two cards sharing one key. |
| `bucket` | No card number was ever read, so the identity fell back to the player. The row is every card of that player in that set. |

```
GET /v1/cards?quality=clean&sort=rising      # just the good ones
GET /v1/cards?quality=suspect,bucket         # the rest, as their own list
GET /v1/quality                              # how many are in each pile
```

**The whole catalogue is served by default.** Filtering to `clean` is one
parameter, but it is never applied for you — a dataset that quietly drops a
third of itself is worse than one that tells you which third is doubtful.

`spread` is the number behind the verdict: the card's 90th-percentile price over
its 10th, inside its largest single grade. It is measured within a grade because
grade is deliberately not part of a card's identity, so comparing a PSA 10
against a raw copy would measure grading rather than a bad grouping. The
threshold is 8x, chosen from the data: across 1,153 judgeable cards the median
spread is 1.9x and the 80th percentile is 7.5x, so there is a knee there. Below
it sits ordinary variation — a raw 1984 Elway runs $30 to $141 on condition
alone, and that is one card. Above it sits *2026 Topps Drew Allar #304 Base,
$1.00 to $28.99*, which is not.

It was worth checking that this does not simply punish vintage, where ungraded
condition varies most. It does not: pre-2000 cards are flagged at 17.3% and
everything else at 19.8%, so one threshold treats them alike.

**Three fields worth understanding before you display them.**

`trend` compares the newer half of a card's sales against the older half, not
the newest sale against the oldest — one unusual sale at either end would
otherwise be the whole trend. It is `null` below four sales, because two points
make a line through anything. Sorting by `rising` or `falling` drops those
rather than parking them at one end of the list.

`raw_median` is the ungraded market on its own, and it is usually the honest
number to show. `median` covers every grade, so a single PSA 10 among twenty raw
copies drags it somewhere that describes neither market. On one real card the
all-grades median read $54 while raw alone was $33 and PSA 10 was $106.

`numberless: true` means no sale of that card ever yielded a card number, so its
identity fell back to the player's name — the row is *every* card of that player
in that set at once, not one card. They are served because the sales are real,
and flagged because a price history across them is not. Pass
`numbered_only=true` to leave them out.

### `GET /v1/card/grades?key=...`
**One card's markets side by side** — what a card page shows above its chart.

```json
{
  "card": {"card_key": "2024-prizm-n301-silver-prizm", "...": "as above"},
  "grades": [
    {"grade": "Raw",    "sales": 14, "median": 33.19, "low": 24.72,
     "high": 61.00, "last_sold": "2026-08-24"},
    {"grade": "PSA 10", "sales": 12, "median": 105.86, "low": 72.34,
     "high": 176.86, "last_sold": "2026-08-23"}
  ]
}
```

### `GET /v1/card?key=...`
**One card's price history — the endpoint behind a trend chart.**

Every sale that shares a `card_key`, oldest first, split by grade. Optional
`grade` narrows it to one, e.g. `&grade=PSA%2010`.

```json
{
  "card_key": "2021-prizm-n220",
  "card_name": "2021 Prizm Ja'Marr Chase #220",
  "image": "https://i.ebayimg.com/images/g/.../s-l500.jpg",
  "sales": 47,
  "by_grade": {
    "PSA 10": {"n": 22, "median": 95.0, "low": 78.0, "high": 260.0,
               "first": "2026-07-19", "last": "2026-08-09",
               "points": [{"date": "2026-07-19", "price": 78.0, "is_ask": false,
                           "id": "127967084745"}]}
  }
}
```

**Grades are separate series on purpose.** A PSA 10 and a raw copy of the same
card trade at different prices, so one line through both would describe neither.
Plot each `by_grade` entry as its own line.

`is_ask` marks best-offer points — the seller's asking price rather than what
was paid. Worth styling differently, or filtering out, on a chart.

### `GET /v1/players?q=`
Most-traded players with sale counts and averages.

### `GET /v1/daily`
Per-day totals — what the dashboard charts.

---

## The best-offer caveat, in the API

About **46%** of football card sales close via an accepted offer. On those, eBay
publishes the *seller's asking price*, not what the buyer paid.

The API serves that ask as the row's `price`, and repeats it in `ask` so the
rows stay identifiable. Every price figure — `/v1/prices`, the daily medians,
player averages — includes them, and therefore sits above true sale prices.

If you want confirmed amounts only: `exclude_offers=true` on `/v1/sales`, or
`ask IS NULL` against the database directly. Nothing needs re-collecting; the
distinction is preserved per row.

One caveat: best offers are not spread evenly across price. In the collected
data they are ~46% of sales overall, but ~56% of the highest-priced listings —
sellers enable offers more on expensive cards. So including them lifts the top
of the distribution more than the middle.

---

## Errors

| Status | Code | Meaning |
|---|---|---|
| 401 | `no_key` | No `Authorization` header |
| 403 | `bad_key` / `revoked` | Key unknown or switched off |
| 429 | `quota_exceeded` | Daily quota reached; resets 00:00 UTC |
| 400 | `bad_date` | Date not `YYYY-MM-DD` |
| 404 | `not_found` | No such endpoint |

Responses carry `X-Quota-Limit` and `X-Quota-Used`.

## Revoking a key

```bash
cd api
npx wrangler d1 execute nflcarddb --remote \
  --command="UPDATE api_keys SET revoked=1 WHERE label='website'"
```

Effective immediately, since every request checks.
