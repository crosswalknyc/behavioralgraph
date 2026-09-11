# StreamScout — How-To (for Jenna)

StreamScout turns "find the watch id for this title" into **four quick
questions**. You give it a movie or series, it finds that title's exact
watch/play identifier(s) on the streaming platform you pick, and drops them into
a spreadsheet on your Desktop.

It works for **both movies and series on all 10 platforms.**

Every run writes one consistent CSV:

```
SHOW · URL · PRODUCTION · PLATFORM · SEASON
```

- **URL** = the identifier we care about (a UUID, a `watch/<id>`, an
  `item/<CODE>`, an Amazon `detail/<ASIN>`, etc. — depends on the platform).
- **SEASON** = `Season 1`, `Season 2`, … (blank for movies, and for Apple TV+
  which keys a whole series to one id).

> 💡 This how-to is all you need to run the tool. (There's also an optional
> one-page grid, `StreamScout-Platforms.xlsx`, shared with the wider team if you
> ever want an at-a-glance platform reference — but you don't need it here.)

---

## Step 0 — Put the tool on `main` (do this once, ~30 seconds)

The tool lives in **Pull Request #75**. Until it's merged, the `streamscout/`
folder isn't on `main` yet — so do this first:

1. Open **https://github.com/crosswalknyc/behavioralgraph/pull/75**
2. Click **Merge pull request** → **Confirm merge**.
   - If you see "the check **`validate`** didn't run" — that check only applies
     to website (`index.html`) edits, **not** this Python tool, so it's safe to
     merge past. As the owner you'll have a **Merge** button regardless.
3. Done — the whole tool, with every latest fix, is now on `main`.

You only ever do this once.

---

## Step 1 — One-time setup (once per computer)

**a) Get the code:**

```bash
git checkout main
git pull
cd streamscout
```

**b) Check Python 3:**

```bash
python3 --version
```

**c) Install the two libraries** (only the login platforms need them, but it's
easiest to install both now):

```bash
python3 -m pip install playwright clickhouse-connect
python3 -m playwright install firefox
```

**d) Logins — only for Netflix, HBO Max, Disney+.** Put them in a file named
`.env.local` at the **repo root** (the folder *above* `streamscout/`):

```
NETFLIX_EMAIL=your_netflix_login
NETFLIX_PASSWORD=your_netflix_password
MAX_EMAIL=your_max_login
MAX_PASSWORD=your_max_password
DISNEY_EMAIL=your_disney_login
DISNEY_PASSWORD=your_disney_password
```

> `.env.local` is **gitignored** — it never gets committed or shared. Ask Jessie
> for the shared logins if you need them. The other 7 platforms need no login.

---

## Step 2 — Run it

```bash
python3 streamscout/streamscout.py
```

It asks four questions:

1. **Movie or Series?** → `m` or `s`
2. **What title?** → e.g. `The Bear`
3. **Which season(s)?** *(series only)* → `1` · `1,3,5` · `1-4` · `all`
4. **Which platform?** → pick the number or name from the list

Then it fetches everything and tells you where the CSV landed:

```
Found 63 result(s) for 'Power' on Starz.
CSV written to: /Users/you/Desktop/lookup_starz_series_power_20260828-153027.csv
```

The CSV appears on your **Desktop**. That's the whole job.

---

## The platforms (quick version)

**10 platforms, movies + series on all of them.** Only **two** need a login:

- 🔓 **No login (7):** Peacock, Hulu, Apple TV+, Paramount+, Starz,
  Hallmark Plus, Amazon
- 🔐 **Login — a Firefox window opens and drives itself (2):** Netflix, HBO Max
- 🔓/🔐 **Disney+:** tries no-login first, only opens Firefox for tricky titles

When a browser platform runs, **let the Firefox window do its thing** — don't
click around in it.

*(That's everything you need. An optional at-a-glance grid of all 10 platforms
lives in the shared `StreamScout-Platforms.xlsx` if you ever want it.)*

---

## Handy tips

- **Can't find a title?** The tool offers to let you **paste a link** from that
  platform (an episode, show, or play URL) and extracts everything from there.
- **Seasons are flexible:** `all` = every season, `1-3` = a range, `1,4,6` = a
  pick-list.
- **Amazon** captures *every* way a title shows up in clickstream (all offer
  ASINs + the GTI in both forms, shells + episodes). It also covers Amazon
  **Channels** — Lionsgate+, Starz, etc. sold through Prime Video.
- **PRODUCTION** (studio) is filled in automatically.

---

## Troubleshooting

- **"Missing NETFLIX_EMAIL / …"** → `.env.local` is missing or misplaced. It goes
  at the **repo root**, not inside `streamscout/`.
- **No Firefox window / browser error** → run
  `python3 -m playwright install firefox` again.
- **A login gets stuck** → close the window and re-run; the session is
  remembered, so the second try usually sails through. If it asks for a one-time
  device code, complete it once.
- **Nothing found** → check spelling, or use the paste-a-URL fallback.
- **Peacock mentions a "clickstream" database fallback and errors** → that path
  needs the company network + `clickhouse-connect` and is rarely needed. Paste a
  `peacocktv.com` URL when prompted, or ask Jessie.

---

## What's in the folder

`streamscout/` is self-contained — keep these files together:

- `streamscout.py` — the tool you run
- `production_tags.py` — fills the PRODUCTION column
- `*_identifier.py` — one resolver per platform (Peacock, Hulu, Netflix,
  Apple TV+, Paramount+, HBO Max, Disney+, Starz, Hallmark Plus, Amazon)

That's it — four questions, one spreadsheet. Happy scouting. 🛰️
