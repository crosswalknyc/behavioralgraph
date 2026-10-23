# StreamScout → Promy: Explicit How-To (for Jenna)

This is the **step-by-step, copy-paste** guide for wiring StreamScout into Promy.
It covers the one function Promy calls, every parameter, the platform groups,
and the two **new YouTube client choices** (full-vs-clips + the fair-survey cap).

> **What StreamScout does:** you give it a title (movie, series, podcast,
> audiobook, book, or game); it returns the platform-specific **watch / listen /
> play identifiers**, one row per asset, in a fixed schema:
>
> `SHOW · URL · PRODUCTION · PLATFORM · SEASON`

---

## 1. The one entry point Promy uses

Everything goes through **`streamscout_query()`** (non-interactive — no prompts,
no browser babysitting). Import it and call it:

```python
from streamscout_query import streamscout_query

rows = streamscout_query(
    "The Daily",            # title (required)
    platforms="podcasts",   # a platform key, a list, or a group name
    kind="series",          # "series" (default) or "movie"
    seasons=None,           # None = all; or [1,2] for video series
    url=None,               # optional pasted product/episode link hint
    show=None,              # override SHOW on every row (franchise roll-up)
    include_clips=None,     # YouTube only — see §4
    limit=None,             # YouTube only — fair-survey cap, see §4
    strict=False,           # explicit-platform scope guard — see §5
    notify=False,           # with strict: email Crosswalk on a scope miss — §5
)
# -> list[dict], each: {"SHOW","URL","PRODUCTION","PLATFORM","SEASON"}
```

**CLI equivalent** (handy for spot checks):

```bash
python3 streamscout_query.py --title "The Daily" --platforms podcasts --csv
python3 streamscout_query.py --title "Rene Vaca" --platforms youtube --include-clips --limit 600 --csv
```

Every row is content-map ready. `PRODUCTION` is auto-filled; one platform
failing never sinks the batch.

---

## 2. Platform keys & groups

Pass a **single key** (`"youtube"`), a **list** (`["spotify","youtube"]`), or a
**group name**:

| Group | Expands to |
|---|---|
| `video` | hulu, netflix, appletv, paramount, max, disney, starz, hallmark, amazon, mgmplus, britbox, **youtube** |
| `podcasts` | spotify, applepodcasts, iheart, pandora, amazonpodcasts, siriusxm, **npr**, **youtube** |
| `audio` | audible |
| `books` | books |
| `games` | games |
| `all` | everything above (de-duped) |

> **Peacock is intentionally not in any group** — it's slug/ClickHouse and needs
> the company network. Query it through the interactive tool, not here.

---

## 3. Podcasts now cover native platforms **and** YouTube **and** NPR

A **`platforms="podcasts"`** query sweeps, in one call:

- **Native audio:** Spotify · Apple Podcasts · iHeart · Pandora · Amazon
  Podcasts · SiriusXM
- **NPR:** reads the show's public feed, one row per episode (`npr.org` page)
- **YouTube:** the show's video channel (podcasts are increasingly video-first)

So a single podcast audience request captures **listeners *and* video viewers**
with no extra wiring.

---

## 4. YouTube: the two client choices (THE NEW FEATURE)

When the title resolves to a YouTube **channel**, Promy should let the client
choose two things. StreamScout **accepts** the choice; Promy shows the prompt.

### 4a. Full episodes/videos vs. include clips  →  `include_clips`

| Client picks | Pass | What they get |
|---|---|---|
| **Full episodes/videos only** (default) | `include_clips=False` (or omit) | the long-form **Videos tab** only |
| **Include clips** | `include_clips=True` | Videos tab **+ the channel's Shorts** (the clips) |

The clip machinery only runs when the client selects "include clips." Full mode
is the plain long-form pull.

### 4b. Fair-survey cap  →  `limit`

Big channels (news, sports, clip factories) can have **thousands** of videos.
The cap keeps a run processable **without biasing toward only the newest uploads**:

