"""Reading the card off the photo, and what happens when it disagrees.

Every test here feeds `TextLine`s in directly rather than running OCR. That is
deliberate: the OCR engine is a 200MB optional install, and none of the logic
worth testing lives inside it. What is worth testing is what happens to text
that arrives mangled in the specific ways OCR mangles it -- spaces gone, zeros
for O's -- and how two disagreeing readings get resolved.

The mangled strings below are not invented. They are what RapidOCR actually
returned from a rendered PSA slab.
"""

import pytest

from nflcarddb.models import CardAttrs
from nflcarddb.parse_title import parse_title
from nflcarddb.vision import (
    LABEL_BAND,
    Reading,
    TextLine,
    attrs_from_lines,
    cache_path,
    group_lines,
    reconcile,
)

ROSTER = {"jayden daniels", "barry sanders", "caleb williams", "ja'marr chase"}

# Verbatim OCR output from a rendered slab, positions included.
PSA_SLAB = [
    TextLine("2024PANINIPRIZM", 0.017, 0.99),
    TextLine("JAYDENDANIELS", 0.054, 1.00),
    TextLine("MINT9", 0.047, 1.00),
    TextLine("#316R00KIE", 0.091, 0.98),
    TextLine("94612385", 0.094, 1.00),
    TextLine("JAYDENDANIELS", 0.834, 1.00),   # the name printed on the card
]


def test_a_slab_label_reads_as_a_card():
    attrs = attrs_from_lines(PSA_SLAB, ROSTER)

    assert attrs.player == "Jayden Daniels"
    assert attrs.year == 2024
    assert attrs.set_name == "Prizm"
    assert attrs.card_number == "316"
    assert attrs.grader == "PSA"
    assert attrs.grade == 9.0


def test_a_missing_space_does_not_hide_the_year():
    """`2024PANINIPRIZM` has no word boundary after the year, so a `\\b` pattern
    finds nothing. This is the single most common shape OCR returns."""
    assert attrs_from_lines([TextLine("2024PANINIPRIZM", 0.02)], ROSTER).year == 2024


def test_the_card_number_stops_before_a_word_run_together_with_it():
    """`#316R00KIE` is the number and "ROOKIE" with zeros for O's. Reading the
    R as a variant letter would give 316R, a card that does not exist."""
    attrs = attrs_from_lines([TextLine("#316R00KIE", 0.09)], ROSTER)
    assert attrs.card_number == "316"


def test_a_real_variant_letter_is_still_kept():
    """The rule must not cost `#12A` its A -- nothing alphanumeric follows it."""
    attrs = attrs_from_lines([TextLine("#12A MINT 9", 0.09)], ROSTER)
    assert attrs.card_number == "12A"


def test_a_lettered_card_number_survives():
    attrs = attrs_from_lines([TextLine("#NFS-1 GEM MT 10", 0.09)], ROSTER)
    assert attrs.card_number == "NFS-1"
    assert attrs.grade == 10.0


def test_digits_read_as_letters_still_match_the_vocabulary():
    """OCR trades 1 for I and 0 for O. Both sides get folded, so it cancels."""
    attrs = attrs_from_lines([TextLine("2021 PR1ZM JA'MARR CHASE", 0.02)], ROSTER)
    assert attrs.set_name == "Prizm"
    assert attrs.player == "Ja'Marr Chase"


def test_the_roster_is_what_makes_a_name_readable():
    """Without a roster there is no way to know where the name ends: the text is
    one unbroken run. Nothing is guessed at."""
    lines = [TextLine("JAYDENDANIELS", 0.05)]
    assert attrs_from_lines(lines, roster=None).player is None
    assert attrs_from_lines(lines, ROSTER).player == "Jayden Daniels"


def test_psa_grade_words_carry_their_number():
    for word, expected in [("GEM MT 10", 10.0), ("MINT 9", 9.0),
                           ("NM-MT 8", 8.0), ("EX-MT 6", 6.0)]:
        attrs = attrs_from_lines([TextLine(word, 0.05)], ROSTER)
        assert attrs.grade == expected, word
        assert attrs.grader == "PSA", word


def test_a_numeric_grader_is_read_from_its_number():
    """BGS and SGC print a number where PSA prints a word."""
    attrs = attrs_from_lines([TextLine("BGS 9.5", 0.05)], ROSTER)
    assert attrs.grader == "BGS"
    assert attrs.grade == 9.5


def test_text_below_the_label_is_not_read_as_identity():
    """A name printed on the card face is stylised and half-hidden by logos. It
    is corroboration, never the source."""
    face_only = [TextLine("BARRYSANDERS", 0.83, 1.0)]
    assert attrs_from_lines(face_only, ROSTER).player is None
    assert LABEL_BAND < 0.83


