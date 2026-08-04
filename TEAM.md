# NflCardDB — integration brief

Hand this to whoever maintains the website.

## What this is

A dataset of eBay **sold** football-card listings, collected daily onto Peyton's
PC into SQLite (~20,000 sales/day), with listing titles parsed into structured
columns: player, team, year, set, parallel, card number, grade, rookie/auto
flags, plus a parse-confidence score.

Repo: `PeytonFrankeCode/NflCardDB`. Two ways to consume it. **Pick one.**

---

## Loading data without wrangler

Cloudflare's D1 has an HTTP API, so the data upload needs no Node at all:

```bash
export CLOUDFLARE_API_TOKEN=...        # Account | D1 | Edit
nflcarddb d1-push --account-id <ACCT> --database-id <DBID> --schema api/schema.sql --schema-only
nflcarddb d1-push --account-id <ACCT> --database-id <DBID>
```

Idempotent — every statement is an upsert. `--dry-run` shows what would be sent.
`--since YYYY-MM-DD` uploads only recent days once the dataset is large.

`--verify-only` reads back row count, priced-row count, day count and date range
without uploading anything — the quickest way to answer "is the data actually
there". A full push runs the same check afterwards and exits non-zero if the
remote count does not match the local one.

**Binding name:** the site currently binds this database as `env.NFLDB`. The
examples in `examples/` use `env.DB`; adjust whichever you keep.

## Option A — D1 binding (no API, no key)

If the site is on Cloudflare and in the same account, skip the API entirely.

```bash
git clone https://github.com/PeytonFrankeCode/NflCardDB && cd NflCardDB
pip install -e .
cd api
npx wrangler login
npx wrangler d1 create nflcarddb          # paste the id into api/wrangler.toml
npx wrangler d1 execute nflcarddb --remote --file=schema.sql
cd .. && nflcarddb export-api             # writes api/import.sql from the local db
cd api && npx wrangler d1 execute nflcarddb --remote --file=import.sql
```

Then bind it to the Pages project — dashboard → **Settings → Functions → D1
bindings**, variable `DB`, database `nflcarddb` — and redeploy.

Query it directly from a Pages Function. Working example:
`examples/cloudflare/functions/api/sales-direct.js`.

```js
export async function onRequestGet({ request, env }) {
  const { searchParams } = new URL(request.url);
  const rows = await env.DB.prepare(
    `SELECT title, price_cents, sold_date, grader, grade, image_url
     FROM sales
     WHERE player LIKE ? AND best_offer = 0 AND price_cents IS NOT NULL
     ORDER BY sold_date DESC LIMIT 25`
  ).bind(`%${searchParams.get("player")}%`).all();
  return Response.json(rows.results);
}
```

No secret, no quota, no extra hop. This is the recommended option.

**Dashboard alternative:** D1 databases, Workers, and bindings can all be created
by clicking in the Cloudflare dashboard if you would rather not install
wrangler. The only step that genuinely wants CLI is the bulk data import — the
dashboard SQL console is not practical for ~20k rows per day.

---

## Option B — hosted API + key

Use this if the site is *not* on Cloudflare, or something outside the account
needs the data.

```bash
cd NflCardDB && nflcarddb setup-api
```

One command: creates the D1 database, uploads the data, deploys the Worker, and
prints the API URL and a key. Idempotent — re-run freely.

Then in the **website's** Pages project → Settings → Environment variables
(tick Encrypt):

```
NFLCARDDB_API = https://nflcarddb-api.<subdomain>.workers.dev
NFLCARDDB_KEY = nfl_...
```

Redeploy the website — Cloudflare only attaches variables on deploy.

Call it from a Function, never from page JS. Example:
`examples/cloudflare/functions/api/price.js`.

### Endpoints

All require `Authorization: Bearer <key>`. `/` and `/health` do not.

| Endpoint | Returns |
|---|---|
| `GET /v1/summary` | row counts, date range, last update |
| `GET /v1/sales` | sale rows |
| `GET /v1/prices?player=` | median, mean, p10/p90, low, high |
| `GET /v1/players?q=` | most-traded, counts and averages |
| `GET /v1/daily` | per-day totals |

`/v1/sales` filters: `player`, `set` (partial); `team`, `grader`, `card_number`
(exact); `year`, `grade`; `from`, `to` (ISO dates); `rookie`, `auto`;
`min_confidence`; `include_offers`; `limit` (≤500), `offset`.

Errors: `401` no key · `403` bad/revoked · `429` quota · `400` bad date.
Responses carry `X-Quota-Limit` / `X-Quota-Used`.

---

## Three things that will bite you if nobody says them

**1. `price` is null on ~46% of rows, and that is correct.**

Those are best-offer sales. eBay publishes the seller's *asking* price on them,
not what the buyer paid, so there is no sale price to report. Storing null rather
than the ask is deliberate: a null cannot be averaged into your figures by
mistake, an asking price silently can.

They are excluded from `/v1/sales` unless `include_offers=true`, and never
appear in `/v1/prices` or the daily medians. Volume counts do include them —
that is why `sales` and `priced_sales` differ.

**2. `image_url` is a link to eBay, not a copy.**

The front photo of each listing, on eBay's CDN, sized for display (500px longest
edge). Nothing is downloaded — 20,000 photos a day is a storage bill nobody
asked for, and an `<img src>` is what a site needs anyway.

The consequence: **the URL rots.** eBay purges images for old listings around 90
days after the sale, so a photo that works today 404s eventually. Render with an
`onerror` fallback rather than assuming it resolves. If you need photos to
outlive the listing, they have to be copied to R2 or similar — say so and that
can be built, but it is a deliberate cost, not a default.

It is `null` on listings with no usable photo. Served as `image` by the API.

**3. Filter on `confidence` for anything player-driven.**

Sellers write titles freely. `confidence` (0–1) is how much of a title the parser
explained. `>= 0.5` is a sensible floor; about 86% of rows clear it. Below that,
`player` may be missing or wrong — the `title` is always verbatim.

Also: `team` is populated opportunistically and is often null. Do not join on it.

---

## Refresh cadence

Peyton runs `collect.bat` (collects yesterday) and then `api-deploy.bat`
(exports + uploads). Both are manual today.

If you want this automated, the collector must run somewhere with a
residential-looking connection and a signed-in eBay session — it does **not**
work from CI. That constraint is real and was established the hard way: GitHub
Actions runners get bot-checked, and eBay only serves sold listings to
signed-in accounts.

`item_id` is the primary key end to end, so every import is an upsert. Re-running
anything is safe.
