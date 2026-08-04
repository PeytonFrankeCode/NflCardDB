/**
 * Calling the API from a server, so the key is never sent to visitors.
 *
 * This is the arrangement to use when the key must stay secret. The browser
 * talks to your server; your server holds the key and talks to the API. A
 * visitor can see the requests their own browser makes, and none of them
 * contain the key.
 *
 *   npm install express
 *   NFLCARDDB_KEY=nfl_xxx NFLCARDDB_API=https://...workers.dev node server-node.js
 */
import express from "express";

const app = express();
const API = process.env.NFLCARDDB_API;
const KEY = process.env.NFLCARDDB_KEY;   // from the environment, never in the source

if (!API || !KEY) {
  console.error("Set NFLCARDDB_API and NFLCARDDB_KEY before starting.");
  process.exit(1);
}

// Cache upstream answers briefly. Sales for a past day never change, so this
// cuts request volume hard without ever serving anything stale enough to matter.
const cache = new Map();
const TTL_MS = 5 * 60 * 1000;

async function upstream(path) {
  const hit = cache.get(path);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.body;

  const res = await fetch(API + path, { headers: { Authorization: `Bearer ${KEY}` } });
  if (!res.ok) {
    const err = new Error(`upstream ${res.status}`);
    err.status = res.status;
    throw err;
  }
  const body = await res.json();
  cache.set(path, { at: Date.now(), body });
  return body;
}

/**
 * Expose only what your pages need, with parameters you control.
 *
 * Deliberately not a pass-through proxy: forwarding arbitrary query strings
 * would let anyone drive your key at full rate, which defeats the point of
 * keeping it on the server.
 */
app.get("/api/price/:player", async (req, res) => {
  const player = req.params.player.slice(0, 60);
  const qs = new URLSearchParams({ player });
  if (req.query.grader) qs.set("grader", String(req.query.grader).slice(0, 8));
  if (req.query.grade) qs.set("grade", String(Number(req.query.grade) || ""));

  try {
    res.set("Cache-Control", "public, max-age=300");
    res.json(await upstream(`/v1/prices?${qs}`));
  } catch (err) {
    // Never leak the upstream body or the key's quota state to the public.
    res.status(err.status === 429 ? 503 : 502)
       .json({ error: "price lookup unavailable" });
  }
});

app.get("/api/recent", async (req, res) => {
  const limit = Math.min(Number(req.query.limit) || 20, 100);
  try {
    res.set("Cache-Control", "public, max-age=300");
    res.json(await upstream(`/v1/sales?limit=${limit}`));
  } catch {
    res.status(502).json({ error: "unavailable" });
  }
});

app.listen(3000, () => console.log("listening on http://localhost:3000"));
