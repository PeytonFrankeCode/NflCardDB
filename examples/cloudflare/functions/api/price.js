/**
 * Cloudflare Pages Function -- /api/price
 *
 * This runs on Cloudflare's servers, not in the visitor's browser, so the key
 * in env.NFLCARDDB_KEY is never sent to anyone. The browser calls this; this
 * calls the API.
 *
 * Put this file at:  functions/api/price.js  in your Pages project.
 * Then the browser can call:  /api/price?player=CJ%20Stroud
 */
export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const player = (url.searchParams.get("player") || "").slice(0, 60);

  if (!player) {
    return json({ error: "player is required" }, 400);
  }
  if (!env.NFLCARDDB_API || !env.NFLCARDDB_KEY) {
    return json({ error: "API not configured" }, 500);
  }

  // Only forward parameters we control. Passing the caller's query string
  // straight through would let anyone drive the key at full rate, which is the
  // whole thing this arrangement exists to prevent.
  const qs = new URLSearchParams({ player });
  const grader = url.searchParams.get("grader");
  const grade = url.searchParams.get("grade");
  if (grader) qs.set("grader", grader.slice(0, 8));
  if (grade && Number.isFinite(Number(grade))) qs.set("grade", grade);

  const upstream = await fetch(`${env.NFLCARDDB_API}/v1/prices?${qs}`, {
    headers: { Authorization: `Bearer ${env.NFLCARDDB_KEY}` },
    // Sales for a past day never change, so let Cloudflare cache this hop.
    cf: { cacheTtl: 300, cacheEverything: true },
  });

  if (!upstream.ok) {
    // Return a generic failure: the upstream body would reveal quota state.
    return json({ error: "price lookup unavailable" }, upstream.status === 429 ? 503 : 502);
  }

  return new Response(upstream.body, {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=300",
    },
  });
}

function json(body, status) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}
