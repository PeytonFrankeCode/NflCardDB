/**
 * Tests for the read API, run against real SQL rather than a mock.
 *
 * `env.DB` is stubbed over node:sqlite, which is the same engine D1 runs, so
 * the queries in worker.js are executed rather than string-matched. That is the
 * point: the catalogue endpoint builds its WHERE clause and its ORDER BY from
 * query parameters, and a test that only checked the JSON shape would pass with
 * a sort that silently does nothing.
 *
 *   node --test api/worker.test.mjs
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

import worker from "./worker.js";

const KEY = "nfl_test_key";
const KEY_HASH = createHash("sha256").update(KEY).digest("hex");

/** The slice of the D1 client surface worker.js actually uses. */
function d1(db) {
  return {
    prepare(sql) {
      let bound = [];
      const stmt = {
        bind(...args) { bound = args; return stmt; },
        async first() { return db.prepare(sql).get(...bound) ?? null; },
        async all() { return { results: db.prepare(sql).all(...bound) }; },
        async run() { return db.prepare(sql).run(...bound); },
      };
      return stmt;
    },
  };
}

function card(over = {}) {
  return {
    card_key: "k", card_name: "A Card", player: "Someone", team: null,
    year: 2024, brand: "Panini", set_name: "Prizm", subset: null,
    parallel: null, card_number: "1", print_run: null, is_rookie: 0,
    is_auto: 0, is_relic: 0, numberless: 0, image_url: null, sales: 5,
    median_cents: 1000, low_cents: 500, high_cents: 2000, raw_sales: 5,
    raw_median_cents: 1000, first_sold: "2026-01-01", last_sold: "2026-02-01",
    trend_pct: 0, ...over,
  };
}

function makeEnv(cards = []) {
  const db = new DatabaseSync(":memory:");
  db.exec(readFileSync(new URL("./schema.sql", import.meta.url), "utf8"));
  db.prepare(
    "INSERT INTO api_keys (key_hash, label, created_at) VALUES (?, 'test', '2026-01-01')"
  ).run(KEY_HASH);
  const cols = Object.keys(card());
  const insert = db.prepare(
    `INSERT INTO cards (${cols.join(", ")}) VALUES (${cols.map(() => "?").join(", ")})`
  );
  for (const c of cards) insert.run(...cols.map((k) => c[k]));
  return { DB: d1(db), db };
}

async function get(env, path) {
  const res = await worker.fetch(
    new Request(`https://x${path}`, { headers: { Authorization: `Bearer ${KEY}` } }),
    env
  );
  return { status: res.status, body: await res.json() };
}

const CATALOGUE = [
  card({ card_key: "cheap", card_name: "Cheap One", sales: 30,
         median_cents: 200, trend_pct: -40, year: 2020 }),
  card({ card_key: "riser", card_name: "Rising Star", player: "Caleb Williams",
         sales: 12, median_cents: 5000, trend_pct: 88.5, year: 2024,
         is_rookie: 1 }),
  card({ card_key: "grail", card_name: "The Grail", sales: 4,
         median_cents: 330000, trend_pct: null, year: 2000, is_auto: 1 }),
  // A distinct year on every card, so a sort that ties and falls back to
  // `sales DESC` cannot be mistaken for a sort that works.
  card({ card_key: "bucket", card_name: "No Number Bucket", sales: 50,
         median_cents: 1500, trend_pct: 5, numberless: 1, card_number: null,
         year: 2015 }),
];

test("a key is required", async () => {
  const env = makeEnv(CATALOGUE);
  const res = await worker.fetch(new Request("https://x/v1/cards"), env);
  assert.equal(res.status, 401);
});

test("the catalogue pages, and reports how many there are in total", async () => {
  const env = makeEnv(CATALOGUE);
  const { body } = await get(env, "/v1/cards?limit=2");
  assert.equal(body.total, 4);
  assert.equal(body.cards.length, 2);
  const second = await get(env, "/v1/cards?limit=2&offset=2");
  assert.equal(second.body.cards.length, 2);
  const seen = [...body.cards, ...second.body.cards].map((c) => c.card_key);
  assert.equal(new Set(seen).size, 4, "paging must not repeat or drop a card");
});

