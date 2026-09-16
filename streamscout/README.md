# StreamScout — How-To (for Jenna)

StreamScout turns "find the watch id for this title" into **four quick
questions**. You give it a movie, series, podcast, or audiobook; it finds that
title's exact watch/play/listen identifier(s) on the platform you pick, and
drops them into a spreadsheet on your Desktop.

It works for **both movies and series across all 20 platforms** — streaming
video, podcasts *and* audiobooks.

Every run writes one consistent CSV:

```
SHOW · URL · PRODUCTION · PLATFORM · SEASON
```

- **URL** = the identifier we care about (a UUID, a `watch/<id>`, an
  `item/<CODE>`, an Amazon `detail/<ASIN>`, a full YouTube `watch?v=…` link, a
  podcast episode link, an Audible `/pd/…` listen link, etc. — depends on the
  platform).
- **SEASON** = `Season 1`, `Season 2`, … (blank for movies, podcasts,
  audiobooks, and for Apple TV+, BritBox and YouTube, which don't split their
  ids by season).

> 💡 This how-to is all you need to run the tool. There's also an optional
> flashy one-page grid, `StreamScout-Platforms.xlsx`, to show the wider team all
> 20 platforms at a glance — nice to have, not needed to run anything.

---

## Step 0 — Get the tool (it's already live on `main`)

Good news: **the whole tool is already merged to `main`.** Nothing is stuck on
anyone's laptop. To get it, just pull:

```bash
git checkout main
git pull
```

That's it — `streamscout/` is right there with all 20 platforms (StreamScout
first shipped in PR #75; Audible, #20, landed in PR #163).

<details>
<summary><b>The "PR dance" — how future updates ship to <code>main</code></b> (for when we add a platform or a fix)</summary>

`main` is a **protected branch** — you can't push straight to it. Every change
lands through a Pull Request:

1. **Branch:** `git checkout main && git pull`, then
   `git checkout -b my-change`.
2. **Commit + push:** `git add … && git commit -m "…"`, then
   `git push -u origin my-change`.
3. **Open a PR into `main`:** `gh pr create --base main --head my-change`
   (or the green "Compare & pull request" button on GitHub).
4. **Wait for the `validate` check to go green**, then **Merge** (squash).
   Merging is what auto-deploys prod + dev.

> ⚠️ **The one gotcha we hit with StreamScout:** the required `validate` check
> is *path-filtered* — it only runs when someone edits the website
> (`templates/index.html`) or its validator. A tool-only PR (like ours) never
> triggers it, so GitHub leaves the PR **"blocked / expected"** and even an
> admin **Merge** is refused. The repo's built-in fix (documented right inside
> `scripts/validate_index_html.py`) is to add a one-line **"trigger touch"**
> comment to that validator file in your PR. That makes `validate` actually run
> against the current, valid `index.html`, it goes green in ~15s, and the PR
> merges normally. That's exactly how PR #75 and PR #163 got in.

</details>

---

## Step 1 — One-time setup (once per computer)

**a) You already have the code** from Step 0 (`git pull` on `main`).

**b) Check Python 3:**

```bash
python3 --version
```

**c) Install the browser libraries** (only the login / browser platforms need
them, but it's easiest to install everything now):

```bash
python3 -m pip install playwright clickhouse-connect
python3 -m playwright install firefox chromium
```

*(Netflix / HBO Max / Disney+ drive **Firefox**; Amazon Podcasts, SiriusXM and
Audible drive **Chromium** — so install both.)*

**d) Credentials — only 5 of the 20 platforms need anything.** Put them in a
file named `.env.local` at the **repo root** (the folder *above* `streamscout/`):

```
# Streaming video logins (a browser window opens and drives itself)
NETFLIX_EMAIL=your_netflix_login
NETFLIX_PASSWORD=your_netflix_password
MAX_EMAIL=your_max_login
MAX_PASSWORD=your_max_password
DISNEY_EMAIL=your_disney_login
DISNEY_PASSWORD=your_disney_password

# Spotify — a FREE developer API key (not a normal login)
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret

# SiriusXM — a normal login, only needed for title SEARCH (not for pasted links)
SIRIUSXM_EMAIL=your_siriusxm_login
SIRIUSXM_PASSWORD=your_siriusxm_password
```

> `.env.local` is **gitignored** — it never gets committed or shared. Ask Jessie
> for the shared logins / keys. **The other 15 platforms need nothing.**

---

## Step 2 — Run it

```bash
python3 streamscout/streamscout.py
```

It asks four questions:

1. **Movie or Series?** → `m` or `s`  *(for a podcast, pick **Series** to get
   every episode, or **Movie** to grab a single one; for an audiobook series,
   **Series** grabs every matching title)*
2. **What title?** → e.g. `The Bear`, or `Baby, This Is Keke Palmer`
3. **Which season(s)?** *(series only)* → `1` · `1,3,5` · `1-4` · `all`
4. **Which platform?** → pick the number or name from the list

Then it fetches everything and tells you where the CSV landed:

```
ok  189 episode(s) -> /Users/you/Desktop/lookup_siriusxm_series_baby-this-is-keke-palmer_20260914-164942.csv
```

The CSV appears on your **Desktop**. That's the whole job.

---

## The platforms (quick version)

**20 platforms — movies + series on all of them.** Only **5** need anything:

- 🔓 **No login (15):** Peacock, Hulu, Apple TV+, Paramount Plus, Starz,
  Hallmark Plus, Amazon, MGM Plus, BritBox, YouTube, Apple Podcasts, iHeart,
  Pandora, Amazon Podcasts, **Audible**
