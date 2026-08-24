"""Learn which two-word phrases are players, from the titles already collected.

The name scan takes the longest run of name-shaped words, which is right until
an insert name sits next to the player -- and then "Bomb Squad Jayden Daniels"
and "Season's Best Brett Favre" are single runs and all of it becomes the name.
No positional rule fixes it: "Bomb Squad Jayden Daniels" wants the last two
words and "Jayden Daniels Preview" the first two.

A roster does fix it, because the parser checks the roster before falling back
to the run heuristic. The problem is having one. Rather than shipping a list
that goes stale every draft, this derives it from the data.

The signal is *breadth*, not frequency. Both a player and an insert name repeat
thousands of times, but a player appears across many different sets and years --
Jayden Daniels turns up in Prizm, Optic, Mosaic, Donruss, 2024 and 2025 -- while
"Bomb Squad" lives in exactly one set of one year, because that is what an insert
is. Counting distinct (year, set) pairs separates them cleanly where counting
appearances does not.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from . import db as store

# A name must show up in this many different set-and-year combinations. Two is
# too lenient: a set with a companion release ("Optic Preview" alongside
# Donruss) reaches two without being a person.
MIN_CONTEXTS = 3

# ...and be seen at least this often, so a one-off mis-parse repeated twice in
# different sets does not qualify.
MIN_SIGHTINGS = 8

_WORD = re.compile(r"^[A-Za-z][A-Za-z'’.\-]*$")

# "Patrick Mahomes II" yields the window "Mahomes II", which spans as many sets
# as the real name does and so passes every breadth test. It is not a name, and
# a title matching it would display "Mahomes Ii" as the player.
_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}


def _windows(name: str) -> list[str]:
    """Every two-word run inside a parsed name.

    Two words, because that is what almost every player name is; the three-word
    cases (Amon-Ra St. Brown) still match on their last two, which is enough to
    anchor the scan. A window ending in a generational suffix is dropped -- it
    travels with the player and would otherwise qualify alongside them.
    """
    words = [w for w in name.split() if _WORD.match(w)]
    return [
        " ".join(words[i:i + 2])
        for i in range(len(words) - 1)
        if words[i + 1].lower() not in _SUFFIXES
        and words[i].lower() not in _SUFFIXES
    ]


def build(db_path: str, min_contexts: int = MIN_CONTEXTS,
          min_sightings: int = MIN_SIGHTINGS) -> list[tuple[str, int, int]]:
    """Return (name, sightings, contexts) for phrases that behave like players."""
    conn = store.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT player, year, set_name FROM cards WHERE player IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    sightings: dict[str, int] = defaultdict(int)
    contexts: dict[str, set] = defaultdict(set)

    for player, year, set_name in rows:
        context = (year, set_name)
        for window in _windows(player):
            key = window.lower()
            sightings[key] += 1
            contexts[key].add(context)

    out = [
        (name, sightings[name], len(contexts[name]))
        for name in sightings
        if len(contexts[name]) >= min_contexts and sightings[name] >= min_sightings
    ]
    out.sort(key=lambda r: (-r[2], -r[1]))
    return out


def write(names: list[tuple[str, int, int]], path: str | Path) -> Path:
    """Write the roster the title parser reads."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Player names learned from collected titles by `nflcarddb roster`.",
        "# Rebuilt, not hand-kept: a shipped list goes stale every draft.",
        "#",
        "# A name qualifies by appearing across several different sets and years.",
        "# An insert name repeats just as often but lives in one set of one year,",
        "# which is what separates 'Jayden Daniels' from 'Bomb Squad'.",
        "",
    ]
    lines += [name for name, _, _ in names]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
