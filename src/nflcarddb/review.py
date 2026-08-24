"""Measure real accuracy the only way it can be measured: by checking a sample.

`audit` finds errors the data betrays about itself. That is a floor, not an
accuracy figure -- a group can be quietly wrong in a way no internal check can
see. "Is this sale really that card" compares a title against reality, and
reality is not in the database.

So: draw a random sample, put it in a spreadsheet with a blank column, have a
person mark each row, and count. Tedious, and the only thing that produces a
number worth quoting.

Sample size matters more than people expect. 100 rows gives roughly +/-10
percentage points at 95% confidence -- enough to tell 60% from 90%, not enough
to tell 88% from 92%. `margin_of_error` is reported alongside the score so the
number never gets quoted more precisely than it deserves.
"""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path
from typing import Optional

from . import db as store

# What the reviewer writes. Anything else is treated as "not reviewed" rather
# than guessed at.
YES = {"y", "yes", "1", "true", "correct", "ok"}
NO = {"n", "no", "0", "false", "wrong", "bad"}
SKIP = {"?", "skip", "unsure", "unclear"}

FIELDS = (
    "item_id", "correct", "notes", "title", "card_name", "card_key",
    "player", "year", "set_name", "card_number", "parallel", "grade",
    "confidence", "price", "image_url", "listing",
)


def draw_sample(
    db_path: str,
    size: int = 100,
    seed: Optional[int] = None,
    min_confidence: Optional[float] = None,
    keyed_only: bool = True,
) -> list[dict]:
    """A random sample of parsed sales, ready to be checked by hand.

    Random rather than "the first N": the first N are one day's collection in
    price order, and judging the parser on the cheapest cards of one Tuesday
    would measure the wrong thing.
    """
    where = ["s.sold_date IS NOT NULL"]
    params: list = []
    if keyed_only:
        where.append("c.card_key IS NOT NULL")
    if min_confidence is not None:
        where.append("c.confidence >= ?")
        params.append(min_confidence)

    conn = store.connect(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT s.item_id, s.title, s.price_cents, s.image_url,
                   c.card_key, c.card_name, c.player, c.year, c.set_name,
                   c.card_number, c.parallel, c.grader, c.grade, c.confidence
            FROM sales s JOIN cards c USING (item_id)
            WHERE {' AND '.join(where)}
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    rng = random.Random(seed)
    picked = rng.sample(rows, min(size, len(rows)))

    out = []
    for r in picked:
        grade = (f"{r['grader']} {r['grade']:g}"
                 if r["grader"] and r["grade"] is not None
                 else (r["grader"] or "Raw"))
        out.append({
            "item_id": r["item_id"],
            "correct": "",
            "notes": "",
            "title": r["title"],
            "card_name": r["card_name"] or "",
            "card_key": r["card_key"] or "",
            "player": r["player"] or "",
            "year": r["year"] or "",
            "set_name": r["set_name"] or "",
            "card_number": r["card_number"] or "",
            "parallel": r["parallel"] or "",
            "grade": grade,
            "confidence": r["confidence"],
            "price": (round(r["price_cents"] / 100.0, 2)
                      if r["price_cents"] is not None else ""),
            "image_url": r["image_url"] or "",
            "listing": f"https://www.ebay.com/itm/{r['item_id']}",
        })
    return out


def write_sample(rows: list[dict], out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def score(path: str | Path) -> dict:
    """Turn a marked-up sample into a percentage, with its margin of error."""
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        raise ValueError(f"{path} has no rows in it.")
    if "correct" not in (rows[0] or {}):
        raise ValueError(
            f"{path} has no 'correct' column. Use the file that "
            "`nflcarddb review` wrote, and fill in that column."
        )

    yes = no = skipped = blank = 0
    wrong: list[dict] = []
    for row in rows:
        mark = (row.get("correct") or "").strip().lower()
        if mark in YES:
            yes += 1
        elif mark in NO:
            no += 1
            wrong.append({
                "title": row.get("title", "")[:80],
                "card_name": row.get("card_name", ""),
                "confidence": row.get("confidence", ""),
                "notes": row.get("notes", ""),
            })
        elif mark in SKIP:
            skipped += 1
        else:
            blank += 1

    judged = yes + no
    if not judged:
        raise ValueError(
            "Nothing was marked. Put y or n in the 'correct' column, "
            "then run this again."
        )

    rate = yes / judged
    # Wilson would be better at the extremes, but the plain interval is what
    # people recognise, and this is a sanity check rather than a study.
    margin = 1.96 * math.sqrt(rate * (1 - rate) / judged)

    return {
        "reviewed": judged,
        "correct": yes,
        "wrong": no,
        "unsure": skipped,
        "not_reviewed": blank,
        "accuracy": round(rate, 4),
        "margin_of_error": round(margin, 4),
        "range": [round(max(0.0, rate - margin), 4),
                  round(min(1.0, rate + margin), 4)],
        "wrong_examples": wrong[:10],
    }
