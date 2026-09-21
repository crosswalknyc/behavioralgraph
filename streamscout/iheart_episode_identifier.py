#!/usr/bin/env python3
"""
iheart_episode_identifier.py  —  iHeart podcast show -> every episode URL
=========================================================================
An iHeart "podcast" is a show; we enumerate its episodes and emit ONE row per
episode holding the full iheart.com watch link:

    SHOW                        URL                                              PLAT
    Baby, This is Keke Palmer   https://www.iheart.com/podcast/<slug>-<podId>/   iHeart
    10|                                episode/<ep-slug>-<epId>
    ... (one row per episode) ...

How (iHeart's public JSON API — no login, no key)
-------------------------------------------------
  • Title -> podcast: GET api.iheart.com/api/v3/search/all?keywords=<title>
    &podcast=true  (best case-insensitive match on results.podcasts).
  • Podcast -> slug/name: GET api.iheart.com/api/v3/podcast/podcasts/<id>
    (returns the URL `slug`, e.g. "272-baby-this-is-keke-palmer").
  • Podcast -> episodes: GET .../podcast/podcasts/<id>/episodes?limit=100 ,
    20|    paged via the opaque `links.next` token (?pageKey=...). Each episode
    carries an `id` + `title` only — iHeart does NOT return a per-episode
    slug/url, so we build the canonical path ourselves:
        /podcast/<podcast-slug>-<podId>/episode/<title-slug>-<episodeId>
    The trailing episode id is what actually resolves the page (iHeart 301s any
    slug to the live title's slug), so the id makes each row unique and durable.
  • URL hint: paste any iheart.com podcast/episode link (or its trailing
    numeric ids) to skip the search.

Slug rule (matches iHeart's live canonical): unicode-fold accents (Monáe ->
    30|monae), lowercase, DROP intra-word punctuation (apostrophes, periods, ?, !),
then turn every other non-alphanumeric run into a single hyphen. iHeart freezes
a slug at publish time, but because the id resolves the page, the exact slug
never has to match a months-old snapshot to be correct + unique.

Usage
-----
    python3 iheart_episode_identifier.py --title "Baby, this is Keke Palmer"
    python3 iheart_episode_identifier.py --url https://www.iheart.com/podcast/272-baby-this-is-keke-palmer-108064458/
    python3 iheart_episode_identifier.py                     # interactive
"""

import argparse
import csv
import difflib
import gzip
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
API = "https://api.iheart.com/api/v3"
WEB = "https://www.iheart.com"
PLATFORM = "iHeart"
PAGE_LIMIT = 100          # episodes per API page
MAX_PAGES = 120           # safety cap (=> up to 12k episodes)

# podcast path segment: "<slug>-<podcastId>"  (trailing digits = id)
_POD_ID = re.compile(r"/podcast/([^/?#]+)")
_EP_ID = re.compile(r"/episode/([^/?#]+)")
_TRAIL_ID = re.compile(r"-(\d+)$")


# ── tiny helpers ──────────────────────────────────────────────────────────────
try:
    from match_gate import is_relevant           # shared over-match relevance floor
except ImportError:                              # keep the sibling importable
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from match_gate import is_relevant


