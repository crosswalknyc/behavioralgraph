#!/usr/bin/env python3
"""
max_episode_identifier.py  —  title + season(s) -> episode watch/<uuid>s (Max)
=============================================================================
Max (HBO Max) URLs are opaque UUIDs (`/video/watch/<uuid>`), and the whole
catalog — even browsing episode metadata — is gated behind a login. Scripted
HTTP gets bounced to the marketing site, and a HEADLESS browser never fires the
content API (the SPA throttles background rendering, like Netflix). So this tool
drives a REAL logged-in Firefox (Playwright, persistent profile) to do what you'd
do by hand, then reads Max's own JSON content API:

    log in  ->  warm up the app (captures its x-disco / x-wbd request headers)
            ->  search the title  ->  open the show route
            ->  pull each season's episode collection (pf[seasonNumber]=N)
            ->  read every episode's /video/watch/<uuid>

How the ids map (confirmed via recon)
-------------------------------------
Max's content API (`default.any-amer.prd.api.hbomax.com/cms/...`) is JSON:API.
A show route returns `video` items with `videoType == "EPISODE"` carrying
`seasonNumber`, `episodeNumber`, `name` and an `alternateId`. That `alternateId`
IS the `/video/watch/<uuid>` segment (each episode's edit->route confirms it).
The show route only returns the DEFAULT season; other seasons come from the
episodes collection filtered by `pf[seasonNumber]=<n>`.

Auth is cookie + a set of `x-disco-*` / `x-wbd-*` headers (no bearer token). We
capture those headers from the app's own requests during warm-up and replay the
API with an in-page `fetch(..., {credentials:'include', headers})`.

One-time setup
--------------
Uses a PERSISTENT Firefox profile on disk, so you log in ONCE. The first run
opens a visible window; if Max shows device/email verification or a "Who's
watching" profile screen, complete it there. After that the profile stays
trusted. Because the SPA needs a real (non-headless) render, a window opens on
every run — you can push it to the background.

Credentials come from the gitignored `.env.local` (MAX_EMAIL / MAX_PASSWORD).

Usage
-----
    /opt/homebrew/bin/python3 max_episode_identifier.py \
        --type series --title "House of the Dragon" --seasons 1,2
    /opt/homebrew/bin/python3 max_episode_identifier.py \
        --type series --title "House of the Dragon" --seasons all
    /opt/homebrew/bin/python3 max_episode_identifier.py \
        --type movie  --title "Sinners"
    # skip search with a play.hbomax.com/show/<id> URL:
    ... --url https://play.hbomax.com/show/c68e69d7-9317-428a-a615-cdf8fe5a2e06
"""

import argparse
import csv
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime

from playwright.sync_api import sync_playwright

PROFILE_DIR = os.path.expanduser("~/.max_scraper_profile_firefox")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
API_BASE = "https://default.any-amer.prd.api.hbomax.com"
DEC = "viewingHistory,isFavorite,contentAction,badges"
UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

# request headers we replay (cookies are auto-attached by credentials:'include')
_REPLAY_HEADERS = ("x-disco-client", "x-disco-params", "x-device-info",
                   "x-wbd-ace", "x-wbd-device-consent", "x-wbd-preferred-language",
                   "x-wbd-session-state", "x-wbd-time-zone")


# ── helpers ───────────────────────────────────────────────────────────────────
def load_env(path=".env.local") -> dict:
    env = {}
    for p in (path, os.path.join(os.path.dirname(__file__), path),
              os.path.join(os.path.dirname(os.path.dirname(__file__)), path)):
        if os.path.exists(p):
            for line in open(p):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip())
    return env


def tokens(s: str):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def parse_seasons(raw: str):
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


# ── JSON:API parsing ──────────────────────────────────────────────────────────
def _index(data):
    return {(it.get("type"), it.get("id")): it for it in data.get("included", [])}


def _watch_id(idx, video):
    """Return the /video/watch/<uuid> segment for an episode/movie video."""
    at = video.get("attributes", {})
    # authoritative: follow edit -> route url; fall back to alternateId
    editref = video.get("relationships", {}).get("edit", {}).get("data")
    if editref:
        ed = idx.get((editref["type"], editref["id"]))
        if ed:
            for rr in ed.get("relationships", {}).get("routes", {}).get("data", []):
                rt = idx.get((rr["type"], rr["id"]))
                url = rt and rt.get("attributes", {}).get("url")
                if url and "/video/watch/" in url:
                    return url.rsplit("/", 1)[-1]
    return at.get("alternateId")


