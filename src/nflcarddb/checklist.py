"""What cards actually exist, as opposed to what a seller typed.

Every other part of this project infers a card's identity from free text. That
works until the text does not carry the information, and measurement says it
frequently does not: of 1,500 real titles, 736 yield no card number, and 701 of
those contain no number anywhere in the title. No parser recovers what is not
written down.

A checklist inverts the problem. Instead of guessing which card a listing is,
the printed cards are known in advance and a listing is matched against them.
That answers three questions nothing else can:

* **Which insert a number belongs to.** "#TD-34" is a Touchdown card; the
  prefix is the insert and the title rarely says so.
* **What a set's parallels are really called.** The current parallel list is
  guessed from colour words, which is why "Prizmania" and "Kaiju" are missed.
* **Whether a parse names a real card at all.** Nothing checks that today, so a
  mis-parse becomes a confident wrong answer.

Two deliberate limits, because a checklist is not magic:

**A missing match is not a missing card** unless the product was loaded. That is
what `checklist_sets` is for -- without it, every card in an unloaded set looks
like a bad parse.

**Ambiguity survives.** "2026 Topps Josh Allen" with no number is #TD-16, #WC-1,
#PC-7 or #S-5, and the checklist says so rather than resolving it. Knowing there
are four candidates is worth a great deal; picking one at random is worth less
than nothing.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Iterable, Iterator, Optional

from .card_key import card_key, normalize_player
from .models import CardAttrs

# What a checklist row may carry. Only year, set and one of number/player are
# required -- sources differ, and a partial checklist still beats none.
FIELDS = ("year", "set_name", "subset", "card_number", "player", "parallel",
          "print_run", "is_auto", "is_relic")


def _attrs(row: dict) -> CardAttrs:
    """A checklist row as the same shape a parsed title produces.

    Going through CardAttrs rather than building a key by hand is the point: the
    checklist and the sales then use one identity function, so they cannot drift
    into keying the same card two ways.
    """
    return CardAttrs(
        year=row.get("year"),
        set_name=row.get("set_name"),
        subset=row.get("subset") or None,
        card_number=(str(row["card_number"]).strip()
                     if row.get("card_number") not in (None, "") else None),
        player=row.get("player") or None,
        parallel=row.get("parallel") or None,
        print_run=row.get("print_run"),
        is_auto=bool(row.get("is_auto")),
        is_relic=bool(row.get("is_relic")),
        # A checklist states facts rather than guessing at them, so nothing here
        # is uncertain. Without this the shared key function would reject every
        # row for thin confidence.
        confidence=1.0,
    )


def normalise(rows: Iterable[dict]) -> Iterator[dict]:
    """Drop rows too thin to identify anything, and key the rest."""
    for row in rows:
        if not row.get("year") or not row.get("set_name"):
            continue
        if not row.get("card_number") and not row.get("player"):
            continue
        attrs = _attrs(row)
        key = card_key(attrs)
        if not key:
            continue
        yield {
            "card_key": key,
            "year": int(attrs.year),
            "set_name": attrs.set_name,
            "subset": attrs.subset,
            "card_number": attrs.card_number,
            "player": attrs.player,
            "parallel": attrs.parallel,
            "print_run": attrs.print_run,
            "is_auto": int(attrs.is_auto),
            "is_relic": int(attrs.is_relic),
        }


def import_rows(conn: sqlite3.Connection, rows: Iterable[dict],
                source: str = "import") -> dict:
    """Load checklist rows, replacing any previous copy of the same card.

    Upsert rather than append: a checklist gets corrected and reloaded, and a
    second import must not double every card.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    kept = list(normalise(rows))
    conn.executemany(
        "INSERT INTO checklist (card_key, year, set_name, subset, card_number, "
        " player, parallel, print_run, is_auto, is_relic, source, updated_at) "
        "VALUES (:card_key, :year, :set_name, :subset, :card_number, :player, "
        " :parallel, :print_run, :is_auto, :is_relic, :source, :updated_at) "
        "ON CONFLICT(card_key) DO UPDATE SET "
        " subset=excluded.subset, card_number=excluded.card_number, "
        " player=excluded.player, parallel=excluded.parallel, "
        " print_run=excluded.print_run, is_auto=excluded.is_auto, "
        " is_relic=excluded.is_relic, source=excluded.source, "
        " updated_at=excluded.updated_at",
        [{**r, "source": source, "updated_at": now} for r in kept],
    )
    # Recorded from what actually landed, not from what the caller intended, so
    # a partly-failed import cannot claim coverage it does not have.
    conn.execute(
        "INSERT INTO checklist_sets (year, set_name, cards, source, updated_at) "
        "SELECT year, set_name, COUNT(*), ?, ? FROM checklist "
        "GROUP BY year, set_name "
        "ON CONFLICT(year, set_name) DO UPDATE SET "
        " cards=excluded.cards, source=excluded.source, updated_at=excluded.updated_at",
        (source, now),
    )
    conn.commit()
    return {"rows_in": None, "loaded": len(kept)}


