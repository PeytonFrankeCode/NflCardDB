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


def _same_person(a: str, b: str) -> bool:
    """Whether two folded names are one player written two ways.

    "calebwilliams" and "calebwilliamsfuturestars" are the same player with a
    subset name swept into the field -- a parsing wobble, not a bad group. The
    card those sales belong to is identified by year, set and number, none of
    which the name touches.

    Treating containment as agreement is what stopped this reporting 454
    correctly-grouped cards as grouping failures.
    """
    return a in b or b in a


def contradictory_groups(db_path: str, limit: int = 25) -> list[dict]:
    """Groups whose own sales name genuinely different people.

    Wrong without needing a catalogue: one key, two players, so at least one
    sale is in the wrong group. Names that merely differ by absorbed junk do
    not count -- see `_same_person`.
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
        leader = ordered[0][0]

        # Only names that are not the leading name wearing extra words.
        dissenting = sum(n for name, n in ordered if not _same_person(name, leader))
        if not dissenting:
            continue
        # One odd spelling among fifty is a parser wobble, not a bad group.
        # A genuine split shows the minority holding a real share.
        if dissenting / total < 0.1:
            continue
        bad.append({
            "card_key": key,
            "sales": total,
            "players": [name for name, _ in ordered[:4]],
            "minority_share": round(dissenting / total, 2),
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
    messy = messy_named_groups(db_path, limit=1000)
    splits = number_split_groups(db_path)

    suspect_sales = sum(r["sales"] for r in contradictions)
    stats["number_split"] = splits
    stats["messy_name_groups"] = len(messy)
    stats["contradictory_groups"] = len(contradictions)
    stats["wide_spread_groups"] = len(spreads)
    # The honest headline: a floor, not an accuracy figure.
    stats["known_bad_rate"] = (
        round(suspect_sales / stats["with_key"], 4) if stats["with_key"] else 0.0
    )
    stats["examples"] = {
        "contradictory": contradictions[:5],
        "wide_spread": spreads[:5],
        "messy_names": messy[:5],
    }
    return stats


def number_split_groups(db_path: str, limit: int = 25) -> dict:
    """Sales stranded because the seller did not type the card number.

    `card_key` uses the number when it is there and the player's name when it is
    not, so one physical card owns two possible keys and sales scatter between
    them by nothing more than how much the seller bothered to type.

    Whether that is fixable depends on what else sold from the same set:

    * If (year, set, player, parallel) shows exactly **one** number across every
      sale, an unnumbered sale can only be that card. Recoverable.
    * If it shows several -- a base card and an insert of the same player in the
      same set -- an unnumbered title genuinely does not say which. Merging
      those would invent a fact, so they stay apart.

    Counting the two separately is the point: the first is the size of the prize,
    the second is the floor no amount of parsing gets under.
    """
    conn = store.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT card_key, year, set_name, player, parallel, card_number "
            "FROM cards WHERE card_key IS NOT NULL AND player IS NOT NULL "
            "AND year IS NOT NULL AND set_name IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    buckets: dict[tuple, dict] = {}
    for key, year, set_name, player, parallel, number in rows:
        folded = normalize_player(player)
        if not folded:
            continue
        bucket = (year, set_name.lower(), folded, (parallel or "").lower())
        entry = buckets.setdefault(
            bucket, {"numbers": set(), "unnumbered": 0, "numbered": 0, "keys": set()}
        )
        entry["keys"].add(key)
        if number:
            entry["numbers"].add(str(number).upper())
            entry["numbered"] += 1
        else:
            entry["unnumbered"] += 1

    recoverable = ambiguous = 0
    examples = []
    for bucket, entry in buckets.items():
        if not entry["unnumbered"] or not entry["numbers"]:
            continue
        if len(entry["numbers"]) == 1:
            recoverable += entry["unnumbered"]
            if len(examples) < limit:
                year, set_name, folded, _ = bucket
                examples.append({
                    "year": year,
                    "set_name": set_name,
                    "player": folded,
                    "number": next(iter(entry["numbers"])),
                    "stranded": entry["unnumbered"],
                    "joined": entry["numbered"],
                })
        else:
            ambiguous += entry["unnumbered"]

    examples.sort(key=lambda r: -r["stranded"])
    return {
        "recoverable_sales": recoverable,
        "ambiguous_sales": ambiguous,
        "examples": examples[:limit],
    }


def messy_named_groups(db_path: str, limit: int = 25) -> list[dict]:
    """Correctly grouped cards whose player field varies across their sales.

    Reported apart from the contradictions because the consequence is
    different: the grouping and therefore the price history are right, but a
    page showing "Caleb Williams Future Stars" as a player name looks broken.
    Filed as cosmetic, and a lead on what the title parser is over-reading.
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
            by_key.setdefault(key, {})
            by_key[key][folded] = by_key[key].get(folded, 0) + 1

    out = []
    for key, players in by_key.items():
        if len(players) < 2:
            continue
        ordered = sorted(players.items(), key=lambda kv: -kv[1])
        leader = ordered[0][0]
        # Variants of one name only -- a real disagreement belongs above.
        if not all(_same_person(name, leader) for name, _ in ordered):
            continue
        out.append({
            "card_key": key,
            "sales": sum(players.values()),
            "variants": [name for name, _ in ordered[:4]],
        })

    out.sort(key=lambda r: -r["sales"])
    return out[:limit]
