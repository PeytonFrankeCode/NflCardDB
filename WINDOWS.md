# Running the collector on your Windows PC

No typing commands. You install two free programs once, then it's double-clicking
files.

**Why your own PC and not GitHub's servers:** we tried it on GitHub's servers and
eBay showed a "verify you are human" page instead of results. eBay treats traffic
from data centres as suspicious. Your home internet looks like an ordinary person
browsing, which is exactly what it is.

---

## One-time setup (about 15 minutes)

### 1. Install Python

1. Go to **https://www.python.org/downloads/**
2. Click the big yellow **Download Python** button.
3. Open the file that downloads.
4. Click **Install Now** and wait for it to finish.

That's it. If you see a checkbox saying **"Add python.exe to PATH"**, tick it —
but don't go looking for it. `setup.bat` searches the places Python installs to
and uses whichever it finds, so the checkbox doesn't decide whether this works.

**Not seeing that checkbox?** Two ordinary reasons, neither a problem:

- **Python is already installed**, so the installer shows *Modify / Repair /
  Uninstall* instead of the first-time screen. Close it and go straight to
  step 2 — `setup.bat` will find the copy you already have.
- **You got the Microsoft Store version**, which has no installer screen at all.
  It works fine and sets itself up automatically.

Either way: skip ahead. If Python genuinely isn't there, `setup.bat` says so in
plain English and tells you what to do.

### 2. Install GitHub Desktop

1. Go to **https://desktop.github.com/**
2. Download and install it.
3. Open it and sign in with your GitHub account (the same one that owns this
   project).

### 3. Download the project onto your PC

In GitHub Desktop: **File** → **Clone repository** → **GitHub.com** tab → pick
**PeytonFrankeCode/NflCardDB** → click **Clone**.

Note the folder it says it's saving to — usually
`C:\Users\<your name>\Documents\GitHub\NflCardDB`.

### 4. Get your PC ready

Open that folder and **double-click `setup.bat`**.

A black window opens and prints what it's doing. It takes a couple of minutes,
and it ends with one of these:

The first run also downloads a browser engine (a few hundred MB), because eBay
refuses plain scripts on most connections. That download happens once.

| It says | What it means |
|---|---|
| **IT WORKS** | eBay returned real listings. You're ready. |
| **eBay turned us away** | Wait an hour or two and double-click `setup.bat` again. Not your fault. |
| **Reached eBay, but read zero listings** | The category number needs updating. It saves the page in `data\html` — send that file to Claude. |
| **Could not reach eBay** | Check the PC is online. |
| **Python is not on this PC yet** | Step 1 didn't take. Run the python.org installer again. |
| **Python … is too old** | You have an old Python. Install the current one from python.org. |

Windows may warn you about running a downloaded file. Click **More info** →
**Run anyway**.

---

## If eBay turns the collector away

It may. eBay refused both the simple method and a real browser on at least one
home connection. When automatic collecting doesn't work, there is a way that
**cannot** be blocked, because nothing on your PC talks to eBay — you do.

1. In your normal browser, search eBay for the cards you want, and turn on the
   **Sold items** filter in the left sidebar.
2. Press **Ctrl+S** and save the page. Choose **Webpage, Complete**. Put the
   files anywhere you like — a folder on your Desktop is fine.
3. **Drag the saved file (or the whole folder) onto `import.bat`.**

That reads the prices off the pages and files them away exactly as if the
collector had fetched them, then updates your dashboard. Then commit and push in
GitHub Desktop as usual.

Set eBay to show **240 items per page** (a dropdown at the bottom of the
results) and each saved page is worth 240 sales instead of 60.

## Sign in once (this is what makes it automatic)

eBay only shows sold prices to signed-in accounts, so the collector needs its
own signed-in session.

**Double-click `login.bat`.** A Chrome window opens. Sign in exactly as you
normally would, and if eBay asks you to prove you are human, do the puzzle
yourself -- that is why this step happens in a real window with you sitting
there. Tick **Stay signed in** if offered; the session then lasts weeks.

