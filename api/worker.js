/**
 * NflCardDB read API -- a Cloudflare Worker over D1.
 *
 * Read-only by design: the collector on the user's PC is the only writer, and
 * it pushes through wrangler rather than through this Worker. Nothing here can
 * modify sales data, so a leaked key cannot corrupt the dataset -- only read it.
 *
 * Keys are compared as SHA-256 hashes, so the database never holds a usable
 * credential. Comparison is constant-time to avoid leaking a prefix by timing.
 *
 * A note on where keys can live: a key shipped in browser JavaScript is public,
 * because anyone can read the source. For that case use a `public: 1` key with a
 * low quota and treat it as identification, not protection. Keep real keys on a
 * server.
 */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
  "Access-Control-Max-Age": "86400",
};

const MAX_LIMIT = 500;
const DEFAULT_LIMIT = 100;

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...CORS, ...extra },
  });
}

function fail(status, code, message, extra = {}) {
  return json({ error: { code, message, ...extra } }, status);
}

async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Compare two equal-length hex strings without an early exit. */
function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function bearerFrom(request, url) {
  const header = request.headers.get("Authorization") || "";
  const m = header.match(/^Bearer\s+(.+)$/i);
  if (m) return m[1].trim();
  // Query-string keys are accepted because browsers cannot set headers on an
  // <img> or a plain link, but they end up in logs and Referer -- so they are
  // only sensible for keys that are already public.
  return url.searchParams.get("key");
}

async function authenticate(request, url, env) {
  const presented = bearerFrom(request, url);
  if (!presented) {
    return { error: fail(401, "no_key", "Pass your key as: Authorization: Bearer <key>") };
  }

  const hash = await sha256Hex(presented);
  const row = await env.DB.prepare(
    "SELECT key_hash, label, revoked, daily_quota FROM api_keys WHERE key_hash = ?"
  ).bind(hash).first();

  // Hash the lookup result rather than the input so a wrong key and a revoked
  // key take the same path.
  if (!row || !timingSafeEqual(row.key_hash, hash)) {
    return { error: fail(403, "bad_key", "That key is not recognised.") };
  }
  if (row.revoked) {
    return { error: fail(403, "revoked", "That key has been revoked.") };
  }

  const day = new Date().toISOString().slice(0, 10);
  await env.DB.prepare(
    "INSERT INTO usage (key_hash, day, requests) VALUES (?, ?, 1) " +
    "ON CONFLICT(key_hash, day) DO UPDATE SET requests = requests + 1"
  ).bind(hash, day).run();

  const used = await env.DB.prepare(
    "SELECT requests FROM usage WHERE key_hash = ? AND day = ?"
  ).bind(hash, day).first();

  const count = used ? used.requests : 1;
  if (count > row.daily_quota) {
    return {
      error: fail(429, "quota_exceeded",
        `Daily quota of ${row.daily_quota} requests reached. Resets at 00:00 UTC.`,
        { used: count, quota: row.daily_quota }),
    };
  }

  return { key: { label: row.label, quota: row.daily_quota, used: count } };
}

/** Turn a row into the shape callers see. Cents stay out of the public API. */
function shapeSale(r) {
  return {
    id: r.item_id,
    sold_date: r.sold_date,
    title: r.title,
    // The price eBay published. On a best-offer row that is the seller's ask,
    // and the buyer paid less -- `best_offer` below marks those.
    price: r.price_cents == null ? null : r.price_cents / 100,
    // Set only on best-offer rows, repeating the same number. Kept so callers
    // can find the asks without also carrying the best_offer flag around.
    ask: r.ask_cents == null ? null : r.ask_cents / 100,
    shipping: r.shipping_cents == null ? null : r.shipping_cents / 100,
    currency: r.currency,
    best_offer: !!r.best_offer,
    format: r.listing_format,
    bids: r.bids,
    // eBay's CDN, straight into an <img>. Null when the tile had no photo, and
    // it stops resolving once eBay purges the listing (~90 days after sale).
    image: r.image_url || null,
    player: r.player,
    team: r.team,
    year: r.year,
    brand: r.brand,
    set: r.set_name,
    parallel: r.parallel,
    card_number: r.card_number,
    grader: r.grader,
    grade: r.grade,
    rookie: !!r.is_rookie,
    auto: !!r.is_auto,
    confidence: r.confidence,
    url: `https://www.ebay.com/itm/${r.item_id}`,
  };
}

