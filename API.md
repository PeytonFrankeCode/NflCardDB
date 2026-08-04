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
| `include_offers=true` | include best-offer rows (see below) |
| `limit`, `offset` | up to 500 per page |

```bash
curl -H "Authorization: Bearer KEY" \
  "https://YOUR-URL/v1/sales?player=Stroud&grader=PSA&grade=10&limit=20"
```

### `GET /v1/prices?player=Name`
Median, mean, p10/p90, low and high for one player. Optional `grader` and
`grade` to narrow it — `?player=CJ Stroud&grader=PSA&grade=10` is the common one.

### `GET /v1/players?q=`
Most-traded players with sale counts and averages.

### `GET /v1/daily`
Per-day totals — what the dashboard charts.

---

## The best-offer caveat, in the API

About **46%** of football card sales close via an accepted offer. On those, eBay
publishes the *seller's asking price*, not what the buyer paid.

So the API stores `price: null` for them rather than the ask. A wrong number is
worse than a missing one — a null cannot be averaged into your figures by
mistake, an asking price can.

They are excluded from `/v1/sales` unless you pass `include_offers=true`, and
they are never in `/v1/prices` or the daily medians. Volume counts include them,
which is why `sales` and `priced_sales` differ in `/v1/summary`.

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