Nothing is bypassed and no password is read. The session is stored on your PC
in `data\browser-profile`.

> **Why not your everyday Chrome?** It cannot work. Since Chrome 127 cookies are
> locked to the Chrome process that wrote them, so a Chrome started by this
> project cannot decrypt a session created from your desktop shortcut -- it opens
> the profile and finds itself logged out. When the same launcher writes *and*
> reads the profile, that problem disappears. Hence a profile of its own.

## Set it and forget it

Two double-clicks, once, and it collects and uploads every day by itself.

**1. `connect-cloudflare.bat`** — paste your Cloudflare API token and Account ID.
It checks they work before saving, so you find out now rather than in a silent
failure at 7am. After this, uploads never ask again.

**2. `schedule.bat`** — choose **1**, pick a time, done. Pick a time this PC is
usually on and you're signed in to Windows.

From then on it collects yesterday's sales, **works backwards through the older
days eBay still has**, sizes the photos, refreshes the dashboard files, and
uploads to your website — with nobody watching. Option **2** in the same menu
runs it immediately so you can prove it works instead of waiting a day.

### The catch-up, and why it takes weeks

eBay keeps sold listings for about **90 days**, then drops them for good. So
there is a backlog worth collecting — roughly 15 hours of it, at ten minutes a
day of history.

Rather than one enormous run, each nightly run spends a few hours going
backwards (you choose how many when you set the schedule; 3 is the default).
Days already collected are skipped, so it walks steadily back through the window
and then quietly becomes a no-op. It also fills in any day the schedule missed,
which means a week away from the PC repairs itself.

**How long a day takes.** Roughly 2-4 seconds a page, so a full day is 10-15
minutes. If it feels slower than that, the log line `seconds_per_page` at the end
of a run says exactly where you are. Bot checks add a pause each time — they are
survivable now, but not free.

**Double-click `catchup.bat`** to see a progress bar of how much of the 90 days
you hold, and to push harder if you want — "collect for a few hours" or "collect
until it's finished", right now, on top of whatever the schedule is doing.

The unavoidable part: the oldest days are disappearing while you collect. If you
want the deepest history, run `catchup.bat` overnight a couple of times early on
rather than leaving it entirely to the nightly window.

### "It collected way fewer sales the further back it went"

That was real, and it is fixed. eBay has no way to ask for one specific date, so
the collector has to page through everything sold since the day it wants. A day
three weeks back sits about 2,300 pages in — and the collector was allowed 600
before giving up. It kept whatever it had reached, called the day done, and
never went back.

Two changes. Older days are now approached from the *far* end of eBay's 90-day
window, so the oldest days are the **cheapest** to collect rather than the most
expensive. And a day that gets cut short is now recorded as unfinished, so it
gets collected again instead of being skipped forever.

`catchup.bat` now shows any days that came back too small, with option **3** to
re-do them. Sales already collected are kept, not duplicated — so re-doing a day
costs time and nothing else.

Three honest limits:

- **The PC has to be on and you signed in.** Not just powered — actually logged
  in, because the collector drives a real Chrome window holding your eBay
  session. If the PC is off at that time, the run happens when it next comes
  back, so a missed morning isn't a lost day.
- **The eBay session still expires** every few weeks. When it does, the run
  stops and says so in the log; double-click `login.bat` once and it resumes.
  That is the only thing that ever needs you.
- **The public dashboard is still a manual push.** GitHub Desktop: Commit, then
  Push. Automating that means storing a GitHub credential too, which is a
  bigger ask than it's worth for a once-a-day click.

**How did last night go?** Open the `logs` folder — one file per day, newest at
the bottom. Or run `schedule.bat` and read the top, which shows the last run's
result and the next one's time.

## Doing it by hand instead

Still works, any time, whether or not it's scheduled.

1. **Double-click `collect.bat`.**
   It gathers yesterday's sales on its own — no Chrome to close, no window to
   watch. Takes 5–10 minutes, deliberately slow so eBay doesn't mistake it for
   an attack. Leave it and come back. If you've run `connect-cloudflare.bat`, it
   uploads to your website at the end too.

   If it ever says **"Not signed in"**, the session expired: run `login.bat`
   once more.

