"""Recover structured card attributes from a free-text listing title.

Strategy: match the mechanical parts first (grade, serial, card number, year,
set, parallel), blank each match out of a working copy of the title, then treat
the longest surviving run of name-shaped tokens as the player. Every field is
optional and ``confidence`` reports how much of the title we actually explained,
so downstream queries can filter on parse quality.

Sellers write titles however they like, so this will never be perfect. It is
tuned for the modern Panini/Topps football conventions that dominate the data.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from .models import CardAttrs

# Bumped whenever parsing changes materially. It is stamped on every row, and
# the audit reports it -- which is how "did my update actually land" becomes a
# line in a report rather than a guess from whether the numbers moved. An audit
# that came back byte-for-byte identical after a parser fix cost a round here.
#
# title/2: draft positions, set ranges, pick-your-card listings, set names
#          ending in a subset name, and #1/1 serial numbering.
# title/3: named insert sets became part of the identity, because an insert
#          restarts its numbering at one.
# title/4: more insert names -- Sunday Kings, Uptown, Rookie Kings and the rest
#          of the Donruss Optic family that was still colliding.
# title/5: Oversized and Horizontal printings separated from the base insert,
#          the Contenders insert names, and parallels put in a canonical order
#          so word order stops splitting one card into two.
# title/6: autographs and patches became separate cards from the base, the
#          display name gained the print run, auto, patch, the claimed variety
#          and a Base tag, and whatever the parser could not account for is
#          recorded so the vocabulary gaps can be ranked.
PARSER_VERSION = "title/6"

# --- vocabularies -----------------------------------------------------------
# Order matters within each tuple: longest / most specific first, because the
# matcher takes the first hit and blanks it out.

BRANDS = (
    "Panini", "Topps", "Upper Deck", "Bowman", "Leaf", "Donruss", "Score",
    "Fleer", "Playoff", "Pacific", "Sage", "Press Pass", "Wild Card",
)

SETS = (
    # Panini flagship + premium
    "Prizm Draft Picks", "Prizm Collegiate", "Prizm", "Donruss Optic", "Optic",
    "Mosaic", "Select Draft Picks", "Select", "Contenders Optic",
    "Contenders Draft Picks", "Contenders", "Donruss Elite", "Elite Extra Edition",
    "Elite", "Absolute", "Phoenix", "Certified", "Illusions", "Legacy",
    "Chronicles", "Obsidian", "Spectra", "Immaculate Collection", "Immaculate",
    "National Treasures", "Flawless", "Origins", "Playbook", "Rookies & Stars",
    "Rookies and Stars", "Zenith", "Gold Standard", "Limited", "Impeccable",
    "Crown Royale", "Encased", "Unparalleled", "Prestige", "Luminance",
    "Panini One", "Noir", "Vertex", "XR", "Instant", "Donruss",
    # Topps / Bowman
    "Bowman Chrome", "Bowman Sterling", "Bowman", "Topps Chrome", "Chrome",
    "Stadium Club", "Museum Collection", "Allen & Ginter", "Gypsy Queen",
    "Heritage", "Finest", "Inception", "Five Star", "Gold Label", "Definitive",
    "Dynasty", "Tribute", "Fire", "Topps Now",
    # Upper Deck
    "SP Authentic", "Ultimate Collection", "Exquisite", "SPx",
    "Score", "Sage Hit",
)

PARALLELS = (
    # Insert / chase names first -- they are the most valuable signal.
    "Color Blast", "Kaboom", "Downtown", "Stained Glass", "Night Vision",
    "Hyperplaid", "Light It Up", "My House", "Zoom",
    "Championship Ticket", "Playoff Ticket", "Cracked Ice Ticket",
    "Rookie Ticket", "Variation",
    # Physical variants of an insert, which carry their own checklists. The
    # data proves they are separate printings rather than descriptions: three
    # Downtown numbers each held two different players, one seller saying
    # Oversized or Horizontal and the other not. If it were one checklist the
    # same number would be the same player.
    "Oversized", "Horizontal", "Vertical",
    # Prizm / Optic / Mosaic finishes
    "Gold Vinyl", "Black Finite", "Silver Prizm", "Green Ice", "Red Ice",
    "Blue Ice", "Cracked Ice", "Tie-Dye", "Tie Dye", "Snakeskin", "Fast Break",
    "No Huddle", "Pandora", "Disco", "Hyper", "Mojo", "Shimmer", "Pulsar",
    "Sparkle", "Speckle", "Scope", "Lazer", "Laser", "Choice", "Genesis",
    "Reactive", "Fusion", "Flash", "Wave", "Velocity", "Shock", "Fluorescent",
    "Holo", "Camo",
    # Topps refractor family
    "SuperFractor", "Super Fractor", "X-Fractor", "XFractor",
    "Atomic Refractor", "Mini Diamond", "RayWave", "Ray Wave", "Refractor",
    # Select tiers
    "Concourse", "Premier Level", "Field Level", "Club Level", "Zebra",
    # Plain colours (checked last)
    "Neon Green", "Carolina Blue", "Electric Etch", "Platinum", "Bronze",
    "Copper", "Rose Gold", "Gold", "Silver", "Black", "White", "Red", "Blue",
    "Green", "Orange", "Purple", "Pink", "Teal", "Yellow", "Bronze",
)

GRADERS = {
    "PSA": "PSA", "BGS": "BGS", "BVG": "BVG", "SGC": "SGC", "CGC": "CGC",
    "CSG": "CSG", "HGA": "HGA", "TAG": "TAG", "BECKETT": "BGS", "ISA": "ISA",
}

# Subset and insert names that sit right beside the player in a title, so the
# name scan absorbs them: "Caleb Williams Future Stars" and "University Chrome
# Fernando Mendoza" were both read as players. Claimed before the scan, the
# same way teams and parallels are.
#
# They split into two lists, because only one kind belongs in a card's identity.

# Named insert sets. Each restarts its numbering at one, so these are part of
# the card's identity: without them Phoenix "Contours #8", "Phoenician #8",
# "Genies #8" and "Archetype #8" are one card as far as the key is concerned --
# and they were, in a single fourteen-sale group naming four different players.
#
# A name only belongs here if a seller would bother to type it, because it is
# the valuable thing about the card. That is what separates it from the list
# below.
INSERTS = (
    "Micro Mosaic", "Prized Footballers", "Bomb Squad", "Season's Best",
    "Sunday's Best", "Dawn of a Legend", "Contours", "Phoenician", "Genies",
    "Archetype", "Notoriety", "Illuminating", "Emoji",
    # The second wave, from the groups still colliding after the first: one
    # Donruss Optic number was shared by Uptown, Rookie Recruits and Sunday
    # Kings, another by Uptowns, Sunday Kings and Rookie Kings.
    "Sunday Kings", "Rookie Kings", "Rookie Recruits", "Uptowns", "Uptown",
    "Downtowns", "Night Moves", "Elite Series", "Gridiron Kings",
    "The Rookies", "Zero Gravity", "Full Throttle",
    # Third wave: one Contenders number held Power Players, Rookie Stallions
    # and Round Numbers.
    "Power Players", "Rookie Stallions", "Round Numbers", "Legendary Lids",
    "Ticket Stubs", "Hometown Heroes", "Rookie Phenoms",
)

# Boilerplate that sits beside the player and would otherwise be read as part
# of the name. Claimed for that reason alone and deliberately kept OUT of the
# key: "Rated Rookie" is what Donruss calls its base rookie cards, so a seller
# who types it and one who does not are describing the same card. Keying it
# would split a real card in two -- the opposite of the bug above, and worse,
# because it breaks cards that currently work.
DESIGNATIONS = (
    "Future Stars", "Rated Rookie", "Rated Rookies", "University Chrome",
    "University", "Star Rookies", "Rookie Card", "Rookie Ticket",
    "Draft Picks", "All Pro", "All-Pro", "Pro Bowl", "Team Leaders",
    "Record Breakers", "League Leaders", "Hall of Fame", "Legends",
    "Franchise", "Phenoms", "Sensational", "Freshman", "Class of",
)

SUBSETS = INSERTS + DESIGNATIONS

# Tokens that are never part of a player's name.
NOISE = {
    "card", "cards", "football", "nfl", "ncaa", "rookie", "rookies", "rc", "ssp",
    "sp", "sport", "sports", "trading", "lot", "mint", "gem", "insert", "base",
    "parallel", "auto", "autograph", "autographed", "signed", "patch", "relic",
    "jersey", "psa", "bgs", "sgc", "cgc", "graded", "ungraded", "raw", "numbered",
    "serial", "pop", "low", "hot", "invest", "rare", "case", "hit", "mvp", "hof",
    "roy", "sealed", "pack", "fresh", "centered", "true", "pristine", "near",
    "condition", "free", "shipping", "ship", "the", "and", "of", "with", "for",
    "new", "used", "vintage", "no", "on", "in", "sharp", "clean", "beautiful",
    "nice", "great", "awesome", "stunning", "rated", "prospect", "draft", "pick",
    "picks", "team", "logo", "die", "cut", "short", "print", "sp", "ssp", "case",
    "qb", "rb", "wr", "te", "quarterback", "edition", "collection", "series",
    "die-cut", "1st", "first", "year", "debut", "prizm", "sale", "read", "look",
    "combined", "bundle", "you", "pick", "choice", "digital", "reprint", "custom",
    "gem-mt", "gemmt", "nm-mt", "nm", "ex", "vg",
    # Finishes and qualifiers that trail a name: "Jaxson Dart Leather".
    "future", "leather", "refractor", "refractors", "holo", "holofoil", "foil",
    "chrome",
    "wave", "shimmer", "sparkle", "glitter", "rr", "sr", "stars", "star",
    "aqua", "lot", "bulk", "investment", "grail", "comp", "comps",
}

NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}

# Team names get blanked before the player scan so "Jayden Daniels Commanders"
# does not turn into a three-word player. Full "City Nickname" forms come first
# so that "Green Bay Packers" is consumed whole -- otherwise "Green" would be
# picked up as a colour parallel. Bare city names are deliberately absent: they
# collide with real surnames (Washington, Carolina, Jackson).
TEAMS = (
    "Arizona Cardinals", "Atlanta Falcons", "Baltimore Ravens", "Buffalo Bills",
    "Carolina Panthers", "Chicago Bears", "Cincinnati Bengals", "Cleveland Browns",
    "Dallas Cowboys", "Denver Broncos", "Detroit Lions", "Green Bay Packers",
    "Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars",
    "Kansas City Chiefs", "Las Vegas Raiders", "Los Angeles Chargers",
    "Los Angeles Rams", "Miami Dolphins", "Minnesota Vikings",
    "New England Patriots", "New Orleans Saints", "New York Giants",
    "New York Jets", "Philadelphia Eagles", "Pittsburgh Steelers",
    "San Francisco 49ers", "Seattle Seahawks", "Tampa Bay Buccaneers",
    "Tennessee Titans", "Washington Commanders", "Green Bay", "Tampa Bay",
    "Cardinals", "Falcons", "Ravens", "Bills", "Panthers", "Bears", "Bengals",
    "Browns", "Cowboys", "Broncos", "Lions", "Packers", "Texans", "Colts",
    "Jaguars", "Chiefs", "Raiders", "Chargers", "Rams", "Dolphins", "Vikings",
    "Patriots", "Saints", "Giants", "Jets", "Eagles", "Steelers", "49ers",
    "Niners", "Seahawks", "Buccaneers", "Bucs", "Titans", "Commanders",
)

# --- regexes ----------------------------------------------------------------

GRADE_RE = re.compile(
    r"\b(?P<grader>PSA|BGS|BVG|SGC|CGC|CSG|HGA|TAG|ISA|BECKETT)\s*"
    r"(?:GEM\s*-?\s*MT|GEM\s*MINT|MINT|AUTH(?:ENTIC)?)?\s*"
    r"(?P<grade>10(?:\.0)?|[1-9](?:\.5|\.0)?)\b",
    re.I,
)
GRADER_ONLY_RE = re.compile(r"\b(PSA|BGS|BVG|SGC|CGC|CSG|HGA|TAG|ISA|BECKETT)\b", re.I)
# 2023, 2023-24, '23 Panini
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)(?:\s*[-/]\s*\d{2})?\b")
SERIAL_RE = re.compile(r"\b(?P<num>\d{1,4})\s*/\s*(?P<run>\d{1,5})\b")
PRINT_RUN_ONLY_RE = re.compile(r"(?<![\d/])/\s*(\d{1,5})\b")
# The trailing `(?!/)` is load-bearing. Sellers write a one-of-one as "#1/1"
# and a numbered parallel as "#8/10", and without it the numerator is claimed as
# the card number -- so every 1-of-1 in a set collapsed onto `<year>-<set>-n1`
# whoever was on it. Four different players shared 2025-prizm-n1.
#
# A slash *attached* to the digits means serial numbering. A detached one is a
# print run belonging to a real card number, which is why "#301 /249" must keep
# reading as card 301 and the lookahead deliberately does not span whitespace.
# Words that turn a "#N" into something other than a card number. A draft
# position and a ranking are both written exactly like one, and both are common
# enough to build fake groups: 2024-contenders-n1 collected Mahomes, Williams
# and Daniels purely from "#1 Draft Pick" and "#1 Ranked".
#
# "Pick" is matched only in the singular, because "Draft Picks" is a Panini set
# name -- and the set is claimed *after* the number, so the plural is still in
# the text at this point and rejecting it would cost "#25 Draft Picks" its
# number.
_NOT_A_NUMBER = r"(?:overall|draft\s+pick(?!s)|pick(?!s)|ranked|rank|seed|prospect)"

CARD_NUM_RE = re.compile(
    # A slash *attached* to the digits means serial numbering: sellers write a
    # one-of-one as "#1/1" and a numbered parallel as "#8/10". A detached slash
    # is a print run belonging to a real card number, which is why this must not
    # span whitespace -- "#301 /249" has to keep reading as card 301.
    r"#\s?(?P<num>[A-Z]{0,4}-?\d{1,4}[A-Z]?)\b(?!/)"
    # "#1-330" is the range of a whole set being offered, not card one.
    r"(?!-\d)"
    rf"(?!\s+{_NOT_A_NUMBER}\b)",
    re.I,
)

# Listings that sell an unspecified card out of many. The price is real but it
# belongs to no particular card, so keying one would put a $3 "pick your card"
# sale into a genuine card's price history.
MULTI_CARD_RE = re.compile(
    r"\b(?:pick\s+your|you\s+pick|complete\s+your|choose\s+your|"
    r"your\s+choice|build\s+your)\b", re.I
)
ROOKIE_RE = re.compile(r"\b(RC|RY|ROOKIE|ROOKIE\s+CARD|1ST\s+YEAR)\b", re.I)
AUTO_RE = re.compile(r"\b(AUTO(?:GRAPH(?:ED)?)?|SIGNED|ON[-\s]CARD)\b", re.I)
RELIC_RE = re.compile(r"\b(PATCH|RELIC|JERSEY|RPA|MEM(?:ORABILIA)?|SWATCH|GLOVE)\b", re.I)
NAME_TOKEN_RE = re.compile(r"^[A-Z][A-Za-z'’.\-]*$|^[A-Z]{2,4}$|^[A-Z]\.[A-Z]\.$")


def _vocab_pattern(terms: Iterable[str]) -> re.Pattern:
    """Alternation over a vocabulary, longest term first so specific wins."""
    ordered = sorted(set(terms), key=len, reverse=True)
    escaped = [re.escape(t).replace(r"\ ", r"\s+") for t in ordered]
    return re.compile(r"\b(" + "|".join(escaped) + r")\b", re.I)


BRAND_PAT = _vocab_pattern(BRANDS)
SET_PAT = _vocab_pattern(SETS)
PARALLEL_PAT = _vocab_pattern(PARALLELS)
TEAM_PAT = _vocab_pattern(TEAMS)
SUBSET_PAT = _vocab_pattern(SUBSETS)

# Subsets included so a SHOUTED "GENIES" and a lowercase "genies" fold to one
# spelling -- the subset is part of the key now, and two spellings would be two
# cards.
_CANONICAL = {t.lower(): t for t in (*BRANDS, *SETS, *PARALLELS, *TEAMS, *SUBSETS)}

_INSERT_LOOKUP = {t.lower() for t in INSERTS}

# Harvested set spellings mapped onto the name they resolve to.
_SET_ALIASES: dict[str, str] = {}

# Insert names learned from collected titles, registered at startup. Kept apart
# from INSERTS so re-registering replaces them rather than piling up.
_LEARNED_INSERTS: tuple[str, ...] = ()


_LEARNED_DESIGNATIONS: tuple[str, ...] = ()


def register_designations(names: Iterable[str]) -> int:
    """Words to claim out of a title without letting them into the key.

    This is where eBay's Parallel/Variety list belongs, and finding that out
    cost a measured regression: wired in as inserts, those 760 names dropped
    grouped cards from 5,238 to 4,187.

    The reason is what the field is. Parallel/Variety is filled in on eBay's
    listing *form*; it does not have to appear in the title at all. So keying
    it splits every card between sellers who typed the word and sellers who
    only ticked the box -- the same failure as "Rated Rookie", at 760x the
    scale.

    Claimed, they still earn their keep: they stop insert names being read as
    part of the player's name, which is the bug that started all of this.
    """
    global _LEARNED_DESIGNATIONS
    reserved = {t.lower() for t in (*INSERTS, *DESIGNATIONS)}
    _LEARNED_DESIGNATIONS = tuple(
        n.strip() for n in names
        if n.strip() and n.strip().lower() not in reserved
    )
    _rebuild_vocabulary()
    return len(_LEARNED_DESIGNATIONS)


_LEARNED_SETS: tuple[str, ...] = ()


def register_sets(names: Iterable[str]) -> int:
    """Add harvested set names, folding each onto a known set where one exists.

    The folding is the whole difficulty. eBay writes "Panini Donruss" where the
    parser holds "Donruss", and titles use both -- so registering the eBay
    spelling as a *separate* set would split one card between sellers who typed
    the brand and sellers who did not. Instead both spellings are matched and
    both resolve to the same canonical name.

    "Topps Chrome" is why the brand is not simply stripped: the brand is part
    of that product's name. So a known set is preferred where one matches, and
    only a genuinely new product keeps its full harvested spelling.
    """
    global _LEARNED_SETS

    known = {t.lower(): t for t in SETS}
    brands = sorted(BRANDS, key=len, reverse=True)
    folded: dict[str, str] = {}
    kept: list[str] = []

    for raw in names:
        name = raw.strip()
        if not name or name.lower() in known:
            continue
        without_brand = name
        for brand in brands:
            if name.lower().startswith(brand.lower() + " "):
                without_brand = name[len(brand) + 1:].strip()
                break
        # A known set wins, whichever spelling reaches it.
        canonical = known.get(name.lower()) or known.get(without_brand.lower())
        if canonical is None:
            canonical = name
            kept.append(name)
        folded[name.lower()] = canonical

    _LEARNED_SETS = tuple(dict.fromkeys(kept))
    _rebuild_vocabulary(folded)
    return len(_LEARNED_SETS)


def _rebuild_vocabulary(set_aliases: Optional[dict] = None) -> None:
    """Recompile the patterns after a learned list changes."""
    global SUBSETS, SUBSET_PAT, SET_PAT, _CANONICAL, _INSERT_LOOKUP
    global _SET_ALIASES

    if set_aliases is not None:
        _SET_ALIASES = set_aliases

    SUBSETS = (INSERTS + _LEARNED_INSERTS + DESIGNATIONS
               + _LEARNED_DESIGNATIONS)
    SUBSET_PAT = _vocab_pattern(SUBSETS)
    # Every spelling is matchable; `_canonical` collapses them afterwards.
    SET_PAT = _vocab_pattern(tuple(SETS) + tuple(_SET_ALIASES))
    _CANONICAL = {t.lower(): t
                  for t in (*BRANDS, *SETS, *PARALLELS, *TEAMS, *SUBSETS)}
    _CANONICAL.update(_SET_ALIASES)
    # Only inserts key. Learned designations are claimed and then dropped.
    _INSERT_LOOKUP = {t.lower() for t in (*INSERTS, *_LEARNED_INSERTS)}


def register_inserts(names: Iterable[str]) -> int:
    """Add learned insert names to the vocabulary, replacing any previous set.

    A registry rather than a parameter on `parse_title`, because the vocabulary
    is process-wide and the alternative is threading an argument through every
    caller that parses anything -- the collector, the importer, the reparser and
    the D1 restore -- to say the same thing each time.

    Replaces rather than accumulates so calling it twice is the same as calling
    it once, and so tests can clear it with an empty list.
    """
    global _LEARNED_INSERTS

    # DESIGNATIONS are excluded as well as INSERTS, and that is the important
    # half. A built-in designation is a deliberate decision that a word is
    # boilerplate -- "Rated Rookie" means the same card whether or not the
    # seller typed it. Letting a learned list promote one into the key would
    # split cards that currently group correctly, which is the failure this
    # whole feature is supposed to avoid causing.
    reserved = {t.lower() for t in (*INSERTS, *DESIGNATIONS)}
    _LEARNED_INSERTS = tuple(
        n.strip() for n in names
        if n.strip() and n.strip().lower() not in reserved
    )
    _rebuild_vocabulary()
    return len(_LEARNED_INSERTS)


def load_inserts(path: str) -> list[str]:
    """Read a word list, ignoring comments and blank lines.

    An unreadable path yields an empty list rather than raising. Guarding this
    at each call site is what let the same crash happen twice: a config holding
    `inserts: .` was fixed in the startup path and then took the audit down
    from a second call site added the same day. A word list that cannot be read
    is an absent word list, and that is true wherever it is asked for.
    """
    names: list[str] = []
    if not path or not Path(path).is_file():
        return names
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                name = line.split("#", 1)[0].strip()
                if name:
                    names.append(name)
    except OSError:
        return []
    return names

# Nickname -> full team name, so "Commanders" and "Washington Commanders"
# land on the same value.
_TEAM_BY_NICKNAME = {
    full.rsplit(" ", 1)[-1].lower(): full
    for full in TEAMS
    if " " in full and full not in ("Green Bay", "Tampa Bay")
}
_TEAM_ALIASES = {"niners": "San Francisco 49ers", "bucs": "Tampa Bay Buccaneers",
                 "green bay": "Green Bay Packers", "tampa bay": "Tampa Bay Buccaneers"}


def _canonical_team(text: str) -> str:
    key = re.sub(r"\s+", " ", text.strip()).lower()
    return _TEAM_ALIASES.get(key) or _TEAM_BY_NICKNAME.get(key) or _canonical(text)


def _canonical(text: str) -> str:
    key = re.sub(r"\s+", " ", text.strip()).lower()
    return _CANONICAL.get(key, re.sub(r"\s+", " ", text.strip()))


class _Working:
    """A title plus a mask of characters already claimed by a matcher."""

    def __init__(self, title: str) -> None:
        self.original = title
        self.chars = list(title)

    def claim(self, start: int, end: int) -> None:
        for i in range(start, min(end, len(self.chars))):
            self.chars[i] = " "

    @property
    def remaining(self) -> str:
        return "".join(self.chars)


def _take(work: _Working, pattern: re.Pattern, group: int | str = 0) -> Optional[re.Match]:
    m = pattern.search(work.remaining)
    if m:
        work.claim(m.start(), m.end())
    return m


def _extract_player(remaining: str, roster: Optional[set[str]] = None) -> tuple[Optional[str], bool]:
    """Longest run of name-shaped tokens in what's left of the title."""
    cleaned = re.sub(r"[^\w\s'’.\-]", " ", remaining)
    tokens = cleaned.split()

    runs: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        bare = tok.strip(".,-'’")
        if not bare or bare.lower() in NOISE or bare.isdigit():
            if current:
                runs.append(current)
                current = []
            continue
        if NAME_TOKEN_RE.match(tok) or bare.lower() in NAME_SUFFIXES:
            current.append(tok)
        else:
            if current:
                runs.append(current)
                current = []
    if current:
        runs.append(current)

    if roster:
        for run in runs:
            for size in (3, 2):
                for i in range(len(run) - size + 1):
                    candidate = " ".join(run[i:i + size])
                    if candidate.lower() in roster:
                        # Sellers SHOUT. Normalise so every sale of one card
                        # displays the same name, not one per typing style.
                        return (candidate.title() if candidate.isupper()
                                else candidate, True)

    # Prefer 2-3 token runs (First Last / First Middle Last); fall back to longest.
    scored = [r for r in runs if 2 <= len(r) <= 4]
    if not scored:
        scored = [r for r in runs if len(r) == 1]
    if not scored:
        return (None, False)
    best = max(scored, key=lambda r: (2 <= len(r) <= 3, len(r)))
    name = " ".join(best[:4]).strip(" .,-")
    return (name.title() if name.isupper() else name, False)


