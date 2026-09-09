"""Counting what is unusable, by what would actually fix it.

The point of this module is to decide what to build next, so the counts have
to separate work that a machine can do from work that only a person can. A
report that lumps them together answers "how much is broken" and not "what
should I do", and those are different questions.
"""

from nflcarddb import db as store
from nflcarddb.db import Sale
from nflcarddb.parse_title import parse_title
from nflcarddb.triage import review_queue, triage


def _db(tmp_path, listings, name="triage.sqlite"):
    """listings: (title, image_url) pairs."""
    path = tmp_path / name
    conn = store.connect(path)
    run = store.start_run(conn, "2026-01-01")
    rows = [
        Sale(item_id=f"t{i}", title=title, price_cents=2500, currency="USD",
             shipping_cents=0, sold_date=f"2026-08-{(i % 28) + 1:02d}",
             listing_format="Auction", bids=1, best_offer=0, condition=None,
             seller="s", url="u", image_url=img, query_id="q")
        for i, (title, img) in enumerate(listings)
    ]
    store.upsert_sales(conn, rows, run)
    store.upsert_cards(conn, [(s.item_id, parse_title(s.title)) for s in rows],
                       "title/12")
    store.finish_run(conn, run, "ok", len(rows), len(rows), len(rows))
    return store.connect(path)


NUMBERED = "2024 Panini Prizm Jayden Daniels #316"
NO_NUMBER_GRADED = "2024 Panini Prizm Caleb Williams Rookie PSA 10"
NO_NUMBER_RAW = "2023 Panini Mosaic Bijan Robinson Rookie"


def test_a_numbered_card_is_not_counted_as_a_problem(tmp_path):
    conn = _db(tmp_path, [(NUMBERED, "http://i/1")] * 4)
    try:
        report = triage(conn)
    finally:
        conn.close()

    assert report["cards"] == 1
    assert report["numberless"]["groups"] == 0
    assert report["numberless_worth_fixing"]["groups"] == 0


def test_a_graded_group_with_a_photo_is_machine_reachable(tmp_path):
    """The grader printed the card number on the label. That is the one case
    where a photo settles the question without anyone looking at it."""
    conn = _db(tmp_path, [(NO_NUMBER_GRADED, "http://i/1")] * 4)
    try:
        report = triage(conn)
    finally:
        conn.close()

    assert report["numberless_worth_fixing"]["groups"] == 1
    assert report["a_photo_could_read_it"]["groups"] == 1
    assert report["a_photo_could_read_it"]["sales"] == 4
    assert report["only_a_person_could"]["groups"] == 0


def test_a_raw_group_needs_a_person_however_many_photos_it_has(tmp_path):
    """A photo of a raw card shows the card. It does not show the number in a
    place anything can read reliably -- the number is printed small, on the
    back as often as the front, and the front is what eBay shows."""
    conn = _db(tmp_path, [(NO_NUMBER_RAW, "http://i/1")] * 5)
    try:
        report = triage(conn)
    finally:
        conn.close()

    assert report["a_photo_could_read_it"]["groups"] == 0
    assert report["only_a_person_could"]["groups"] == 1
    assert report["and_has_a_photo_to_look_at"]["groups"] == 1


def test_a_group_with_no_photo_at_all_is_counted_apart(tmp_path):
    """eBay drops the picture about ninety days after the sale. A group whose
    photos have expired cannot be reviewed by machine OR by eye, and saying so
    is the difference between a plan and a wish."""
    conn = _db(tmp_path, [(NO_NUMBER_RAW, None)] * 5)
    try:
        report = triage(conn)
    finally:
        conn.close()

    assert report["only_a_person_could"]["groups"] == 1
    assert report["and_has_a_photo_to_look_at"]["groups"] == 0


def test_one_off_sales_are_not_counted_as_work(tmp_path):
    """A group of one is a price, not a history. Fixing it buys a chart nobody
    would draw, and counting it makes the job look bigger than it is."""
    conn = _db(tmp_path, [(NO_NUMBER_RAW, "http://i/1")])
    try:
        report = triage(conn, min_sales=3)
    finally:
        conn.close()

    assert report["numberless"]["groups"] == 1
    assert report["numberless_worth_fixing"]["groups"] == 0


def test_the_review_queue_puts_the_biggest_group_first(tmp_path):
    """Settling one group settles every sale under it, so the biggest group is
    the most valuable half hour available."""
    listings = ([(NO_NUMBER_RAW, "http://i/1")] * 3
                + [("2022 Donruss Optic Garrett Wilson Rookie", "http://i/2")] * 9)
    conn = _db(tmp_path, listings)
    try:
        queue = review_queue(conn, limit=10)
    finally:
        conn.close()

    assert len(queue) == 2
    assert queue[0]["sales"] == 9
    assert queue[0]["sales"] >= queue[1]["sales"]
    assert queue[0]["photos"] == 9


def test_the_queue_shows_distinct_titles(tmp_path):
    """Four copies of one title tells a reviewer nothing. Where the titles
    differ is where the missing number tends to be hiding."""
    conn = _db(tmp_path, [(NO_NUMBER_RAW, "http://i/1")] * 3
                         + [(NO_NUMBER_RAW + " SSP", "http://i/2")])
    try:
        queue = review_queue(conn, limit=5)
    finally:
        conn.close()

    assert len(queue[0]["titles"]) == len(set(queue[0]["titles"]))
    assert len(queue[0]["titles"]) == 2


def test_photo_coverage_counts_the_combination_that_matters(tmp_path):
    conn = _db(tmp_path, [(NO_NUMBER_GRADED, "http://i/1"),
                          (NO_NUMBER_GRADED, None),
                          (NO_NUMBER_RAW, "http://i/2"),
                          (NUMBERED, None)])
    try:
        cov = triage(conn)["photo_coverage"]
    finally:
        conn.close()

    assert cov["sales"] == 4
    assert cov["with_photo"] == 2
    assert cov["graded"] == 2
    assert cov["graded_with_photo"] == 1, "graded AND still has its picture"
