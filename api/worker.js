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
    // Shared by every sale of the same physical card, whatever the seller
    // titled it. Group on this for price history.
    card_key: r.card_key,
    card_name: r.card_name,
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

/**
 * GET /v1/card?key=...  -- one card's sales over time.
 *
 * Split by grade rather than pooled: a PSA 10 and a raw copy of the same card
 * trade at different prices, so a single line through both describes neither.
 */
async function cardHistory(url, env) {
  const key = url.searchParams.get("key");
  if (!key) return fail(400, "missing_key", "Pass ?key=<card_key>");

  const rows = await env.DB.prepare(
    `SELECT sold_date, price_cents, best_offer, item_id, title, image_url,
            card_name, grader, grade
     FROM sales WHERE card_key = ? AND price_cents IS NOT NULL
       AND sold_date IS NOT NULL
     ORDER BY sold_date LIMIT 2000`
  ).bind(key).all();

  const wanted = url.searchParams.get("grade");
  const series = new Map();
  let name = null;
  let image = null;

  for (const r of rows.results || []) {
    const label = r.grader
      ? (r.grade == null ? r.grader : `${r.grader} ${r.grade}`)
      : "Raw";
    if (wanted && label !== wanted) continue;
    name = name || r.card_name;
    image = image || r.image_url;
    if (!series.has(label)) series.set(label, []);
    series.get(label).push({
      date: r.sold_date,
      price: r.price_cents / 100,
      is_ask: !!r.best_offer,
      id: r.item_id,
    });
  }

  if (!series.size) {
    return fail(404, "no_such_card", `No sales found for card_key ${key}`);
  }

  const summarise = (points) => {
    const prices = points.map((p) => p.price).sort((a, b) => a - b);
    const mid = Math.floor(prices.length / 2);
    return {
      n: points.length,
      first: points[0].date,
      last: points[points.length - 1].date,
      low: prices[0],
      high: prices[prices.length - 1],
      median: prices.length % 2 ? prices[mid] : (prices[mid - 1] + prices[mid]) / 2,
      points,
    };
  };

  const by_grade = {};
  for (const [label, points] of [...series.entries()].sort(
         (a, b) => b[1].length - a[1].length)) {
    by_grade[label] = summarise(points);
  }

  return json({
    card_key: key,
    card_name: name,
    image,
    sales: [...series.values()].reduce((n, p) => n + p.length, 0),
    by_grade,
  });
}

/** GET /v1/cards -- the cards actually trading, most sales first. */
// The sort orders a browsing site offers. An allow-list rather than a column
// name off the query string: the value is interpolated into SQL, so accepting
// caller input here would be an injection. Each maps to an index on `cards`.
const QUALITY_TIERS = ["clean", "suspect", "unproven", "bucket"];

const CARD_SORTS = {
  traded:   "sales DESC, median_cents DESC",
  value:    "median_cents DESC, sales DESC",
  cheapest: "median_cents ASC, sales DESC",
  rising:   "trend_pct DESC, sales DESC",
  falling:  "trend_pct ASC, sales DESC",
  recent:   "last_sold DESC, sales DESC",
  newest:   "year DESC, sales DESC",
  oldest:   "year ASC, sales DESC",
  name:     "card_name ASC",
};