def test_a_raw_card_with_no_label_reads_as_nothing():
    """An ungraded card has no label to read. No key beats a wrong key."""
    attrs = attrs_from_lines([TextLine("blurry", 0.6, 0.9)], ROSTER)
    assert attrs.confidence == 0.0
    assert attrs.player is None


def test_low_confidence_detections_are_dropped():
    """Holo glare gets read as characters. It should not become a card number."""
    attrs = attrs_from_lines([TextLine("#9999", 0.05, 0.2)], ROSTER)
    assert attrs.card_number is None


def test_a_split_row_is_read_as_one_row():
    """The name and the grade are one row of the label but two detections,
    because the gap between them is wide."""
    rows = group_lines([TextLine("JAYDENDANIELS", 0.054),
                        TextLine("MINT9", 0.047)])
    assert len(rows) == 1
    assert "JAYDENDANIELS" in rows[0].text and "MINT9" in rows[0].text


def test_separate_rows_stay_separate():
    rows = group_lines([TextLine("a", 0.02), TextLine("b", 0.30)])
    assert len(rows) == 2


# --- reconciling the two readings ----------------------------------------


def test_a_useless_title_is_rescued_by_the_photo():
    """The reason this exists. The seller wrote nothing usable; the slab did."""
    title = parse_title("HUGE FOOTBALL CARD LOT MUST SEE!!!", ROSTER)
    reading = reconcile(title, attrs_from_lines(PSA_SLAB, ROSTER))

    assert reading.attrs.player == "Jayden Daniels"
    assert reading.attrs.year == 2024
    assert reading.attrs.set_name == "Prizm"
    assert reading.attrs.card_number == "316"


def test_agreement_between_two_independent_readings_raises_confidence():
    title = CardAttrs(player="Jayden Daniels", year=2024, set_name="Prizm",
                      card_number="316", confidence=0.6)
    reading = reconcile(title, attrs_from_lines(PSA_SLAB, ROSTER))

    assert not reading.conflicts
    assert "player" in reading.agreed
    assert reading.attrs.confidence > 0.6


def test_agreement_never_lowers_confidence():
    """A title parsed with full certainty must not be talked *down* by the
    photo agreeing with it -- which is what a ceiling below 1.0 would do."""
    title = parse_title("2024 Panini Prizm Jayden Daniels #316 PSA 9", ROSTER)
    assert title.confidence == 1.0            # the case that exposed it

    reading = reconcile(title, attrs_from_lines(PSA_SLAB, ROSTER))
    assert reading.agreed
    assert reading.attrs.confidence >= title.confidence


def test_the_photo_only_fills_blanks_outside_the_name():
    """The title carries parallels, serial numbers and autographs that no label
    mentions, so it is the richer source everywhere except the name."""
    title = CardAttrs(player="Jayden Daniels", year=2024, set_name="Prizm",
                      card_number="316", parallel="Silver Prizm",
                      serial_number=12, print_run=99, is_auto=True,
                      confidence=0.8)
    reading = reconcile(title, attrs_from_lines(PSA_SLAB, ROSTER))

    assert reading.attrs.parallel == "Silver Prizm"
    assert reading.attrs.serial_number == 12
    assert reading.attrs.is_auto is True


def test_a_padded_title_name_is_not_a_conflict():
    """"Bomb Squad Jayden Daniels" is the same player wearing an insert name."""
    title = CardAttrs(player="Bomb Squad Jayden Daniels", year=2024,
                      set_name="Prizm", confidence=0.6)
    reading = reconcile(title, attrs_from_lines(PSA_SLAB, ROSTER))

    assert not reading.conflicts
    assert "player" in reading.agreed


def test_two_different_players_is_a_conflict_the_photo_wins():
    """The label was printed by a grader holding the card. The title was typed
    by someone selling it."""
    title = CardAttrs(player="Caleb Williams", year=2024, set_name="Prizm",
                      confidence=0.8)
    reading = reconcile(title, attrs_from_lines(PSA_SLAB, ROSTER))

    assert reading.attrs.player == "Jayden Daniels"
    assert [f for f, _, _ in reading.conflicts] == ["player"]
    assert reading.attrs.confidence < title.confidence


def test_a_conflict_outside_the_name_is_reported_but_not_applied():
    """OCR misreading a year should not silently rewrite a title that was
    perfectly clear. It is recorded so the disagreement can be counted."""
    title = CardAttrs(player="Jayden Daniels", year=2023, set_name="Prizm",
                      confidence=0.8)
    reading = reconcile(title, attrs_from_lines(PSA_SLAB, ROSTER))

    assert reading.attrs.year == 2023
    assert ("year", 2023, 2024) in reading.conflicts