- 🔐 **Browser login — a Firefox window opens and drives itself (2):**
  Netflix, HBO Max
- 🔑 **Free API key (1):** Spotify (Client ID + Secret in `.env.local`)
- 🔓 / 🔐 **Situational (2):**
  - **Disney+** — tries no-login first, only opens Firefox for tricky titles
  - **SiriusXM** — pasting a show link needs **no login**; **title search** needs
    a one-time SiriusXM login (see the SiriusXM note below)

When a browser platform runs, **let the Firefox/Chromium window do its thing** —
don't click around in it.

*(The flashy at-a-glance grid of all 20 platforms lives in the shared
`StreamScout-Platforms.xlsx` if you ever want it.)*

---

## Handy tips

- **Can't find a title?** The tool offers to let you **paste a link** from that
  platform (an episode, show, play, or Audible/Amazon URL) and extracts
  everything from there.
- **Messy titles are OK.** Extra words, wrong subtitle, typos, wrong casing, or a
  stray year still resolve to the right title — handy for client-typed queries.
- **Seasons are flexible:** `all` = every season, `1-3` = a range, `1,4,6` = a
  pick-list. (MGM Plus returns one shell per season; Apple TV+ and BritBox use a
  single shell for the whole series, so their SEASON is blank.)
- **Podcasts (Spotify, Apple Podcasts, iHeart, Pandora, Amazon Podcasts,
  SiriusXM):** pick **Series** and you get **one row per episode** with that
  platform's full episode URL. (Apple Podcasts' public feed caps at ~200
  episodes and warns if a show is longer.)
- **YouTube** treats a show as its **channel** and returns **one row per
  episode** with the full `watch?v=…` link — every long-form video on the
  channel's Videos tab (Shorts are left out). Paste a channel, `@handle`, or
  `playlist` link if the title search doesn't land it. Heads-up: clip-heavy
  channels (e.g. *Wild 'N Out*) return **a lot** of rows — that's every clip as
  it appears in clickstream, by design.
- **Amazon** captures *every* way a title shows up in clickstream (all offer
  ASINs + the GTI in both forms, shells + episodes, **and every film edition** —
  theatrical / ad-supported / Director's Cut). It also covers Amazon
  **Channels** — Lionsgate+, Starz, etc. sold through Prime Video.
- **PRODUCTION** (studio) is filled in automatically.

---

## 📚 Audible — two links per title (direct + via-Amazon)

Audiobooks live in **two** places, and StreamScout grabs **both** — so every
title gets **two rows**:

- **Audible** → the direct listen page, `audible.com/pd/<slug>/<ASIN>`
- **Amazon** → the parallel *Audible Audio Edition*, `amazon.com/dp/<ASIN>`
  (a **different** ASIN from the Audible one — Amazon keeps its own)

Each row is labeled in the **PLATFORM** column (`Audible` vs `Amazon`), so you
can see both routes at a glance. It's a self-driving **Chromium** browse of the
public US Audible + Amazon pages — **no login, no key**.

- **Movie** → the single best-matching audiobook (+ its Amazon twin).
- **Series** → every audiobook that matches your title query (+ each twin).
- **Paste a link** → an `audible.com` `/pd` or `/series` link, *or* an
  `amazon.com` `/dp` link — the tool recovers the other side automatically.

> Heads-up on grouping: title search matches on the **words in the title**. A
> book whose title shares no words with your query (e.g. *Bereavement Committee*
> under a *"Lady Miss Jacqueline"* search) won't group in — search it by its own
> name, or paste the Audible **series** link to pull the whole set.

---

## ⭐ SiriusXM — the one with a small first-run wrinkle

SiriusXM returns **one row per episode** via a self-driving **Chromium** window,
exactly like the other podcast platforms. Two ways in:

- **Paste a SiriusXM show link** → **no login needed**, ever.
- **Search by title** → needs a **one-time SiriusXM login** (just like Netflix).

The first title search on a **new computer** trips SiriusXM's one-time **device
check**: it texts/emails a 6-digit code to the account. Complete that code
**once** (run the tool and let the browser window prompt you, or ask Jessie to
do it), and the login is **remembered** in a saved profile from then on — every
future run is silent and headless. Jessie has already primed this on the shared
machine, so title search there just works.

> In short: SiriusXM title search = Netflix-style. One code the first time on a
> fresh laptop, then it's automatic. Pasted links never need it.

---

## Troubleshooting

- **"Missing NETFLIX_EMAIL / SPOTIFY_CLIENT_ID / SIRIUSXM_EMAIL / …"** →
  `.env.local` is missing that key or is misplaced. It goes at the **repo root**,
  not inside `streamscout/`.
- **No browser window / browser error** → run
  `python3 -m playwright install firefox chromium` again.
- **A login gets stuck** → close the window and re-run; the session is
  remembered, so the second try usually sails through.
- **SiriusXM says "asked for a one-time verification code"** → that's the
  first-run device check. Complete the code once (see the SiriusXM note above),
  then re-run — or paste a SiriusXM show link to skip login entirely.
- **Spotify "Not found" right after adding the key** → the free key sometimes
  needs a minute to warm up; just re-run. (The tool already retries a few times.)
- **Audible / Amazon "Nothing found"** → try the exact title, or paste an
  `audible.com/pd` or `amazon.com/dp` link — the tool fills in the twin.
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
  Apple TV+, Paramount Plus, HBO Max, Disney+, Starz, Hallmark Plus, Amazon,
  MGM Plus, BritBox, YouTube, Spotify, Apple Podcasts, iHeart, Pandora,
  Amazon Podcasts, SiriusXM, **Audible**)

That's it — four questions, one spreadsheet. Happy scouting. 🛰️