2. **Open GitHub Desktop.** You'll see a list of changed files on the left.

3. Type anything in the **Summary** box (e.g. `new data`), click
   **Commit to main**, then click **Push origin** at the top.

4. A minute or two later your dashboard updates at
   **https://peytonfrankecode.github.io/NflCardDB/**

5. `d1-push.bat` uploads to Cloudflare on its own if you ever need it; and
   `d1-check.bat` prints what Cloudflare currently holds without uploading
   anything. Details in `CLOUDFLARE.md`.

---

## Card photos

Every sale keeps the picture from its eBay listing, and they show in the
dashboard table. Nothing is downloaded — the picture stays on eBay and the
database just remembers where it is, so 20,000 photos a day cost you no space.

**Double-click `photos.bat`** to see how many sales have one. It also fixes an
old wrinkle: sales collected early on kept eBay's tiny 140-pixel thumbnail, and
this rewrites those to the full-size picture without re-scraping anything.

One catch worth knowing: eBay deletes listing photos about 90 days after the
sale, and the picture disappears from your dashboard when they do. The prices
are yours forever; the photos are borrowed.

> First time only: turn the website on at **Settings → Pages** in your repo on
> github.com, and under "Source" choose **GitHub Actions**.

---

## If this PC is wiped, reset or replaced

Your sales are not on GitHub — the database is working data, not a project file,
so it is deliberately not committed. **They are on Cloudflare**, which is
unaffected by anything that happens to this computer.

To get them back on a fresh PC:

1. Clone the project again in GitHub Desktop. If it refuses, saying the folder
   must be empty, the old folder is still there — delete it, or type a different
   name in the **Local path** box.
2. `setup.bat`
3. **`restore.bat`** — paste your Cloudflare token and Account ID, and it
   downloads every sale you had uploaded back into a local database.
4. `login.bat` to sign in to eBay again.

What does not come back: the record of which run collected what, and anything
that was collected but never uploaded to Cloudflare. Neither affects your prices.

The lesson worth taking: **anything only on this PC is one accident from gone.**
Run `d1-push.bat` (or let the schedule do it) rather than letting collected days
pile up locally.

## "An Application Control policy has blocked this file"

Windows is refusing to load one of the browser engine's files. Nothing is broken
in this project and nothing needs reinstalling — this is a security setting on
the PC, and it usually appears after a Windows reset, because Smart App Control
is switched on by default on a fresh install.

**Try this first: move the project out of OneDrive.** OneDrive marks synced
files as having come from the internet, which is exactly what the policy blocks
— and it also stops OneDrive trying to sync several hundred megabytes of browser
files, which it should never have been doing.

### How to move it

Don't drag the folder. A Python `venv` has the old path written inside it, so a
moved copy is broken anyway. Cloning fresh is quicker and cleaner.

**1. Close everything** — any black collector windows, and GitHub Desktop.

**2. Save your data folder.** Open the current project folder and copy the
`data` folder to your Desktop. That's your sales database and your saved
Cloudflare details. (If you skip this, `restore.bat` can download the sales back
from Cloudflare later — but copying takes a second.)

**3. Clone to the new place.** Open GitHub Desktop → **File** → **Clone
repository** → **PeytonFrankeCode/NflCardDB**. Before clicking Clone, change
**Local path** so it reads `C:\NflCardDB`.

The only thing that matters is that the path does **not** contain the word
OneDrive. GitHub Desktop defaults to your Documents folder, which on this PC is
inside OneDrive, so you have to change it deliberately.

**4. Put your data back.** Copy the `data` folder from your Desktop into
`C:\NflCardDB`, replacing what's there.

**5. Double-click `setup.bat`** in `C:\NflCardDB`. This rebuilds the workspace
and re-downloads the browser engine — a few minutes.

**6. Double-click `login.bat`** and sign in to eBay again.

**7. Delete the old folder** in OneDrive once collecting works from the new one.
Leaving it costs you OneDrive storage and invites confusion about which copy is
real.

