-- NflCardDB storage schema.
-- Safe to re-run: every statement is CREATE ... IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS sales (
    item_id        TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    price_cents    INTEGER,
    currency       TEXT NOT NULL DEFAULT 'USD',
    shipping_cents INTEGER,
    total_cents    INTEGER GENERATED ALWAYS AS
                       (COALESCE(price_cents, 0) + COALESCE(shipping_cents, 0)) VIRTUAL,
    sold_date      TEXT,                       -- ISO YYYY-MM-DD
    listing_format TEXT NOT NULL DEFAULT 'unknown',  -- auction | fixed | unknown
    bids           INTEGER,
    best_offer     INTEGER NOT NULL DEFAULT 0, -- 1 = price is the ASK, not the accepted offer
    condition      TEXT,
    seller         TEXT,
    url            TEXT,
    image_url      TEXT,
    query_id       TEXT,
    run_id         TEXT,
    first_seen_at  TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sales_sold_date ON sales (sold_date);
CREATE INDEX IF NOT EXISTS idx_sales_query      ON sales (query_id, sold_date);
CREATE INDEX IF NOT EXISTS idx_sales_run        ON sales (run_id);

-- Structured attributes parsed out of sales.title.
CREATE TABLE IF NOT EXISTS cards (
    item_id       TEXT PRIMARY KEY REFERENCES sales (item_id) ON DELETE CASCADE,
    player        TEXT,
    team          TEXT,
    year          INTEGER,
    brand         TEXT,
    set_name      TEXT,
    parallel      TEXT,
    card_number   TEXT,
    -- The named insert set, when there is one. Part of the identity because
    -- an insert restarts its numbering at one: Phoenix "Contours #8" and
    -- "Genies #8" are different cards sharing a number.
    subset        TEXT,
    -- Claimed out of the title for display but deliberately not part of
    -- the key: keying these split 1,051 cards apart when it was tried.
    variety       TEXT,
    serial_number INTEGER,
    print_run     INTEGER,
    grader        TEXT,
    grade         REAL,
    is_graded     INTEGER NOT NULL DEFAULT 0,
    is_rookie     INTEGER NOT NULL DEFAULT 0,
    is_auto       INTEGER NOT NULL DEFAULT 0,
    is_relic      INTEGER NOT NULL DEFAULT 0,
    confidence    REAL NOT NULL DEFAULT 0,
    -- Shared by every sale of the same physical card, whatever the seller
    -- called it. NULL when the parse was too thin to identify one: a wrong
    -- grouping silently averages two different cards into one price history,
    -- which is worse than no grouping.
    card_key      TEXT,
    card_name     TEXT,
    parser_version TEXT,
    parsed_at     TEXT
);

-- The query behind a price history: one card, oldest sale to newest.
CREATE INDEX IF NOT EXISTS idx_cards_key ON cards (card_key);

CREATE INDEX IF NOT EXISTS idx_cards_player ON cards (player);
CREATE INDEX IF NOT EXISTS idx_cards_lookup ON cards (player, year, set_name, parallel);
CREATE INDEX IF NOT EXISTS idx_cards_grade  ON cards (grader, grade);

-- One row per scrape invocation, for observability and resume.
CREATE TABLE IF NOT EXISTS scrape_runs (
    run_id       TEXT PRIMARY KEY,
    target_date  TEXT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',  -- running | ok | partial | failed
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    items_seen   INTEGER NOT NULL DEFAULT 0,
    items_new    INTEGER NOT NULL DEFAULT 0,
    error        TEXT
);

-- Per-(run, query, price band) progress so an interrupted run can resume
-- instead of re-walking pages it already paid for.
CREATE TABLE IF NOT EXISTS scrape_segments (
    run_id      TEXT NOT NULL,
    segment_id  TEXT NOT NULL,
    query_id    TEXT NOT NULL,
    price_lo    REAL,
    price_hi    REAL,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | done | capped | failed
    pages       INTEGER NOT NULL DEFAULT 0,
    items       INTEGER NOT NULL DEFAULT 0,
    note        TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (run_id, segment_id)
);

-- Full-text search over titles, kept in sync by triggers.
CREATE VIRTUAL TABLE IF NOT EXISTS sales_fts USING fts5 (
    title,
    content = 'sales',
    content_rowid = 'rowid'
);

CREATE TRIGGER IF NOT EXISTS sales_fts_ai AFTER INSERT ON sales BEGIN
    INSERT INTO sales_fts (rowid, title) VALUES (new.rowid, new.title);
END;

CREATE TRIGGER IF NOT EXISTS sales_fts_ad AFTER DELETE ON sales BEGIN
    INSERT INTO sales_fts (sales_fts, rowid, title) VALUES ('delete', old.rowid, old.title);
END;

CREATE TRIGGER IF NOT EXISTS sales_fts_au AFTER UPDATE OF title ON sales BEGIN
    INSERT INTO sales_fts (sales_fts, rowid, title) VALUES ('delete', old.rowid, old.title);
    INSERT INTO sales_fts (rowid, title) VALUES (new.rowid, new.title);
END;

-- Convenience view: sales joined to parsed attributes, best-offer rows flagged.
CREATE VIEW IF NOT EXISTS v_sales AS
SELECT s.item_id,
       s.sold_date,
       c.player,
       c.team,
       c.year,
       c.set_name,
       c.parallel,
       c.card_number,
       c.grader,
       c.grade,
       c.is_rookie,
       c.is_auto,
       s.price_cents / 100.0 AS price,
       s.shipping_cents / 100.0 AS shipping,
       s.total_cents / 100.0 AS total,
       s.currency,
       s.listing_format,
       s.bids,
       s.best_offer,
       s.title,
       s.url
FROM sales s
LEFT JOIN cards c USING (item_id);

-- Where a sync last got to, so an upload can send only what changed.
-- Rebuilding the whole export every push is fine at 20,000 rows and absurd at
-- a million; `updated_at` on sales is the watermark, and it moves when a row is
-- re-collected as well as when it is new.
CREATE TABLE IF NOT EXISTS sync_state (
    target     TEXT PRIMARY KEY,        -- e.g. the D1 database id
    pushed_at  TEXT NOT NULL,           -- highest sales.updated_at sent
    rows_sent  INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- Manufacturer checklists: what cards actually exist.
--
-- Everything else in this project infers a card's identity from free text a
-- seller typed. This is the opposite -- a list of the cards that were printed,
-- so identity can be looked up rather than guessed. It answers three questions
-- nothing else can: which insert a number belongs to (#TD-34 is Touchdown),
-- what a set's real parallels are called, and whether a parse names a card that
-- exists at all.
--
-- Keyed by the same card_key the sales carry, so a checklist row and a sale of
-- that card meet on one column.
CREATE TABLE IF NOT EXISTS checklist (
    card_key    TEXT PRIMARY KEY,
    year        INTEGER NOT NULL,
    set_name    TEXT NOT NULL,
    -- The insert set, NULL for a base card. This is the column that pays for
    -- the whole table: an insert restarts numbering at one, and the insert's
    -- name is usually absent from the seller's title.
    subset      TEXT,
    card_number TEXT,
    player      TEXT,
    parallel    TEXT,
    print_run   INTEGER,
    is_auto     INTEGER NOT NULL DEFAULT 0,
    is_relic    INTEGER NOT NULL DEFAULT 0,
    source      TEXT,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checklist_who ON checklist (year, set_name, player);
CREATE INDEX IF NOT EXISTS idx_checklist_num ON checklist (year, set_name, card_number);

-- Which (year, set) pairs the checklist actually covers.
--
-- Without this, "no matching row" is ambiguous between "that card does not
-- exist" and "we have never loaded that product" -- and treating the second as
-- the first would condemn every card in an unloaded set as a bad parse.
CREATE TABLE IF NOT EXISTS checklist_sets (
    year       INTEGER NOT NULL,
    set_name   TEXT NOT NULL,
    cards      INTEGER NOT NULL DEFAULT 0,
    source     TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (year, set_name)
);
