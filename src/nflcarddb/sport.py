"""Which sport a card belongs to.

This exists because of a collision, not because of a feature request. The card
key is year + set + number, deliberately leaving the player out so that
"Ja'Marr" and "JaMarr" do not split one card in two. That works while every
card is football. It stops working the moment a second sport arrives:

    2024 Prizm #1  (football)     ->  2024-prizm-n1
    2024 Prizm #1  (basketball)   ->  2024-prizm-n1
    2024 Prizm #1  (baseball)     ->  2024-prizm-n1

Three different cards, three different players, one price history averaging
them together -- and nothing in the data would look wrong. Panini and Topps
print the same set names across every sport they licence, so this is not an
edge case; it is most of the catalogue.

**Where the answer comes from.** Two sources, in this order:

1. *The search that found it.* A sale collected by a basketball search is a
   basketball card. This is the reliable one: it does not depend on how the
   seller worded anything, so every sale from one search agrees with every
   other, and a card cannot be split by one title mentioning a team and the
   next not bothering.

2. *The title.* Team names and league words, when they are there. Used to
   correct source (1) rather than replace it -- searches overlap, and eBay's
   keyword matching is loose enough that a football search does return the odd
   basketball card.

A title saying nothing is the normal case, not a failure. That is precisely
why the search's own answer is what the key ends up using.
"""

from __future__ import annotations

import re
from typing import Optional

FOOTBALL = "football"
BASKETBALL = "basketball"
BASEBALL = "baseball"
SPORTS = (FOOTBALL, BASKETBALL, BASEBALL)

# Team names that belong to exactly one sport. Shared nicknames are left out
# entirely rather than guessed at -- see AMBIGUOUS below.
BASKETBALL_TEAMS = (
    "Atlanta Hawks", "Boston Celtics", "Brooklyn Nets", "Charlotte Hornets",
    "Chicago Bulls", "Cleveland Cavaliers", "Dallas Mavericks",
    "Denver Nuggets", "Detroit Pistons", "Golden State Warriors",
    "Houston Rockets", "Indiana Pacers", "LA Clippers", "Los Angeles Clippers",
    "Los Angeles Lakers", "Memphis Grizzlies", "Miami Heat",
    "Milwaukee Bucks", "Minnesota Timberwolves", "New Orleans Pelicans",
    "New York Knicks", "Oklahoma City Thunder", "Orlando Magic",
    "Philadelphia 76ers", "Phoenix Suns", "Portland Trail Blazers",
    "Sacramento Kings", "San Antonio Spurs", "Toronto Raptors", "Utah Jazz",
    "Washington Wizards",
    "Hawks", "Celtics", "Nets", "Hornets", "Bulls", "Cavaliers", "Cavs",
    "Mavericks", "Mavs", "Nuggets", "Pistons", "Warriors", "Rockets",
    "Pacers", "Clippers", "Lakers", "Grizzlies", "Timberwolves", "Wolves",
    "Pelicans", "Knicks", "Thunder", "Magic", "76ers", "Sixers", "Suns",
    "Trail Blazers", "Blazers", "Spurs", "Raptors", "Wizards",
    # Retired, and still on every card printed before the move.
    "Seattle SuperSonics", "SuperSonics", "Sonics", "New Jersey Nets",
    "Charlotte Bobcats", "Bobcats", "Vancouver Grizzlies",
)

BASEBALL_TEAMS = (
    "Arizona Diamondbacks", "Atlanta Braves", "Baltimore Orioles",
    "Boston Red Sox", "Chicago Cubs", "Chicago White Sox", "Cincinnati Reds",
    "Cleveland Guardians", "Colorado Rockies", "Detroit Tigers",
    "Houston Astros", "Kansas City Royals", "Los Angeles Angels",
    "Los Angeles Dodgers", "Miami Marlins", "Milwaukee Brewers",
    "Minnesota Twins", "New York Mets", "New York Yankees",
    "Oakland Athletics", "Philadelphia Phillies", "Pittsburgh Pirates",
    "San Diego Padres", "San Francisco Giants", "Seattle Mariners",
    "St. Louis Cardinals", "Tampa Bay Rays", "Texas Rangers",
    "Toronto Blue Jays", "Washington Nationals",
    "Diamondbacks", "Dbacks", "Braves", "Orioles", "Red Sox", "Cubs",
    "White Sox", "Reds", "Guardians", "Rockies", "Tigers", "Astros",
    "Royals", "Angels", "Dodgers", "Marlins", "Brewers", "Twins", "Mets",
    "Yankees", "Athletics", "Phillies", "Pirates", "Padres", "Mariners",
    "Rays", "Blue Jays", "Nationals", "Nats",
    # Retired.
    "Cleveland Indians", "Indians", "Florida Marlins", "Montreal Expos",
    "Expos", "Tampa Bay Devil Rays", "Devil Rays",
)

# Nicknames two leagues share. Naming them here is the point: "Giants" is the
# New York Giants and the San Francisco Giants, "Cardinals" is Arizona and
# St. Louis, "Panthers" is Carolina in two sports. Matching one of these as
# evidence would file cards under the wrong sport with total confidence, so
# they are refused as evidence unless the city is written too -- and the full
# names above carry the city.
AMBIGUOUS = {
    "giants", "cardinals", "panthers", "jets", "kings", "rangers",
    "cavaliers", "titans", "athletics",
}

