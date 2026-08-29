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
    -- The insert set. Part of the card's identity, because an insert restarts
    -- its numbering at one: without it, Phoenix "Contours #8" and "Genies #8"
    -- are the same card as far as any query can tell.
    subset         TEXT,
    parallel       TEXT,
    card_number    TEXT,
    print_run      INTEGER,
    grader         TEXT,
    grade          REAL,
    -- Shared by every sale of the same physical card. Group on this for price
    -- history; add grader/grade to it for a single market unit. NULL when the
    -- parse was too thin to identify a card.
    card_key       TEXT,
    card_name      TEXT,
    is_rookie      INTEGER NOT NULL DEFAULT 0,
    is_auto        INTEGER NOT NULL DEFAULT 0,
    is_relic       INTEGER NOT NULL DEFAULT 0,
    confidence     REAL NOT NULL DEFAULT 0
);

-- Query shapes the API actually serves, one index each.
CREATE INDEX IF NOT EXISTS idx_sales_date   ON sales (sold_date DESC);
CREATE INDEX IF NOT EXISTS idx_sales_player ON sales (player, sold_date DESC);
CREATE INDEX IF NOT EXISTS idx_sales_set    ON sales (set_name, sold_date DESC);
CREATE INDEX IF NOT EXISTS idx_sales_grade  ON sales (grader, grade);
-- The price-history query: one card, in date order.
CREATE INDEX IF NOT EXISTS idx_sales_card   ON sales (card_key, sold_date);

-- One row per physical card: the catalogue a browsing site pages through.
--
-- Precomputed for the same reason `daily` is. Sorting cards by "biggest riser"
-- means computing a trend for every group on every request, and D1 bills by
-- rows scanned -- so a site that offers six sort orders would re-derive the
-- whole table six ways per visitor. The writer already computes these numbers
-- once, locally, where they cost nothing.
--
-- Grade is deliberately NOT part of a card here. A PSA 10 and a raw copy are
-- the same cardboard, so they are one row -- but they are different markets, so
-- the price columns come in both flavours and `card_grades` holds the split.
CREATE TABLE IF NOT EXISTS cards (
    card_key       TEXT PRIMARY KEY,
    card_name      TEXT,
    player         TEXT,
    team           TEXT,
    year           INTEGER,
    brand          TEXT,
    set_name       TEXT,
    subset         TEXT,
    parallel       TEXT,
    card_number    TEXT,
    print_run      INTEGER,
    is_rookie      INTEGER NOT NULL DEFAULT 0,
    is_auto        INTEGER NOT NULL DEFAULT 0,
    is_relic       INTEGER NOT NULL DEFAULT 0,
    -- 1 when no sale of this card ever yielded a card number, so the key fell
    -- back to the player's name. Such a row is every card of that player in
    -- that set gathered together -- a bucket, not a card. Served rather than
    -- hidden, and flagged so a caller can exclude it in one filter.
    numberless     INTEGER NOT NULL DEFAULT 0,
    image_url      TEXT,
    sales          INTEGER NOT NULL,
    median_cents   INTEGER,
    low_cents      INTEGER,
    high_cents     INTEGER,
    -- The ungraded market on its own. The all-grades median is the one number
    -- most likely to mislead: a single PSA 10 among twenty raw copies moves it
    -- somewhere that describes neither market.
    raw_sales      INTEGER NOT NULL DEFAULT 0,
    raw_median_cents INTEGER,
    first_sold     TEXT,
    last_sold      TEXT,
    -- Percent change from the older half of this card's sales to the newer.
    -- NULL below four sales: two points make a line through anything.
    trend_pct      REAL
);

-- One row per card per market. A card's PSA 10 price and its raw price are
-- different questions and a site shows both side by side.
CREATE TABLE IF NOT EXISTS card_grades (
    card_key     TEXT NOT NULL,
    grade_label  TEXT NOT NULL,
    sales        INTEGER NOT NULL,
    median_cents INTEGER,
    low_cents    INTEGER,
    high_cents   INTEGER,
    last_sold    TEXT,
    PRIMARY KEY (card_key, grade_label)
);

-- The sort orders a browsing site offers, one index each. Without these every
-- ORDER BY is a full scan of the catalogue, which is exactly what D1 charges
-- for.
CREATE INDEX IF NOT EXISTS idx_cards_sales  ON cards (sales DESC);
CREATE INDEX IF NOT EXISTS idx_cards_value  ON cards (median_cents DESC);
CREATE INDEX IF NOT EXISTS idx_cards_trend  ON cards (trend_pct DESC);
CREATE INDEX IF NOT EXISTS idx_cards_recent ON cards (last_sold DESC);
CREATE INDEX IF NOT EXISTS idx_cards_player ON cards (player, sales DESC);
CREATE INDEX IF NOT EXISTS idx_cards_set    ON cards (year, set_name, sales DESC);

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