- Default **`limit=600`**. When a channel has more than `limit` videos,
  StreamScout **even-strides** down to `limit` **across the whole timespan** — a
  representative survey, not just the most recent block.
- `limit=0` → **no cap** (return every video).

> **Suggested Promy prompt:**
> *"For this YouTube channel, pull **full episodes/videos only** or **include
> clips** too? (Large channels are evenly sampled to 600 across their history —
> raise or remove that cap if you want more.)"*
>
> Map the answer: full → `include_clips=False`; include clips →
> `include_clips=True`; cap override → `limit=<n>` (or `0` for everything).

### 4c. Worked example (real numbers, Rene Vaca)

```python
streamscout_query("Rene Vaca", platforms="youtube")                         # 17  (full only)
streamscout_query("Rene Vaca", platforms="youtube", include_clips=True)     # 446 (clips folded in)
streamscout_query("Rene Vaca", platforms="youtube", include_clips=True, limit=50)  # 50 (capped, even spread)
```

And a firehose channel (Law&Crime, ~6,030 videos) with the default cap returns a
**600-video even-stride across the full history**, not the latest 600.

---

## 5. Explicit-platform scope  →  `strict=True`  (don't profile the wrong platform)

When a client wants **only specific platform(s)** — e.g. *"Gilmore Girls, Hulu
viewers only, not Netflix"* — pass **`strict=True`**. It guarantees the profile
is built on exactly the platforms requested, and **hard-fails** if any
directly-named platform has **zero seeds**, instead of silently returning a
partial (or wrong-platform) set.

```python
# Client wants Hulu viewers only:
streamscout_query("Gilmore Girls", platforms="hulu", strict=True)    # 153 Hulu rows ✅

# Not actually on Disney+ → refuses to build (rather than quietly matching nothing/Netflix):
streamscout_query("Gilmore Girls", platforms="disney", strict=True)
#   raises PlatformScopeError: "... Disney Plus (0 seeds). Refusing to build ..."
```

- **Why it exists:** a scoped request can go wrong if the content map was
  preloaded with a *different* platform's seeds (the Gilmore Girls case —
  Netflix-only seeds were loaded, so a "Hulu-only" profile matched Netflix
  viewers). `strict=True` makes StreamScout the source of truth: only the
  requested platform's fresh seeds, or a loud failure.
- **What it enforces:** every platform you name **directly** (not via a group)
  must return ≥1 seed. On the CLI, `--strict` exits **3** on a scope failure.
- **Groups are not individually enforced.** `platforms="podcasts"` with
  `strict=True` won't fail just because one member (say NPR) legitimately has no
  episodes — a group is a "whatever's in this bucket" request.
- **Handling it in Promy:** catch `PlatformScopeError` and surface it to the
  client rather than building. The error carries `.title`, `.missing` (platform
  keys), `.counts`, `.missing_labels` (display names), and **`.client_message`**
  — an already-worded, client-ready line to relay verbatim.

> **Rule of thumb:** any client request that names platforms → pass
> `strict=True`. Broad "give me everything" sweeps → leave it off.

### 5a. The anti-fallback contract (the important part) + `notify=True`

This is the whole point of `strict`: when a client scopes to **Hulu only** and
StreamScout finds the title on **no Hulu**, Promy must **NOT get desperate and
fall back** to another platform's stored seeds (e.g. handing back the preloaded
**Netflix** URLs for a Hulu-only ask). That would profile the wrong audience.
The contract is:

> **On `PlatformScopeError`, do not substitute any other platform's seeds. Full
> stop.** Relay `.client_message` to the client and let a human pick it up.

`.client_message` reads, verbatim:

> *"We can't see Gilmore Girls on Hulu at this time. Notifying Crosswalk and will
> get back to you soon."*

To actually **notify Crosswalk**, pass **`notify=True`** alongside `strict=True`.
On a scope miss StreamScout emails **Jenna + Jess**
(`jenna@crosswalknyc.com`, `jessie@crosswalknyc.com`) that the build was refused,
**then** raises `PlatformScopeError`:

