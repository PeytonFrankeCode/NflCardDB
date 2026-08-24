"""Read the card out of the listing photo, as a second opinion on the title.

The title is what a seller typed to sell something. The card is what it is. When
those disagree the card wins, and when the title says nothing usable -- "HUGE
FOOTBALL CARD LOT MUST SEE" -- the photo is the only witness left.

The tractable version of "recognise the card" is not matching the picture
against a catalogue of every card ever printed. It is reading the words already
printed on it. A graded slab carries a label that is, in effect, the title
written by the grader instead of the seller:

    2024 PANINI PRIZM
    JAYDEN DANIELS
    #316 ROOKIE                                 MINT 9      94612385

That is the same shape `parse_title` already handles, so the OCR output is fed
through the same vocabularies rather than a second parser.

Two things OCR does that shape this module:

**It loses the spaces.** A detected line comes back as `JAYDENDANIELS`. That
turns out to be convenient rather than a problem, because `normalize_player`
already strips spaces to fold `Ja'Marr`/`JaMarr`/`Ja Marr` together -- so the
learned roster is *already* in the form OCR emits, and matching is a lookup
instead of a reconstruction.

**It confuses digits for letters**: `R00KIE`, `PR1ZM`. Both sides of every
vocabulary comparison are folded through `_deconfuse`, so the confusion cancels
out instead of having to be guessed at.

What this deliberately does not do is decide anything on its own. It produces a
second reading, `reconcile` compares it with the title's, and a disagreement is
*reported* rather than silently resolved -- two independent readings that
contradict each other mean one of them is wrong, which is a measurement worth
having and not a thing to paper over.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from .card_key import normalize_player
from .models import CardAttrs
from .parse_title import GRADERS, SETS, parse_title

# The label on a slab occupies roughly the top sixth of the photo. The player's
# name is usually printed on the card as well, and that copy is stylised,
# angled, and half the time obscured by a logo -- so the band is where the
# reliable text is, and text below it is treated as corroboration at best.
LABEL_BAND = 0.22

# eBay resizes on demand; a label strip is unreadable at the 500px used for
# thumbnails on the dashboard.
OCR_SIZE = 1600

# Detections below this are usually furniture -- holo glare read as characters.
MIN_TEXT_CONFIDENCE = 0.5

# A folded roster name shorter than this risks matching inside an unrelated
# word. Full names are comfortably longer.
MIN_NAME_LENGTH = 8

# PSA prints words where other graders print numbers, and the words are a
# reliable tell for which grader it is.
PSA_GRADE_WORDS = {
    "GEMMT": 10.0, "GEMMINT": 10.0, "MINT": 9.0, "NMMT": 8.0, "NM": 7.0,
    "EXMT": 6.0, "EX": 5.0, "VGEX": 4.0, "VG": 3.0, "GOOD": 2.0, "PR": 1.0,
}

# No `\b` on either side. OCR returns `2024PANINIPRIZM` as one run, and a word
# boundary needs a non-word character that is not there -- so the year has to be
# bounded by digits alone.
_YEAR_RE = re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)")

# Same problem, worse: `#316R00KIE` is the number and the word "ROOKIE" run
# together with two zeros for O's. A trailing variant letter is only accepted
# when nothing alphanumeric follows it, so `#12A` keeps its A while `#316R00KIE`
# stops at 316 instead of reading the first letter of a word as part of it.
_NUMBER_RE = re.compile(
    r"#\s?([A-Z]{0,4}-?\d{1,4}(?:[A-Z](?![A-Za-z0-9]))?)", re.I
)
_DECIMAL_GRADE_RE = re.compile(r"\b(10|[1-9](?:\.5)?)\b")

# Characters OCR trades for one another. Both the read text and the vocabulary
# go through this, so `R00KIE` and `ROOKIE` land on the same string.
_CONFUSIONS = str.maketrans({
    "0": "o", "1": "i", "l": "i", "5": "s", "8": "b", "2": "z", "6": "g",
})


class OcrUnavailable(RuntimeError):
    """No OCR engine installed. Carries the install line, not a stack trace."""


@dataclass(slots=True)
class TextLine:
    """One run of text OCR found, and where on the image it sits."""

    text: str
    top: float          # fraction of image height, 0 at the top
    confidence: float = 1.0


@dataclass(slots=True)
class Reading:
    """A merged reading, and everything the two sources disagreed about."""

    attrs: CardAttrs
    from_title: CardAttrs
    from_photo: Optional[CardAttrs] = None
    filled: list[str] = field(default_factory=list)
    agreed: list[str] = field(default_factory=list)
    conflicts: list[tuple[str, object, object]] = field(default_factory=list)

    @property
    def saw_card(self) -> bool:
        return self.from_photo is not None and (self.from_photo.confidence or 0) > 0


def _fold(text: str) -> str:
    """Lowercase, strip everything that is not a letter or digit, deconfuse.

    The result is directly comparable with `normalize_player` output, which is
    what lets the learned roster be reused as the OCR dictionary.
    """
    return re.sub(r"[^a-z0-9]+", "", text.lower()).translate(_CONFUSIONS)


def _deconfuse(text: str) -> str:
    return _fold(text)


# Vocabulary folded once, longest first so "Bowman Chrome" is tried before
# "Bowman" and "Donruss Optic" before "Donruss".
_FOLDED_SETS = sorted(
    ((_fold(s), s) for s in SETS), key=lambda pair: -len(pair[0])
)


def group_lines(lines: Iterable[TextLine], tolerance: float = 0.02) -> list[TextLine]:
    """Join detections that sit on the same visual row.

    OCR splits a label row wherever the gap is wide -- the player's name and the
    grade are one row of the label but two detections. Reading them apart loses
    the row; reading them joined is how a person reads it.
    """
    ordered = sorted(lines, key=lambda l: l.top)
    rows: list[list[TextLine]] = []
    for line in ordered:
        if rows and abs(line.top - rows[-1][0].top) <= tolerance:
            rows[-1].append(line)
        else:
            rows.append([line])
    return [
        TextLine(text=" ".join(l.text for l in row),
                 top=row[0].top,
                 confidence=min(l.confidence for l in row))
        for row in rows
    ]


def _match_set(folded: str) -> Optional[str]:
    for candidate, original in _FOLDED_SETS:
        if candidate and candidate in folded:
            return original
    return None


def _match_player(folded: str, roster: Optional[set[str]]) -> Optional[str]:
    if not roster:
        return None
    best = None
    for name in roster:
        key = _deconfuse(normalize_player(name))
        if len(key) >= MIN_NAME_LENGTH and key in folded:
            # Longest wins: "jaydendaniels" beats a shorter name nested in it.
            if best is None or len(key) > len(best[0]):
                best = (key, name)
    return best[1].title() if best else None


def _match_grade(text: str, folded: str) -> tuple[Optional[str], Optional[float]]:
    grader = next((g for g in GRADERS if g in text.upper()), None)

    for word, value in sorted(PSA_GRADE_WORDS.items(), key=lambda kv: -len(kv[0])):
        if _fold(word) in folded:
            # The words are PSA's; seeing one identifies the grader too.
            return (GRADERS.get(grader or "PSA", "PSA"), value)

    if grader:
        found = _DECIMAL_GRADE_RE.search(text)
        if found:
            return (GRADERS[grader], float(found.group(1)))
        return (GRADERS[grader], None)
    return (None, None)


def attrs_from_lines(lines: Iterable[TextLine],
                     roster: Optional[set[str]] = None) -> CardAttrs:
    """Turn OCR output into card attributes.

    Only text in the label band is trusted for identity. The card face is read
    too, but a name printed on the card is stylised and frequently unreadable,
    so it only ever confirms what the label already said.
    """
    rows = group_lines(l for l in lines if l.confidence >= MIN_TEXT_CONFIDENCE)
    band = [l for l in rows if l.top <= LABEL_BAND]
    if not band:
        return CardAttrs(confidence=0.0)

    text = " ".join(l.text for l in band)
    folded = _fold(text)

    attrs = CardAttrs()
    year = _YEAR_RE.search(text)
    if year:
        attrs.year = int(year.group(1))
    attrs.set_name = _match_set(folded)
    attrs.player = _match_player(folded, roster)

    number = _NUMBER_RE.search(text)
    if number:
        attrs.card_number = number.group(1).upper()

    attrs.grader, attrs.grade = _match_grade(text, folded)
    attrs.is_graded = attrs.grader is not None

    found = sum(1 for v in (attrs.year, attrs.set_name, attrs.player,
                            attrs.card_number) if v)
    # Four fields off a printed label is as good as this gets; one is a guess.
    attrs.confidence = round(min(0.95, 0.2 * found + 0.15 * bool(attrs.grader)), 2)
    return attrs


def reconcile(from_title: CardAttrs, from_photo: Optional[CardAttrs]) -> Reading:
    """Merge the two readings, keeping every disagreement visible.

    The photo wins on the player's name and only there: the label was printed by
    a grader looking at the card, while the title was typed by someone selling
    it, and the name is the field sellers pad with insert names and hype. On
    everything else the title is richer -- it carries parallels, serial numbers
    and autographs that no label mentions -- so the photo only fills blanks.

    Nothing is discarded quietly. A conflict is recorded whichever way it is
    resolved, because two independent readings disagreeing is evidence one of
    them is wrong, and that is measurable in a way a single reading never is.
    """
    merged = CardAttrs(**{f: getattr(from_title, f)
                          for f in from_title.__slots__})
    reading = Reading(attrs=merged, from_title=from_title, from_photo=from_photo)
    if from_photo is None:
        return reading

    for name in ("year", "set_name", "player", "card_number", "grader", "grade"):
        theirs = getattr(from_photo, name)
        ours = getattr(merged, name)
        if theirs in (None, ""):
            continue
        if ours in (None, ""):
            setattr(merged, name, theirs)
            reading.filled.append(name)
        elif _equivalent(name, ours, theirs):
            reading.agreed.append(name)
        else:
            reading.conflicts.append((name, ours, theirs))
            if name == "player":
                setattr(merged, name, theirs)

    if reading.agreed:
        # Two sources read independently landing in the same place. Clamped
        # upward only: a title that was already certain must not be talked down
        # by the photo agreeing with it, which a plain `min` against a ceiling
        # below 1.0 would do.
        bonus = round(min(1.0, merged.confidence + 0.05 * len(reading.agreed)), 2)
        merged.confidence = max(merged.confidence, bonus)
    if reading.conflicts:
        merged.confidence = round(max(0.1, merged.confidence - 0.15), 2)
    return reading


def _equivalent(field_name: str, a, b) -> bool:
    if field_name == "player":
        # The title's name may still be wearing an insert name; containment is
        # agreement for the same reason it is in the audit.
        x, y = normalize_player(str(a)), normalize_player(str(b))
        return bool(x) and bool(y) and (x in y or y in x)
    if field_name in ("set_name", "card_number", "grader"):
        return _fold(str(a)) == _fold(str(b))
    return a == b


# --------------------------------------------------------------------------
# Getting pixels. Everything above is pure and testable without either a
# network or an OCR engine; everything below needs both.
# --------------------------------------------------------------------------


def cache_path(url: str, cache_dir: str | Path) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    return Path(cache_dir) / f"{digest}.jpg"


def fetch_image(url: str, cache_dir: str | Path, timeout: float = 20.0) -> Path:
    """Download one listing photo, or return the copy already on disk.

    This is the one place the project stores an image. Storing every photo was
    never worth it -- the dashboard shows them straight off eBay's CDN -- but a
    photo being read has to be fetched, and fetching the same one twice while
    tuning is waste. The cache is disposable: delete the folder and it refills.
    """
    from .images import normalize_image_url

    target = normalize_image_url(url, OCR_SIZE) or url
    path = cache_path(target, cache_dir)
    if path.exists() and path.stat().st_size > 0:
        return path

    import requests

    path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(
        target, timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*"},
    )
    response.raise_for_status()
    path.write_bytes(response.content)
    return path


def rapidocr_reader() -> Callable[[Path], list[TextLine]]:
    """The default OCR backend, loaded once and reused.

    RapidOCR rather than Tesseract because it installs from pip with its models
    inside the wheel. Tesseract needs a system binary installed separately,
    which on the Windows PC this runs on is the difference between a working
    feature and a support conversation.
    """
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise OcrUnavailable(
            "No OCR engine installed. Install it with:\n"
            "    pip install rapidocr-onnxruntime"
        ) from exc

    engine = RapidOCR()

    def read(path: Path) -> list[TextLine]:
        result, _ = engine(str(path))
        if not result:
            return []
        # The image's own height, not the lowest detection. On a photo cropped
        # tight to the slab label every detection is near the top, and dividing
        # by the lowest one would rescale the label to fill the frame -- putting
        # its bottom row outside the band that defines it.
        from PIL import Image

        with Image.open(path) as img:
            height = img.height or 1
        return [
            TextLine(text=text,
                     top=min(pt[1] for pt in box) / height,
                     confidence=float(score))
            for box, text, score in result
        ]

    return read