`setup.bat` now warns you if it's run from inside OneDrive, so this can't be
stumbled into twice.

**If that does not fix it:** Windows Security → App & browser control → Smart App
Control settings → Off.

Know the cost before you do it: **once Smart App Control is off it cannot be
turned back on without reinstalling Windows.** That is Microsoft's design. Try
the OneDrive move first.

**If neither is possible** — a work laptop with policies you do not control —
collecting still works by hand. `tools/grabber.html` needs no browser engine at
all: it reads listings off a page you already have open.

## Grouping sales of the same card together

For a price chart to mean anything, every sale of one card has to land in one
place. That depends on reading the player's name off the title correctly, and
titles fight back:

```
2024 PANINI DONRUSS BOMB SQUAD #29 JAYDEN DANIELS ROOKIE RC PSA 9
2024 Jayden Daniels Donruss Optic Preview Emoji Prizm Rookie PSA 9 #389
```

"Bomb Squad" and "Preview" are the insert, not the player — but nothing about
the words says so, and no rule about position works either: one of those wants
the last two words and the other the first two. Read wrong, `Bomb Squad Jayden
Daniels` and `Jayden Daniels Preview` become different players, and when a title
has no card number the name *is* the identity, so one card splits several ways.

**Double-click `names.bat`.** It works out which phrases are players from your
own collected titles, using breadth rather than repetition: a player appears in
Prizm and Mosaic and Donruss, across years, while an insert lives in one set of
one year. Then it re-reads every title you have.

It prints your own before-and-after, so the improvement is measured rather than
claimed. The shape of it:

```
  sales matched to a card          17,126 ->    19,880   +2,754  better
  distinct cards                   14,906 ->    11,240   -3,666  better
  cards seen more than once         1,248 ->     3,910   +2,662  better
```

(Those figures are an illustration. Yours will differ — the left column is the
real starting point from your last audit.)

Fewer distinct cards holding more sales each is the whole point: sales that were
split apart are now one price history.

Worth re-running every few weeks. The list is learned from your data, not
shipped, so it improves as you collect and it never goes stale on rookies —
which a hand-written list does every draft.

## Reading the card off its photo (started, not switched on)

The title is what a seller typed. The card is what it is. When a title says
`HUGE FOOTBALL CARD LOT MUST SEE!!!` there is nothing to read — but the photo
often shows a graded slab, and a slab has a label printed on it *by the grader*:

```
2024 PANINI PRIZM
JAYDEN DANIELS
#316 ROOKIE                          MINT 9      94612385
```

That is a title written by someone holding the card. Reading it gives a full
identity where the seller's words gave none.

**Double-click `read-photos.bat`.** It offers the one-time engine download
(~200MB), reads 50 photos, and reports how often the photo and the title agree.
**Nothing is saved** — this measures whether the idea works before it goes
anywhere near your data.

### What's actually built

Working: fetching the photo at full size, reading the label, and merging the two
readings. The merge has rules worth knowing:

- **The photo wins on the player's name, and only there.** The label was printed
  by a grader looking at the card; the name is the one field sellers pad with
  insert names and hype.
- **Everywhere else the title wins**, because it carries parallels, serial
  numbers and autographs that no slab label mentions. The photo only fills
  blanks.
- **Disagreements are reported, never hidden.** Two independent readings that
  contradict each other mean one is wrong — that's a free accuracy signal, and
  the same reasoning behind the "errors the data admits to" figure in
  `accuracy.bat`.

It leans on `names.bat`: text recognition returns `JAYDENDANIELS` with the
spaces gone, and the learned roster is what splits it back into a name. Without
a roster it reads the year, set and number but not the player.

### What is honestly untested

The engine was tested against slab labels rendered on this end, not against real
eBay photos. Real ones are angled, glared, and often show the slab small in
frame. **I do not know the hit rate yet, and I'm not going to guess at one** —
that's what `read-photos.bat` is for. Run it and send me the three numbers it
prints.

