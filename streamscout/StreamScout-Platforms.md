# StreamScout — Platforms We Support

StreamScout takes a **title** (movie, series, podcast, or audiobook) and returns
the platform's unique **watch / play / listen identifier(s)** for it, written to
a CSV on your Desktop.

Every lookup asks four questions — *Movie or Series? → Title → Season(s) (series
only) → Platform* — and every platform writes the **same CSV**:

```
SHOW · URL · PRODUCTION · PLATFORM · SEASON
```

**Both movies and series work on every platform below** (podcasts return one row
per episode). For series you can ask for one season, a range like `1-4`,
specific seasons `1,3,5`, or `all`. Movies come back as a single title with a
blank SEASON.

---

## 📺 Streaming Video (13)

| # | Platform | Access | Movies | Series | What the URL column holds |
|---|----------|:---:|:---:|:---:|---|
| 1 | **Peacock** | No login | ✅ | ✅ | Playback UUID (full episode tree; movies via sitemap) |
| 2 | **Hulu** | No login | ✅ | ✅ | Episode / movie UUID (full episode tree) |
| 3 | **Netflix** | **Browser login** | ✅ | ✅ | `watch/<id>` |
| 4 | **Apple TV+** | No login | ✅ | ✅ | One `umc.cmc…` id per show/movie (Apple keys all episodes to one id) |
| 5 | **Paramount Plus** | No login | ✅ | ✅ | Episode watch URLs (walks every season) |
| 6 | **HBO MAX** | **Browser login** | ✅ | ✅ | `/video/watch/<uuid>` |
| 7 | **Disney+** | Situational | ✅ | ✅ | Play UUID (no-login first; browser only for tricky titles) |
| 8 | **Starz** | No login | ✅ | ✅ | `starz.com/us/en/play/<id>` |
| 9 | **Hallmark Plus** | No login | ✅ | ✅ | `item/<CODE>` (main episodes only) |
| 10 | **Amazon** (Prime Video **+ Channels**) | No login | ✅ | ✅ | Every watch id: `detail/<ASIN>`, `detail/<GTI>`, `amzn1.dv.gti.<uuid>` |
| 11 | **MGM Plus** | No login | ✅ | ✅ | Watch paths: `movie/<slug>/watch`; series `<slug>/watch/season/<N>/episode` (one shell per season) |
| 12 | **BritBox** | No login | ✅ | ✅ | One shell path per title: `show/<slug>` or `movie/<slug>` |
| 13 | **YouTube** | No login | ✅ | ✅ | Full `watch?v=<id>` link — one row per long-form video (Shorts excluded) |

## 🎧 Podcasts (6)

| # | Platform | Access | Movies | Series | What the URL column holds |
|---|----------|:---:|:---:|:---:|---|
| 14 | **Spotify** | **Free API key** | ✅ | ✅ | One row per episode — full `open.spotify.com/episode/<id>` URL |
| 15 | **Apple Podcasts** | No login | ✅ | ✅ | One row per episode — full `podcasts.apple.com` URL (feed caps ~200 eps) |
| 16 | **iHeart** | No login | ✅ | ✅ | One row per episode — full `iheart.com/podcast/…/episode/…` URL |
| 17 | **Pandora** | No login | ✅ | ✅ | One row per episode — full `pandora.com/podcast/…` URL |
| 18 | **Amazon Podcasts** | No login | ✅ | ✅ | One row per episode — full `music.amazon.com/podcasts/…` URL (self-driving Chromium) |
| 19 | **SiriusXM** | Situational | ✅ | ✅ | One row per episode — full `siriusxm.com/player/episode-podcast/…` URL |

## 📚 Audiobooks (1)

| # | Platform | Access | Movies | Series | What the URL column holds |
|---|----------|:---:|:---:|:---:|---|
| 20 | **Audible** | No login | ✅ | ✅ | **Two rows per title** — the direct `audible.com/pd/<slug>/<ASIN>` listen link **and** the parallel `amazon.com/dp/<ASIN>` Audible Audio Edition (a *different* ASIN), each labeled `Audible` / `Amazon` in PLATFORM (self-driving Chromium) |

---

## Good to know

- **No login for 15 of 20.** Only **5** need anything: **Netflix** & **HBO Max**
  (browser login), **Spotify** (a free developer API key), and **Disney+** &
  **SiriusXM** (situational). Those credentials live in a local, gitignored
  `.env.local` file — never in the tool.
- **SiriusXM is Netflix-style for title search.** Pasting a SiriusXM show link
  needs **no login**. Searching by title needs a **one-time** login — the first
  search on a new computer completes SiriusXM's device code once, then it's
  remembered and runs silently after that.
- **Amazon is special in two ways:**
  1. It captures **every way a title can show up in clickstream** — all of a
     title's offer ASINs (SD/HD/UHD/ad-tier) plus its GTI in both encodings, for
     the season shell *and* every episode. For films it also pulls **every
     edition** (theatrical, ad-supported, Director's Cut).
  2. It also covers **Amazon Channels** — titles serviced *through* Amazon
     (Lionsgate+, Starz, etc.) still resolve to their Amazon ids.
- **Audible gives you both routes.** Every audiobook returns **two rows** — the
  direct `audible.com/pd` listen link and the parallel `amazon.com/dp` Audible
  Audio Edition (a *different* ASIN) — each tagged `Audible` / `Amazon` in the
  PLATFORM column. Title search matches on the words in the title; for a set
  whose names differ, paste the Audible **series** link to pull the whole run.
- **Messy titles are OK.** Clients can mistype — extra words, wrong subtitle,
  typos ("Alien Extinciton"), wrong casing, a stray year — and StreamScout still
  finds the right title and counts it as an exact match.
- **Same-name titles are handled.** StreamScout tells a movie apart from a
  same-named series.
- **Season depth varies by how the platform builds its URLs:**
  - **Full episode tree:** Peacock, Hulu, Netflix, HBO Max, Disney+, Starz,
    Hallmark+, Amazon, Paramount Plus.
  - **One shell per season:** MGM Plus.
  - **One shell per whole show:** Apple TV+ and BritBox.
  - **Flat episode list (no season split):** YouTube, all 6 podcast platforms,
    and Audible — one row per episode/title, SEASON left blank.
- **Podcasts return every episode.** Pick *Series* for the full run, *Movie* for
  a single episode.
- **PRODUCTION** (studio) is filled automatically for every title.

*Questions? Ask Cousin — happy scouting. 🛰️*