def parse_episodes(data):
    """[(season, episode, name, watch_uuid)] for videoType==EPISODE."""
    idx = _index(data)
    out = []
    for it in data.get("included", []):
        if it.get("type") != "video":
            continue
        at = it.get("attributes", {})
        if at.get("videoType") != "EPISODE":
            continue
        wid = _watch_id(idx, it)
        if wid:
            out.append((at.get("seasonNumber"), at.get("episodeNumber"),
                        at.get("name") or "", wid))
    out.sort(key=lambda r: ((r[0] or 0), (r[1] or 0)))
    return out


def parse_movie(data, want_name=None):
    """Return (name, watch_uuid) for the feature film, or (None, None).

    Max models a movie as a single primary video under the show (typed EPISODE
    or MOVIE), alongside TRAILER clips and EXTRA/PROMO featurettes (whose ids are
    NOT real /video/watch UUIDs). So we take the first video whose watch id is a
    real UUID and whose type isn't a trailer/extra, preferring a name match.
    """
    idx = _index(data)
    want = tokens(want_name) if want_name else None
    feats = []
    for it in data.get("included", []):
        if it.get("type") != "video":
            continue
        at = it.get("attributes", {})
        if at.get("videoType") in ("TRAILER", "SHORT_PREVIEW", "EXTRA",
                                   "CLIP", "PROMO", "BONUS"):
            continue
        wid = _watch_id(idx, it)
        if not wid or not re.fullmatch(UUID_RE, wid):
            continue  # EXTRA/PROMO ids like "PROM736793" aren't watch pages
        feats.append((at.get("name") or "", wid))
    if not feats:
        return (None, None)
    if want:  # prefer the video whose title matches the movie name
        for nm, wid in feats:
            if tokens(nm) == want:
                return (nm, wid)
    return feats[0]


def show_name_from_route(data, show_id):
    for it in data.get("included", []):
        if it.get("type") == "show" and it.get("id") == show_id:
            return it.get("attributes", {}).get("name")
    # movies/singles may be a 'show' too; else use route title
    d = data.get("data", {})
    return (d.get("attributes", {}) or {}).get("title")


# ── browser / login ───────────────────────────────────────────────────────────
def _dismiss_consent(page):
    for sel in ('[data-testid="consent-modal-button-0"]',
                'button:has-text("Agree")'):
        try:
            if page.query_selector(sel):
                page.click(sel)
                time.sleep(1)
                return
        except Exception:
            pass


def _logged_in(page):
    """True if we appear to be inside the authenticated app (not marketing/auth)."""
    url = page.url
    if "auth.hbomax" in url or "/signin" in url:
        return False
    if re.search(r"hbomax\.com/\?(.*reason=anonymous|$)", url):
        return False
    return "play.hbomax.com" in url


def ensure_login(page, email, pw, headed=True):
    page.goto("https://play.hbomax.com/", wait_until="domcontentloaded")
    time.sleep(6)
    if _logged_in(page):
        return True

    print("  Logging in to Max ...")
    page.goto("https://auth.hbomax.com/login?flow=login",
              wait_until="domcontentloaded")
    time.sleep(5)
    _dismiss_consent(page)
    time.sleep(2)
    try:
        page.fill('input[name="phoneEmail"]', email)
        page.fill('input[name="password"]', pw)
        page.click('button[type="submit"]')
    except Exception as e:
        print(f"  ! Could not fill the login form ({e}).")

    warned = False
    deadline = time.time() + (1800 if headed else 60)
    while time.time() < deadline:
        time.sleep(3)
        try:
            page.goto("https://play.hbomax.com/", wait_until="domcontentloaded")
        except Exception:
            pass
        time.sleep(3)
        if _logged_in(page):
            print("  Logged in.")
            return True
        if not warned:
            warned = True
            if headed:
                print("\n  " + "=" * 62)
                print("  If Max shows device/email verification or a 'Who's")
                print("  watching' profile screen, complete it in the Firefox")
                print("  window. I'll continue automatically once you're in.")
                print("  " + "=" * 62 + "\n")
            else:
                print("  ! Login needs a visible window (verification/profile). "
                      "Re-run headed.")
                return False
    print("  ! Timed out waiting for login.")
    return False