```python
try:
    rows = streamscout_query(
        "Gilmore Girls", platforms="hulu",
        strict=True, notify=True,
        notify_context={"window": "Jul 1 – Oct 14, 2026", "client": "Alexia"},
    )
except PlatformScopeError as e:
    reply_to_client(e.client_message)   # "We can't see Gilmore Girls on Hulu …"
    # DO NOT fall back to any other platform's seeds.
```

- **The email never blocks or crashes the request** — if SES is down the build
  is *still* refused (we never silently substitute). The alert is best-effort;
  the refusal is guaranteed.
- `notify_context` is optional (window / client / requested_by) — it just enriches
  the alert email so whoever picks it up has context.
- Uses the same SES path as the rest of Promy's mail
  (`prometheus@crosswalknyc.com`). Override recipients without a deploy via the
  `STREAMSCOUT_ALERT_TO` env var (comma-separated).
- **CLI:** add `--notify` to `--strict`; the client line prints to **stdout**,
  the detail to stderr, exit **3**.

### 5b. Current-availability advisory (works even on preloaded seeds)

`strict=True` only helps when Promy **freshly resolves**. If a profile is built
from **preloaded content_map seeds** (no re-resolve), use the advisory
availability check — a lightweight *"where to watch {title}"* web lookup that
needs **no resolve** and tells you whether each requested platform **currently**
carries the title:

```python
from streamscout_availability import check_availability, warnings_from

report = check_availability("Gilmore Girls", platforms=["hulu", "disney"])
# [{"platform":"hulu","label":"Hulu","available":True,  ...},
#  {"platform":"disney","label":"Disney Plus","available":False, "note":"not found … confirm before profiling …"}]

for w in warnings_from(report):
    surface_to_client(w)   # ⚠️ Disney Plus: not found in a where-to-watch search …
```

- **`available`** is `True` / `False` / `None` (unknown — no search key, or a
  non-streaming platform). It is **advisory and never blocks** — the data is a
  public where-to-watch search, so a `False` means "confirm," not "forbidden."
- **Why it's separate from `strict`:** it catches the *Gilmore-Girls-from-stale-
  seeds* case — a profile built on preloaded "GG on Netflix" seeds after the
  title left Netflix — **without** re-resolving, because it only does a search.
- **History is kept on purpose.** This answers *currently available?* only; old
  seeds for a platform the title has since left stay valid for **past-window
  viewership** behavior. The advisory is about whether StreamScout can *access*
  a platform now, not about pruning history.
- **Credentials:** reuses the **same shared web-search key** as SiriusXM
  (`BRAVE_SEARCH_API_KEY`, or `GOOGLE_SEARCH_API_KEY` + `GOOGLE_SEARCH_CX`). No
  key → everything returns `None` (unknown), still non-blocking.
- **CLI:** `python3 streamscout_availability.py --title "Gilmore Girls" --platforms hulu,disney`

> **Rule of thumb:** fresh resolve + named platforms → `strict=True`. Building
> from preloaded seeds → run `check_availability()` first and surface any ⚠️.

### 5c. Always rescan current placements  →  `current_placements()`  (a content_map hit must NOT skip the scan)

The Gilmore Girls incident had a deeper root cause than platform scope: GG was
**already in the content_map on Netflix**, so nobody ever scanned **Hulu**, where
it actually lives now. A pre-existing seed quietly **suppressed the scan**.

Fix the behavior at the source: **on every seeding pass, rescan — even if the
title is already in the content_map.** StreamScout's resolvers always hit each
platform's **live catalog**, so a fresh scan is the authority on *current*
placement. `current_placements()` is the one call for that:

```python
from streamscout_query import current_placements

# Where does GG live RIGHT NOW? (ignores whatever's already stored)
r = current_placements("Gilmore Girls", platforms="video")
# r["present_labels"] -> ["Hulu"]      ← found, even though it was stored on Netflix

# Reconcile against what the content_map already has:
r = current_placements("Gilmore Girls", platforms="video", known=["Netflix"])
# r["added"]     -> ["hulu"]      ← now available here; seed it
# r["gone"]      -> ["netflix"]   ← no longer live here (keep history, stop treating as current)
# r["unchanged"] -> []
```

