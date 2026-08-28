#!/usr/bin/env python3
"""
disney_episode_identifier.py  —  Disney+ title -> episode play UUIDs (by season)
================================================================================
Given a show/movie *title* (or a Disney+ URL / UUID), return the per-episode
playable UUIDs grouped by season — the same granularity the Netflix/HBO MAX
tools produce.  The playable id is exactly the `/play/<uuid>` segment.

How it works (NO login required — Disney+ serves this server-side from the US)
------------------------------------------------------------------------------
1. Resolve the title to a Disney+ entity page:
     - a `/play/<uuid>` URL 30x-redirects to the canonical
       `/browse/entity-<entityId>` details page;
     - a bare title is looked up with a lightweight web search that returns the
       `disneyplus.com/browse/entity-...` (or `/shows/<slug>/<id>`) link.
2. Fetch the page and read its embedded Next.js `__NEXT_DATA__` JSON.  The
   `stitchDocument` carries the FULL episode tree even when logged out:
     - the SELECTED season's episodes live under an "episodes" module, and
     - EVERY OTHER season lives under that same module's "seoSeasons" list.
   Each episode is an `ImageCard` whose
       `_id`   == the `/play/<uuid>` playable id, and
       `title` == "S2:E1 <episode name>"   (season + number are in the title).
3. Filter to the requested season(s), sort, and (as a CLI) write a Desktop CSV.

Usage
-----
    python3 disney_episode_identifier.py --type series \
        --title "Percy Jackson and the Olympians" --seasons 1,2
    python3 disney_episode_identifier.py --type series \
        --url https://www.disneyplus.com/play/a1c72e34-cfba-4355-a0b3-218b904e2af9
    python3 disney_episode_identifier.py --type movie --title "Elemental"
    python3 disney_episode_identifier.py            # fully interactive
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
# persistent Firefox profile for the OPTIONAL logged-in discovery fallback
PROFILE_DIR = os.path.expanduser("~/.disney_scraper_profile_firefox")
_NEXT = re.compile(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_LD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S)
# episode ImageCard titles look like "S2:E1 I Play Dodgeball With Cannibals"
_EP_TITLE = re.compile(r'^\s*S(\d+)\s*:\s*E(\d+)\b[\s.:-]*(.*)$', re.I)


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


# ── title -> Disney+ URL discovery ────────────────────────────────────────────
def _disney_links(html):
    """Pull disneyplus.com entity/show/movie links (encoded + plain) from SERP."""
    pats = [
        r'disneyplus\.com%2Fbrowse%2Fentity-[0-9a-f-]+',
        r'disneyplus\.com/browse/entity-[0-9a-f-]+',
        r'disneyplus\.com%2F[a-z]{0,3}%?2?F?(?:shows|movies|series|movie)%2F[^"&]+',
        r'disneyplus\.com/(?:[a-z]{2}/)?(?:shows|movies|series|movie)/[A-Za-z0-9/_-]+',
    ]
    out = []
    for p in pats:
        for m in re.findall(p, html):
            u = urllib.parse.unquote(m)
            if not u.startswith("http"):
                u = "https://" + u.lstrip("/")
            out.append(u)
    # de-dup, keep order, prefer browse/entity first
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    uniq.sort(key=lambda u: (0 if "/browse/entity-" in u else 1))
    return uniq


def search_disney_url(title, kind):
    """Best-effort title -> disneyplus.com entity/show link via web search."""
    noun = "movie" if kind == "movie" else "series"
    queries = [f"{title} disney plus {noun}",
               f"{title} disneyplus.com",
               f"{title} disney plus watch"]
    engines = [
        ("https://lite.duckduckgo.com/lite/", True),
        ("https://html.duckduckgo.com/html/", True),
        ("https://www.bing.com/search?q=", False),
    ]
    for q in queries:
        for base, is_post in engines:
            for attempt in range(2):
                try:
                    if is_post:
                        _, _, html = fetch(
                            base, data=urllib.parse.urlencode({"q": q}).encode())
                    else:
                        _, _, html = fetch(base + urllib.parse.quote(q))
                    links = _disney_links(html)
                    if links:
                        want = set(tokens(title))
                        # prefer a link whose slug best covers the title tokens
                        links.sort(key=lambda u: (
                            0 if "/browse/entity-" in u else 1,
                            -len(want & set(tokens(u.rsplit("/", 1)[-1])))))
                        return links[0]
                    time.sleep(0.8 * (attempt + 1))
                except Exception:
                    time.sleep(0.8 * (attempt + 1))
    return None


def normalize_input_url(s):
    """Accept a full Disney+ URL or a bare UUID; return a fetchable URL."""
    s = (s or "").strip()
    if not s:
        return None
    if re.fullmatch(UUID, s):
        return "https://www.disneyplus.com/play/" + s
    if "disneyplus.com/" in s:
        if not s.startswith("http"):
            s = "https://" + s.lstrip("/")
        return s
    return None


# ── persistent title -> entity-URL cache ──────────────────────────────────────
# A confirmed (title, kind) -> canonical /browse/entity-<id> URL. Repeat lookups
# (e.g. a client asking for the same show again) skip discovery entirely, and it
# lets a title that once needed the logged-in search resolve no-login next time.
DISNEY_CACHE = os.path.expanduser("~/Desktop/disney_title_cache.json")


def _cache_key(title, kind):
    return f"{'movie' if kind == 'movie' else 'series'}:" + " ".join(tokens(title))


def cache_get(title, kind, path=DISNEY_CACHE):
    if not title or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f).get(_cache_key(title, kind))
    except Exception:
        return None


def cache_put(title, kind, url, path=DISNEY_CACHE):
    if not (title and url):
        return
    try:
        data = {}
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
        data[_cache_key(title, kind)] = url
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def discover_url(title, kind):
    """No-login title -> entity URL: cache first, then web search. (None if miss.)"""
    if not title:
        return None
    cu = cache_get(title, kind)
    if cu:
        return cu
    return search_disney_url(title, kind or "series")


# ── page -> episode tree ──────────────────────────────────────────────────────
def series_name_from_page(html, data):
    # 1) prefer clean names embedded in the stitchDocument, in priority order:
    #    seriesTitle (series episodes module)  >  the hero titleVisual/background
    #    image alt-text (works for movies too, where seriesTitle is absent).
    series_titles, alt_titles = [], []

    def walk(o):
        if isinstance(o, dict):
            st = o.get("seriesTitle")
            if isinstance(st, str) and st.strip():
                series_titles.append(st.strip())
            for imgk in ("titleVisual", "backgroundImage"):
                img = o.get(imgk)
                if isinstance(img, dict) and isinstance(img.get("alt"), str) \
                        and img["alt"].strip():
                    alt_titles.append(img["alt"].strip())
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    try:
        walk(data.get("props", {}).get("pageProps", {}).get("stitchDocument", {}))
    except Exception:
        pass
    if series_titles:
        return series_titles[0]
    if alt_titles:
        return alt_titles[0]
    # 2) ld+JSON TVSeries/Movie name
    for m in _LD.finditer(html):
        try:
            ld = json.loads(m.group(1))
        except Exception:
            continue
        cands = ld if isinstance(ld, list) else [ld]
        for c in cands:
            if isinstance(c, dict) and c.get("name") and \
                    str(c.get("@type", "")).lower() in ("tvseries", "movie"):
                return c["name"]
        if isinstance(ld, dict) and isinstance(ld.get("@graph"), list):
            for c in ld["@graph"]:
                if isinstance(c, dict) and c.get("name") and \
                        str(c.get("@type", "")).lower() in ("tvseries", "movie"):
                    return c["name"]
    return None


def collect_episodes(data):
    """Walk stitchDocument and pull every episode ImageCard.

    An episode card has a UUID `_id` (== the /play/<uuid>) and a title of the
    form "S<season>:E<number> <name>".  We dedup on the play id.
    """
    eps, seen = [], set()

    def walk(o):
        if isinstance(o, dict):
            _id = o.get("_id")
            ttl = o.get("title")
            if isinstance(_id, str) and re.fullmatch(UUID, _id) and \
                    isinstance(ttl, str):
                m = _EP_TITLE.match(ttl)
                if m and _id not in seen:
                    seen.add(_id)
                    eps.append({
                        "season": int(m.group(1)),
                        "number": int(m.group(2)),
                        "name": m.group(3).strip(),
                        "id": _id,
                    })
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    eps.sort(key=lambda e: (e["season"], e["number"]))
    return eps


def entity_id_from_url(u):
    """A Disney+ MOVIE plays at /play/<entityId>, and /play/<id> canonicalises to
    /browse/entity-<id>; so the movie's own play id is the entity id in the URL.
    (Tiles on the page are 'More Like This' recommendations — never trust those.)
    """
    m = re.search(r"/browse/entity-(" + UUID + ")", u or "")
    if m:
        return m.group(1)
    m = re.search(r"/play/(" + UUID + ")", u or "")
    return m.group(1) if m else None


# ── reusable resolver (imported by title_lookup.py) ───────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None):
    """Resolve a Disney+ title to episode play UUIDs.

    Returns (show_name, rows) where each row is a dict:
        {season, episode, title, identifier, watch_url}
    (season/episode/title are "" for a movie).  `seasons` is a set or None (all).
    `url` may be a disneyplus.com play/browse link or a bare UUID (skips search).
    NO login required — Disney+ embeds the tree in the public entity page.
    """
    pasted_play = None
    candidates = []
    explicit_url = False
    if url:
        u = normalize_input_url(url)
        if u:
            candidates.append(u)
            explicit_url = True
            m = re.search(r"/play/(" + UUID + ")", u)
            if m:
                pasted_play = m.group(1)
    # Only spend time on discovery (cache -> rate-limit-prone web search) when no
    # URL was handed in.
    if not candidates:
        du = discover_url(title, kind or "series")
        if du:
            candidates.append(du)

    code = final = html = None
    for page_url in candidates:
        try:
            code, final, html = fetch(page_url)
        except Exception:
            continue
        if code == 200 and html:
            break
        code = final = html = None
    if not html:
        return (title or "", [])

    m = _NEXT.search(html)
    if not m:
        return (title or "", [])
    data = json.loads(m.group(1))
    sd = data.get("props", {}).get("pageProps", {}).get("stitchDocument", {})
    show = series_name_from_page(html, data) or (title or "")

    rows = []
    if kind == "movie":
        # The movie's play id == its entity id (the id in the final /browse/entity
        # URL). Prefer a pasted /play/<uuid>, else read it off the canonical URL.
        vid = pasted_play or entity_id_from_url(final)
        if vid:
            rows.append({"season": "", "episode": "", "title": "",
                         "identifier": vid,
                         "watch_url": f"https://www.disneyplus.com/play/{vid}"})
    else:
        for e in collect_episodes(sd):
            if seasons is not None and e["season"] not in seasons:
                continue
            rows.append({
                "season": str(e["season"]),
                "episode": str(e["number"]),
                "title": e["name"], "identifier": e["id"],
                "watch_url": f"https://www.disneyplus.com/play/{e['id']}"})

    # Remember a confirmed title -> canonical entity URL so the next "run <title>"
    # skips discovery (and a title that once needed login resolves no-login later).
    if rows and title and final and "/browse/entity-" in final:
        cache_put(title, kind or "series", final)
    return (show, rows)


# ── OPTIONAL logged-in discovery fallback (Playwright) ────────────────────────
# The no-login web search finds most titles, but when a client asks out of the
# blue for something obscure and the search rate-limits/misses, we escalate to a
# real logged-in Disney+ session and use Disney's OWN search — its result tiles
# link straight to /browse/entity-<id>, which we then hand to the no-login parse.
# Credentials come from the gitignored .env.local (DISNEY_EMAIL / DISNEY_PASSWORD).
def load_env(path=".env.local"):
    env = {}
    for p in (path, os.path.join(os.path.dirname(__file__), path)):
        if os.path.exists(p):
            for line in open(p):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip())
    return env


def _pw_click(page, sels):
    for sel in sels:
        try:
            el = page.query_selector(sel)
            if el:
                el.click()
                return sel
        except Exception:
            pass
    return None


def _pw_fill(page, sels, val):
    for sel in sels:
        try:
            el = page.query_selector(sel)
            if el:
                el.click()
                el.fill("")
                el.fill(val)
                return sel
        except Exception:
            pass
    return None


def _has_login_form(page):
    for sel in ('input[type="email"]', 'input[type="password"]',
                'input[autocomplete="username"]',
                'input[autocomplete="current-password"]'):
        try:
            if page.query_selector(sel):
                return True
        except Exception:
            pass
    return False


def _logged_in(page):
    u = page.url
    if any(x in u for x in ("/login", "/identity", "auth.", "login.disney")):
        return False
    if "disneyplus.com" not in u:
        return False
    return not _has_login_form(page)


def _into_app(page, want_profile="liz"):
    """Get past a profile gate (edit / who's-watching) into the app."""
    if "edit-profile" in page.url:
        _pw_click(page, ('button:has-text("DONE")', 'button:has-text("Done")'))
        time.sleep(3)
    try:
        el = page.query_selector(f'text="{want_profile}"')
        if el:
            el.click(); time.sleep(3); return
    except Exception:
        pass
    _pw_click(page, ('img[alt]', 'div[role="button"]', 'li button'))
    time.sleep(3)


def _ensure_login(page, email, pw, headed=True):
    page.goto("https://www.disneyplus.com/home", wait_until="domcontentloaded")
    time.sleep(6)
    if _logged_in(page):
        return True
    print("  Logging in to Disney+ (MyDisney) ...")
    page.goto("https://www.disneyplus.com/login", wait_until="domcontentloaded")
    time.sleep(5)
    _pw_fill(page, ('input[type="email"]', 'input[name="email"]',
                    'input[autocomplete="username"]'), email)
    _pw_click(page, ('button:has-text("Continue")', 'button[type="submit"]'))
    time.sleep(5)
    # MyDisney defaults to emailing a one-time code — switch to the password form.
    _pw_click(page, ('button:has-text("Enter password instead")',
                     'a:has-text("Enter password instead")',
                     'a:has-text("password instead")'))
    time.sleep(3)
    _pw_fill(page, ('input[type="password"]',
                    'input[autocomplete="current-password"]'), pw)
    _pw_click(page, ('button:has-text("Log In")', 'button:has-text("Login")',
                     'button:has-text("Continue")', 'button[type="submit"]'))
    deadline = time.time() + (900 if headed else 60)
    warned = False
    while time.time() < deadline:
        time.sleep(3)
        if _logged_in(page):
            return True
        if not warned:
            warned = True
            if headed:
                print("  If Disney+ shows email verification or a 'Who's "
                      "watching' screen, complete it in the window — I'll "
                      "continue automatically.")
            else:
                print("  ! Disney+ login needs a visible window; re-run headed.")
                return False
    return False


def _clean_result_title(aria):
    """Result aria-labels look like
    'Percy Jackson and the Olympians Disney+ Original Rated TV-PG Released 2023...'
    — keep just the leading title."""
    return re.split(r'\s+(?:Disney\+ Original|Rated\b|Released\b|\|)',
                    aria or "", 1)[0].strip()


def _pick_result(results, title, kind):
    """From [(href, aria)] pick the best entity URL for `title`/`kind`."""
    want = tokens(title)
    scored = []
    for href, aria in results:
        rt = tokens(_clean_result_title(aria))
        if not rt:
            continue
        exact = rt == want
        subset = set(want) <= set(rt)
        extra = len(set(rt) - set(want))
        a = aria or ""
        looks_series = bool(re.search(r'TV-\w|Season|Episode|Series', a, re.I))
        looks_movie = bool(re.search(r'Rated (G|PG|PG-13|R|NR)\b', a)) \
            and not re.search(r'TV-', a)
        if kind == "movie":
            kind_pen = 1 if looks_series else 0
        else:
            kind_pen = 1 if (looks_movie and not looks_series) else 0
        scored.append(((0 if exact else 1, kind_pen, 0 if subset else 1, extra),
                       href))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0])
    href = scored[0][1]
    if not href.startswith("http"):
        href = "https://www.disneyplus.com" + href
    return href


