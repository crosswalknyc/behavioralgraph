#!/usr/bin/env python3
"""
paramount_episode_identifier.py  —  Paramount+ title -> watch video ids (no login)
==================================================================================
Paramount+ watch URLs look like  https://www.paramountplus.com/shows/video/<id>/
(and /movies/video/<id>/ for films).  The <id> is what we return, grouped by
season for series:

    Landman   6hPvmRO1ZRVTbgEqEI8_DdZoX7Y5AciH   Paramount TV Studios   Paramount Plus   Season 1
    Landman   x5NEbffXFD_NfJ135CpYteQWjyoe4tdI   Paramount TV Studios   Paramount Plus   Season 2

How (no login): Paramount+'s public per-season episode feed is
    /shows/<slug>/xhr/episodes/page/<p>/size/100/xs/0/season/<N>/
which returns JSON with content_id / season_number / episode_number /
episode_title for every episode.  The site is behind Akamai, which 406s Python's
TLS fingerprint, so we shell out to `curl` (real TLS) for every request.

Discovery: the show slug is just the title ("Landman" -> "landman"), confirmed by
fetching /shows/<slug>/.  Some slugs use underscores ("Big Brother" ->
"big_brother"); the hyphen guess 301s to the underscore form, so we follow the
redirect (curl -L) and adopt the canonical slug from the final URL.  Movies are
catalog-JS-rendered, so movie-by-title is best-effort (slug guess); pasting a
paramountplus.com URL always works.

Could not look is never not there (2026-11-11, Jessie's "Georgie & Mandy's
First Marriage Viewers - Paramount+", run 4pKpMKnh7Lt77Q)
-----------------------------------------------------------------------------
The build was refused as "We can't see ... on Paramount Plus" while the content
map held 49 Paramount Plus placements for the title. Three things were wrong:

1. The build server sits in Germany. paramountplus.com answers every request
   from there with the German catalog (/de/...); a US-only CBS show 404s. The
   resolver read the 404 as (title, []), an honest zero.
2. The slug guess: "Georgie & Mandy's First Marriage" slugified to
   georgie-mandy-s-first-marriage; the page is georgie-and-mandys-first-marriage.
   A wrong guess is not an answer either.
3. An in-season network show is not listed by season: its numbered season feeds
   are empty and its episodes sit in the season-less feed (season/0).

Now: the slug is tried in the forms Paramount+ writes it ('&' as 'and',
apostrophes dropped, then the plain slug), then found from a placement the
content map already holds (a watch id opens /shows/video/<id>/, whose page names
its show); a show with no numbered seasons is read from the season-less feed;
and when nothing resolves and the site is answering for another country, or did
not answer at all, the resolver raises DiscoveryUnavailable. A caller records
the platform as unverified and keeps the placements it holds; it never reads
that as 'not on Paramount Plus'. An empty answer is an honest zero only when
the US site itself gave it.

Usage
-----
    python3 paramount_episode_identifier.py --type series --title "Landman" --seasons 1,2
    python3 paramount_episode_identifier.py --type series --url https://www.paramountplus.com/shows/landman/
    python3 paramount_episode_identifier.py --type movie  --url https://www.paramountplus.com/movies/video/<id>/
    python3 paramount_episode_identifier.py                 # interactive
"""

import argparse
import csv
import html as _html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
BASE = "https://www.paramountplus.com"
VIDEO_ID = r"[A-Za-z0-9_]{16,}"
_SENTINEL = "__HTTP_CODE__"
_OG_TITLE = re.compile(
    r'property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', re.I)
MAX_SEASONS = 40  # safety cap when enumerating "all"
_BLOCKED = (0, 403, 406, 429)          # no answer, or Akamai's refusal: not a verdict
_FOREIGN_PATH = re.compile(r"^/(?:[a-z]{2}(?:-[a-z]{2})?|intl)(?:/|$)")
_US_CACHE = {"ts": 0.0, "us": None, "where": ""}
_US_TTL_S = 1800


