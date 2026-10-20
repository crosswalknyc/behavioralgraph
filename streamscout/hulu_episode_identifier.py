#!/usr/bin/env python3
"""
hulu_episode_identifier.py  —  Hulu title -> episode watch UUIDs (by season)
============================================================================
The reverse of the bulk cache builder (`hulu_title_identifier.py`): given a
show/movie *title* (or a Hulu URL / UUID), return the per-episode playable
UUIDs grouped by season -- the same granularity the Netflix tool produces.

How it works (NO login required — Hulu serves this server-side from the US)
--------------------------------------------------------------------------
1. Resolve the title to a Hulu page:
     - a `/watch/<uuid>` URL 302-redirects to the canonical
       `/series/<slug>-<uuid>` (series) or is itself a `/movie/<uuid>`;
     - a bare title is matched against Hulu's own sitemap (every series and
       movie page Hulu lists), with a web search only as the last resort.
   Hulu serves the US only: from any other address hulu.com redirects to
   disneyplus.com, and the resolver raises DiscoveryUnavailable instead of
   reading that as 'not on Hulu'.
2. Fetch the page and read its embedded `__NEXT_DATA__` JSON, which contains
   the FULL episode tree: each episode dict carries
       {"type":"episode","id":<watch-uuid>,"season":N,"number":E,
        "name":..., "seriesName":...}
   The episode `id` is exactly the `/watch/<uuid>` id.
3. Filter to the requested season(s), sort, and write a CSV to the Desktop.

Usage
-----
    python3 hulu_episode_identifier.py --type series --title "The Bear" --seasons 1,2
    python3 hulu_episode_identifier.py --type series \
        --url https://www.hulu.com/watch/5a706a72-e590-4576-a3ca-42f298edc9db
    python3 hulu_episode_identifier.py --type movie --title "Prey"
    python3 hulu_episode_identifier.py            # fully interactive
"""

import argparse
import csv
import gzip
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_NEXT = re.compile(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_LD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S)

# Hulu's own catalog listing (2026-10-19). The search engines stopped being a
# discovery path: DuckDuckGo answers automated lookups with its anomaly page
# and Bing no longer indexes hulu.com/series pages. Hulu publishes every
# series and movie page in its sitemaps, so a title resolves against the
# catalog itself: no search engine, and a miss there is a real miss.
SITEMAP_URLS = {
    "series": "https://www.hulu.com/sitemap_en-us_series.xml",
    "movie": "https://www.hulu.com/sitemap_en-us_movies.xml",
}
_SITEMAP_TTL_S = 6 * 3600
_SITEMAP_CACHE = {}   # kind -> (fetched_at, [page urls])


class DiscoveryUnavailable(Exception):
    """Hulu could not be checked from this machine: hulu.com answered from
    another host (outside the US every hulu.com address redirects to
    disneyplus.com), or no discovery source answered at all (the search
    engines refused the lookup). This is NOT a verdict on the title. A
    caller records the platform as unverified and keeps whatever placement
    it already holds; it never reads this as 'not on Hulu'."""
    unverified = True

    def __init__(self, reason):
        self.reason = str(reason or "")
        super().__init__("Hulu cannot be checked from here: " + self.reason)


def on_hulu(url):
    """True when the address is on hulu.com (the geo redirect lands elsewhere)."""
    host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    return host == "hulu.com" or host.endswith(".hulu.com")


def _require_hulu(final_url):
    if final_url and not on_hulu(final_url):
        host = urllib.parse.urlparse(final_url).hostname or final_url
        raise DiscoveryUnavailable(
            f"hulu.com answered from {host} (Hulu serves the US only; check from a US address)")


