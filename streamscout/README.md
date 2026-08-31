# StreamScout — How-To (for Jenna)

StreamScout finds the exact **watch/play identifier** for a movie or TV series
across the major streaming platforms and drops the results into a spreadsheet on
your Desktop. It turns the tedious manual hunt (opening each episode to copy its
URL slug) into four quick questions.

Every run writes one consistent CSV:

```
SHOW · URL · PRODUCTION · PLATFORM · SEASON
```

- **URL** = the identifier segment we care about (e.g. `watch/81268514` for
  Netflix, a UUID for Hulu/Peacock/Max/Disney, `starz.com/us/en/play/23688` for
  Starz).
- **SEASON** = `Season 1`, `Season 2`, … (blank for movies and Apple TV, which
  keys a whole series to one id).

---

## 0. First — put the tool on `main` (do this once)

The tool currently lives in **Pull Request #75**. Until that PR is merged, the
`streamscout/` folder isn't on `main` yet, so the setup steps below can't find
it. Merging is a ~30-second click, and you (as the repo owner) can do it:

1. Open the PR: **https://github.com/crosswalknyc/behavioralgraph/pull/75**
2. Click **Merge pull request** → **Confirm merge**.
   - You may see a note that a check named **`validate` didn't run** — that check
     only applies to website (`index.html`) edits, *not* this Python tool, so
     it's safe to merge past it. As the owner you'll have a **Merge** button
     regardless; use it.
3. That's it — the entire tool, with all the latest fixes, is now on `main`.

You only do this once. After it's merged, everything below "just works."

---

## 1. One-time setup

You only do this once per computer.

**a) Get the code.** It lives in the `behavioralgraph` repo under the
`streamscout/` folder. Pull the latest `main`:

```bash
git checkout main
git pull
cd streamscout
```

**b) Python 3.** Check you have it:

```bash
python3 --version
```

**c) Install the two libraries.** Only the login-based platforms need these; the
no-login platforms work with nothing extra, but it's easiest to just install
both up front:

```bash
python3 -m pip install playwright clickhouse-connect
python3 -m playwright install firefox
```

- `playwright` + Firefox → needed for **Netflix, HBO MAX, Disney+** (they open a
  real browser window to search while logged in).
- `clickhouse-connect` → only used by Peacock's rare database fallback. Skip it
  if you never need that (see Troubleshooting).

**d) Logins (`.env.local`).** Netflix, HBO MAX, and Disney+ need credentials.
They're read from a file named `.env.local` at the **root of the repo** (the
folder *above* `streamscout/`). Create it if it isn't there, with these lines:

```
NETFLIX_EMAIL=your_netflix_login
NETFLIX_PASSWORD=your_netflix_password
MAX_EMAIL=your_max_login
MAX_PASSWORD=your_max_password
DISNEY_EMAIL=your_disney_login
DISNEY_PASSWORD=your_disney_password
```

> `.env.local` is **gitignored** — it never gets committed or shared through the
> repo. Keep your copy local. (Ask Jessie for the shared logins if you don't have
> them.) The first time a browser platform logs in, it may ask for a one-time
> device verification; after that it remembers the session.

---

## 2. Running it

From the repo root **or** from inside the `streamscout/` folder:

```bash
python3 streamscout/streamscout.py
```

It asks four questions:

1. **Movie or Series?** → type `m` or `s`
2. **What title?** → e.g. `The Bear`
3. **Which season(s)?** *(series only)* → `1` · `1,3,5` · `1-4` · `all`
4. **Which platform?** → pick the number or name from the list

Then it fetches everything and tells you where the CSV landed, e.g.:

```
Found 63 result(s) for 'Power' on Starz.
CSV written to: /Users/you/Desktop/lookup_starz_series_power_20260828-153027.csv
```

The CSV appears on your **Desktop**.

---

## 3. Platform cheat-sheet

| Platform     | Login needed?            | Notes                                             |
|--------------|--------------------------|---------------------------------------------------|
| Peacock      | No                       | Reads its public site (DB fallback is optional)   |
| Hulu         | No                       | —                                                 |
| Apple TV     | No                       | One id for the whole series (no season split)     |
| Paramount+   | No                       | —                                                 |
| Starz        | No                       | URL column is `starz.com/us/en/play/<id>`         |
| Netflix      | **Yes** (opens Firefox)  | Uses `.env.local`; a window will open             |
| HBO MAX      | **Yes** (opens Firefox)  | Uses `.env.local`; needs a visible window         |
| Disney+      | Situational              | Tries no-login first; opens Firefox only if unsure|

When a browser platform runs, **a Firefox window will pop up and drive itself** —
that's expected. Don't click around in it; just let it finish.

---

## 4. Handy tips

- **Paste a URL instead of searching.** If a title is obscure and the search
  can't find it, the tool offers to let you paste a link from that platform
  (an episode page, show page, or play URL). It'll extract everything from there.
- **Seasons are flexible:** `all` grabs every season; `1-3` is a range; `1,4,6`
  is a pick-list.
- **Production column** is filled automatically (studio/house), with a few
  hand-pinned overrides for accuracy.
- **Low-confidence flag:** if the tool isn't sure about a result, it prints a
  notice and records it locally for review. It does **not** send anything
  anywhere yet — that's a future hookup. (Nothing to action; just so you know
  what the "flagged for Jenna" message means.)

---

## 5. Troubleshooting

- **"Missing NETFLIX_EMAIL / …"** → your `.env.local` is missing or in the wrong
  place. It goes at the **repo root**, not inside `streamscout/`.
- **No Firefox window / browser error** → run
  `python3 -m playwright install firefox` again.
- **A Netflix/Disney/MAX login gets stuck** → close the window and re-run; the
  session is remembered, so the second try usually sails through. If it asks for
  a device verification code, complete it once.
- **Peacock says it's "falling back to clickstream" and errors** → that database
  fallback needs the company network/credentials and `clickhouse-connect`. It's
  rarely needed; Peacock's normal path works without it. If you hit it, paste a
  `peacocktv.com` URL when prompted, or ask Jessie.
- **Nothing found** → double-check spelling, or try the paste-a-URL fallback.

---

## 6. What's in the folder

`streamscout/` is self-contained — keep these files together:

- `streamscout.py` — the tool you run
- `production_tags.py` — fills the PRODUCTION column
- `*_identifier.py` — one resolver per platform (Hulu, Netflix, Peacock, Apple
  TV, Paramount+, HBO MAX, Disney+, Starz)

That's it — four questions, one spreadsheet. Happy scouting. 🛰️