def warm_up(page, seed_title="house of the dragon"):
    """Load the app so it fires its content API; capture the replay headers."""
    box = {}

    def on_response(resp):
        try:
            if "api.hbomax.com/cms/" not in resp.url or resp.status != 200:
                return
            if box.get("x-disco-client"):
                return
            h = resp.request.all_headers()
            if h.get("x-disco-client"):
                box.update({k: v for k, v in h.items() if k in _REPLAY_HEADERS})
        except Exception:
            pass

    page.on("response", on_response)
    page.goto("https://play.hbomax.com/", wait_until="domcontentloaded")
    time.sleep(7)
    page.goto("https://play.hbomax.com/search?q=" +
              urllib.parse.quote_plus(seed_title), wait_until="domcontentloaded")
    for _ in range(20):
        time.sleep(1)
        if box.get("x-disco-client"):
            break
    page.remove_listener("response", on_response)
    return box


# ── content API (in-page fetch, reuses cookies + captured headers) ────────────
_JS_FETCH = """
async ([url, extra]) => {
  try {
    const r = await fetch(url, {credentials:'include', headers:extra});
    const t = await r.text();
    return {status:r.status, body:t};
  } catch(e) { return {error:String(e)}; }
}
"""


def _api(page, extra, url):
    res = page.evaluate(_JS_FETCH, [url, extra])
    if res.get("error"):
        raise RuntimeError(res["error"])
    if res["status"] != 200:
        raise RuntimeError(f"HTTP {res['status']}: {res['body'][:160]}")
    import json
    return json.loads(res["body"])


def discover_show(page, extra, title, kind):
    """Search Max for `title`; return (show_id, show_name) best match or (None,None)."""
    url = (f"{API_BASE}/cms/routes/search?include=default&decorators={DEC}"
           f"&page[items.size]=25&contentFilter[query]="
           f"{urllib.parse.quote(title)}")
    data = _api(page, extra, url)
    want = tokens(title)
    cands = []
    for it in data.get("included", []):
        if it.get("type") != "show":
            continue
        name = it.get("attributes", {}).get("name") or ""
        cands.append((it.get("id"), name, tokens(name)))
    if not cands:
        return None, None
    exact = [c for c in cands if c[2] == want]
    if exact:
        return exact[0][0], exact[0][1]
    # else highest token overlap, then shortest name
    def score(c):
        return (-len(set(c[2]) & set(want)), len(c[2]))
    cands.sort(key=score)
    top = cands[0]
    if set(top[2]) & set(want):
        return top[0], top[1]
    return None, None


def _episodes_collection_id(show_data):
    """Find the season-filterable episodes collection id from a show route."""
    cands = [it for it in show_data.get("included", [])
             if it.get("type") == "collection"
             and "episode" in (it.get("attributes", {}).get("name", "").lower())]
    cands.sort(key=lambda it: it.get("attributes", {}).get("kind") != "automatic")
    return cands[0]["id"] if cands else None


def _season_numbers(show_data):
    return sorted({it.get("attributes", {}).get("seasonNumber")
                   for it in show_data.get("included", [])
                   if it.get("type") == "season"
                   and it.get("attributes", {}).get("seasonNumber")})


def _fetch_season(page, extra, col_id, sn, show_id):
    """All episodes for one season (handles pagination).

    The episodes collection requires BOTH pf[show.id] and pf[seasonNumber]
    (pf[show.id] is the "mandatory parameter" the generic collection rejects
    when missing).
    """
    eps, page_no = [], 1
    while True:
        url = (f"{API_BASE}/cms/collections/{col_id}?include=default"
               f"&decorators={DEC}&page[items.size]=100&page[items.number]={page_no}"
               f"&pf[show.id]={show_id}&pf[seasonNumber]={sn}")
        try:
            data = _api(page, extra, url)
        except Exception:
            break
        batch = parse_episodes(data)
        eps.extend(batch)
        meta = (data.get("data", {}) or {}).get("meta", {}) or {}
        total = meta.get("itemsTotalPages") or 1
        if page_no >= total or not batch:
            break
        page_no += 1
    return eps


