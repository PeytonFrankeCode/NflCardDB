"""Measure real accuracy the only way it can be measured: by checking a sample.

`audit` finds errors the data betrays about itself. That is a floor, not an
accuracy figure -- a group can be quietly wrong in a way no internal check can
see. "Is this sale really that card" compares a title against reality, and
reality is not in the database.

So: draw a random sample, have a person mark each row, and count. Tedious, and
the only thing that produces a number worth quoting.

Marking happens in a browser page rather than a spreadsheet, because a
spreadsheet turned out not to be a safe assumption -- Windows offered Notepad
for the CSV, and a hundred rows of quoted CSV hand-edited in Notepad is a
corrupted sample waiting to happen. The page shows the photo beside the title,
takes one keypress per row, and does the arithmetic itself. The CSV is still
written, and still scored by `--score`, for anyone who does have a spreadsheet.

Sample size matters more than people expect. 100 rows gives roughly +/-10
percentage points at 95% confidence -- enough to tell 60% from 90%, not enough
to tell 88% from 92%. `margin_of_error` is reported alongside the score so the
number never gets quoted more precisely than it deserves.
"""

from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path
from typing import Optional

from . import db as store

# What the reviewer writes. Anything else is treated as "not reviewed" rather
# than guessed at.
YES = {"y", "yes", "1", "true", "correct", "ok"}
NO = {"n", "no", "0", "false", "wrong", "bad"}
SKIP = {"?", "skip", "unsure", "unclear"}

FIELDS = (
    "item_id", "correct", "notes", "title", "card_name", "card_key",
    "player", "year", "set_name", "card_number", "parallel", "grade",
    "confidence", "price", "image_url", "listing",
)