def parse_title(title: str, roster: Optional[set[str]] = None) -> CardAttrs:
    """Parse one listing title into CardAttrs."""
    attrs = CardAttrs()
    if not title:
        return attrs

    work = _Working(title)
    hits = 0

    # Grade: "PSA 10", "BGS 9.5", or a bare grader with no number.
    m = _take(work, GRADE_RE)
    if m:
        attrs.grader = GRADERS.get(m.group("grader").upper(), m.group("grader").upper())
        try:
            attrs.grade = float(m.group("grade"))
        except (TypeError, ValueError):
            attrs.grade = None
        attrs.is_graded = True
        hits += 1
    else:
        m = _take(work, GRADER_ONLY_RE)
        if m:
            attrs.grader = GRADERS.get(m.group(1).upper(), m.group(1).upper())
            attrs.is_graded = True
            hits += 1

    # Card number is claimed before serial numbering, because "#301 /249" would
    # otherwise read as "serial 301 of 249" instead of card #301 out of 249.
    m = _take(work, CARD_NUM_RE)
    if m:
        attrs.card_number = m.group("num").upper()
        hits += 1

    # Serial numbering: "12/99" -> serial 12 of a 99 print run. A numerator
    # larger than the run is not a serial at all -- "202/99" is card 202 from a
    # /99 parallel, written without the space that would have made it obvious.
    m = _take(work, SERIAL_RE)
    if m:
        num, run = int(m.group("num")), int(m.group("run"))
        if num <= run:
            attrs.serial_number, attrs.print_run = num, run
        else:
            attrs.print_run = run
            if not attrs.card_number:
                attrs.card_number = str(num)
        hits += 1
    else:
        m = _take(work, PRINT_RUN_ONLY_RE)
        if m:
            attrs.print_run = int(m.group(1))
            hits += 1

    m = _take(work, YEAR_RE)
    if m:
        attrs.year = int(m.group(1))
        hits += 1

    # Teams are claimed before parallels and the player scan: it keeps colour
    # words in city names ("Green Bay") from being read as a parallel, and keeps
    # nicknames out of the player name.
    m = _take(work, TEAM_PAT)
    if m:
        attrs.team = _canonical_team(m.group(1))
        hits += 1

    # The set is claimed before the subset, because several set names *end* in a
    # subset name: "Prizm Draft Picks" is a different product from "Prizm", and
    # taking "Draft Picks" out first left the set matching bare "Prizm" -- so a
    # college card and an NFL card with the same number merged into one.
    m = _take(work, SET_PAT)
    if m:
        attrs.set_name = _canonical(m.group(1))
        hits += 1

    # Subsets sit right beside the player -- "Caleb Williams Future Stars" --
    # so they are claimed before the name scan for the same reason teams are.
    #
    # This was once discarded, on the reasoning that a card number already
    # separates an insert from a base card. That is wrong, and Peyton's data
    # shows it plainly: an insert set restarts its numbering at one, so Phoenix
    # "Contours #8", "Phoenician #8", "Genies #8" and "Archetype #8" are four
    # different cards that collapsed into a single fourteen-sale group naming
    # four different players. The insert name is part of the card's identity.
    # Up to two, because a title can carry both a keyed insert and a claim-only
    # word -- "Decade Dominance Silver" needs both read, not whichever the
    # matcher happened to reach first.
    varieties: list[str] = []
    subset_match = None
    for _ in range(2):
        found = _take(work, SUBSET_PAT)
        if not found:
            break
        subset_match = subset_match or found
        claimed = _canonical(found.group(1))
        # Only a named insert set becomes part of the identity. A designation
        # is claimed to keep it out of the player's name -- but it is still
        # RECORDED, because claiming it and then dropping it is how a "Decade
        # Dominance Silver" ended up displayed as plain "Silver".
        if claimed.lower() in _INSERT_LOOKUP:
            attrs.subset = claimed
            hits += 1
        else:
            varieties.append(claimed)
    if varieties:
        attrs.variety = " ".join(sorted(dict.fromkeys(varieties)))
    # "Rated Rookie" is a subset AND states the card is a rookie. Claiming the
    # phrase consumes the word before the flag pass sees it, so the flag is
    # read off the match rather than lost.
    if subset_match and "rookie" in subset_match.group(1).lower():
        attrs.is_rookie = True

    m = _take(work, BRAND_PAT)
    if m:
        attrs.brand = _canonical(m.group(1))
        hits += 1
    elif attrs.set_name:
        attrs.brand = _infer_brand(attrs.set_name)

    # Parallels stack: "Orange Lazer", "Gold Shimmer Refractor". Collect up to
    # three, otherwise the unclaimed words leak into the player name.
    found_parallels: list[str] = []
    for _ in range(3):
        m = _take(work, PARALLEL_PAT)
        if not m:
            break
        found_parallels.append(_canonical(m.group(1)))
    if found_parallels:
        # Alphabetical, NOT the order the seller wrote them in. This field is
        # part of the key, and sellers put the same words in any order:
        # "Downtown! Oversized" and "OVERSIZED ... Downtown!" are one card, and
        # source order gave them two keys. A canonical order also means one card
        # always renders the same name, which source order did not guarantee
        # either.
        attrs.parallel = " ".join(sorted(dict.fromkeys(found_parallels)))
        hits += 1

    # Flags are read off the remaining text and also blanked out, since words
    # like "Auto" would otherwise look like a name token.
    if _take(work, ROOKIE_RE):
        attrs.is_rookie = True
    if _take(work, AUTO_RE):
        attrs.is_auto = True
    if _take(work, RELIC_RE):
        attrs.is_relic = True

    attrs.player, roster_hit = _extract_player(work.remaining, roster)
    if attrs.player:
        hits += 2 if roster_hit else 1

    # Confidence: weighted toward the fields that make a sale comparable.
    weights = [
        (attrs.player is not None, 0.35),
        (roster_hit, 0.10),
        (attrs.year is not None, 0.20),
        (attrs.set_name is not None, 0.20),
        (attrs.card_number is not None, 0.10),
        (attrs.is_graded, 0.05),
    ]
    attrs.confidence = round(min(1.0, sum(w for ok, w in weights if ok)), 3)

    # Whatever survived every matcher and the name scan. Noise words and stray
    # fragments are dropped, so what is left is genuinely unrecognised -- the
    # "Dragonscale" and "Decade Dominance" that no vocabulary knows yet.
    #
    # The player's own name is subtracted, but only as far as it is trustworthy.
    # A roster hit is exactly a name, so all of it goes. A name from the run
    # heuristic is a guess, and the words past the first two are usually what
    # leaked in -- "Ja'Marr Chase Dragonscale" is a player plus the parallel
    # nobody has in a vocabulary yet, and that word is the whole point of
    # looking. Subtracting the full guessed name would hide it.
    spoken_for = set(NOISE)
    if attrs.player:
        words = attrs.player.split()
        spoken_for |= {w.lower() for w in (words if roster_hit else words[:2])}

    leftovers = [
        w for w in re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", work.remaining)
        if w.lower() not in spoken_for
    ]
    attrs.unparsed = " ".join(dict.fromkeys(leftovers)) or None

    if MULTI_CARD_RE.search(title):
        # "Pick your card" sells one unspecified card out of a set. The price is
        # real, the card is not knowable, and any name in the title is an
        # example rather than what sold. Clearing both the number and the name
        # leaves card_key with nothing to build from, which is the honest
        # outcome -- the alternative is a $3 sale sitting in a real card's price
        # history. The sale itself is still stored with its title and price.
        attrs.card_number = None
        attrs.player = None
        attrs.confidence = min(attrs.confidence, 0.3)

    return attrs