Two limits that won't go away: an **ungraded** card has no label at all, so this
only ever helps graded sales; and eBay deletes listing photos after about 90
days, so it only works on recent ones.

## Cards that share a number

An **insert set** restarts its numbering at 1. So in Panini Phoenix, "Contours
#8", "Phoenician #8", "Genies #8" and "Archetype #8" are four completely
different cards — and if you only look at the number, they're one card with
fourteen sales and four different players in it. That was real, in your data.

So the insert name is part of a card's identity. The problem is knowing the
names: every product ships a dozen inserts, and next year ships different ones.
A hand-written list is out of date the day it's written.

**Double-click `inserts.bat`.** It works the names out from your own listings,
using the mirror image of the roster trick: **a player turns up across many sets
and years; an insert lives in one product, standing next to lots of different
players.** That second half is what matters — a rookie who only appears in one
product looks exactly like an insert by breadth alone, and what separates them
is that nobody else's name is beside theirs.

Run `names.bat` first. Working out which phrases are inserts needs to know which
ones are players.

### It asks you to check the list, and that's not a formality

The file opens automatically, with the evidence beside each name:

```
Moonstruck  # 142 listings, 2025 Donruss Optic, 37 players
```

**Delete any line that isn't really the name of an insert set.** The risk here
runs one way and it's worth understanding: a *missed* insert leaves cards merged,
which is where you already are. A *wrong* entry **splits** a card in two —
between sellers who typed that word and sellers who didn't — which breaks cards
that currently work. Missing one costs you nothing new; adding a bad one costs
you a card.

Then answer **y** and it re-reads every title.

## Seeing one card over time

All of the above — the roster, the insert names, the set names — exists for one
reason: so that twenty people selling the same card produce **one card with
twenty prices** instead of twenty unrelated rows. Twenty prices is a market.
Twenty rows is a list.

**Double-click `cards.bat`.** It re-reads every title with the current word
lists, regroups your sales, refreshes the website files, and then prints your
cards busiest first:

```
SALES     MEDIAN              RANGE    TREND  CARD
   26      54.09    24.72 - 176.86    +14.5%  2024 Prizm Caleb Williams #301 Silver Prizm
   14      11.98     8.30 - 21.52     -14.7%  2023 Select Dragonscale Jayden Daniels #12 /81
```

The website gets the same thing with a chart per card: a searchable list on the
left, and the card's price history on the right.

Three things about that report are deliberate:

**Cards that sold once are left out.** One price is not a history, and drawing a
trend line through a single point would be inventing one.

**The trend compares the newer half of a card's sales against the older half**,
not the newest sale against the oldest. One unusual sale at either end would
otherwise become the entire trend. Below four sales no trend is shown at all.

**A graded copy and a raw copy stay the same card.** They are the same cardboard
sold in two different markets, so the website lets you switch between grades on
the chart — but splitting them into separate cards would hide the fact that they
are one card at all, which is the thing you came for.

## Regrouping everything (the one that makes it usable)

