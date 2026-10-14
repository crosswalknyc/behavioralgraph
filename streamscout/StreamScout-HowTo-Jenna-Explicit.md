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

## 5. Output schema (every row)

| Column | Meaning |
|---|---|
| `SHOW` | the resolved show/franchise (UPPERCASED); use `show=` to force a franchise label |
| `URL` | the platform identifier (e.g. `watch?v=…`, `npr.org/…`, `detail/<ASIN>`, `app/<id>`) |
| `PRODUCTION` | studio/producer tag, auto-filled |
| `PLATFORM` | the platform label (YouTube, NPR, Spotify, …) |
| `SEASON` | `Season N` for video series; blank for movies, podcasts, audiobooks, YouTube |

---

## 6. Quick recipes

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
```

---

## 7. Gotchas / good-to-know

- **YouTube is throttle-sensitive.** A rapid repeat pull of the *same* big
  channel can come back thin — wait a minute and retry; that's the throttle, not
  a bad channel.
- **NPR stays native.** The NPR resolver only accepts shows whose feed is a real
  `feeds.npr.org` feed, so it won't drag in a non-NPR look-alike.
- **The cap is a *survey*, not a filter.** It never changes a show's audience
  size (sizing is show-level); it just keeps the id set processable while
  staying representative across the timespan.
- **Full platform grid:** see `StreamScout-Platforms.xlsx` (23 platforms).