def login_discover_url(title, kind="series", headed=True, want_profile="liz"):
    """Authoritative title -> /browse/entity URL via a logged-in Disney+ search.
    Returns a canonical entity URL or None. Requires DISNEY_EMAIL/PASSWORD.
    """
    if not title:
        return None
    env = load_env()
    email, pw = env.get("DISNEY_EMAIL"), env.get("DISNEY_PASSWORD")
    if not email or not pw:
        raise RuntimeError("Missing DISNEY_EMAIL / DISNEY_PASSWORD in .env.local.")
    from playwright.sync_api import sync_playwright  # lazy: only when escalating

    os.makedirs(PROFILE_DIR, exist_ok=True)
    url = None
    with sync_playwright() as p:
        ctx = p.firefox.launch_persistent_context(
            PROFILE_DIR, headless=not headed,
            viewport={"width": 1360, "height": 1000}, user_agent=UA)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            if not _ensure_login(page, email, pw, headed=headed):
                return None
            _into_app(page, want_profile)
            page.goto("https://www.disneyplus.com/home",
                      wait_until="domcontentloaded")
            time.sleep(6)
            _into_app(page, want_profile)
            time.sleep(2)
            # open Disney's own search and type the title
            _pw_click(page, ('a[href*="/browse/search"]', 'a[href*="search"]',
                             '[data-testid="navigation-item-search"]',
                             '[data-testid="icon-search"]'))
            time.sleep(3)
            _pw_fill(page, ('input[type="search"]', 'input[placeholder*="Search"]',
                            'input[aria-label*="Search"]', 'input[type="text"]'),
                     title)
            # let search-as-you-type settle
            for _ in range(12):
                time.sleep(1)
                results = page.evaluate("""() => {
                    const out=[];
                    document.querySelectorAll('a[href*="/browse/entity-"]').forEach(a=>{
                      out.push([a.getAttribute('href')||'',
                                (a.getAttribute('aria-label')||a.textContent||'').trim()]);
                    });
                    return out;
                }""")
                if results:
                    break
            url = _pick_result(results, title, kind)
        finally:
            ctx.close()
    return url


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Disney+ title -> episode play UUIDs (grouped by season).")
    ap.add_argument("--type", choices=["movie", "series"])
    ap.add_argument("--title")
    ap.add_argument("--seasons", help="e.g. 1 | 1,3 | 1-4 | all")
    ap.add_argument("--url", help="a disneyplus.com play/browse/shows URL or a "
                                  "bare UUID (skips title search)")
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
        show, rows = resolve(title=title, url=args.url,
                             kind=kind or "series", seasons=seasons)
    except Exception as e:
        print(f"  ! Fetch/parse failed: {e!r}"); return 2
    if not rows:
        print("  ! Couldn't find episodes. Re-run with --url <disney url or uuid> "
              "(paste any disneyplus.com play/browse link, or a bare UUID).")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "disney").lower()).strip("-")
    out = os.path.join(args.outdir, f"disney_{kind or 'series'}_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "URL", "PRODUCTION", "PLATFORM", "SEASON"])
        for r in rows:
            season = f"Season {r['season']}" if r["season"] else ""
            w.writerow([show, r["identifier"], "", "Disney Plus", season])

    print(f"\nWrote {len(rows)} play id(s) for {show!r}.")
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
