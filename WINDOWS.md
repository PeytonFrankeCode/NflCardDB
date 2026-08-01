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

## Every time you want data

1. **Double-click `collect.bat`.**
   It gathers yesterday's sales. Takes 5–10 minutes — it goes slowly on purpose,
   so eBay doesn't mistake it for an attack. Leave the window open.

2. **Open GitHub Desktop.** You'll see a list of changed files on the left.

3. Type anything in the **Summary** box (e.g. `new data`), click
   **Commit to main**, then click **Push origin** at the top.

4. A minute or two later your dashboard updates at
   **https://peytonfrankecode.github.io/NflCardDB/**

> First time only: turn the website on at **Settings → Pages** in your repo on
> github.com, and under "Source" choose **GitHub Actions**.

---

## Things that will happen eventually

**"eBay showed a robot check partway through."** Whatever it collected before
that point is saved — nothing is lost. Running `collect.bat` again later picks up
where it left off. If it happens every time, tell Claude and the collector can be
slowed down.

**You want more than yesterday.** eBay only keeps sold listings for about 90
days, so older days are gone for good once they age out. If you want history,
collect it sooner rather than later — ask Claude to add a "catch me up on the
last 30 days" button.

**You closed the window too early.** No harm done. It saves as it goes, in
batches, so you keep everything collected up to that point.

**Nothing shows on the website.** Check you did step 3 (Commit *and* Push) — the
data lives on your PC until you push it.