class DiscoveryUnavailable(Exception):
    """Paramount+ could not be checked from this machine: paramountplus.com
    answered for another country (outside the US it serves that country's
    catalog under /<cc>/, and a US-only title 404s), or it did not answer at
    all. This is NOT a verdict on the title. A caller records the platform as
    unverified and keeps whatever placement it already holds; it never reads
    this as 'not on Paramount Plus'."""
    unverified = True

    def __init__(self, reason):
        self.reason = str(reason or "")
        super().__init__("Paramount Plus cannot be checked from here: " + self.reason)


def foreign_catalog(final_url):
    """The country prefix when the address is another country's catalog
    ('/de/shows/landman/' -> 'de'); '' on the US site."""
    m = re.match(r"https?://[^/]+(/.*)?$", final_url or "")
    path = (m.group(1) if m else "") or "/"
    fm = _FOREIGN_PATH.match(path)
    return fm.group(0).strip("/") if fm else ""


def in_us():
    """True when paramountplus.com is answering with the US catalog, False
    when it answers for another country, None when it did not answer. One
    request to the home page, remembered for half an hour."""
    now = time.time()
    if _US_CACHE["us"] is not None and now - _US_CACHE["ts"] < _US_TTL_S:
        return _US_CACHE["us"]
    code, _html, final = curl(BASE + "/", with_url=True)
    where = foreign_catalog(final)
    if code in _BLOCKED or code >= 500:
        us = None
    elif where:
        us = False
    else:
        us = code == 200
    _US_CACHE.update(ts=now, us=us, where=where or ("" if us else "no answer (HTTP %s)" % code))
    return us


def _require_us(what):
    """Raise DiscoveryUnavailable unless the US site is the one answering."""
    us = in_us()
    if us is True:
        return
    if us is False:
        raise DiscoveryUnavailable(
            "paramountplus.com answered with the %s catalog while looking for %s "
            "(the US catalog is read from a US address)" % (_US_CACHE["where"].upper(), what))
    raise DiscoveryUnavailable("paramountplus.com did not answer while looking for %s (%s)"
                               % (what, _US_CACHE["where"] or "no answer"))