function intParam(url, name, fallback, max) {
  const raw = url.searchParams.get(name);
  if (raw == null || raw === "") return fallback;
  const n = Number.parseInt(raw, 10);
  if (!Number.isFinite(n) || n < 1) return fallback;
  return max ? Math.min(n, max) : n;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

async function listSales(url, env) {
  const where = [];
  const binds = [];

  const eq = (param, column) => {
    const v = url.searchParams.get(param);
    if (v) { where.push(`${column} = ?`); binds.push(v); }
  };

  // Player and set match loosely, because callers type what they remember.
  const like = (param, column) => {
    const v = url.searchParams.get(param);
    if (v) { where.push(`${column} LIKE ?`); binds.push(`%${v}%`); }
  };

  like("player", "player");
  like("set", "set_name");
  eq("team", "team");
  eq("grader", "grader");
  eq("card_number", "card_number");

  const year = url.searchParams.get("year");
  if (year && /^\d{4}$/.test(year)) { where.push("year = ?"); binds.push(Number(year)); }

  const grade = url.searchParams.get("grade");
  if (grade && Number.isFinite(Number(grade))) {
    where.push("grade = ?"); binds.push(Number(grade));
  }

  for (const [param, column, op] of [["from", "sold_date", ">="], ["to", "sold_date", "<="]]) {
    const v = url.searchParams.get(param);
    if (v) {
      if (!ISO_DATE.test(v)) {
        return fail(400, "bad_date", `${param} must look like 2026-08-03`);
      }
      where.push(`${column} ${op} ?`);
      binds.push(v);
    }
  }

  if (url.searchParams.get("rookie") === "true") where.push("is_rookie = 1");
  if (url.searchParams.get("auto") === "true") where.push("is_auto = 1");

  // Every listing with a published price, best offers included. On those the
  // price is the seller's ask rather than what was paid -- `best_offer` says
  // which, and exclude_offers=true drops them for callers who want only
  // confirmed amounts.
  where.push("price_cents IS NOT NULL");
  if (url.searchParams.get("exclude_offers") === "true") {
    where.push("best_offer = 0");
  }

  const minConf = url.searchParams.get("min_confidence");
  if (minConf && Number.isFinite(Number(minConf))) {
    where.push("confidence >= ?"); binds.push(Number(minConf));
  }

  const limit = intParam(url, "limit", DEFAULT_LIMIT, MAX_LIMIT);
  const offset = intParam(url, "offset", 1) - 1;

  const clause = where.length ? `WHERE ${where.join(" AND ")}` : "";
  const rows = await env.DB.prepare(
    `SELECT * FROM sales ${clause} ORDER BY sold_date DESC, price_cents DESC ` +
    `LIMIT ? OFFSET ?`
  ).bind(...binds, limit + 1, offset).all();

  const results = rows.results || [];
  const more = results.length > limit;

  return json({
    count: Math.min(results.length, limit),
    has_more: more,
    next_offset: more ? offset + limit + 1 : null,
    sales: results.slice(0, limit).map(shapeSale),
  });
}

async function priceSummary(url, env) {
  const player = url.searchParams.get("player");
  if (!player) return fail(400, "missing_player", "Pass ?player=Name");

  const binds = [`%${player}%`];
  let extra = "";
  const grader = url.searchParams.get("grader");
  const grade = url.searchParams.get("grade");
  if (grader) { extra += " AND grader = ?"; binds.push(grader); }
  if (grade && Number.isFinite(Number(grade))) {
    extra += " AND grade = ?"; binds.push(Number(grade));
  }

  // Median needs the values themselves; D1 has no percentile function. Both
  // columns come back in one pass -- D1 bills by rows scanned, and a second
  // query over the same predicate would double that to read a sibling column.
  const rows = await env.DB.prepare(
    `SELECT price_cents, ask_cents FROM sales WHERE player LIKE ?${extra} ` +
    `AND price_cents IS NOT NULL`
  ).bind(...binds).all();

  // Headline figures cover everything with a price. Asks are also reported on
  // their own, so anyone who wants confirmed-only numbers can still get them.
  const prices = [];
  const asks = [];
  for (const r of rows.results || []) {
    prices.push(r.price_cents);
    if (r.ask_cents != null) asks.push(r.ask_cents);
  }
  prices.sort((a, b) => a - b);
  asks.sort((a, b) => a - b);

  const stats = (values) => {
    if (!values.length) return { n: 0, median: null, mean: null, p10: null, p90: null, low: null, high: null };
    const at = (p) => values[Math.min(values.length - 1, Math.floor((values.length - 1) * p))];
    const sum = values.reduce((a, b) => a + b, 0);
    return {
      n: values.length,
      median: at(0.5) / 100,
      mean: Math.round(sum / values.length) / 100,
      p10: at(0.1) / 100,
      p90: at(0.9) / 100,
      low: values[0] / 100,
      high: values[values.length - 1] / 100,
    };
  };

  const sold = stats(prices);
  return json({
    player,
    grader: grader || null,
    grade: grade ? Number(grade) : null,
    // Every priced row, asks included.
    matched: sold.n,
    median: sold.median,
    mean: sold.mean,
    p10: sold.p10,
    p90: sold.p90,
    low: sold.low,
    high: sold.high,
    // The best-offer subset of the same rows, reported separately so the
    // effect of including them is visible: these are asks, and each card went
    // for some unpublished amount below its own.
    asking: stats(asks),
  });
}

async function listPlayers(url, env) {
  const q = url.searchParams.get("q");
  const limit = intParam(url, "limit", 50, 200);
  const binds = [];
  let clause = "WHERE player IS NOT NULL AND confidence >= 0.5 " +
               "AND price_cents IS NOT NULL";
  if (q) { clause += " AND player LIKE ?"; binds.push(`%${q}%`); }

  const rows = await env.DB.prepare(
    `SELECT player, team, COUNT(*) AS n, ` +
    `CAST(AVG(price_cents) AS INTEGER) AS avg_cents, ` +
    `MAX(price_cents) AS max_cents FROM sales ${clause} ` +
    `GROUP BY player ORDER BY n DESC LIMIT ?`
  ).bind(...binds, limit).all();

  return json({
    count: (rows.results || []).length,
    players: (rows.results || []).map((r) => ({
      player: r.player,
      team: r.team,
      sales: r.n,
      average: r.avg_cents / 100,
      highest: r.max_cents / 100,
    })),
  });
}

async function daily(url, env) {
  const limit = intParam(url, "limit", 90, 400);
  const rows = await env.DB.prepare(
    "SELECT * FROM daily ORDER BY sold_date DESC LIMIT ?"
  ).bind(limit).all();
  return json({
    days: (rows.results || []).map((r) => ({
      date: r.sold_date,
      sales: r.sales,
      priced: r.priced,
      median: r.median_cents == null ? null : r.median_cents / 100,
      p90: r.p90_cents == null ? null : r.p90_cents / 100,
      total: r.total_cents == null ? null : r.total_cents / 100,
    })),
  });
}

async function summary(env) {
  const totals = await env.DB.prepare(
    "SELECT COUNT(*) AS sales, " +
    "SUM(CASE WHEN price_cents IS NOT NULL THEN 1 ELSE 0 END) AS priced, " +
    "SUM(best_offer) AS best_offers, MIN(sold_date) AS first_day, MAX(sold_date) AS last_day " +
    "FROM sales"
  ).first();
  const updated = await env.DB.prepare("SELECT v FROM meta WHERE k = 'updated_at'").first();

  return json({
    sales: totals.sales,
    priced_sales: totals.priced,
    best_offer_sales: totals.best_offers,
    first_day: totals.first_day,
    last_day: totals.last_day,
    updated_at: updated ? updated.v : null,
    note: "Price statistics include best offers. eBay publishes the seller's " +
          "ask on those, not what the buyer paid, so figures read slightly " +
          "high; `best_offer` flags the rows and `ask` repeats the number. " +
          "Pass exclude_offers=true to /v1/sales for confirmed prices only.",
  });
}

function index() {
  return json({
    service: "NflCardDB",
    version: 1,
    auth: "Authorization: Bearer <your key>",
    endpoints: {
      "GET /v1/summary": "dataset totals and freshness",
      "GET /v1/sales": "sale rows; filters: player, set, team, year, grader, " +
        "grade, card_number, from, to, rookie, auto, min_confidence, " +
        "exclude_offers, limit (max 500), offset",
      "GET /v1/prices": "price stats for one player; ?player= plus optional grader, grade",
      "GET /v1/players": "most-traded players; ?q= to search",
      "GET /v1/daily": "per-day totals",
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
    if (request.method !== "GET") {
      return fail(405, "method_not_allowed", "This API is read-only.");
    }

    // Health and index need no key, so a misconfigured caller can still tell
    // the difference between "down" and "unauthorised".
    if (url.pathname === "/" || url.pathname === "/v1") return index();
    if (url.pathname === "/health") return json({ ok: true });

    if (!env.DB) {
      return fail(503, "no_database", "This Worker has no D1 binding named DB.");
    }

    const auth = await authenticate(request, url, env);
    if (auth.error) return auth.error;

    const headers = {
      "X-Quota-Limit": String(auth.key.quota),
      "X-Quota-Used": String(auth.key.used),
      // Sales for a past day never change, so let callers and the edge cache.
      "Cache-Control": "public, max-age=300",
    };

    try {
      let response;
      switch (url.pathname) {
        case "/v1/summary": response = await summary(env); break;
        case "/v1/sales":   response = await listSales(url, env); break;
        case "/v1/prices":  response = await priceSummary(url, env); break;
        case "/v1/players": response = await listPlayers(url, env); break;
        case "/v1/daily":   response = await daily(url, env); break;
        default:
          return fail(404, "not_found", `No endpoint ${url.pathname}. See / for the list.`);
      }
      const merged = new Headers(response.headers);
      for (const [k, v] of Object.entries(headers)) merged.set(k, v);
      return new Response(response.body, { status: response.status, headers: merged });
    } catch (err) {
      // Never surface SQL or stack traces to callers.
      console.error("query failed", err);
      return fail(500, "query_failed", "The query could not be completed.");
    }
  },
};