function shapeCard(r) {
  return {
    card_key: r.card_key,
    card_name: r.card_name,
    player: r.player,
    team: r.team,
    year: r.year,
    brand: r.brand,
    set: r.set_name,
    subset: r.subset,
    parallel: r.parallel,
    card_number: r.card_number,
    print_run: r.print_run,
    rookie: !!r.is_rookie,
    auto: !!r.is_auto,
    relic: !!r.is_relic,
    // True when no sale of this card ever gave up a card number, so the
    // identity fell back to the player's name. Such a row is every card of
    // that player in that set at once. Served, but never silently: a caller
    // charting it as one card would be charting a bucket.
    numberless: !!r.numberless,
    image_url: r.image_url,
    sales: r.sales,
    median: r.median_cents == null ? null : r.median_cents / 100,
    low: r.low_cents == null ? null : r.low_cents / 100,
    high: r.high_cents == null ? null : r.high_cents / 100,
    // The ungraded market alone. The all-grades median is the number most
    // likely to mislead -- one PSA 10 among twenty raw copies drags it
    // somewhere that describes neither.
    raw_sales: r.raw_sales,
    raw_median: r.raw_median_cents == null ? null : r.raw_median_cents / 100,
    first_sold: r.first_sold,
    last_sold: r.last_sold,
    // Percent change from the older half of this card's sales to the newer,
    // rather than newest against oldest, so one odd sale at either end cannot
    // be the whole trend. Null below four sales.
    trend: r.trend_pct,
    // clean | suspect | unproven | bucket -- see /v1/quality for what each
    // means. `spread` is the number behind the verdict: the card's 90th
    // percentile price over its 10th, inside its largest single grade.
    quality: r.quality,
    spread: r.spread,
  };
}

async function listCards(url, env) {
  const limit = intParam(url, "limit", 50, MAX_LIMIT);
  const offset = intParam(url, "offset", 0) || 0;
  const where = [];
  const binds = [];

  const like = (param, column) => {
    const v = url.searchParams.get(param);
    if (v) { where.push(`${column} LIKE ?`); binds.push(`%${v}%`); }
  };
  const eq = (param, column) => {
    const v = url.searchParams.get(param);
    if (v) { where.push(`${column} = ?`); binds.push(v); }
  };

  const q = url.searchParams.get("q");
  if (q) { where.push("card_name LIKE ?"); binds.push(`%${q}%`); }
  like("player", "player");
  like("set", "set_name");
  like("subset", "subset");
  like("parallel", "parallel");
  eq("team", "team");
  eq("card_number", "card_number");

  const year = url.searchParams.get("year");
  if (year && /^\d{4}$/.test(year)) { where.push("year = ?"); binds.push(Number(year)); }
  for (const [param, op] of [["year_from", ">="], ["year_to", "<="]]) {
    const v = url.searchParams.get(param);
    if (v && /^\d{4}$/.test(v)) { where.push(`year ${op} ?`); binds.push(Number(v)); }
  }

  for (const [param, column] of [["rookie", "is_rookie"], ["auto", "is_auto"],
                                 ["relic", "is_relic"]]) {
    const v = url.searchParams.get(param);
    if (v === "true") where.push(`${column} = 1`);
    if (v === "false") where.push(`${column} = 0`);
  }

  // Buckets are in by default because they are real sales, and out in one
  // parameter because they are not one card.
  if (url.searchParams.get("numbered_only") === "true") where.push("numberless = 0");

  // The whole catalogue is served by default rather than only the tidy part:
  // hiding rows a caller did not ask to hide is how a dataset quietly loses a
  // third of itself. `quality=clean` is the one-parameter version of "just the
  // good ones", and a comma list covers the rest.
  const quality = url.searchParams.get("quality");
  if (quality) {
    const wanted = quality.split(",").map((v) => v.trim()).filter(Boolean);
    const bad = wanted.filter((v) => !QUALITY_TIERS.includes(v));
    if (bad.length) {
      return fail(400, "bad_quality", `Unknown quality ${bad.join(", ")}.`,
                  { valid: QUALITY_TIERS });
    }
    where.push(`quality IN (${wanted.map(() => "?").join(", ")})`);
    binds.push(...wanted);
  }

  // The floor that makes a trend mean anything. A caller sorting by "rising"
  // without it gets cards whose entire history is four sales.
  const minSales = intParam(url, "min_sales", 0);
  if (minSales) { where.push("sales >= ?"); binds.push(minSales); }

  for (const [param, op] of [["min_price", ">="], ["max_price", "<="]]) {
    const v = url.searchParams.get(param);
    if (v && Number.isFinite(Number(v))) {
      where.push(`median_cents ${op} ?`);
      binds.push(Math.round(Number(v) * 100));
    }
  }

  const sortName = url.searchParams.get("sort") || "traded";
  const order = CARD_SORTS[sortName];
  if (!order) {
    return fail(400, "bad_sort",
      `Unknown sort "${sortName}".`, { valid: Object.keys(CARD_SORTS) });
  }
  // Sorting by a trend that does not exist puts the cards with no history at
  // one end of the list, which is never what the caller meant.
  if (sortName === "rising" || sortName === "falling") {
    where.push("trend_pct IS NOT NULL");
  }

  const clause = where.length ? `WHERE ${where.join(" AND ")}` : "";
  const total = await env.DB.prepare(
    `SELECT COUNT(*) AS n FROM cards ${clause}`
  ).bind(...binds).first();

  const rows = await env.DB.prepare(
    `SELECT * FROM cards ${clause} ORDER BY ${order} LIMIT ? OFFSET ?`
  ).bind(...binds, limit, offset).all();

  return json({
    total: total ? total.n : 0,
    limit,
    offset,
    sort: sortName,
    cards: (rows.results || []).map(shapeCard),
  });
}

