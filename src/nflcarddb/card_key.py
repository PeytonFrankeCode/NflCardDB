"""Give the same physical card the same identity across differently-worded sales.

Sellers write titles freely, so one card arrives under many names:

    2021 Panini Prizm Ja'Marr Chase RC #220 PSA 10
    Ja'Marr Chase 2021 Prizm Rookie Card #220 PSA 10 GEM MINT Bengals
    2021 PRIZM #220 JAMARR CHASE ROOKIE PSA 10

Those are one card and three rows, and nothing groups them, so there is no such
thing as "this card's price over time". This assigns a key they all share.

Two decisions worth stating, because both are trade-offs rather than facts.

**Grade is not part of the card.** A PSA 10 and a PSA 9 of #220 are the same
card in different condition. They are also different *market items* -- the whole
point of grading -- so callers group by (card_key, grader, grade) when plotting
prices. Baking grade into the key would make "how many of this card sold"
unanswerable.

**The number wins when we have it.** Year + set + number identifies a card
without the player's name, and leaving the name out avoids splitting a card in
two when it parses as "Jamarr" once and "Ja'Marr" the next time. Only when there
is no number does the player become part of the identity.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

from .models import CardAttrs

# Enough of a parse to be an identity at all. Below this a key would be a guess
# dressed as a fact, and a wrong grouping is worse than no grouping: it silently
# averages two different cards into one price history.
MIN_CONFIDENCE = 0.4

_PUNCT = re.compile(r"[^a-z0-9]+")
_SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def normalize_player(name: Optional[str]) -> str:
    """Fold every spelling of one player's name to a single identity token.

    This is a comparison key, not a display name -- "Ja'Marr Chase" folds to
    `jamarrchase`. Separators are removed rather than normalised because the
    variants differ in *whether* there is one: Ja'Marr, JaMarr and Ja Marr are
    the same player written three ways, and replacing punctuation with a space
    would fold only two of the three.

    Suffixes go too, since "Odell Beckham Jr" and "Odell Beckham" are one
    player and sellers are inconsistent about which they type.

    The risk is over-merging -- two players whose names concatenate to the same
    string. That needs a genuine collision of full names, which does not happen
    in a card set; under-merging, by contrast, happens on every apostrophe.
    """
    if not name:
        return ""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    folded = _PUNCT.sub(" ", folded.lower())
    folded = _SUFFIXES.sub(" ", folded)
    return "".join(folded.split())


def _slug(value) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return _PUNCT.sub("-", text.lower()).strip("-")


def card_key(attrs: CardAttrs) -> Optional[str]:
    """A stable identity for the physical card, or None if too little is known.

    Readable rather than hashed, so a wrong grouping can be spotted by eye and
    the key can go straight into a URL.
    """
    if attrs is None or (attrs.confidence or 0) < MIN_CONFIDENCE:
        return None
    if not attrs.year or not attrs.set_name:
        return None

    parts = [str(attrs.year), _slug(attrs.set_name)]

    # The insert set, when there is one. An insert restarts its numbering at
    # one, so the number alone does not separate it from the base set or from a
    # sibling insert: Phoenix "Contours #8", "Genies #8" and "Archetype #8" are
    # three different cards. Leaving this out merged them into one price
    # history naming three different players.
    if attrs.subset:
        parts.append(_slug(attrs.subset))

    if attrs.card_number:
        parts.append(f"n{_slug(attrs.card_number)}")
    elif attrs.player:
        parts.append(_slug(normalize_player(attrs.player)))
    else:
        # Year and set alone describe thousands of cards, not one.
        return None

    if attrs.parallel:
        parts.append(_slug(attrs.parallel))

    # An autographed card and a patch card are different cards from the base,
    # not conditions of it -- they are separately printed, separately
    # numbered and worth many times more. Leaving them out of the key averaged
    # a $700 auto into a $10 base card's price history, which is what most of
    # the audit's 20x price spreads turn out to be.
    if attrs.is_auto:
        parts.append("auto")
    if attrs.is_relic:
        parts.append("patch")

    return "-".join(p for p in parts if p)


def card_name(attrs: CardAttrs) -> Optional[str]:
    """A readable name for the group, carrying everything that makes it that card.

    "2024 Select Ja'Marr Chase #12 Dragonscale /81 Auto".

    Every part here was missing at some point and each absence made the name
    describe the wrong thing:

    * **The print run.** A Select Dragonscale is a "Dragonscale /81", not a
      "Dragonscale" and not a "/81". The colour without the run does not say
      which of several numbered versions it is.
    * **Auto and patch.** They were parsed and then never shown, so an
      autographed card and a base card rendered identically -- while selling
      for wildly different money, which is most of what the wide price spreads
      in the audit turn out to be.
    * **The claimed variety.** Words claimed out of the title used to be
      discarded, so a "Decade Dominance Silver" came back as plain "Silver".
    * **"Base".** Without it, a base card is named by what it lacks, and reads
      as though something failed to parse.

    Built from the parsed attributes rather than one seller's wording, so every
    sale of a card renders the same name however it was written.
    """
    if attrs is None:
        return None
    bits: list[str] = []
    if attrs.year:
        bits.append(str(attrs.year))
    if attrs.set_name:
        bits.append(attrs.set_name)
    if attrs.subset:
        # What the card is: "2025 Phoenix Genies Bo Nix #8" tells a reader
        # which #8 they are looking at, and the key says so too.
        bits.append(attrs.subset)
    if attrs.variety:
        bits.append(attrs.variety)
    if attrs.player:
        bits.append(attrs.player)
    if attrs.card_number:
        bits.append(f"#{attrs.card_number}")

    # Below this there is not enough to name, and "Base" must not be what tips
    # it over: a CardAttrs holding only a year would otherwise render as
    # "2021 Base", which names a card that was never identified.
    if len(bits) < 2:
        return None

    if attrs.parallel:
        bits.append(attrs.parallel)
    elif not attrs.subset and not attrs.variety and not attrs.unparsed:
        # Named rather than left blank: a base card is a real thing, and an
        # absence reads like a parse that gave up.
        #
        # But only when the whole title was accounted for. "No parallel found"
        # and "this is the base card" are different claims, and a title holding
        # an unrecognised word like "Dragonscale" is the first, not the second.
        # Saying "Base" there states a fact that is not in evidence.
        bits.append("Base")

    if attrs.print_run:
        bits.append(f"/{attrs.print_run}")
    if attrs.is_auto:
        bits.append("Auto")
    if attrs.is_relic:
        bits.append("Patch")

    return " ".join(bits) if len(bits) >= 2 else None


def grade_label(attrs: CardAttrs) -> str:
    """The market unit within a card: PSA 10, BGS 9.5, or Raw."""
    if attrs and attrs.grader:
        if attrs.grade is not None:
            return f"{attrs.grader} {attrs.grade:g}"
        return attrs.grader
    return "Raw"