- **`present` / `present_labels`** — where the title lives **now** (seed counts).
- **`added` / `gone` / `unchanged`** — only when you pass **`known`** (the
  platforms already in the content_map, as keys or labels like `"Netflix"`). This
  is the exact signal to update the map: seed the `added`, stop treating `gone`
  as a *current* placement.
- It **scans the union** of the requested platforms **and** `known`, so it can
  confirm a stored platform is truly `gone`.
- **History is preserved.** `gone` means "not live there now," not "delete the
  old seeds" — past-window viewership still needs them. This only corrects what
  counts as *currently* available.
- Never raises on a single platform failure (hands-off). **CLI:**
  `python3 streamscout_query.py --title "Gilmore Girls" --placements --platforms video --known Netflix`

> **Rule of thumb:** a title already being in the content_map is **not** a reason
> to skip scanning. Run `current_placements()` every pass; let the live catalog —
> not a stale seed — decide where it's available now.

### 5d. Viewers profiles ride on watch URLs ONLY  →  `split_profile_terms()` / `viewer_terms()`

Profile IQ seeds a profile **two** ways, and they must not be mixed:

| Profile kind | Seeds on | Example terms |
|---|---|---|
| **Person / brand** | host-map + **name variations** (generic "touchpoints") | `gilmore-girls`, `gilmore+girls`, `Gilmore%20Girls` |
| **Viewers / listeners / players** | **StreamScout watch URLs** from the content_map | `4af2e0cf-…`, `ce5edf4f-…` (the Hulu asset IDs) |

When the client asked for **Gilmore Girls viewers**, the Hulu watch URLs came
back correctly — but the generic name-variation touchpoints
(`Gilmore-Girls`, `Gilmore+Girls`, `Gilmore%20Girls`, …) were **also** added.
Those capture *anyone searching or posting about the show*, not Hulu viewers, so
they contaminate a viewers profile. **A viewers/listeners/players profile must
seed on watch URLs only.**

Two helpers enforce it:

```python
from streamscout_query import streamscout_query, viewer_terms, split_profile_terms

rows = streamscout_query("Gilmore Girls", platforms="hulu", strict=True)

# (a) the authoritative term set — just the watch URLs, ready for Profile IQ:
terms = viewer_terms(rows)          # ['4af2e0cf-…', 'ce5edf4f-…', …] only

# (b) guard a term list Promy assembled — strip any generic touchpoints:
kept, dropped = split_profile_terms(candidate_terms, "Gilmore Girls", rows=rows)
# dropped -> ['Gilmore Girls','Gilmore-Girls','Gilmore+Girls','Gilmore%20Girls', …]
# kept    -> the watch URLs only
```

- **`viewer_terms(rows)`** — the clean allowlist straight from a `streamscout_query()`
  result. This is what a viewers profile should be built on, full stop.
- **`split_profile_terms(candidate, title, rows=None)`** — returns
  `(watch_terms, generic_touchpoints)`. It drops every **name variation** of the
  title (all spacing/delimiter/URL-encoding/case forms). Pass **`rows=`** for the
  strict version: a term survives only if it's an actual StreamScout identifier
  (bare-UUID ↔ full-URL aware), so nothing but real watch URLs gets through.
- **CLI:** `cat terms.txt | python3 streamscout_query.py --title "Gilmore Girls" --scrub-terms`
  (prints the kept watch URLs; dropped touchpoints go to stderr).

> **Rule of thumb:** building a **viewers/listeners/players** profile → seed on
> `viewer_terms(rows)` only, and run `split_profile_terms()` on anything else
> before it reaches Profile IQ. Name variations belong to **person/brand**
> profiles, never to a viewers profile.

### 5e. Look-alike guard — a wrong-title match can't seed a profile (automatic)