test("each sort actually orders by what it names", async () => {
  const env = makeEnv(CATALOGUE);
  const first = async (q) => (await get(env, q)).body.cards[0].card_key;
  assert.equal(await first("/v1/cards?sort=traded"), "bucket");   // 50 sales
  assert.equal(await first("/v1/cards?sort=value"), "grail");     // $3,300
  assert.equal(await first("/v1/cards?sort=cheapest"), "cheap");  // $2
  assert.equal(await first("/v1/cards?sort=rising"), "riser");    // +88.5%
  assert.equal(await first("/v1/cards?sort=falling"), "cheap");   // -40%
  assert.equal(await first("/v1/cards?sort=newest"), "riser");    // 2024
  assert.equal(await first("/v1/cards?sort=oldest"), "grail");    // 2000
});

test("an unknown sort is refused rather than ignored", async () => {
  // The value is interpolated into SQL. Anything but an allow-list here is an
  // injection, and silently falling back to a default would hide it.
  const env = makeEnv(CATALOGUE);
  const { status, body } = await get(env, "/v1/cards?sort=sales;DROP TABLE cards--");
  assert.equal(status, 400);
  assert.equal(body.error.code, "bad_sort");
  assert.ok(body.error.valid.includes("traded"));
});

test("sorting by trend excludes cards that have none", async () => {
  const env = makeEnv(CATALOGUE);
  const { body } = await get(env, "/v1/cards?sort=rising");
  assert.ok(!body.cards.some((c) => c.trend === null),
    "a card with no trend must not be ranked by trend");
  assert.equal(body.total, 3);
});

test("buckets are included by default and excluded on request", async () => {
  const env = makeEnv(CATALOGUE);
  const all = await get(env, "/v1/cards");
  assert.ok(all.body.cards.some((c) => c.numberless));
  const numbered = await get(env, "/v1/cards?numbered_only=true");
  assert.equal(numbered.body.total, 3);
  assert.ok(!numbered.body.cards.some((c) => c.numberless));
});

test("filters narrow the catalogue", async () => {
  const env = makeEnv(CATALOGUE);
  assert.equal((await get(env, "/v1/cards?player=caleb")).body.total, 1);
  assert.equal((await get(env, "/v1/cards?rookie=true")).body.total, 1);
  assert.equal((await get(env, "/v1/cards?auto=true")).body.total, 1);
  assert.equal((await get(env, "/v1/cards?year_from=2024")).body.total, 1);
  assert.equal((await get(env, "/v1/cards?min_sales=20")).body.total, 2);
  assert.equal((await get(env, "/v1/cards?min_price=100")).body.total, 1);
  assert.equal((await get(env, "/v1/cards?max_price=20")).body.total, 2);
});

test("a search term is not read as SQL", async () => {
  const env = makeEnv(CATALOGUE);
  const { body } = await get(env, "/v1/cards?q=" + encodeURIComponent("'; DROP TABLE cards--"));
  assert.equal(body.total, 0);
  const after = await get(env, "/v1/cards");
  assert.equal(after.body.total, 4, "the table must still be there");
});

test("one card's markets come back side by side", async () => {
  const env = makeEnv(CATALOGUE);
  env.db.prepare(
    "INSERT INTO card_grades (card_key, grade_label, sales, median_cents, " +
    "low_cents, high_cents, last_sold) VALUES (?, ?, ?, ?, ?, ?, ?)"
  ).run("riser", "PSA 10", 4, 20000, 15000, 25000, "2026-02-01");
  env.db.prepare(
    "INSERT INTO card_grades (card_key, grade_label, sales, median_cents, " +
    "low_cents, high_cents, last_sold) VALUES (?, ?, ?, ?, ?, ?, ?)"
  ).run("riser", "Raw", 8, 5000, 3000, 7000, "2026-02-01");

  const { body } = await get(env, "/v1/card/grades?key=riser");
  assert.equal(body.card.card_name, "Rising Star");
  assert.deepEqual(body.grades.map((g) => g.grade), ["Raw", "PSA 10"]);
  assert.equal(body.grades[0].median, 50);
  assert.equal(body.grades[1].median, 200);
});

test("an unknown card is a 404, not an empty card", async () => {
  const env = makeEnv(CATALOGUE);
  const { status, body } = await get(env, "/v1/card/grades?key=nope");
  assert.equal(status, 404);
  assert.equal(body.error.code, "not_found");
});

test("prices leave the API as dollars, never cents", async () => {
  const env = makeEnv(CATALOGUE);
  const { body } = await get(env, "/v1/cards?sort=value&limit=1");
  assert.equal(body.cards[0].median, 3300);
  assert.ok(!JSON.stringify(body).includes("_cents"));
});
