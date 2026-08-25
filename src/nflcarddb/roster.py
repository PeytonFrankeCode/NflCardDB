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


# --- learning insert names, which are the mirror image of players -----------

# An insert lives in one product. Two allows for a name that runs a second year
# under the same set; beyond that it is describing cards, not naming a set.
MAX_INSERT_CONTEXTS = 2

MIN_INSERT_SIGHTINGS = 6

# The discriminating signal. An insert set has a checklist, so it appears beside
# many different players; a player appears beside themselves. This is what keeps
# a rookie who only ever turns up in one product from being learned as an
# insert -- their name has a context count of one either way, but nobody else's
# name is next to it.
MIN_DISTINCT_PLAYERS = 4

_TITLE_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*")


def _candidate_phrases(title: str, noise: set[str], known: set[str]) -> set[str]:
    """One- and two-word phrases from a title that could name an insert.

    Anything containing a noise word is dropped outright. That single filter
    removes most of what would otherwise qualify -- "Case Hit", "Rookie RC",
    "Free Shipping" all appear beside many players in one product, which is
    exactly the shape being looked for.

    A pair is also dropped when *both* its words are already vocabulary, which
    is what "Panini Donruss" is. Dropping a pair because *either* word is known
    would be wrong: "Micro Mosaic" is a real insert whose second word is a set
    name, and so is "Elite Series".
    """
    # A lone word that lives inside a phrase we already know is a fragment of
    # it, not a name of its own: "Micro" out of "Micro Mosaic".
    known_words = {w for phrase in known for w in phrase.split()}

    words = [w for w in _TITLE_WORD.findall(title) if len(w) > 2]
    keep = [w for w in words if w.lower() not in noise]
    phrases = {w for w in keep if len(w) >= 5 and w.lower() not in known_words}
    for i in range(len(words) - 1):
        a, b = words[i], words[i + 1]
        if a.lower() in noise or b.lower() in noise:
            continue
        if a.lower() in known and b.lower() in known:
            continue
        phrases.add(f"{a} {b}")
    return phrases


def build_inserts(db_path: str, roster: set[str],
                  max_contexts: int = MAX_INSERT_CONTEXTS,
                  min_sightings: int = MIN_INSERT_SIGHTINGS,
                  min_players: int = MIN_DISTINCT_PLAYERS) -> list[dict]:
    """Propose insert-set names, with the evidence for each.

    Deliberately a proposal. Getting this wrong in the permissive direction
    *splits* cards that currently group correctly -- a name wrongly treated as
    an insert divides a card between sellers who typed it and sellers who did
    not -- and that is worse than the merging it fixes, because it breaks what
    already works. So the caller sees the counts and an example title.
    """
    from .parse_title import NOISE, PARALLELS, SETS, SUBSETS, BRANDS, TEAMS

    known = {t.lower() for t in (*SUBSETS, *PARALLELS, *SETS, *BRANDS, *TEAMS)}
    noise = set(NOISE)

    conn = store.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT s.title, c.year, c.set_name FROM cards c "
            "JOIN sales s USING (item_id) "
            "WHERE c.year IS NOT NULL AND c.set_name IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    sightings: dict[str, int] = defaultdict(int)
    contexts: dict[str, set] = defaultdict(set)
    with_players: dict[str, set] = defaultdict(set)
    example: dict[str, str] = {}

    for title, year, set_name in rows:
        lowered = title.lower()
        # Which known players this title mentions, so a phrase can be scored on
        # how many different people it keeps company with.
        here = {name for name in roster if name in lowered}
        context = (year, set_name)
        for phrase in _candidate_phrases(title, noise, known):
            key = phrase.lower()
            if key in known or key in roster:
                continue
            sightings[key] += 1
            contexts[key].add(context)
            with_players[key] |= here
            example.setdefault(key, title)

    out = []
    for key, count in sightings.items():
        if count < min_sightings:
            continue
        if len(contexts[key]) > max_contexts:
            continue
        if len(with_players[key]) < min_players:
            continue
        out.append({
            "name": key.title(),
            "sightings": count,
            "contexts": len(contexts[key]),
            "players": len(with_players[key]),
            "where": ", ".join(f"{y} {s}" for y, s in sorted(
                contexts[key], key=lambda c: str(c))),
            "example": example[key],
        })

    out.sort(key=lambda r: (-r["players"], -r["sightings"]))
    return out


def write_inserts(rows: list[dict], path: str | Path) -> Path:
    """Write the proposal, evidence included so it can be argued with."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Insert-set names learned from collected titles by `nflcarddb inserts`.",
        "#",
        "# An insert restarts its numbering at one, so its name is part of a",
        "# card's identity -- without it, four different cards share a number.",
        "#",
        "# Delete any line that is not really the name of an insert set. A wrong",
        "# entry SPLITS a card between sellers who typed the word and sellers who",
        "# did not, which is worse than the merging this fixes.",
        "#",
        "# name  # seen N times, in M product(s), beside P different players",
        "",
    ]
    for row in rows:
        lines.append(
            f"{row['name']}  # {row['sightings']} listings, {row['where']}, "
            f"{row['players']} players"
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
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