def _infer_brand(set_name: str) -> Optional[str]:
    panini = {
        "prizm", "prizm draft picks", "optic", "donruss optic", "mosaic", "select",
        "contenders", "donruss", "score", "absolute", "phoenix", "certified",
        "illusions", "legacy", "chronicles", "obsidian", "spectra", "immaculate",
        "national treasures", "flawless", "origins", "playbook", "zenith", "elite",
        "gold standard", "limited", "impeccable", "crown royale", "encased",
        "unparalleled", "prestige", "luminance", "noir", "vertex", "xr",
    }
    topps = {
        "topps chrome", "chrome", "bowman", "bowman chrome", "bowman sterling",
        "stadium club", "museum collection", "allen & ginter", "gypsy queen",
        "heritage", "finest", "inception", "five star", "gold label", "definitive",
        "dynasty", "tribute", "fire", "topps now",
    }
    upper_deck = {"sp authentic", "ultimate collection", "exquisite", "spx"}

    key = set_name.lower()
    if key in panini:
        return "Panini"
    if key in topps:
        return "Topps"
    if key in upper_deck:
        return "Upper Deck"
    return None


def load_roster(path: str) -> set[str]:
    """Load a newline-delimited player-name list for exact-match boosting.

    Comments are skipped, which they were not before: every roster this project
    has written carries a `#` header explaining where it came from, and each of
    those lines was being loaded as a player's name. Harmless in practice --
    no title contains "# player names learned from collected titles" -- but it
    made the roster's size a lie and would bite the moment a comment happened
    to contain a real name.
    """
    names: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.lstrip().startswith("#"):
                continue
            name = line.strip().split(",")[0].strip()
            if name:
                names.add(name.lower())
    return names
