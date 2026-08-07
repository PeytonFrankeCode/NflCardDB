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

## Things that will happen eventually

**"eBay showed a robot check partway through."** Whatever it collected before
that point is saved — nothing is lost. Running `collect.bat` again later picks up
where it left off. If it happens every time, tell Claude and the collector can be
slowed down.

**You want more than yesterday.** That is what `catchup.bat` is for, and the
daily schedule already works backwards on its own. eBay only keeps about 90
days, so the backlog shrinks whether or not you collect it.

**You closed the window too early.** No harm done. It saves as it goes, in
batches, so you keep everything collected up to that point.

**Nothing shows on the website.** Check you did step 3 (Commit *and* Push) — the
data lives on your PC until you push it.
