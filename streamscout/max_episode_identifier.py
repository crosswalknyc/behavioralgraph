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
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
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


# ── headless auth: capture once (headed), replay forever (pure HTTP) ───────────
# Max's catalog calls are plain HTTPS GETs; the browser is only there to log in
# and attach cookies + the x-disco/x-wbd headers. We capture those once and
# replay the same GETs via urllib — no browser — so Max resolves UNATTENDED
# (Promy/servers) with no window. Auth comes from (in priority order):
#   MAX_AUTH_JSON env (inline JSON blob, for deploys) -> MAX_AUTH_PATH file ->
#   ~/.max_auth.json. Refresh it with `max_episode_identifier.py --refresh-auth`.
_DEFAULT_AUTH_PATH = os.path.expanduser("~/.max_auth.json")


class _AuthError(RuntimeError):
    """Saved Max auth is missing or stale (401/403) — needs a refresh."""


def _auth_path():
    return os.environ.get("MAX_AUTH_PATH") or _DEFAULT_AUTH_PATH


def load_saved_auth():
    """Return the stored {"headers":..., "cookies":...} or None."""
    blob = os.environ.get("MAX_AUTH_JSON")
    if blob:
        try:
            return json.loads(blob)
        except Exception:
            pass
    path = _auth_path()
    if os.path.exists(path):
        try:
            return json.load(open(path))
        except Exception:
            pass
    return None


def save_auth(auth):
    """Persist captured auth to the MAX_AUTH_PATH file (not MAX_AUTH_JSON env)."""
    try:
        path = _auth_path()
        json.dump(auth, open(path, "w"))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path
    except Exception:
        return None


def _cookie_header(cookies, host):
    """Build a Cookie: header from the cookies that apply to ``host``."""
    out = []
    for c in cookies or []:
        dom = (c.get("domain") or "").lstrip(".")
        if not dom or host == dom or host.endswith("." + dom) or dom in host:
            out.append("%s=%s" % (c.get("name"), c.get("value")))
    return "; ".join(out)


def _http_api(auth, url):
    """Pure-HTTP GET of a Max CMS endpoint using captured cookies + headers.
    Raises _AuthError on 401/403 (stale auth), RuntimeError on other failures."""
    host = urllib.parse.urlparse(API_BASE).netloc
    hdrs = {"User-Agent": UA, "Accept": "application/json",
            "Cookie": _cookie_header(auth.get("cookies"), host)}
    hdrs.update({k: v for k, v in (auth.get("headers") or {}).items()
                 if k in _REPLAY_HEADERS})
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise _AuthError("Max auth stale (HTTP %s)" % e.code)
        raise RuntimeError("Max API HTTP %s: %s" % (e.code, e.reason))


def _discover_show_http(auth, title):
    data = _http_api(auth, f"{API_BASE}/cms/routes/search?include=default"
                     f"&decorators={DEC}&page[items.size]=25&contentFilter[query]="
                     f"{urllib.parse.quote(title)}")
    want = tokens(title)
    cands = [(it.get("id"), it.get("attributes", {}).get("name") or "",
              tokens(it.get("attributes", {}).get("name") or ""))
             for it in data.get("included", []) if it.get("type") == "show"]
    if not cands:
        return None, None
    exact = [c for c in cands if c[2] == want]
    if exact:
        return exact[0][0], exact[0][1]
    cands.sort(key=lambda c: (-len(set(c[2]) & set(want)), len(c[2])))
    top = cands[0]
    return (top[0], top[1]) if set(top[2]) & set(want) else (None, None)


def resolve_headless(title, url, kind, seasons, auth):
    """Resolve a Max title to watch UUIDs via pure HTTP — NO browser.
    Returns (show_name, rows). Raises _AuthError if the saved auth is stale."""
    def mkrow(season, ep, eptitle, wid):
        return {"season": season, "episode": ep, "title": eptitle,
                "identifier": wid,
                "watch_url": f"https://play.hbomax.com/video/watch/{wid}"}

    show_id = None
    if url:
        m = re.search(r"/show/(" + UUID_RE + ")", url)
        if m:
            show_id = m.group(1)
    show = title or ""
    if not show_id:
        show_id, found = _discover_show_http(auth, title)
        if found:
            show = found
    if not show_id:
        return (show, [])

    show_data = _http_api(auth, f"{API_BASE}/cms/routes/show/{show_id}"
                          f"?include=default&decorators={DEC}&page[items.size]=100")
    show = show_name_from_route(show_data, show_id) or show
    rows = []
    if kind == "movie":
        nm, wid = parse_movie(show_data, want_name=show)
        if wid:
            show = nm or show
            rows.append(mkrow("", "", "", wid))
        return (show, rows)

    col_id = _episodes_collection_id(show_data)
    if not col_id:
        for s, e, n, wid in parse_episodes(show_data):
            rows.append(mkrow(str(s or "1"), str(e or ""), n, wid))
        return (show, rows)
    for sn in (_season_numbers(show_data) or [1]):
        if seasons is not None and sn not in seasons:
            continue
        page_no, got = 1, []
        while True:
            try:
                data = _http_api(auth, f"{API_BASE}/cms/collections/{col_id}"
                                 f"?include=default&decorators={DEC}"
                                 f"&page[items.size]=100&page[items.number]={page_no}"
                                 f"&pf[show.id]={show_id}&pf[seasonNumber]={sn}")
            except _AuthError:
                raise
            except Exception:
                break
            batch = parse_episodes(data)
            got.extend(batch)
            meta = (data.get("data", {}) or {}).get("meta", {}) or {}
            if page_no >= (meta.get("itemsTotalPages") or 1) or not batch:
                break
            page_no += 1
        for s, e, n, wid in got:
            rows.append(mkrow(str(s if s is not None else sn), str(e or ""), n, wid))
    return (show, rows)


