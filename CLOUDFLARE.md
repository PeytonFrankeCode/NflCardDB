# Wiring the data into a Cloudflare site

Your website is already on Cloudflare, which means you have a better option than
an API key. Both are written up here; pick one.

---

## Option A — bind D1 straight to your site (recommended)

If the website and the data are in the same Cloudflare account, the website can
read the database directly. No API key, no quota, no HTTP hop, nothing extra to
deploy — and **no secret that can leak**, because there isn't one.

**1. Bind the database to your Pages project**

Cloudflare dashboard → your Pages project → **Settings** → **Functions** →
**D1 database bindings** → **Add binding**:

| Field | Value |
|---|---|
| Variable name | `DB` |
| D1 database | `nflcarddb` |

Add it for **Production** (and Preview if you use it). Redeploy afterwards —
bindings only attach on a new deployment.

**2. Add a function**

Copy `examples/cloudflare/functions/api/sales-direct.js` into your site at
`functions/api/sales.js`. Your pages can then call `/api/sales?player=Stroud`.

**3. That's it**

Nothing to rotate, nothing to keep secret, no per-request quota. The key
management below simply doesn't apply.

The catch: this only works from *your* Cloudflare account. If anything outside
it ever needs the data, you want Option B as well — and you can run both.

---

## Option B — the API with a key

Use this if your site is elsewhere, or you want other people or apps to be able
to read the data.

### The short way: `api-setup.bat`

Double-click it. It signs you in to Cloudflare, creates the database, uploads
your sales, deploys the API, and prints the two values to paste into your
website. A few minutes, once.

Everything below is the same thing done by hand, if you would rather see each
step. Both are safe to re-run.

### Your key

```
nfl_yu7GC98MVAHyjAukYpUxO9HrzsAG1zNfZMrTI_B9PQ4
```

Its hash — this is what goes in the database, and it is not sensitive:

```
b726105b3bd306337cd20bb0c5bd64b02debf1550f1f98178749c5fe0eb43558
```

> **This key was generated in a chat, so treat it as slightly used.** It works
> fine, but if you would rather have one that has never appeared in a
> conversation, mint your own — it is one command, and the steps are identical:
> ```
> nflcarddb api-key --label website
> ```

### 1. Activate it

The API only accepts keys it knows about, so include the hash in your next
upload:

```bash
nflcarddb export-api --add-key b726105b3bd306337cd20bb0c5bd64b02debf1550f1f98178749c5fe0eb43558:website
cd api && npx wrangler d1 execute nflcarddb --remote --file=import.sql
```

After that, `api-deploy.bat` keeps it activated automatically — the key stays in
the database.

### 2. Add it to your site as a secret

**Dashboard:** your Pages project → **Settings** → **Environment variables** →
**Add variable** → tick **Encrypt**:

| Name | Value |
|---|---|
| `NFLCARDDB_KEY` | `nfl_yu7GC98MVAHyjAukYpUxO9HrzsAG1zNfZMrTI_B9PQ4` |
| `NFLCARDDB_API` | `https://nflcarddb-api.YOUR-NAME.workers.dev` |

Add them to **Production**, and redeploy — variables attach on deployment.

**Or by command line:**

```bash
npx wrangler pages secret put NFLCARDDB_KEY --project-name YOUR-PROJECT
```

### 3. Use it from a Function, never from the browser

Copy `examples/cloudflare/functions/api/price.js` to `functions/api/price.js`.

This is the part that matters: **the Function runs on Cloudflare's servers**, so
`env.NFLCARDDB_KEY` never reaches a visitor. Your page calls `/api/price`, and
the Function calls the API.

```js
// in your page -- no key anywhere
const res = await fetch(`/api/price?player=${encodeURIComponent(name)}`);
const stats = await res.json();
```

**Do not put the key in page JavaScript.** Anything the browser can send, a
visitor can read out of the page source. Encrypting the variable protects it at
rest in Cloudflare; it does nothing once you print it into a page.

---

## Which to pick

| | Option A (D1 binding) | Option B (API + key) |
|---|---|---|
| Secret to protect | none | one key |
| Speed | fastest — same datacentre | one extra hop |
| Request limits | D1's own | your key's quota |
| Usable outside your account | no | yes |
| Setup | one binding | key + secret + deploy |

**Start with A.** Add B later if something outside Cloudflare ever needs the
data — they coexist happily, since both just read the same D1 database.

---

## Rotating or revoking

Mint a new one, activate it, then switch the secret over:

```bash
nflcarddb api-key --label website-v2
nflcarddb export-api --add-key <NEW_HASH>:website-v2
```

Kill the old one:

```bash
cd api
npx wrangler d1 execute nflcarddb --remote \
  --command="UPDATE api_keys SET revoked=1 WHERE label='website'"
```

Effective immediately — every request checks.