def covers(conn: sqlite3.Connection, year, set_name) -> bool:
    """Has this product been loaded? The question that stops a gap in coverage
    being reported as a parsing failure."""
    if not year or not set_name:
        return False
    row = conn.execute(
        "SELECT 1 FROM checklist_sets WHERE year = ? AND set_name = ?",
        (int(year), set_name),
    ).fetchone()
    return row is not None


def candidates(conn: sqlite3.Connection, attrs: CardAttrs) -> list[dict]:
    """Every printed card a parse could be, narrowed by whatever it did read.

    Player matching is on the folded spelling, because a checklist writes
    "Ja'Marr Chase" and a title may say "JaMarr" -- the same fold the keys use.
    """
    if not attrs or not attrs.year or not attrs.set_name:
        return []
    where = ["year = ?", "set_name = ?"]
    params: list = [int(attrs.year), attrs.set_name]
    if attrs.card_number:
        where.append("card_number = ?")
        params.append(attrs.card_number)
    if attrs.subset:
        where.append("subset = ?")
        params.append(attrs.subset)
    if attrs.parallel:
        where.append("parallel = ?")
        params.append(attrs.parallel)

    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM checklist WHERE {' AND '.join(where)}", params)]
    if attrs.player:
        want = normalize_player(attrs.player)
        rows = [r for r in rows if normalize_player(r["player"]) == want]
    return rows


def resolve_number(conn: sqlite3.Connection, attrs: CardAttrs) -> Optional[str]:
    """The card number a parse is missing, but ONLY when it is not a choice.

    One candidate means the checklist has determined the card. More than one
    means the title genuinely did not say which, and picking would be inventing
    a fact -- the failure this whole module exists to avoid.
    """
    if not attrs or attrs.card_number:
        return None
    found = candidates(conn, attrs)
    numbers = {r["card_number"] for r in found if r["card_number"]}
    return numbers.pop() if len(numbers) == 1 else None


def verify(conn: sqlite3.Connection, attrs: CardAttrs) -> Optional[bool]:
    """True if this card is printed, False if it is not, None if unknown.

    Three answers rather than two. False is a real finding -- a parse naming a
    card that was never made is wrong somewhere. None means the product has not
    been loaded, and reporting that as False would flag every card in it.
    """
    if not attrs or not covers(conn, attrs.year, attrs.set_name):
        return None
    return bool(candidates(conn, attrs))


def vocabulary(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Insert and parallel names as the manufacturer named them.

    The reason to want a checklist at all. Insert names are currently guessed
    from how a phrase behaves across listings, and parallel names from whether
    they contain a colour word -- which is why "Prizmania" and "Kaiju" are
    missed. Here they are simply read off.
    """
    def column(name: str) -> list[str]:
        return [r[0] for r in conn.execute(
            f"SELECT DISTINCT {name} FROM checklist "
            f"WHERE {name} IS NOT NULL AND TRIM({name}) != '' ORDER BY {name}")]

    return {"inserts": column("subset"),
            "parallels": column("parallel"),
            "sets": column("set_name")}


def stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS cards, COUNT(DISTINCT set_name) AS sets, "
        " MIN(year) AS first_year, MAX(year) AS last_year, "
        " COUNT(DISTINCT subset) AS inserts, COUNT(DISTINCT parallel) AS parallels "
        "FROM checklist").fetchone()
    products = conn.execute("SELECT COUNT(*) FROM checklist_sets").fetchone()[0]
    return {**dict(row), "products": products}