def _capture_auth_from_context(ctx, page, seed_title):
    """After a headed login, grab the replay headers + cookies and save them so
    future runs can go headless. Returns the auth dict (or None)."""
    extra = warm_up(page, seed_title=seed_title)
    if not extra.get("x-disco-client"):
        return None
    auth = {"headers": extra, "cookies": ctx.cookies()}
    save_auth(auth)
    return auth


# ── reusable resolver (imported by title_lookup.py) ───────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None,
            browser="firefox", headed=None):
    """Resolve a Max title to episode watch UUIDs.

    Returns (show_name, rows) where each row is a dict:
        {season, episode, title, identifier, watch_url}
    (season/episode/title are "" for a movie).  `seasons` is a set or None (all).
    `url` may be a play.hbomax.com/show/<id> link to skip search.
    Raises RuntimeError if credentials are missing.

    Window policy:
      headed=None  (default / library / Promy): UNATTENDED — replay saved auth
          via pure HTTP; if auth is missing/stale, raise (NEVER opens a window).
      headed=True  (interactive CLI): headless replay first; only if auth is
          missing/stale do we open a window to log in and capture fresh auth.
      headed=False (explicit --headless): same as unattended.
    STREAMSCOUT_HEADLESS forces window-less even when headed=True.
    """
    force_headless = str(os.environ.get("STREAMSCOUT_HEADLESS", "")).strip().lower() \
        in ("1", "true", "yes", "on")
    allow_window = (headed is True) and not force_headless

    # 1) Fast path: replay saved auth via pure HTTP — no browser, works anywhere.
    auth = load_saved_auth()
    if auth:
        try:
            return resolve_headless(title, url, kind, seasons, auth)
        except _AuthError:
            auth = None  # stale -> refresh below if we're allowed a window
        except Exception:
            if not allow_window:
                raise  # transient API error; surfaced as unverified upstream
            # else fall through to re-login as a backstop

    # 2) No usable auth. Unattended runs must NOT open a window.
    if not allow_window:
        raise _AuthError(
            "Max auth missing or stale and no interactive window is allowed. "
            "Refresh it on a desktop with: "
            "python3 max_episode_identifier.py --refresh-auth")

    # 3) Interactive refresh: open a window, log in, capture fresh auth, then
    #    resolve through the exact same pure-HTTP path.
    fresh = refresh_auth(seed_title=title or "house of the dragon")
    return resolve_headless(title, url, kind, seasons, fresh)


def refresh_auth(seed_title="house of the dragon"):
    """Open a visible Firefox, log in, and capture fresh Max auth
    (cookies + replay headers) to the MAX_AUTH_PATH file. Returns the auth dict.
    This is the ONLY code path that opens a browser window."""
    env = load_env()
    email, pw = env.get("MAX_EMAIL"), env.get("MAX_PASSWORD")
    if not email or not pw:
        raise RuntimeError("Missing MAX_EMAIL / MAX_PASSWORD in .env.local.")
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with sync_playwright() as pw_ctx:
        ctx = pw_ctx.firefox.launch_persistent_context(
            PROFILE_DIR, headless=False,
            viewport={"width": 1360, "height": 1000}, user_agent=UA)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            if not ensure_login(page, email, pw, headed=True):
                raise RuntimeError("Max login did not complete.")
            fresh = _capture_auth_from_context(ctx, page, seed_title=seed_title)
            if not fresh:
                raise RuntimeError("could not capture Max API headers "
                                   "(is a profile/verification screen blocking?)")
        finally:
            ctx.close()
    return fresh


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Max title -> episode watch uuids")
    ap.add_argument("--type", choices=["movie", "series"])
    ap.add_argument("--title")
    ap.add_argument("--seasons", help="e.g. 1 | 1,3 | 1-4 | all")
    ap.add_argument("--url", help="play.hbomax.com/show/<id> URL (skip search)")
    ap.add_argument("--headless", action="store_true",
                    help="never open a window; replay saved auth only "
                         "(fails if auth is missing/stale — run --refresh-auth)")
    ap.add_argument("--refresh-auth", action="store_true",
                    help="open a window, log in, and save fresh Max auth "
                         "(cookies + headers) for unattended headless runs, "
                         "then exit")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    if args.refresh_auth:
        try:
            auth = refresh_auth()
        except RuntimeError as e:
            print(f"  ! {e}"); return 1
        print(f"\n✓ Saved Max auth: {len(auth.get('headers', {}))} headers, "
              f"{len(auth.get('cookies', []))} cookies -> {_auth_path()}")
        print("  Unattended runs can now resolve Max headless (no window).\n")
        return 0

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