**Save the checklist export into `data\checklists\`, then double-click
`regroup.bat`.** That is the whole thing — no path to type and nothing to drag.

It looks for the file in `data\checklists\`, then the project folder, then
your Downloads, newest first, so leaving it wherever your browser put it also
works. If it finds nothing it reads thecardhuddle.com instead — which works,
but the site serves a *summary*: 11,797 rows with 4 insert names, against the
export's 2,012,671 rows with 4,355 inserts and 3,970 parallels. Use the export.

It does four things, in an order that matters:

1. **Loads the checklist** — the list of cards that actually exist.
2. **Re-reads every title** against it.
3. **Refreshes the dashboard files.**
4. **Uploads everything to Cloudflare**, which is what your other site reads.

It is one script rather than four on purpose, because getting the order wrong
fails *silently*. Regrouping before the checklist is loaded just redoes the
grouping you already had — it looks like the whole thing worked and changes
nothing. Uploading before regrouping sends the old keys.

About ten minutes. Nothing is deleted at any point: every step rewrites, so
running it twice is safe, and if the upload fails your sales are still
regrouped on this PC — `d1-push.bat` on its own will finish the job.

### What the checklist actually changes

Step 2 is the one that alters your data. The parser keeps reading only what it
reads well — year, set, card number, player — and then the card is **looked up**
and the insert name is read off the checklist rather than hunted for in the
title. So:

```
"2025 Topps Chrome Football Patrick Mahomes II #KAI-2 Kaiju"  ->  Kaiju
"2024 Panini Prizm - Prizmania Drake Maye #3 (RC)"            ->  Prizmania
```

Those are separate cards from the base card, and until now they were merged
into it. A listing the checklist cannot place is left exactly as it was, so
this cannot damage cards it knows nothing about.

**The obvious version of this was wrong**, which is worth knowing because it
looks so reasonable: teaching the parser the checklist's 4,355 insert names
made things *worse*, from 435 correctly-identified cards down to 377 over 1,500
real listings. An insert name belongs to one product — "Legends" is a section
heading somewhere else — so a global list of them poisons every title that
happens to contain the word.

## How accurate is it?

**Double-click `accuracy.bat`.** It matches up any sales collected before this
feature existed, then reports two different things — and the difference matters.

The report starts with a line like `read by parser  title/4`. That is which
version of the code actually produced these numbers. If it names an older
version than the one you were told to expect, **the report is describing old
code** — everything below it will look perfectly normal and mean nothing. This
happened twice before the line existed, which is why it is there.

`accuracy.bat` and `names.bat` now check GitHub before they run and offer to
update, so you should not have to think about it. If the check can't find git
it quietly skips, and the version line is still your backstop.

**What it can work out on its own** — how many sales got matched to a card at
all, how confident the reading was, and which groups contradict themselves (one
card whose sales name two different players is wrong without anyone checking).
That last figure is a *floor*: everything it finds is definitely wrong, but a
group can be wrong and still look consistent.

**A real percentage** needs you. Choose option **1** and it pulls 100 random
sales into a page that opens in your browser. Each one shows the card's photo
beside the seller's title and what we read it as. Press **Y** if that's the
right card, **N** if it isn't, **S** if you can't tell — about a second each.

It remembers your place if you close it, and works out the percentage itself
when you reach the end. Nothing to save, nothing to score afterwards:

```
  ACCURACY: 91.0%  (somewhere between 85.4% and 96.6%)
  91 right, 9 wrong, out of 100 judged
```

That range is the honest part. 100 rows gets you within about 10 points; 400
gets you within 5. Anyone quoting "91%" off 100 rows is quoting more precision
than they bought.

(The same sample is also written as `review-sample.csv`, and option **2** scores
that file if you'd rather mark it in a spreadsheet. The browser page exists
because a spreadsheet turned out not to be a safe assumption — Windows offered
Notepad, and a hundred rows of CSV hand-edited in Notepad is a corrupted sample
waiting to happen.)

**When a title is too vague to read**, nothing is invented — the sale is stored
with its price and title, but gets no card identity, so it never joins a group
and never pollutes a price history. Titles like "HUGE FOOTBALL CARD LOT MUST
SEE" fall in here. `audit` counts them under "no key (too unclear)".

## Things that will happen eventually

**"eBay showed a robot check partway through."** Whatever it collected before
that point is saved — nothing is lost. Running `collect.bat` again later picks up
where it left off.

**If it happens every single time, double-click `bisect.bat`.** It also tells
the two cases apart: a passing interruption versus a genuine block. It asks eBay for
the same search seven times, adding one setting each time, and reports the first
one refused. That turns "eBay is blocking me" into "eBay objects to *this*",
which is something that can actually be changed. It writes `bisect-report.txt` —
send that to Claude.

**You want more than yesterday.** That is what `catchup.bat` is for, and the
daily schedule already works backwards on its own. eBay only keeps about 90
days, so the backlog shrinks whether or not you collect it.

**You closed the window too early.** No harm done. It saves as it goes, in
batches, so you keep everything collected up to that point.

**Nothing shows on the website.** Check you did step 3 (Commit *and* Push) — the
data lives on your PC until you push it.