/** How the catalogue splits by quality, so a site can label its own tabs. */
async function qualityCounts(url, env) {
  const rows = await env.DB.prepare(
    "SELECT quality, COUNT(*) AS n, SUM(sales) AS sales FROM cards GROUP BY quality"
  ).all();
  const counts = {};
  for (const r of rows.results || []) {
    counts[r.quality] = { cards: r.n, sales: r.sales };
  }
  return json({
    tiers: QUALITY_TIERS,
    counts,
    meaning: {
      clean: "Numbered, and its prices agree within each grade.",
      suspect: "Numbered, but prices scatter inside a single grade enough that " +
        "this is probably two cards sharing one key.",
      unproven: "Numbered, but too few sales in any one grade to check.",
      bucket: "No card number was ever read, so the identity fell back to the " +
        "player: this row is every card of that player in that set, not one card.",
    },
  });
}

/** Every market for one card: its PSA 10 price and its raw price side by side. */
async function cardGrades(url, env) {
  const key = url.searchParams.get("key");
  if (!key) return fail(400, "no_key", "Pass ?key=<card_key>.");

  const card = await env.DB.prepare(
    "SELECT * FROM cards WHERE card_key = ?"
  ).bind(key).first();
  if (!card) return fail(404, "not_found", `No card ${key}.`);

  const rows = await env.DB.prepare(
    "SELECT grade_label, sales, median_cents, low_cents, high_cents, last_sold " +
    "FROM card_grades WHERE card_key = ? ORDER BY sales DESC"
  ).bind(key).all();

  return json({
    card: shapeCard(card),
    grades: (rows.results || []).map((r) => ({
      grade: r.grade_label,
      sales: r.sales,
      median: r.median_cents == null ? null : r.median_cents / 100,
      low: r.low_cents == null ? null : r.low_cents / 100,
      high: r.high_cents == null ? null : r.high_cents / 100,
      last_sold: r.last_sold,
    })),
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
      "GET /v1/cards": "the card catalogue, one row per physical card. " +
        "sort: traded|value|cheapest|rising|falling|recent|newest|oldest|name. " +
        "filters: q, player, set, subset, parallel, team, year, year_from, " +
        "year_to, card_number, rookie, auto, relic, numbered_only, min_sales, " +
        "min_price, max_price, quality, limit (max 500), offset. " +
        "quality=clean is the one-parameter version of \"just the good ones\". " +
        "Returns total, so a browsing site can page.",
      "GET /v1/card": "one card's sales over time; ?key=<card_key>, optional grade",
      "GET /v1/card/grades": "one card's markets side by side; ?key=<card_key>",
      "GET /v1/quality": "how the catalogue splits into clean / suspect / " +
        "unproven / bucket, and what each means",
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
        case "/v1/cards":   response = await listCards(url, env); break;
        case "/v1/card":    response = await cardHistory(url, env); break;
        case "/v1/card/grades": response = await cardGrades(url, env); break;
        case "/v1/quality": response = await qualityCounts(url, env); break;
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