def tokens(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def similarity(a, b):
    """0..1 title similarity, case-folded & robust to word order/typos."""
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    if ta == tb or ta <= tb or tb <= ta:
        return 1.0
    jacc = len(ta & tb) / len(ta | tb)
    ratio = difflib.SequenceMatcher(
        None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return max(jacc, ratio)


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def get_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {}


def slugify(title):
    """iHeart's canonical episode/title slug.

    Fold accents to ASCII (Monáe->monae, Jaé->jae), lowercase, DROP intra-word
    punctuation (apostrophes / periods / ? / ! / quotes — so 'H.E.R.'->'her',
    "I'm"->'im'), then collapse every other non-alphanumeric run to one hyphen.
    """
    t = unicodedata.normalize("NFKD", title or "")
    t = t.encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[\u2019\u2018'`.?!\"]", "", t)        # deleted, no hyphen
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t


# ── discovery + enumeration ───────────────────────────────────────────────────
def search_podcast(title):
    """(podcastId, slug, name) for the best case-insensitive podcast match."""
    q = urllib.parse.quote(title or "")
    url = (f"{API}/search/all?keywords={q}&bundle=false&keyword=false"
           f"&station=false&artist=false&track=false&playlist=false"
           f"&podcast=true&maxRows=20")
    j = get_json(url)
    pods = ((j.get("results") or {}).get("podcasts")) or []
    pods = [p for p in pods if p.get("id")]
    if not pods:
        return None, None, None
    best = max(pods, key=lambda p: similarity(title, p.get("title", "")))
    if not is_relevant(title, best.get("title", "")):   # relevance floor (no dump)
        return None, None, None
    return best.get("id"), best.get("slug"), best.get("title")


def podcast_info(pid):
    """(slug, name) for a podcast id."""
    j = get_json(f"{API}/podcast/podcasts/{pid}")
    return j.get("slug"), j.get("title")


def podcast_episodes(pid):
    """[(episodeId, title, podcastSlug)] for every episode (paged)."""
    eps, nxt, pages = [], None, 0
    while pages < MAX_PAGES:
        url = f"{API}/podcast/podcasts/{pid}/episodes?limit={PAGE_LIMIT}"
        if nxt:
            url += "&pageKey=" + urllib.parse.quote(nxt)
        j = get_json(url)
        data = j.get("data") or []
        if not data:
            break
        for e in data:
            eps.append((e.get("id"), e.get("title", ""), e.get("podcastSlug")))
        nxt = (j.get("links") or {}).get("next")
        pages += 1
        if not nxt:
            break
    return eps


def episode_url(pod_slug, pod_id, title, episode_id):
    """Build the canonical iheart.com watch URL for one episode."""
    ep_slug = slugify(title)
    tail = f"{ep_slug}-{episode_id}" if ep_slug else str(episode_id)
    return f"{WEB}/podcast/{pod_slug}-{pod_id}/episode/{tail}"


def parse_url(url):
    """(podcastId, episodeId) from an iheart.com link (episodeId may be None)."""
    if not url:
        return None, None
    pod_id = ep_id = None
    m = _POD_ID.search(url)
    if m:
        t = _TRAIL_ID.search(m.group(1))
        if t:
            pod_id = t.group(1)
    m = _EP_ID.search(url)
    if m:
        t = _TRAIL_ID.search(m.group(1))
        if t:
            ep_id = t.group(1)
    # bare numeric id fallback
    if not pod_id and re.fullmatch(r"\d+", url.strip()):
        pod_id = url.strip()
    return pod_id, ep_id


# ── reusable resolver (imported by streamscout.py) ────────────────────────────
def _rows(pod_slug, pod_id, episodes):
    rows = []
    for eid, etitle, eslug in episodes:
        if not eid:
            continue
        u = episode_url(pod_slug, pod_id, etitle, eid)
        rows.append({"season": "", "episode": "", "title": etitle or "",
                     "identifier": u, "watch_url": u})
    return rows


def resolve(title=None, url=None, kind="series", seasons=None):
    """Resolve an iHeart podcast to every episode's iheart.com URL.

    Returns (show_name, rows); each row is
        {season:"", episode:"", title:<episode name>,
         identifier:"https://www.iheart.com/podcast/<slug>-<id>/episode/<ep>-<eid>",
         watch_url:<same>}
    `seasons` is accepted for interface parity but ignored (podcasts are a flat
    episode list). All anonymous — no login, no API key.
    """
    # 1) URL hint
    if url:
        pod_id, ep_id = parse_url(url)
        if pod_id:
            slug, name = podcast_info(pod_id)
            if ep_id and kind == "movie":
                # single pasted episode — keep the pasted link verbatim
                return (name or title or "",
                        [{"season": "", "episode": "", "title": "",
                          "identifier": url, "watch_url": url}])
            eps = podcast_episodes(pod_id)
            return (name or title or "", _rows(slug, pod_id, eps))

    # 2) movie by title -> single best-matching episode of the show
    if kind == "movie" and title:
        pid, slug, name = search_podcast(title)
        if not pid:
            return (title or "", [])
        if not slug:
            slug, name = podcast_info(pid)
        eps = podcast_episodes(pid)
        if not eps:
            return (name or title or "", [])
        best = max(eps, key=lambda e: similarity(title, e[1]))
        u = episode_url(slug, pid, best[1], best[0])
        return (name or title or "",
                [{"season": "", "episode": "", "title": best[1],
                  "identifier": u, "watch_url": u}])

    # 3) series by title -> podcast -> all episodes
    if title:
        pid, slug, name = search_podcast(title)
        if not pid:
            return (title or "", [])
        if not slug:
            slug, name = podcast_info(pid)
        eps = podcast_episodes(pid)
        return (name or title or "", _rows(slug, pid, eps))

    return (title or "", [])


# ── main (standalone CLI) ─────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="iHeart podcast -> every episode URL (public iHeart API).")
    ap.add_argument("--type", choices=["movie", "series"], default="series")
    ap.add_argument("--title")
    ap.add_argument("--url", help="an iheart.com podcast/episode URL")
    ap.add_argument("--seasons", help="(accepted but ignored; flat episode list)")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    kind, title = args.type, args.title
    if not args.url and not title:
        title = ask("What iHeart podcast?: ")
    if not args.url and not title:
        print("Need a --title or --url."); return 1

    try:
        show, rows = resolve(title=title, url=args.url, kind=kind)
    except Exception as e:  # noqa: BLE001
        print(f"  ! Search/fetch failed: {e!r}"); return 2
    if not rows:
        print("  ! Not found on iHeart. Try --url with the podcast link.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "iheart").lower()).strip("-")
    out = os.path.join(args.outdir, f"iheart_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "URL", "PLATFORM", "SEASON"])
        for r in rows:
            w.writerow([show, r["identifier"], PLATFORM, ""])

    print(f"\n{show}  ->  {len(rows)} episode(s) on iHeart")
    for r in rows[:15]:
        print(f"   {r['identifier']}   {r['title'][:55]}")
    if len(rows) > 15:
        print(f"   ... and {len(rows) - 15} more (see CSV)")
    print(f"\nCSV: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