def test_no_photo_leaves_the_title_reading_untouched():
    title = parse_title("2024 Panini Prizm Jayden Daniels #316 PSA 9", ROSTER)
    reading = reconcile(title, None)

    assert reading.attrs.player == title.player
    assert reading.attrs.confidence == title.confidence
    assert reading.conflicts == []
    assert reading.saw_card is False


def test_reconciling_does_not_mutate_the_title_reading():
    """The title's own parse stays available for comparison afterwards."""
    title = CardAttrs(player="Caleb Williams", year=2024, confidence=0.8)
    reading = reconcile(title, attrs_from_lines(PSA_SLAB, ROSTER))

    assert title.player == "Caleb Williams"
    assert reading.from_title.player == "Caleb Williams"
    assert reading.attrs.player == "Jayden Daniels"


def test_the_image_cache_is_keyed_by_url(tmp_path):
    a = cache_path("https://i.ebayimg.com/images/g/abc/s-l1600.jpg", tmp_path)
    b = cache_path("https://i.ebayimg.com/images/g/xyz/s-l1600.jpg", tmp_path)
    assert a != b
    assert a.parent == tmp_path
    assert a == cache_path("https://i.ebayimg.com/images/g/abc/s-l1600.jpg", tmp_path)


def _vision_db(tmp_path, titles):
    from nflcarddb import db as store
    from nflcarddb.models import Sale

    path = tmp_path / "v.db"
    conn = store.connect(path)
    run = store.start_run(conn, "2026-08-03")
    sales = [Sale(item_id=f"{900000000000 + i}", title=t, price_cents=1000,
                  sold_date="2026-08-03",
                  image_url=f"https://i.ebayimg.com/images/g/{i}/s-l140.jpg")
             for i, t in enumerate(titles)]
    store.upsert_sales(conn, sales, run)
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in sales], "v1")
    conn.close()
    return path


def _wire(monkeypatch, tmp_path, titles, lines=PSA_SLAB):
    """Point the command at a fake camera and a fake OCR engine."""
    from nflcarddb import cli, vision

    db = _vision_db(tmp_path, titles)
    roster = tmp_path / "players.txt"
    roster.write_text("\n".join(sorted(ROSTER)) + "\n", encoding="utf-8")

    monkeypatch.setattr(vision, "fetch_image", lambda url, cache, **kw: tmp_path / "x.jpg")
    monkeypatch.setattr(vision, "rapidocr_reader", lambda: (lambda path: list(lines)))
    return cli, db, roster


def test_the_command_reports_agreement_without_saving_anything(monkeypatch,
                                                              tmp_path, capsys):
    cli, db, roster = _wire(monkeypatch, tmp_path,
                            ["2024 Panini Prizm Jayden Daniels #316 PSA 9"])

    assert cli.main(["vision", "--db", str(db), "--roster", str(roster),
                     "--config", "none.yml", "--limit", "5"]) == 0

    out = capsys.readouterr().out
    assert "label read" in out
    assert "agreed with the title" in out
    assert "Nothing was saved" in out


def test_the_command_finds_the_titles_that_needed_rescuing(monkeypatch,
                                                           tmp_path, capsys):
    """--unclear is the case worth measuring: sales with no identity at all."""
    cli, db, roster = _wire(monkeypatch, tmp_path,
                            ["HUGE FOOTBALL CARD LOT MUST SEE!!!"])

    assert cli.main(["vision", "--db", str(db), "--roster", str(roster),
                     "--config", "none.yml", "--unclear", "--limit", "5"]) == 0

    out = capsys.readouterr().out
    assert "photo supplied" in out


def test_a_dead_photo_url_is_counted_not_crashed(monkeypatch, tmp_path, capsys):
    """eBay deletes listing photos after about 90 days. That is an ordinary
    state for older sales, not a run-ending error."""
    from nflcarddb import cli, vision

    db = _vision_db(tmp_path, ["2024 Panini Prizm Jayden Daniels #316 PSA 9"])

    def gone(url, cache, **kw):
        raise OSError("404")

    monkeypatch.setattr(vision, "fetch_image", gone)
    monkeypatch.setattr(vision, "rapidocr_reader", lambda: (lambda p: []))

    assert cli.main(["vision", "--db", str(db), "--config", "none.yml",
                     "--limit", "5"]) == 0
    assert "photo could not load" in capsys.readouterr().out


def test_a_missing_ocr_engine_says_how_to_install_it(monkeypatch):
    """A 200MB optional dependency being absent is an ordinary state, not a
    crash. The message has to be the install line."""
    import builtins

    from nflcarddb.vision import OcrUnavailable, rapidocr_reader

    real = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("rapidocr"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(OcrUnavailable, match="pip install rapidocr-onnxruntime"):
        rapidocr_reader()
