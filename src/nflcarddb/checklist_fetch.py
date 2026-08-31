"""Read checklists from thecardhuddle.com and turn them into checklist rows.

Runs on the collector's PC rather than anywhere else, for the same reason the
eBay scraper does: that machine can reach the site.

The field mapping is deliberately tolerant. The exact JSON shape could not be
inspected while this was written, so instead of guessing one layout and failing
silently on another, every field is looked up through a list of the names it
plausibly has, the card list is found wherever it sits, and `describe()` reports
what was actually seen. A mapping that is wrong then shows up as a report saying
so, rather than as an empty table.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Iterable, Iterator, Optional

BASE_URL = "https://thecardhuddle.com/data/checklists"

# Politeness, and a name so the site's owner can see what is calling.
USER_AGENT = "NflCardDB/1.0 (+checklist import; contact via site owner)"
DELAY_SECONDS = 0.25

# Field names in order of preference. First one present wins.
#
# The risk worth naming: "name" means the product at index level and the player
# at card level, so it is never consulted for both. Product-level keys are read
# only from the index entry, card-level keys only from a card.
CARD_ALIASES = {
    "card_number": ("card_number", "cardNumber", "number", "num", "cardNo",
                    "card_no", "no", "n"),
    "player": ("player", "playerName", "player_name", "athlete", "subject",
               "name"),
    "subset": ("subset", "insert", "insertName", "insert_name", "subsetName",
               "subset_name", "group", "category", "section"),
    "parallel": ("parallel", "variation", "variant", "finish", "color",
                 "colour", "parallelName"),
    "print_run": ("print_run", "printRun", "numbered", "serial", "run",
                  "print_run_qty", "qty"),
    "team": ("team", "teamName", "team_name"),
    "is_auto": ("is_auto", "auto", "autograph", "isAuto"),
    "is_relic": ("is_relic", "relic", "memorabilia", "patch", "isRelic"),
}

PRODUCT_ALIASES = {
    "year": ("year", "season", "releaseYear", "release_year", "yr"),
    "set_name": ("set_name", "setName", "set", "product", "productName",
                 "product_name", "title", "name"),
    "id": ("id", "product_id", "productId", "slug", "key", "file"),
}

# Where a list of cards tends to live inside a product file.
LIST_KEYS = ("cards", "checklist", "items", "rows", "data", "entries",
             "players", "cardList")

_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
_RUN = re.compile(r"/\s*(\d{1,5})")


def fetch_json(url: str, timeout: float = 30.0) -> Any:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _pick(row: dict, names: Iterable[str]) -> Any:
    for name in names:
        if name in row and row[name] not in (None, "", []):
            return row[name]
        # Sites are inconsistent about case and separators; try a folded match
        # before giving up rather than requiring an exact spelling.
        for key in row:
            if key.lower().replace("_", "") == name.lower().replace("_", ""):
                if row[key] not in (None, "", []):
                    return row[key]
    return None


def _as_int(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.search(r"\d+", str(value))
    return int(digits.group()) if digits else None


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "auto", "relic"}
    return bool(value)


def find_cards(payload: Any) -> list[dict]:
    """The card list, wherever the product file happens to keep it."""
    if isinstance(payload, list):
        return [c for c in payload if isinstance(c, dict)]
    if not isinstance(payload, dict):
        return []
    for key in LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    # Nothing named as expected: take the longest list of dicts anywhere in the
    # top level, which is nearly always the checklist.
    best: list[dict] = []
    for value in payload.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            if len(value) > len(best):
                best = value
    return best


def product_meta(entry: dict) -> dict:
    """Year and set name for a product, from its index entry."""
    year = _as_int(_pick(entry, PRODUCT_ALIASES["year"]))
    name = _pick(entry, PRODUCT_ALIASES["set_name"])
    name = str(name).strip() if name else None
    # "2024 Panini Prizm" carries its own year; take it when no year field
    # exists, and strip it out of the set name either way so the set matches
    # what a parsed title produces.
    if name:
        found = _YEAR.search(name)
        if found:
            year = year or int(found.group())
            name = _YEAR.sub("", name, count=1).strip(" -–")
    return {"year": year, "set_name": name or None,
            "id": _pick(entry, PRODUCT_ALIASES["id"])}


def rows_from_product(payload: Any, meta: dict) -> Iterator[dict]:
    """Checklist rows for one product, in the shape `checklist.import_rows` takes.

    A product file may repeat the year and set in its own header; when it does
    that wins over the index entry, because it is closer to the data.
    """
    header = product_meta(payload) if isinstance(payload, dict) else {}
    year = header.get("year") or meta.get("year")
    set_name = header.get("set_name") or meta.get("set_name")

    for card in find_cards(payload):
        run = _as_int(_pick(card, CARD_ALIASES["print_run"]))
        number = _pick(card, CARD_ALIASES["card_number"])
        number = str(number).strip().lstrip("#") if number is not None else None
        # "12/99" in a number field is a serial, not a card number.
        if number and "/" in number:
            head, _, tail = number.partition("/")
            run = run or _as_int(tail)
            number = head.strip() or None
        yield {
            "year": year,
            "set_name": set_name,
            "subset": (str(_pick(card, CARD_ALIASES["subset"]) or "").strip()
                       or None),
            "card_number": number,
            "player": (str(_pick(card, CARD_ALIASES["player"]) or "").strip()
                       or None),
            "parallel": (str(_pick(card, CARD_ALIASES["parallel"]) or "").strip()
                         or None),
            "print_run": run,
            "is_auto": _as_bool(_pick(card, CARD_ALIASES["is_auto"])),
            "is_relic": _as_bool(_pick(card, CARD_ALIASES["is_relic"])),
        }


def index_entries(index: Any) -> list[dict]:
    """Product entries from index.json, whatever it wraps them in."""
    if isinstance(index, list):
        return [e for e in index if isinstance(e, dict)]
    if isinstance(index, dict):
        for key in ("products", "checklists", "items", "data", "sets"):
            value = index.get(key)
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
        # A mapping of id -> product also happens.
        if all(isinstance(v, dict) for v in index.values()) and index:
            return [{**v, "id": k} for k, v in index.items()]
    return []


def describe(payload: Any, meta: Optional[dict] = None,
             sample: int = 2) -> dict:
    """What a payload actually looks like, for when the mapping needs checking.

    This exists because the mapping above is a guess made without the data. A
    wrong guess produces an empty import, and an empty import with no
    explanation is the least debuggable outcome there is.
    """
    report: dict = {"top_level": type(payload).__name__}
    if isinstance(payload, dict):
        report["keys"] = sorted(payload.keys())[:30]
    cards = find_cards(payload)
    report["cards_found"] = len(cards)
    if cards:
        report["card_keys"] = sorted(cards[0].keys())[:30]
        report["sample"] = cards[:sample]
        mapped = list(rows_from_product(payload, meta or {}))[:sample]
        report["mapped"] = mapped
        # Which of our fields actually got a value: the honest score of the
        # mapping, rather than "it did not crash".
        report["filled"] = {
            field: sum(1 for r in list(rows_from_product(payload, meta or {}))
                       if r.get(field) not in (None, "", False))
            for field in ("card_number", "player", "subset", "parallel",
                          "print_run")
        }
    return report


def fetch_all(base_url: str = BASE_URL, limit: Optional[int] = None,
              delay: float = DELAY_SECONDS, log=None) -> Iterator[dict]:
    """Every checklist row on the site, one product at a time.

    Streams rather than accumulating: 361 products is a lot of cards, and a
    failure part-way should still leave everything already read importable.
    """
    index = fetch_json(f"{base_url}/index.json")
    entries = index_entries(index)
    if log:
        log(f"index.json: {len(entries)} product(s)")

    for n, entry in enumerate(entries if limit is None else entries[:limit], 1):
        meta = product_meta(entry)
        pid = meta.get("id")
        if not pid:
            continue
        try:
            payload = fetch_json(f"{base_url}/{pid}.json")
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            # One missing product must not end the run: the other 360 are still
            # worth having, and the failure is reported rather than swallowed.
            if log:
                log(f"  [{n}] {pid}: could not read ({exc})")
            continue
        rows = list(rows_from_product(payload, meta))
        if log:
            log(f"  [{n}/{len(entries)}] {meta.get('year') or '?'} "
                f"{meta.get('set_name') or pid}: {len(rows)} card(s)")
        yield from rows
        if delay:
            time.sleep(delay)