# League and sport words. The strongest single signal when present, and
# present on a minority of titles.
LEAGUE_WORDS = {
    FOOTBALL: (r"\bfootball\b", r"\bnfl\b", r"\bncaa\s+football\b",
               r"\bsuper\s?bowl\b", r"\bquarterback\b", r"\bqb\b"),
    BASKETBALL: (r"\bbasketball\b", r"\bnba\b", r"\bwnba\b",
                 r"\bhoops\b", r"\bslam\s?dunk\b"),
    BASEBALL: (r"\bbaseball\b", r"\bmlb\b", r"\bworld\s+series\b",
               r"\brookie\s+pitcher\b", r"\bpitcher\b", r"\boutfielder\b"),
}

# Sets printed for one sport only. Panini and Topps reuse most brand names
# across sports -- Prizm, Select, Optic, Chrome and Donruss all exist in each
# -- so only the genuinely exclusive ones are listed. A set that exists in two
# sports and is claimed for one here would be worse than no rule at all.
EXCLUSIVE_SETS = {
    BASKETBALL: (r"\bnba\s+hoops\b", r"\bcourt\s+kings\b", r"\bhoops\b",
                 r"\bsky\s?box\b", r"\bfleer\s+retro\b"),
    BASEBALL: (r"\bbowman\s+draft\b", r"\bgypsy\s+queen\b",
               r"\ballen\s*&?\s*ginter\b", r"\bstadium\s+club\b",
               r"\btopps\s+heritage\b", r"\bbowman\s+sterling\b"),
    FOOTBALL: (r"\bcontenders\s+optic\b", r"\brookie\s+ticket\b",
               r"\bplaybook\b", r"\bscore\s+football\b"),
}


def _compile(patterns) -> list:
    return [re.compile(p, re.I) for p in patterns]


_LEAGUE_RE = {sport: _compile(pats) for sport, pats in LEAGUE_WORDS.items()}
_SET_RE = {sport: _compile(pats) for sport, pats in EXCLUSIVE_SETS.items()}


def _team_pattern(teams) -> re.Pattern:
    """Longest first, so "Los Angeles Lakers" wins over "Lakers"."""
    usable = [t for t in teams if t.lower() not in AMBIGUOUS]
    ordered = sorted(usable, key=len, reverse=True)
    joined = "|".join(re.escape(t) for t in ordered)
    return re.compile(rf"\b(?:{joined})\b", re.I)


_TEAM_RE = {
    BASKETBALL: _team_pattern(BASKETBALL_TEAMS),
    BASEBALL: _team_pattern(BASEBALL_TEAMS),
}

_FOOTBALL_TEAM_RE: Optional[re.Pattern] = None


def _football_teams() -> re.Pattern:
    """The football names, borrowed from the parser rather than copied.

    Imported at call time because `parse_title` imports this module at load
    time. Two lists of NFL teams in two files would drift, and the drift would
    show up as football cards filed under no sport at all.
    """
    global _FOOTBALL_TEAM_RE
    if _FOOTBALL_TEAM_RE is None:
        from .parse_title import TEAMS
        _FOOTBALL_TEAM_RE = _team_pattern(TEAMS)
    return _FOOTBALL_TEAM_RE


def sport_from_title(title: str, football_teams=()) -> Optional[str]:
    """The sport the title names, or None when it does not name one.

    None is the ordinary answer. Most card titles say the year, the set, the
    player and nothing about which sport that player plays -- which is exactly
    why the search that found the sale is what the key relies on.

    Evidence is weighed rather than taken first-come: a title reading "2024
    Prizm Football Victor Wembanyama Lakers" is a mis-typed football listing
    for a basketball card, and the two basketball signals should outvote the
    one football word.
    """
    if not title:
        return None

    scores = {s: 0 for s in SPORTS}
    for sport, patterns in _LEAGUE_RE.items():
        for pattern in patterns:
            if pattern.search(title):
                scores[sport] += 2
                break
    for sport, patterns in _SET_RE.items():
        for pattern in patterns:
            if pattern.search(title):
                scores[sport] += 2
                break
    for sport, pattern in _TEAM_RE.items():
        if pattern.search(title):
            scores[sport] += 3
    gridiron = _team_pattern(football_teams) if football_teams else _football_teams()
    if gridiron.search(title):
        scores[FOOTBALL] += 3

    best = max(scores, key=lambda s: scores[s])
    if not scores[best]:
        return None
    # A tie is two sports with equal evidence, which is not an answer.
    if list(scores.values()).count(scores[best]) > 1:
        return None
    return best


def resolve_sport(title: str, collected_as: Optional[str] = None,
                  default: Optional[str] = None,
                  football_teams=()) -> Optional[str]:
    """The sport to file this sale under.

    The title wins when it says something, because a search returning the odd
    card from another sport is normal and the title is the card talking rather
    than the search guessing. Otherwise the search that found it, which is the
    answer that stays the same across every sale of a card however each seller
    worded it.
    """
    return sport_from_title(title, football_teams) or collected_as or default
