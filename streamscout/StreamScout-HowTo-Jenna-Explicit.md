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
  client ("requested Hulu, 0 Hulu seeds found — seed Hulu or adjust the
  platforms") rather than building. The error carries `.title`, `.missing`
  (platform keys), and `.counts`.

> **Rule of thumb:** any client request that names platforms → pass
> `strict=True`. Broad "give me everything" sweeps → leave it off.

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
