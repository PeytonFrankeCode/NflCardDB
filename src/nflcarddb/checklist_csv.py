"""Read thecardhuddle.com's checklist export into checklist rows.

The export is two files. `variants` is the one worth having: two million rows,
one per card *per parallel*, with `parallel` and `print_run` as real columns
rather than a sentence to be parsed. `cards` is the same checklist with the
parallels flattened into prose, so it carries strictly less.

Its columns do not line up with this parser's fields by name, and the two
places they disagree are the whole job:

* **`brand` is the set.** "Prizm", "Donruss Optic", "Topps Chrome" -- what a
  title calls the product. 78% of rows already name a set the parser knows, and
  most of the rest are the brand-prefixed spelling ("Panini Prizm"), which the
  existing set folding resolves.
* **`set` is the subset**, and only sometimes. Under `category = base` it names
  a section of the base set -- "Rookies", "Veterans", "Base Set" -- which no
  seller types and which must not become part of a card's identity, or every
  base card splits from itself.
"""

from __future__ import annotations

import csv
import gzip
import io
from pathlib import Path
from typing import Iterator, Optional

from .parse_title import _canonical, register_sets

# Their word for "this row is the plain card", in a column that otherwise holds
# a colour. Keeping it would give every base card a parallel called Base.
BASE_PARALLEL = {"", "base"}

# Sections of a base set. They sit in the `set` column exactly where an insert
# name would, and treating them as inserts would split each base card into
# "Rookies #1" and "#1" depending on which the seller mentioned -- which is
# nothing, because they never mention it.
BASE_SECTIONS = {
    "base set", "base", "rookies", "veterans", "base – rookies",
    "base - rookies", "base – veterans", "base - veterans", "legends",
    "base set rookies", "base set veterans", "rookie cards",
}

REQUIRED = ("year", "brand", "card_number", "player")


def _open(path) -> io.TextIOBase:
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def learn_sets(path) -> int:
    """Register every set name the export uses, folded onto known ones.

    Done as its own pass before importing because the folding is global: it
    teaches the parser that "Panini Prizm" and "Prizm" are one product, which
    then applies to titles as well as to checklist rows.
    """
    names = set()
    with _open(path) as handle:
        for row in csv.DictReader(handle):
            brand = (row.get("brand") or "").strip()
            if brand:
                names.add(brand)
    return register_sets(sorted(names))


def _int(value) -> Optional[int]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def rows_from_csv(path, sport: str = "Football") -> Iterator[dict]:
    """Checklist rows in the shape `checklist.import_rows` takes."""
    with _open(path) as handle:
        for row in csv.DictReader(handle):
            if sport and (row.get("sport") or "").strip().lower() != sport.lower():
                continue
            year = _int(row.get("year"))
            brand = (row.get("brand") or "").strip()
            number = (row.get("card_number") or "").strip().lstrip("#")
            player = (row.get("player") or "").strip()
            if not year or not brand or not (number or player):
                continue

            category = (row.get("category") or "").strip().lower()
            section = (row.get("set") or "").strip()
            # An insert's name is identity; a base-set section's name is not.
            subset = None
            if category != "base" and section.lower() not in BASE_SECTIONS:
                subset = section or None

            parallel = (row.get("parallel") or "").strip()
            if parallel.lower() in BASE_PARALLEL:
                parallel = None

            yield {
                "year": year,
                # Folded through the same vocabulary a title is read with, so
                # "Panini Prizm" here and "Prizm" in a listing are one set.
                "set_name": _canonical(brand),
                "subset": subset,
                "card_number": number or None,
                "player": player or None,
                "parallel": parallel,
                "print_run": _int(row.get("print_run")),
                "is_auto": category == "autograph",
                "is_relic": category == "memorabilia",
            }