# ── reusable resolver (imported by title_lookup.py) ───────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None,
            browser="firefox", headed=True):
    """Resolve a Max title to episode watch UUIDs.

    Returns (show_name, rows) where each row is a dict:
        {season, episode, title, identifier, watch_url}
    (season/episode/title are "" for a movie).  `seasons` is a set or None (all).
    `url` may be a play.hbomax.com/show/<id> link to skip search.
    Raises RuntimeError if credentials are missing.
    """
    env = load_env()
    email, pw = env.get("MAX_EMAIL"), env.get("MAX_PASSWORD")
    if not email or not pw:
        raise RuntimeError("Missing MAX_EMAIL / MAX_PASSWORD in .env.local.")

    os.makedirs(PROFILE_DIR, exist_ok=True)

    show_id = None
    if url:
        m = re.search(r"/show/(" + UUID_RE + ")", url)
        if m:
            show_id = m.group(1)

    def mkrow(season, ep, eptitle, wid):
        return {"season": season, "episode": ep, "title": eptitle,
                "identifier": wid,
                "watch_url": f"https://play.hbomax.com/video/watch/{wid}"}

    show = title or ""
    rows = []
    with sync_playwright() as pw_ctx:
        ctx = pw_ctx.firefox.launch_persistent_context(
            PROFILE_DIR, headless=not headed,
            viewport={"width": 1360, "height": 1000}, user_agent=UA)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            if not ensure_login(page, email, pw, headed=headed):
                return (show, [])
            extra = warm_up(page, seed_title=title or "house of the dragon")
            if not extra.get("x-disco-client"):
                raise RuntimeError("could not capture Max API headers "
                                   "(is a profile/verification screen blocking?)")

            if not show_id:
                show_id, found = discover_show(page, extra, title, kind)
                if found:
                    show = found
            if not show_id:
                return (show, [])

            show_url = (f"{API_BASE}/cms/routes/show/{show_id}?include=default"
                        f"&decorators={DEC}&page[items.size]=100")
            show_data = _api(page, extra, show_url)
            show = show_name_from_route(show_data, show_id) or show

            if kind == "movie":
                nm, wid = parse_movie(show_data, want_name=show)
                if wid:
                    show = nm or show
                    rows.append(mkrow("", "", "", wid))
            else:
                col_id = _episodes_collection_id(show_data)
                if not col_id:
                    # single-season or movie-shaped: read whatever episodes exist
                    for s, e, n, wid in parse_episodes(show_data):
                        rows.append(mkrow(str(s or "1"), str(e or ""), n, wid))
                else:
                    avail = _season_numbers(show_data) or [1]
                    for sn in avail:
                        if seasons is not None and sn not in seasons:
                            continue
                        eps = _fetch_season(page, extra, col_id, sn, show_id)
                        if eps:
                            print(f"  Season {sn}: {len(eps)} episodes.")
                        for s, e, n, wid in eps:
                            rows.append(mkrow(str(s if s is not None else sn),
                                              str(e or ""), n, wid))
        finally:
            ctx.close()
    return (show, rows)


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Max title -> episode watch uuids")
    ap.add_argument("--type", choices=["movie", "series"])
    ap.add_argument("--title")
    ap.add_argument("--seasons", help="e.g. 1 | 1,3 | 1-4 | all")
    ap.add_argument("--url", help="play.hbomax.com/show/<id> URL (skip search)")
    ap.add_argument("--headless", action="store_true",
                    help="run without a visible window (only works if the SPA "
                         "still fires its API — usually it does NOT; not advised)")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    kind = args.type
    title = args.title
    seasons = parse_seasons(args.seasons) if args.seasons is not None else None

    if not kind:
        a = ask("Movie or Series?  [m/s]: ").lower()
        kind = "movie" if a.startswith("m") else "series"
    if not title and not args.url:
        title = ask("What title?: ")
    if kind == "series" and args.seasons is None:
        seasons = parse_seasons(ask("Which season(s)?  1 | 1,3 | 1-4 | all: "))
    if not title and not args.url:
        print("Need a title (or --url)."); return 1

    try:
        show, rows = resolve(title=title, url=args.url, kind=kind, seasons=seasons,
                             headed=not args.headless)
    except RuntimeError as e:
        print(f"  ! {e}"); return 1
    if not rows:
        print(f"  ! No Max result for {title or args.url!r}."); return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-",
                  (title or "title").lower()).strip("-") or "title"
    out = os.path.join(args.outdir, f"max_{kind}_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "SEASON", "EPISODE", "EPISODE_TITLE",
                    "IDENTIFIER", "WATCH_URL", "PLATFORM"])
        for r in rows:
            w.writerow([show, r["season"], r["episode"], r["title"],
                        r["identifier"], r["watch_url"], "Max"])

    print(f"\nWrote {len(rows)} watch id(s) for {show!r}.")
    for r in rows[:25]:
        s = f" S{r['season']} E{r['episode']}" if r["season"] else ""
        print(f"   {show}{s}  ->  {r['identifier']}")
    if len(rows) > 25:
        print(f"   ... and {len(rows) - 25} more (see CSV)")
    print(f"\nCSV: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
