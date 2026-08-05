/**
 * Cloudflare Pages Function -- /api/sales, reading D1 directly.
 *
 * The simpler arrangement when the website and the data live in the same
 * Cloudflare account: bind the D1 database straight to the Pages project and
 * skip the API entirely. No key to leak, no quota, no HTTP hop, nothing to
 * deploy separately.
 *
 * Bind it in the Pages dashboard: Settings -> Functions -> D1 bindings,
 * variable name DB, database nflcarddb.
 */
export async function onRequestGet({ request, env }) {
  if (!env.DB) {
    return json({ error: "no D1 binding named DB" }, 500);
  }

  const url = new URL(request.url);
  const where = ["best_offer = 0", "price_cents IS NOT NULL"];
  const binds = [];

  const player = url.searchParams.get("player");
  if (player) { where.push("player LIKE ?"); binds.push(`%${player.slice(0, 60)}%`); }

  const grader = url.searchParams.get("grader");
  if (grader) { where.push("grader = ?"); binds.push(grader.slice(0, 8)); }

  const limit = Math.min(Number(url.searchParams.get("limit")) || 25, 200);

  // Bound parameters, never string concatenation -- the values come from the
  // public internet.
  const rows = await env.DB.prepare(
    `SELECT item_id, sold_date, title, price_cents, ask_cents, player, grader,
            grade, image_url
     FROM sales WHERE ${where.join(" AND ")}
     ORDER BY sold_date DESC LIMIT ?`
  ).bind(...binds, limit).all();

  return json({
    sales: (rows.results || []).map((r) => ({
      id: r.item_id,
      sold_date: r.sold_date,
      title: r.title,
      price: r.price_cents == null ? null : r.price_cents / 100,
      // Set only where price is null: the card sold, for some unpublished
      // amount below this. An upper bound, not a price -- never average them.
      ask: r.ask_cents == null ? null : r.ask_cents / 100,
      player: r.player,
      // Straight into an <img>. Null on listings with no photo, and eBay
      // purges these ~90 days after the sale, so give it an onerror fallback.
      image: r.image_url || null,
      grade: r.grader ? `${r.grader} ${r.grade}` : "Raw",
      url: `https://www.ebay.com/itm/${r.item_id}`,
    })),
  }, 200);
}

function json(body, status) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=300",
    },
  });
}