def draw_sample(
    db_path: str,
    size: int = 100,
    seed: Optional[int] = None,
    min_confidence: Optional[float] = None,
    keyed_only: bool = True,
) -> list[dict]:
    """A random sample of parsed sales, ready to be checked by hand.

    Random rather than "the first N": the first N are one day's collection in
    price order, and judging the parser on the cheapest cards of one Tuesday
    would measure the wrong thing.
    """
    where = ["s.sold_date IS NOT NULL"]
    params: list = []
    if keyed_only:
        where.append("c.card_key IS NOT NULL")
    if min_confidence is not None:
        where.append("c.confidence >= ?")
        params.append(min_confidence)

    conn = store.connect(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT s.item_id, s.title, s.price_cents, s.image_url,
                   c.card_key, c.card_name, c.player, c.year, c.set_name,
                   c.card_number, c.parallel, c.grader, c.grade, c.confidence
            FROM sales s JOIN cards c USING (item_id)
            WHERE {' AND '.join(where)}
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    rng = random.Random(seed)
    picked = rng.sample(rows, min(size, len(rows)))

    out = []
    for r in picked:
        grade = (f"{r['grader']} {r['grade']:g}"
                 if r["grader"] and r["grade"] is not None
                 else (r["grader"] or "Raw"))
        out.append({
            "item_id": r["item_id"],
            "correct": "",
            "notes": "",
            "title": r["title"],
            "card_name": r["card_name"] or "",
            "card_key": r["card_key"] or "",
            "player": r["player"] or "",
            "year": r["year"] or "",
            "set_name": r["set_name"] or "",
            "card_number": r["card_number"] or "",
            "parallel": r["parallel"] or "",
            "grade": grade,
            "confidence": r["confidence"],
            "price": (round(r["price_cents"] / 100.0, 2)
                      if r["price_cents"] is not None else ""),
            "image_url": r["image_url"] or "",
            "listing": f"https://www.ebay.com/itm/{r['item_id']}",
        })
    return out


def write_sample(rows: list[dict], out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def score(path: str | Path) -> dict:
    """Turn a marked-up sample into a percentage, with its margin of error."""
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        raise ValueError(f"{path} has no rows in it.")
    if "correct" not in (rows[0] or {}):
        raise ValueError(
            f"{path} has no 'correct' column. Use the file that "
            "`nflcarddb review` wrote, and fill in that column."
        )

    yes = no = skipped = blank = 0
    wrong: list[dict] = []
    for row in rows:
        mark = (row.get("correct") or "").strip().lower()
        if mark in YES:
            yes += 1
        elif mark in NO:
            no += 1
            wrong.append({
                "title": row.get("title", "")[:80],
                "card_name": row.get("card_name", ""),
                "confidence": row.get("confidence", ""),
                "notes": row.get("notes", ""),
            })
        elif mark in SKIP:
            skipped += 1
        else:
            blank += 1

    judged = yes + no
    if not judged:
        raise ValueError(
            "Nothing was marked. Put y or n in the 'correct' column, "
            "then run this again."
        )

    rate = yes / judged
    # Wilson would be better at the extremes, but the plain interval is what
    # people recognise, and this is a sanity check rather than a study.
    margin = 1.96 * math.sqrt(rate * (1 - rate) / judged)

    return {
        "reviewed": judged,
        "correct": yes,
        "wrong": no,
        "unsure": skipped,
        "not_reviewed": blank,
        "accuracy": round(rate, 4),
        "margin_of_error": round(margin, 4),
        "range": [round(max(0.0, rate - margin), 4),
                  round(min(1.0, rate + margin), 4)],
        "wrong_examples": wrong[:10],
    }


def write_html(rows: list[dict], out_path: str | Path) -> Path:
    """Write the sample as a page to mark up in a browser.

    A spreadsheet is the obvious home for a CSV and Peyton has no spreadsheet
    app -- Windows offered Notepad, which for a hundred rows of quoted CSV is
    not a review, it is a data-entry hazard. Editing the wrong column or losing
    a quote would corrupt the sample silently.

    So the page does the marking and the arithmetic. It shows the photo beside
    the seller's title and what we read it as, takes one keypress per row, and
    reports the percentage itself -- no round trip through a file that has to
    survive being hand-edited. The marked CSV is still downloadable, because a
    sample worth taking is worth keeping.

    Self-contained by necessity: it is opened as a local file, so there is no
    server to fetch anything from.
    """
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # `<` escaped because the payload sits inside a <script> block and JSON
    # does not escape it: a seller title containing "</script>" would close the
    # tag early and leave a blank page. Sellers write anything at all.
    payload = json.dumps(rows, ensure_ascii=False).replace("<", "\\u003c")
    path.write_text(_HTML_TEMPLATE.replace("__ROWS__", payload),
                    encoding="utf-8")
    return path


_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Card matching review</title>
<style>
  :root {
    --bg: #f6f7f9; --card: #fff; --ink: #16181d; --muted: #5c6370;
    --line: #dfe3e8; --yes: #1a7f4b; --no: #b3261e; --skip: #6b7280;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14161a; --card:#1c1f26; --ink:#e8eaed; --muted:#9aa2ad;
            --line:#2b303a; --yes:#4ade80; --no:#f87171; --skip:#9ca3af; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.5
         system-ui, -apple-system, "Segoe UI", sans-serif; }
  .wrap { max-width: 780px; margin: 0 auto; padding: 24px 16px 80px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 14px; margin-bottom: 20px; }
  .bar { height:6px; background:var(--line); border-radius:99px; overflow:hidden;
         margin-bottom:20px; }
  .bar span { display:block; height:100%; background:var(--yes); width:0; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:20px; }
  .row { display:flex; gap:18px; align-items:flex-start; }
  .shot { width:180px; flex:0 0 180px; background:var(--bg); border-radius:8px;
          border:1px solid var(--line); min-height:180px; display:flex;
          align-items:center; justify-content:center; overflow:hidden; }
  .shot img { width:100%; height:auto; display:block; }
  .shot .none { color:var(--muted); font-size:13px; padding:16px; text-align:center; }
  .label { font-size:12px; text-transform:uppercase; letter-spacing:.06em;
           color:var(--muted); margin-bottom:4px; }
  .title { font-size:15px; margin-bottom:16px; word-break:break-word; }
  .read { font-size:17px; font-weight:600; margin-bottom:4px; }
  .bits { color:var(--muted); font-size:13px; }
  .buttons { display:flex; gap:10px; margin-top:22px; }
  button { flex:1; padding:14px; font-size:15px; font-weight:600; cursor:pointer;
           border-radius:10px; border:1px solid var(--line); background:var(--card);
           color:var(--ink); }
  button:hover { border-color:var(--muted); }
  .b-yes { color:var(--yes); } .b-no { color:var(--no); } .b-skip { color:var(--skip); }
  .foot { margin-top:16px; display:flex; justify-content:space-between;
          font-size:13px; color:var(--muted); }
  a { color:inherit; }
  .done { text-align:center; padding:24px 0; }
  .pct { font-size:52px; font-weight:700; margin:8px 0; }
  .range { color:var(--muted); font-size:15px; }
  .tally { margin:20px 0; font-size:15px; }
  .hint { font-size:13px; color:var(--muted); margin-top:14px; }
</style></head><body><div class="wrap">
<h1>Is this the right card?</h1>
<div class="sub">For each sale: does what we read match what the seller is
selling? <b>Y</b> = yes, <b>N</b> = no, <b>S</b> = can't tell.</div>
<div class="bar"><span id="bar"></span></div>
<div id="app"></div>
</div>
<script>
const ROWS = __ROWS__;
const KEY = "nflcarddb-review";
let marks = {};
try { marks = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { marks = {}; }

function save() {
  try { localStorage.setItem(KEY, JSON.stringify(marks)); } catch (e) {}
}
function nextIndex() {
  for (let i = 0; i < ROWS.length; i++) {
    if (!marks[ROWS[i].item_id]) return i;
  }
  return -1;
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}
function mark(id, value) { marks[id] = value; save(); render(); }

function render() {
  const app = document.getElementById("app");
  const done = Object.keys(marks).length;
  document.getElementById("bar").style.width =
    (100 * done / ROWS.length).toFixed(1) + "%";

  const i = nextIndex();
  if (i === -1) return finish(app);

  const r = ROWS[i];
  const bits = [r.year, r.set_name, r.card_number ? "#" + r.card_number : "",
                r.parallel, r.grade].filter(Boolean).join(" \\u00b7 ");
  const shot = r.image_url
    ? '<img src="' + esc(r.image_url) + '" alt="" loading="lazy">'
    : '<div class="none">no photo<br>(eBay drops them<br>after ~90 days)</div>';

  app.innerHTML =
    '<div class="card"><div class="row">' +
      '<div class="shot">' + shot + '</div>' +
      '<div style="flex:1">' +
        '<div class="label">seller wrote</div>' +
        '<div class="title">' + esc(r.title) + '</div>' +
        '<div class="label">we read it as</div>' +
        '<div class="read">' + esc(r.card_name || "(nothing)") + '</div>' +
        '<div class="bits">' + esc(bits) + '</div>' +
      '</div>' +
    '</div>' +
    '<div class="buttons">' +
      '<button class="b-yes" onclick="mark(\\'' + r.item_id + '\\',\\'y\\')">Yes (Y)</button>' +
      '<button class="b-no" onclick="mark(\\'' + r.item_id + '\\',\\'n\\')">No (N)</button>' +
      '<button class="b-skip" onclick="mark(\\'' + r.item_id + '\\',\\'?\\')">Can\\'t tell (S)</button>' +
    '</div>' +
    '<div class="foot"><span>' + (done + 1) + ' of ' + ROWS.length + '</span>' +
      '<a href="' + esc(r.listing) + '" target="_blank" rel="noopener">open the listing \\u2197</a>' +
    '</div></div>';
}

function finish(app) {
  let yes = 0, no = 0, skip = 0;
  for (const r of ROWS) {
    const m = marks[r.item_id];
    if (m === "y") yes++; else if (m === "n") no++; else skip++;
  }
  const judged = yes + no;
  const rate = judged ? yes / judged : 0;
  const margin = judged ? 1.96 * Math.sqrt(rate * (1 - rate) / judged) : 0;
  const pct = n => (100 * n).toFixed(1) + "%";

  app.innerHTML = '<div class="card done">' +
    '<div class="label">accuracy</div>' +
    '<div class="pct">' + pct(rate) + '</div>' +
    '<div class="range">somewhere between ' + pct(Math.max(0, rate - margin)) +
      ' and ' + pct(Math.min(1, rate + margin)) + '</div>' +
    '<div class="tally">' + yes + ' right, ' + no + ' wrong, out of ' + judged +
      ' judged' + (skip ? ' (' + skip + ' skipped)' : '') + '</div>' +
    '<div class="buttons">' +
      '<button onclick="download()">Save the marked file</button>' +
      '<button onclick="restart()">Start over</button>' +
    '</div>' +
    '<div class="hint">That range is the honest part. 100 rows pins it to about ' +
      '10 points either way; 400 gets you to 5. Send Claude the percentage ' +
      '<i>and</i> the range.</div>' +
    '</div>';
}

function download() {
  const cols = Object.keys(ROWS[0]);
  const cell = v => '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"';
  const lines = [cols.join(",")];
  for (const r of ROWS) {
    const copy = Object.assign({}, r, { correct: marks[r.item_id] || "" });
    lines.push(cols.map(c => cell(copy[c])).join(","));
  }
  const blob = new Blob(["\\ufeff" + lines.join("\\r\\n")],
                        { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "review-sample-marked.csv";
  document.body.appendChild(a); a.click(); a.remove();
}

function restart() {
  if (!confirm("Clear every mark and start again?")) return;
  marks = {}; save(); render();
}

document.addEventListener("keydown", e => {
  const i = nextIndex();
  if (i === -1) return;
  const k = e.key.toLowerCase();
  if (k === "y") mark(ROWS[i].item_id, "y");
  else if (k === "n") mark(ROWS[i].item_id, "n");
  else if (k === "s" || k === "?") mark(ROWS[i].item_id, "?");
});

render();
</script></body></html>
"""