# ── helpers ───────────────────────────────────────────────────────────────────
def tokens(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def slugify(title):
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")


def slug_candidates(title):
    """The slugs a title can live under, most likely first. Paramount+ writes
    '&' as 'and' and drops apostrophes ("Georgie & Mandy's First Marriage" is
    georgie-and-mandys-first-marriage); the plain slug (georgie-mandy-s-first-
    marriage) is the old guess and stays as the last try."""
    t = (title or "").strip()
    if not t:
        return []
    bare = re.sub(r"[\u2018\u2019\u02bc'`]", "", t)
    out = []
    for cand in (slugify(bare.replace("&", " and ")), slugify(bare.replace("&", " ")),
                 slugify(bare.replace("+", " plus ")), slugify(t)):
        if cand and cand not in out:
            out.append(cand)
    return out


def parse_seasons(raw):
    raw = (raw or "").strip().lower()
    if raw in ("", "all", "*", "a"):
        return None
    out = set()
    for part in raw.replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a.isdigit() and b.isdigit():
                out.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out or None


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def curl(url, json_accept=False, timeout=45, with_url=False):
    """Fetch via curl (bypasses Akamai's Python-TLS 406). Follows redirects (-L)
    so a slug like `big-brother` that 301s to the canonical `big_brother`
    resolves. Returns (code, body) — or (code, body, final_url) if with_url."""
    accept = ("application/json, text/plain, */*" if json_accept
              else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    cmd = ["curl", "-sL", "--compressed", "-A", UA,
           "-H", "Accept: " + accept,
           "-H", "Accept-Language: en-US,en;q=0.9",
           "-H", "Referer: https://www.paramountplus.com/"]
    if json_accept:
        cmd += ["-H", "X-Requested-With: XMLHttpRequest"]
    wfmt = "%{http_code}" + ("|%{url_effective}" if with_url else "")
    cmd += ["-w", "\n" + _SENTINEL + wfmt, url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout).stdout
    except Exception:
        return (0, "", "") if with_url else (0, "")
    code, final = 0, ""
    if _SENTINEL in out:
        out, _, meta = out.rpartition(_SENTINEL)
        meta = meta.strip()
        cstr, final = (meta.split("|", 1) if with_url and "|" in meta
                       else (meta, ""))
        try:
            code = int(cstr.strip())
        except ValueError:
            code = 0
        out = out[:-1] if out.endswith("\n") else out
    return (code, out, final) if with_url else (code, out)


def show_name(html, fallback=""):
    m = _OG_TITLE.search(html or "")
    if m:
        t = m.group(1)
        t = re.sub(r"^\s*watch\s+", "", t, flags=re.I)
        t = re.sub(r"\s+movies?\s*(?:-|on)\s*paramount\+?.*$", "", t, flags=re.I)
        t = re.sub(r"\s+on\s+paramount\+?.*$", "", t, flags=re.I)
        t = re.sub(r"\s*[-–—|:]?\s*(?:streaming\s+online|full\s+episodes"
                   r"(?:\s+and\s+clips)?|watch\s+free).*$", "", t, flags=re.I)
        t = re.sub(r"\s*[-–—|]\s*watch\s*$", "", t, flags=re.I)
        # 'Georgie &amp; Mandy\u2019s First Marriage - CBS': the title, unescaped,
        # without the network tag Paramount+ appends to a network show
        t = _html.unescape(t)
        t = re.sub(r"\s+[-\u2013|]\s+(?:CBS|Paramount\+?|SHOWTIME|Showtime|MTV|BET\+?|Comedy Central|"
                   r"Nickelodeon|Nick Jr\.?|Smithsonian Channel|CMT|VH1|TV Land|Pop TV)\s*$", "", t)
        if t.strip():
            return t.strip()
    return fallback


# ── series ────────────────────────────────────────────────────────────────────
def _fetch_season(slug, season):
    """All episode dicts for one season (handles paging + rate-limit retry)."""
    episodes, page, total = [], 0, None
    while True:
        url = ("%s/shows/%s/xhr/episodes/page/%d/size/100/xs/0/season/%d/"
               % (BASE, slug, page, season))
        data = None
        for attempt in range(4):
            _, body = curl(url, json_accept=True)
            try:
                data = json.loads(body)
            except Exception:
                data = None
            if isinstance(data, dict) and data.get("success") is False:
                time.sleep(0.8 * (attempt + 1))  # "Request Exceeds Limits"
                data = None
                continue
            break
        result = (data or {}).get("result")
        if not isinstance(result, dict):
            break
        batch = result.get("data") or []
        episodes.extend(batch)
        total = result.get("total")
        page += 1
        if not batch or total is None or len(episodes) >= total or page > 20:
            break
        time.sleep(0.3)
    return episodes


def _row_from_episode(e):
    vid = e.get("content_id") or ""
    if not vid:
        return None
    return {
        "season": str(e.get("season_number") or e.get("seasonNumber") or ""),
        "episode": str(e.get("episode_number") or e.get("episodeNumber") or ""),
        "title": e.get("episode_title") or e.get("label") or "",
        "identifier": vid,
        "watch_url": "%s/shows/video/%s/" % (BASE, vid),
    }


def _resolve_series(slug, seasons):
    rows = []

    # Explicit season list: fetch exactly those (an empty one is just skipped).
    if seasons is not None:
        for s in sorted(seasons):
            for e in _fetch_season(slug, s):
                r = _row_from_episode(e)
                if r:
                    rows.append(r)
            time.sleep(0.3)
        return rows or _seasonless_rows(slug, seasons)

    # "all": Paramount+ often carries only a NON-CONTIGUOUS subset of a show's
    # seasons (e.g. Wild 'N Out streams seasons 12, 15, 19-21 — nothing earlier).
    # So we can't stop at the first empty season. Scan from 1, and stop only
    # after a run of empty seasons that (a) follows at least one populated season
    # and (b) is long enough to jump internal gaps. Before any hit we keep going
    # (the catalog may start late), bounded by MAX_SEASONS.
    GAP_LIMIT = 5              # jumps up to 4 consecutive empty seasons mid-run
    SEASONLESS_AFTER = 3       # nothing in seasons 1-3: ask the season-less feed
    consecutive_empty, found_any = 0, False
    for s in range(1, MAX_SEASONS + 1):
        eps = _fetch_season(slug, s)
        if eps:
            found_any = True
            consecutive_empty = 0
            for e in eps:
                r = _row_from_episode(e)
                if r:
                    rows.append(r)
        else:
            consecutive_empty += 1
            if found_any and consecutive_empty >= GAP_LIMIT:
                break          # we're past the end of the catalog
            if not found_any and consecutive_empty == SEASONLESS_AFTER:
                # An in-season network show is not listed by season: every
                # numbered feed is empty and its episodes sit in the
                # season-less feed (season/0). Georgie & Mandy's First
                # Marriage, 2026-11-11: seasons 1 and 2 answered 0, season/0
                # answered the five current episodes.
                seasonless = _seasonless_rows(slug)
                if seasonless:
                    return seasonless
        time.sleep(0.3)
    return rows


def _seasonless_rows(slug, seasons=None):
    """Every episode the season-less feed lists, limited to ``seasons`` when
    given (each row carries its own season number)."""
    rows = []
    for e in _fetch_season(slug, 0):
        r = _row_from_episode(e)
        if not r:
            continue
        if seasons is not None:
            try:
                if int(r["season"]) not in seasons:
                    continue
            except (TypeError, ValueError):
                continue
        rows.append(r)
    return rows


def _hint_video_id(url):
    """The watch id inside a hint: a /shows/video/<id>/ URL, or the bare id the
    content map stores."""
    u = (url or "").strip()
    m = re.search(r"/(?:shows|movies)/video/(" + VIDEO_ID + r")", u)
    if m:
        return m.group(1)
    return u if re.match(r"^" + VIDEO_ID + r"$", u) else ""


def _slug_from_video_page(video_id, title):
    """The show slug named on a watch page (/shows/video/<id>/ links back to
    /shows/<slug>/). When the page names several shows the one that shares the
    most words with the title wins. None when the page does not open."""
    code, html, final = curl("%s/shows/video/%s/" % (BASE, video_id), with_url=True)
    if code != 200 or foreign_catalog(final):
        return None
    want = set(tokens(title))
    best, best_score = None, -1
    for slug in dict.fromkeys(re.findall(r"/shows/([a-z0-9_-]+)/", html or "")):
        if slug == "video":
            continue
        score = len(want & set(tokens(slug.replace("_", " ").replace("-", " "))))
        if score > best_score:
            best, best_score = slug, score
    return best if (best and (best_score > 0 or not want)) else None


def _series_slug(title, url):
    """Return (slug, show_name) or (None, name). Confirms via /shows/<slug>/.

    Paramount slugs sometimes use underscores ("big_brother"), while slugify
    produces hyphens ("big-brother") — the hyphen form 301s to the underscore
    form, so we follow the redirect and adopt the canonical slug from the final
    URL (otherwise the /xhr/episodes feed 404s).

    2026-11-11: the slug is tried in every form Paramount+ writes a title
    (slug_candidates), then read off a placement the content map already holds
    (a watch id). A page that did not answer, or answered for another country,
    raises DiscoveryUnavailable; only a 404 from the US site is a miss."""
    cands = []
    if url:
        m = re.search(r"/shows/([a-z0-9_-]+)/", url)
        if m and m.group(1) not in ("video",):
            cands.append(m.group(1))
    cands += [c for c in slug_candidates(title) if c not in cands]
    hint_id = _hint_video_id(url)
    blocked = 0
    tried = 0
    for slug in cands + ["\0hint"]:
        if slug == "\0hint":
            slug = _slug_from_video_page(hint_id, title) if hint_id else None
            if not slug or slug in cands:
                break
        tried += 1
        code, html, final = curl("%s/shows/%s/" % (BASE, slug), with_url=True)
        where = foreign_catalog(final)
        if where:
            raise DiscoveryUnavailable(
                "paramountplus.com answered with the %s catalog for /shows/%s/ "
                "(the US catalog is read from a US address)" % (where.upper(), slug))
        if code in _BLOCKED or code >= 500:
            blocked += 1
            continue
        # adopt the post-redirect canonical slug (e.g. big-brother -> big_brother)
        fm = re.search(r"/shows/([a-z0-9_-]+)", final or "")
        if fm and fm.group(1) != "video":
            slug = fm.group(1)
        if code == 200:
            return slug, show_name(html, title or "")
    if tried and blocked == tried:
        raise DiscoveryUnavailable("paramountplus.com did not answer for %r (%d page(s) refused or timed out)"
                                   % (title or url or "", tried))
    return None, title or ""


# ── movies (best-effort by title; reliable via pasted URL) ────────────────────
def _resolve_movie(title, url):
    vid = None
    if url:
        m = (re.search(r"/(?:movies|shows)/video/(" + VIDEO_ID + r")/", url)
             or re.search(r"/movies/[a-z0-9-]+/(" + VIDEO_ID + r")/", url))
        if m:
            vid = m.group(1)
    name = title or ""
    if not vid and title:
        code, html = curl("%s/movies/%s/" % (BASE, slugify(title)))
        if code == 200:
            name = show_name(html, title)
            m = re.search(r"/movies/video/(" + VIDEO_ID + r")/", html)
            if m:
                vid = m.group(1)
    if not vid:
        return name, []
    return name, [{"season": "", "episode": "", "title": "",
                   "identifier": vid,
                   "watch_url": "%s/movies/video/%s/" % (BASE, vid)}]


# ── reusable resolver (imported by title_lookup.py) ───────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None):
    """Resolve a Paramount+ title to watch video ids.

    Returns (show_name, rows); each row is
        {season, episode, title, identifier, watch_url}
    (season/episode/title are "" for a movie).  `seasons` is a set or None (all).

    Raises DiscoveryUnavailable when Paramount+ could not be checked from
    this machine (another country's catalog, or no answer): an empty list is
    returned only when the US site itself answered with nothing.
    """
    if kind == "movie":
        name, rows = _resolve_movie(title, url)
        if not rows:
            _require_us("the movie %r" % (title or url or ""))
        return name, rows

    slug, name = _series_slug(title, url)
    if not slug:
        _require_us("the show %r" % (title or url or ""))
        return (title or "", [])
    rows = _resolve_series(slug, seasons)
    if not rows:
        _require_us("the episodes of %r" % (name or title or slug))
    return (name or title or "", rows)


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Paramount+ title -> watch video ids (no login).")
    ap.add_argument("--type", choices=["movie", "series"])
    ap.add_argument("--title")
    ap.add_argument("--seasons", help="e.g. 1 | 1,3 | 1-4 | all")
    ap.add_argument("--url", help="a paramountplus.com URL")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    kind, title = args.type, args.title
    seasons = parse_seasons(args.seasons) if args.seasons is not None else None
    if not kind and not args.url:
        a = ask("Movie or Series?  [m/s]: ").lower()
        kind = "movie" if a.startswith("m") else "series"
    if not args.url and not title:
        title = ask("What title?: ")
    if kind == "series" and args.seasons is None and not args.url:
        seasons = parse_seasons(ask("Which season(s)?  1 | 1,3 | 1-4 | all: "))
    if not args.url and not title:
        print("Need a --title or --url."); return 1

    try:
        show, rows = resolve(title=title, url=args.url, kind=kind or "series",
                             seasons=seasons)
    except Exception as e:  # noqa: BLE001
        print(f"  ! Fetch/parse failed: {e!r}"); return 2
    if not rows:
        print("  ! Nothing found. For series, check the title; for movies, pass "
              "--url https://www.paramountplus.com/movies/video/<id>/")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "paramount").lower()).strip("-")
    out = os.path.join(args.outdir, f"paramount_{kind or 'series'}_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "SEASON", "EPISODE", "EPISODE_TITLE",
                    "WATCH_ID", "WATCH_URL", "PLATFORM"])
        for r in rows:
            w.writerow([show, r["season"], r["episode"], r["title"],
                        r["identifier"], r["watch_url"], "Paramount Plus"])

    print(f"\nWrote {len(rows)} watch id(s) for {show!r}.")
    for r in rows[:60]:
        tag = f" S{r['season']} E{r['episode']}" if r["season"] else ""
        print(f"   {show}{tag}  ->  {r['identifier']}  ({r['title']})" if tag
              else f"   {show}  ->  {r['identifier']}")
    if len(rows) > 60:
        print(f"   ... and {len(rows) - 60} more (see CSV)")
    print(f"\nCSV: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
