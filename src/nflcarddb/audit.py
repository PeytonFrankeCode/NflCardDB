"""Measure how well titles are being read -- and be honest about the limits.

Two questions get called "accuracy" and only one of them can be answered without
a human:

**Detectable errors.** A group whose sales name three different players is wrong
without anyone checking against a catalogue: the group contradicts itself. Same
for a group whose prices span 200x. These are found by looking, and they give a
*lower bound* on the error rate -- every one is definitely wrong, but a group
that looks self-consistent may still be wrong in a way nothing here can see.

**True accuracy.** "Is this sale really that card" needs a person comparing a
title against reality. There is no way around it and no substitute for it, so
`nflcarddb review` draws a sample to check by hand and turns the answers into a
percentage. What this module reports is the part that comes free.

Anyone quoting a single accuracy number should know which of the two it is.
"""

from __future__ import annotations

import statistics
from typing import Optional

from . import db as store
from .card_key import normalize_player

# A group whose top price is this many times its median is probably two
# different cards sharing a key -- a base card and a rare parallel whose
# wording the parser missed, most often.
SPREAD_ALARM = 20.0

# Below this many sales, price spread says nothing: two sales can differ wildly
# for ordinary reasons (condition, timing, a bidding war).
MIN_FOR_SPREAD = 5


def coverage(db_path: str) -> dict:
    """How much of the data is identified at all. Exact, no judgement involved."""
    conn = store.connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        if not total:
            return {"cards": 0}

        keyed = conn.execute(
            "SELECT COUNT(*) FROM cards WHERE card_key IS NOT NULL"
        ).fetchone()[0]
        groups = conn.execute(
            "SELECT COUNT(DISTINCT card_key) FROM cards WHERE card_key IS NOT NULL"
        ).fetchone()[0]
        singletons = conn.execute(
            "SELECT COUNT(*) FROM (SELECT card_key FROM cards "
            "WHERE card_key IS NOT NULL GROUP BY card_key HAVING COUNT(*) = 1)"
        ).fetchone()[0]
        confident = conn.execute(
            "SELECT COUNT(*) FROM cards WHERE confidence >= 0.5"
        ).fetchone()[0]

        buckets = {}
        for low in (0.0, 0.2, 0.4, 0.6, 0.8):
            high = low + 0.2
            n = conn.execute(
                "SELECT COUNT(*) FROM cards WHERE confidence >= ? AND confidence < ?",
                (low, high if high < 1.0 else 1.01),
            ).fetchone()[0]
            buckets[f"{low:.1f}-{high:.1f}"] = n

        return {
            "cards": total,
            "with_key": keyed,
            "key_rate": round(keyed / total, 3),
            "without_key": total - keyed,
            "groups": groups,
            "singleton_groups": singletons,
            # A group of one is a card seen once. Common and fine, but it is not
            # evidence the grouping works -- only repeated cards show that.
            "grouped_sales": keyed - singletons,
            "confident_parses": confident,
            "confidence_buckets": buckets,
        }
    finally:
        conn.close()


def contradictory_groups(db_path: str, limit: int = 25) -> list[dict]:
    """Groups whose own sales disagree about who is on the card.

    Wrong without needing a catalogue: one key, two players, so at least one
    sale is in the wrong group.
    """
    conn = store.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT card_key, player FROM cards "
            "WHERE card_key IS NOT NULL AND player IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    by_key: dict[str, dict[str, int]] = {}
    for key, player in rows:
        folded = normalize_player(player)
        if folded:
            by_key.setdefault(key, {}).setdefault(folded, 0)
            by_key[key][folded] += 1

    bad = []
    for key, players in by_key.items():
        if len(players) < 2:
            continue
        ordered = sorted(players.items(), key=lambda kv: -kv[1])
        total = sum(players.values())
        # One odd spelling among fifty is a parser wobble, not a bad group.
        # A genuine split shows the minority holding a real share.
        minority = total - ordered[0][1]
        if minority / total < 0.1:
            continue
        bad.append({
            "card_key": key,
            "sales": total,
            "players": [name for name, _ in ordered[:4]],
            "minority_share": round(minority / total, 2),
        })

    bad.sort(key=lambda r: -r["sales"])
    return bad[:limit]


def wide_spread_groups(db_path: str, limit: int = 25) -> list[dict]:
    """Groups where one price dwarfs the rest, within a single grade.

    Same card, same condition, wildly different money usually means two cards
    share a key -- a base and a parallel whose wording was missed.
    """
    conn = store.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT c.card_key, c.card_name, c.grader, c.grade, s.price_cents
            FROM cards c JOIN sales s USING (item_id)
            WHERE c.card_key IS NOT NULL AND s.price_cents IS NOT NULL
              AND s.best_offer = 0 AND s.currency = 'USD'
            """
        ).fetchall()
    finally:
        conn.close()

    groups: dict[tuple, list] = {}
    names: dict[tuple, str] = {}
    for key, name, grader, grade, cents in rows:
        label = (f"{grader} {grade:g}" if grader and grade is not None
                 else (grader or "Raw"))
        bucket = (key, label)
        groups.setdefault(bucket, []).append(cents)
        names.setdefault(bucket, name)

    flagged = []
    for (key, label), prices in groups.items():
        if len(prices) < MIN_FOR_SPREAD:
            continue
        median = statistics.median(prices)
        if median <= 0:
            continue
        ratio = max(prices) / median
        if ratio < SPREAD_ALARM:
            continue
        flagged.append({
            "card_key": key,
            "card_name": names[(key, label)],
            "grade": label,
            "sales": len(prices),
            "median": round(median / 100.0, 2),
            "high": round(max(prices) / 100.0, 2),
            "ratio": round(ratio, 1),
        })

    flagged.sort(key=lambda r: -r["ratio"])
    return flagged[:limit]


def audit(db_path: str) -> dict:
    """Everything measurable without a human labelling anything."""
    stats = coverage(db_path)
    if not stats.get("cards"):
        return stats

    contradictions = contradictory_groups(db_path, limit=1000)
    spreads = wide_spread_groups(db_path, limit=1000)

    suspect_sales = sum(r["sales"] for r in contradictions)
    stats["contradictory_groups"] = len(contradictions)
    stats["wide_spread_groups"] = len(spreads)
    # The honest headline: a floor, not an accuracy figure.
    stats["known_bad_rate"] = (
        round(suspect_sales / stats["with_key"], 4) if stats["with_key"] else 0.0
    )
    stats["examples"] = {
        "contradictory": contradictions[:5],
        "wide_spread": spreads[:5],
    }
    return stats