# ── tiny helpers ──────────────────────────────────────────────────────────────
def tokens(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def parse_seasons(raw):
    """'all' -> None; '1,3-5' -> {1,3,4,5}."""
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


def fetch(url, data=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return r.getcode(), r.geturl(), raw.decode("utf-8", "replace")


# ── title -> Hulu URL discovery ───────────────────────────────────────────────
def _hulu_links(html, kind):
    ents = "series" if kind == "series" else "movie"
    # DDG/Bing wrap targets in redirect params; catch both encoded + plain forms
    enc = re.findall(rf'https?%3A%2F%2Fwww\.hulu\.com%2F{ents}%2F[^"&]+', html)
    out = [urllib.parse.unquote(x) for x in enc]
    out += re.findall(rf'https?://www\.hulu\.com/{ents}/[a-z0-9-]+', html)
    # keep only ones ending in a uuid, de-dup preserving order
    seen, uniq = set(), []
    for u in out:
        m = re.search(UUID, u)
        if not m:
            continue
        u = u[:m.end()]
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


_BLOCK_MARKERS = ("anomaly", "captcha", "unusual traffic", "verify you are human",
                  "are you a robot", "bot detection")


def _search_answered(code, html):
    """True when an engine returned a results page (even one without a Hulu
    link); False for a refusal (DuckDuckGo's 202 anomaly page, 403, 429,
    an interstitial)."""
    if code in (202, 403, 429):
        return False
    low = (html or "").lower()
    if "b_algo" in low or "result__a" in low or "result-link" in low:
        return True
    return not any(m in low for m in _BLOCK_MARKERS)


def search_hulu_url(title, kind, report=None):
    """Best-effort title -> hulu.com/(series|movie)/<slug>-<uuid> via web search.

    ``report`` (a dict, optional) receives how the engines behaved:
    ``answered`` = results pages seen, ``blocked`` = refusals. A caller that
    gets None with answered == 0 knows nothing was looked up; the title was
    not searched, let alone found missing."""
    ents = "series" if kind == "series" else "movie"
    queries = [f"{title} hulu {ents}", f'"{title}" site:hulu.com/{ents}',
               f"{title} hulu"]
    engines = [
        ("https://html.duckduckgo.com/html/", True),   # POST q
        ("https://lite.duckduckgo.com/lite/", True),
        ("https://www.bing.com/search?q=", False),     # GET
    ]
    if report is None:
        report = {}
    report.setdefault("answered", 0)
    report.setdefault("blocked", 0)
    for q in queries:
        for base, is_post in engines:
            for attempt in range(2):
                try:
                    if is_post:
                        code, _, html = fetch(
                            base, data=urllib.parse.urlencode({"q": q}).encode())
                    else:
                        code, _, html = fetch(base + urllib.parse.quote(q))
                    links = _hulu_links(html, kind)
                    if links:
                        report["answered"] += 1
                        # prefer a slug whose tokens best cover the title
                        want = set(tokens(title))
                        links.sort(key=lambda u: -len(
                            want & set(tokens(u.rsplit("/", 1)[-1]))))
                        return links[0]
                    if _search_answered(code, html):
                        report["answered"] += 1
                        break       # this engine answered; no reason to retry it
                    report["blocked"] += 1
                    time.sleep(1.5 * (attempt + 1))
                except Exception:
                    report["blocked"] += 1
                    time.sleep(1.0 * (attempt + 1))
    return None


def _slug_tokens(page_url):
    """Tokens of a catalog page slug, the trailing uuid dropped:
    .../series/gilmore-girls-e6fd3925-... -> ['gilmore', 'girls']."""
    slug = page_url.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(UUID + r"$", "", slug).strip("-")
    return tokens(slug.replace("-", " "))


def sitemap_urls(kind, force=False):
    """Every hulu.com/(series|movie)/ page Hulu lists in its sitemap, cached
    for a few hours. Raises DiscoveryUnavailable when hulu.com does not
    answer from here (the geo redirect) or the sitemap cannot be read."""
    kind = "movie" if kind == "movie" else "series"
    hit = _SITEMAP_CACHE.get(kind)
    if hit and not force and time.time() - hit[0] < _SITEMAP_TTL_S:
        return hit[1]
    try:
        code, final, body = fetch(SITEMAP_URLS[kind], timeout=45)
    except DiscoveryUnavailable:
        raise
    except Exception as e:
        raise DiscoveryUnavailable(f"sitemap fetch failed ({type(e).__name__})")
    _require_hulu(final)
    urls = re.findall(r"<loc>\s*(https?://[^<\s]+)\s*</loc>", body or "")
    ents = "series" if kind == "series" else "movie"
    urls = [u for u in urls if f"/{ents}/" in u]
    if code != 200 or not urls:
        raise DiscoveryUnavailable(f"sitemap answered {code} with no {ents} pages")
    _SITEMAP_CACHE[kind] = (time.time(), urls)
    return urls


def sitemap_lookup_url(title, kind):
    """Title -> catalog page from Hulu's sitemap. An exact token match wins
    ('gilmore girls' -> gilmore-girls, never gilmore-girls-en-espanol); a
    page whose slug carries every title token in order is the fallback
    only when exactly one such page exists. None = Hulu lists no such
    title (a real miss). Raises DiscoveryUnavailable when the catalog
    cannot be read from here."""
    want = tokens(title)
    if not want:
        return None
    # Hulu runs some titles together in the slug ('Nip/Tuck' is
    # /series/niptuck-...), so the joined letters count as the same name
    want_joined = ''.join(want)
    exact, supersets = [], []
    for u in sitemap_urls(kind):
        got = _slug_tokens(u)
        got_joined = ''.join(got)
        if got == want or got_joined == want_joined:
            exact.append(u)
        elif len(got) > len(want) and got[:len(want)] == want:
            supersets.append(u)
        elif len(got) > 1 and got[0] == want_joined:
            supersets.append(u)      # the run-together title with a suffix ('niptuck-en-espanol')
    if exact:
        return exact[0]
    if len(supersets) == 1:
        return supersets[0]
    return None


def normalize_input_url(s):
    """Accept a full Hulu URL or a bare UUID; return a fetchable Hulu URL."""
    s = (s or "").strip()
    if not s:
        return None
    m = re.fullmatch(UUID, s)
    if m:
        return "https://www.hulu.com/watch/" + s
    if "hulu.com/" in s:
        if not s.startswith("http"):
            s = "https://" + s.lstrip("/")
        return s
    return None


HULU_CACHE = os.path.expanduser("~/Desktop/hulu_title_cache.json")


def cache_lookup_url(title, kind, cache_path=HULU_CACHE):
    """Find a Hulu UUID for `title` in the clickstream cache (title->UUID).

    Any episode UUID of a series redirects to the full series page, so one
    match is enough. Prefers an exact token match, else a superset match.
    """
    path = os.path.expanduser(cache_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            cache = json.load(f)
    except Exception:
        return None
    want = set(tokens(title))
    if not want:
        return None
    want_movie = (kind == "movie")
    exact, near = None, None
    for uid, rec in cache.items():
        rec = rec or {}
        raw = rec.get("title")
        if not raw or not re.fullmatch(UUID, uid or ""):
            continue
        is_movie = (rec.get("kind") or "").lower() == "movie"
        if is_movie != want_movie:
            continue
        got = set(tokens(raw))
        if got == want:
            exact = uid
            break
        if want <= got and near is None:  # title is a subset of the cached name
            near = uid
    uid = exact or near
    return ("https://www.hulu.com/watch/" + uid) if uid else None


# ── page -> episode tree ──────────────────────────────────────────────────────
def series_name_from_page(html, data):
    for m in _LD.finditer(html):
        try:
            ld = json.loads(m.group(1))
        except Exception:
            continue
        if isinstance(ld, dict) and ld.get("name") and \
                str(ld.get("@type", "")).lower() in ("tvseries", "movie"):
            return ld["name"]
    q = (data.get("props", {}).get("pageProps", {}) or {}).get("pageTitle", "")
    q = re.sub(r"^watch\s+", "", q, flags=re.I)
    q = re.sub(r"\s+streaming online.*$", "", q, flags=re.I)
    return q.strip() or None


def collect_episodes(data, series_name):
    """Walk __NEXT_DATA__ and pull episode dicts for THIS series."""
    want = set(tokens(series_name)) if series_name else None
    eps, seen = [], set()

    def ok(o):
        if str(o.get("type", "")).lower() != "episode":
            return False
        if not isinstance(o.get("season"), int):
            return False
        if not (isinstance(o.get("id"), str) and re.fullmatch(UUID, o["id"])):
            return False
        if want:  # exclude "you may also like" episodes from other shows
            got = set(tokens(o.get("seriesName") or ""))
            if want and got and not (want & got):
                return False
        return True

    def walk(o):
        if isinstance(o, dict):
            if ok(o) and o["id"] not in seen:
                seen.add(o["id"])
                eps.append({
                    "season": o["season"],
                    "number": o.get("number"),
                    "name": o.get("name") or "",
                    "id": o["id"],
                    "series": o.get("seriesName") or series_name or "",
                })
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    eps.sort(key=lambda e: (e["season"], e["number"] if isinstance(e["number"], int) else 0))
    return eps


# ── reusable resolver (imported by title_lookup.py) ───────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None):
    """Resolve a Hulu title to episode watch UUIDs.

    Returns (show_name, rows) where each row is a dict:
        {season, episode, title, identifier, watch_url}
    (season/episode/title are "" for a movie).  `seasons` is a set or None (all).
    Discovery order: explicit url/uuid  >  local clickstream cache  >  Hulu's
    own sitemap  >  web search (last resort, only when the sitemap could not
    be read). Each source is tried only when the one before found no page.

    Returns (title, []) only for a REAL miss: a source that answered showed
    no such title. Raises DiscoveryUnavailable when Hulu could not be checked
    from here at all (the geo redirect to disneyplus.com, or every source
    refused), so a caller never mistakes 'could not look' for 'not there'.
    """
    kind = kind or "series"

    def _page(page_url):
        """Fetch a candidate page; None when it is not a usable Hulu page.
        The geo redirect raises: nothing on hulu.com is reachable then."""
        try:
            code, final, html = fetch(page_url)
        except Exception:
            return None
        _require_hulu(final)
        if code == 200 and html:
            return code, final, html
        return None

    got = None
    if url:
        u = normalize_input_url(url)
        if u:
            got = _page(u)
    if got is None:
        cu = cache_lookup_url(title, kind)
        if cu:
            got = _page(cu)
    catalog_answered = False
    if got is None:
        try:
            su = sitemap_lookup_url(title, kind)
            catalog_answered = True
        except DiscoveryUnavailable as e:
            if "answered from" in e.reason:
                raise       # the geo redirect: nothing on hulu.com is reachable from here
            su = None
        if su:
            got = _page(su)
    if got is None and not catalog_answered:
        # the catalog could not be read from here; the engines are the last resort
        report = {}
        su = search_hulu_url(title, kind, report=report)
        if su:
            got = _page(su)
        if got is None and not report.get("answered"):
            raise DiscoveryUnavailable(
                "hulu.com is not reachable from here and the search engines refused the lookup")
    if got is None:
        return (title or "", [])
    code, final, html = got

    if "/movie/" in final:
        kind = "movie"
    m = _NEXT.search(html)
    if not m:
        return (title or "", [])
    data = json.loads(m.group(1))
    show = series_name_from_page(html, data) or (title or "")

    rows = []
    if kind == "movie":
        mm = re.search(UUID, final)
        if mm:
            vid = mm.group(0)
            rows.append({"season": "", "episode": "", "title": "",
                         "identifier": vid,
                         "watch_url": f"https://www.hulu.com/watch/{vid}"})
    else:
        for e in collect_episodes(data, show):
            if seasons is not None and e["season"] not in seasons:
                continue
            rows.append({
                "season": str(e["season"]),
                "episode": str(e["number"]) if e["number"] is not None else "",
                "title": e["name"], "identifier": e["id"],
                "watch_url": f"https://www.hulu.com/watch/{e['id']}"})
    return (show, rows)


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Hulu title -> episode watch UUIDs (grouped by season).")
    ap.add_argument("--type", choices=["movie", "series"])
    ap.add_argument("--title")
    ap.add_argument("--seasons", help="e.g. 1 | 1,3 | 1-4 | all")
    ap.add_argument("--url", help="a Hulu series/movie/watch URL or a bare UUID "
                                  "(skips title search)")
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
        show, rows = resolve(title=title, url=args.url, kind=kind, seasons=seasons)
    except DiscoveryUnavailable as e:
        print(f"  ! {e}"); return 3
    except Exception as e:
        print(f"  ! Fetch/parse failed: {e!r}"); return 2
    if not rows:
        print("  ! Couldn't find episodes. Re-run with --url <hulu url or uuid> "
              "(paste any hulu.com/watch|series|movie link, or a bare UUID).")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "hulu").lower()).strip("-")
    out = os.path.join(args.outdir, f"hulu_{kind or 'series'}_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "SEASON", "EPISODE", "EPISODE_TITLE",
                    "WATCH_ID", "WATCH_URL", "PLATFORM"])
        for r in rows:
            w.writerow([show, r["season"], r["episode"], r["title"],
                        r["identifier"], r["watch_url"], "Hulu"])

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
