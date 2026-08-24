-- Cloudflare D1 schema for the public read API.
--
-- Deliberately one denormalised table rather than the sales/cards split used
-- locally. The API only ever reads, D1 charges by rows scanned, and a join on
-- every request buys nothing when the writer can flatten once at export time.
--
-- price_cents is NULL for best-offer rows: eBay publishes the seller's asking
-- price on those, not what the buyer paid, so there is no sale price to serve.
-- Storing NULL rather than the ask means no caller can accidentally average it.

CREATE TABLE IF NOT EXISTS sales (
    item_id        TEXT PRIMARY KEY,
    sold_date      TEXT NOT NULL,
    title          TEXT NOT NULL,
    price_cents    INTEGER,
    -- What the seller was asking, populated ONLY on best-offer rows -- that is,
    -- exactly where price_cents is NULL. The buyer paid some unknown amount
    -- below it. Kept in its own column rather than filled into price_cents so
    -- that "we know what this sold for" stays answerable by a NULL check.
    ask_cents      INTEGER,
    shipping_cents INTEGER,
    currency       TEXT NOT NULL DEFAULT 'USD',
    best_offer     INTEGER NOT NULL DEFAULT 0,
    listing_format TEXT,
    bids           INTEGER,
    -- eBay's own CDN URL for the front photo. Only the link is stored; the
    -- image stays on eBay, which also means it disappears when they purge the
    -- listing (roughly 90 days after the sale).
    image_url      TEXT,
    player         TEXT,
    team           TEXT,
    year           INTEGER,
    brand          TEXT,
    set_name       TEXT,
    parallel       TEXT,
    card_number    TEXT,
    grader         TEXT,
    grade          REAL,
    -- Shared by every sale of the same physical card. Group on this for price
    -- history; add grader/grade to it for a single market unit. NULL when the
    -- parse was too thin to identify a card.
    card_key       TEXT,
    card_name      TEXT,
    is_rookie      INTEGER NOT NULL DEFAULT 0,
    is_auto        INTEGER NOT NULL DEFAULT 0,
    confidence     REAL NOT NULL DEFAULT 0
);

-- Query shapes the API actually serves, one index each.
CREATE INDEX IF NOT EXISTS idx_sales_date   ON sales (sold_date DESC);
CREATE INDEX IF NOT EXISTS idx_sales_player ON sales (player, sold_date DESC);
CREATE INDEX IF NOT EXISTS idx_sales_set    ON sales (set_name, sold_date DESC);
CREATE INDEX IF NOT EXISTS idx_sales_grade  ON sales (grader, grade);
-- The price-history query: one card, in date order.
CREATE INDEX IF NOT EXISTS idx_sales_card   ON sales (card_key, sold_date);

-- Precomputed daily rollups: cheap to serve, and the common dashboard call.
CREATE TABLE IF NOT EXISTS daily (
    sold_date    TEXT PRIMARY KEY,
    sales        INTEGER NOT NULL,
    priced       INTEGER NOT NULL,
    median_cents INTEGER,
    p90_cents    INTEGER,
    total_cents  INTEGER
);

-- One row per API key. Keys are stored as SHA-256 hashes, never in the clear,
-- so a copy of this table does not hand over working credentials.
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash    TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0,
    daily_quota INTEGER NOT NULL DEFAULT 10000
);

-- Requests per key per day, for quotas and for seeing what is being used.
CREATE TABLE IF NOT EXISTS usage (
    key_hash TEXT NOT NULL,
    day      TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key_hash, day)
);

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