Some platforms' resolvers will answer a query with the *closest* show they can
find, even when it isn't the right one. A **"Paw Patrol"** scan came back with
**Doom Patrol** (HBO Max) and **Rosemary & Thyme** (Hallmark) — two completely
different shows that would have seeded the wrong audience.

`streamscout_query()` / `query_report()` / `current_placements()` now **verify
every returned row's SHOW against the requested title** and **drop the
look-alikes automatically** — no action needed on your side. It uses the same
similarity floor as the rest of the pipeline, so:

- *Paw Patrol* vs *Doom Patrol* (0.57) → **dropped**; *Paw Patrol* vs *Rosemary
  & Thyme* (0.25) → **dropped**.
- Legit messy titles still pass: *Wicked For Good* ↔ *Wicked: For Good*, *The
  Office* ↔ *The Office (U.S.)*, *Paw Patrol* ↔ *Paw Patrol: The Movie* → kept.

Dropped look-alikes are logged to stderr (`⚠ hallmark: dropped look-alike
ROSEMARY & THYME — resolved a different show, not 'Paw Patrol'`). If a caller
pins an exact asset via a `hints=`/`url=` identifier, verification is skipped for
that platform (nothing to second-guess).

> **Why it matters:** this is the automatic backstop behind §5/§5d — even if a
> resolver fuzzy-matches the wrong show, it can never reach a client profile.

### 5f. Interactive-login platforms are skipped unattended (HBO Max)

**HBO Max** needs a real logged-in browser window to read its catalog (its app
won't render headless). So the non-interactive paths (`streamscout_query`,
`current_placements`) **skip Max** instead of popping a browser window on
someone's desktop — it's reported as **unverified**, not as "0 / not available,"
so a stored Max placement is never read as gone. To pull Max, run its own tool
(`max_episode_identifier.py`) interactively. (A `STREAMSCOUT_HEADLESS=1` env var
also forces any direct Max call to stay window-less.)

---

## 6. Output schema (every row)

| Column | Meaning |
|---|---|
| `SHOW` | the resolved show/franchise (UPPERCASED); use `show=` to force a franchise label |
| `URL` | the platform identifier (e.g. `watch?v=…`, `npr.org/…`, `detail/<ASIN>`, `app/<id>`) |
| `PRODUCTION` | studio/producer tag, auto-filled |
| `PLATFORM` | the platform label (YouTube, NPR, Spotify, …) |
| `SEASON` | `Season N` for video series; blank for movies, podcasts, audiobooks, YouTube |

---

## 7. Quick recipes

```python
# Podcast audience (listeners + video viewers), everything in one call
streamscout_query("The Daily", platforms="podcasts")

# A single creator's YouTube, full episodes only
streamscout_query("Rene Vaca", platforms="youtube")

# Same creator, include clips but keep it lean
streamscout_query("Rene Vaca", platforms="youtube", include_clips=True, limit=600)

# A whole book franchise rolled under one SHOW
rows = []
for t in ["Merciless Saints", "Cruel Saints", "Ruthless Saints"]:
    rows += streamscout_query(t, platforms="books", show="Merciless Saints")

# A game's buy/play links across every store
streamscout_query("King of Fighters", platforms="games", show="King of Fighters")

# Client scoped to a single platform (profile only those viewers)
streamscout_query("Gilmore Girls", platforms="hulu", strict=True)
```

---

## 8. Gotchas / good-to-know

- **YouTube is throttle-sensitive.** A rapid repeat pull of the *same* big
  channel can come back thin — wait a minute and retry; that's the throttle, not
  a bad channel.
- **NPR stays native.** The NPR resolver only accepts shows whose feed is a real
  `feeds.npr.org` feed, so it won't drag in a non-NPR look-alike.
- **The cap is a *survey*, not a filter.** It never changes a show's audience
  size (sizing is show-level); it just keeps the id set processable while
  staying representative across the timespan.
- **Platform-scoped requests → `strict=True`.** It's the guard against building a
  profile on the wrong platform's seeds (see §5). Name platforms = pass strict.
- **Full platform grid:** see `StreamScout-Platforms.xlsx` (23 platforms).
