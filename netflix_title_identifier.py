#!/usr/bin/env python3
"""
netflix_title_identifier.py  —  title + season(s) -> episode watch/<id>s
========================================================================
Netflix URLs are numeric-only (`/watch/<id>`), and the season/episode tree is
served only through Netflix's logged-in UI (scripted HTTP gets BLOCKED). So this
tool drives a REAL logged-in Chromium (via Playwright) to do exactly what you'd
do by hand:

    log in  ->  pick profile  ->  search the title  ->  open the show
            ->  select the season(s)  ->  read each episode's watch/<id>

and writes the segments to a CSV on your Desktop.

One-time setup
--------------
It uses a PERSISTENT browser profile on disk, so you log in ONCE. On the first
run a visible Chromium window opens; if Netflix shows a device-verification
screen ("verify we have the right account"), complete it in that window (email
a code) and pick a profile. After that the profile stays trusted and future
runs need no login.

Credentials come from the gitignored `.env.local` (NETFLIX_EMAIL /
NETFLIX_PASSWORD) — never hard-coded, never committed.

Usage
-----
    # interactive (asks type, title, season(s)):
    /opt/homebrew/bin/python3 netflix_title_identifier.py

    # non-interactive:
    /opt/homebrew/bin/python3 netflix_title_identifier.py \
        --type series --title "The Sandman" --seasons 1,2
    /opt/homebrew/bin/python3 netflix_title_identifier.py \
        --type series --title "The Sandman" --seasons all
    /opt/homebrew/bin/python3 netflix_title_identifier.py \
        --type movie  --title "Leave the World Behind"

    # skip search and pin the Netflix title id directly:
    ... --id 81150303 --seasons 1

Options: --profile "Jenna" (which Netflix profile), --headless (no window;
only works once the profile is already logged in), --id <netflixTitleId>.
"""

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

PROFILE_DIR = os.path.expanduser("~/.netflix_scraper_profile")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


# ── helpers ───────────────────────────────────────────────────────────────────
def load_env(path=".env.local") -> dict:
    env = {}
    for p in (path, os.path.join(os.path.dirname(__file__), path)):
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


# ── browser steps ─────────────────────────────────────────────────────────────
_VERIFY_KEYS = ("verify we have the right account", "quick confirmation",
                "enter the code", "verify it's you", "verify its you",
                "we sent a", "verify your")


def _auth_state(page):
    """Return 'profiles' | 'app' | 'verify' | 'login' | None (None = keep waiting)."""
    try:
        url = page.url
    except Exception:
        return None
    # profile gate — most reliable signals first
    try:
        if page.query_selector('a[href*="SwitchProfile"], [data-uia*="profile-link"], '
                               '.profile-gate-label, .list-profiles'):
            return "profiles"
    except Exception:
        pass
    try:
        body = (page.inner_text("body") or "").lower()
    except Exception:
        body = ""  # mid-navigation; treat as unknown, keep waiting
    if "who's watching" in body or "who\u2019s watching" in body:
        return "profiles"
    if "/login" in url or _safe_has(page, 'input[name="userLoginId"]'):
        return "login"
    if any(k in body for k in _VERIFY_KEYS):
        return "verify"
    # logged into the app: nav/search/account chrome, or home rows present
    if _safe_has(page, '[data-uia="account-menu-item"], [data-uia="profile-button"], '
                       '[data-uia="search-box-launcher"], [data-uia="header-search-toggle"], '
                       'a[href="/browse"]'):
        return "app"
    if ("/browse" in url or "/latest" in url) and _safe_has(
            page, '[data-ui-tracking-context], a[href*="/watch/"]'):
        return "app"
    return None


def _safe_has(page, selector):
    try:
        return page.query_selector(selector) is not None
    except Exception:
        return False


def ensure_login(page, email, pw, profile, headed=False):
    """Log in (handling one-time device verification) and select a profile."""
    page.goto("https://www.netflix.com/browse", wait_until="domcontentloaded")
    time.sleep(2)

    if _auth_state(page) in ("login", None) and (
            "/login" in page.url or page.query_selector('input[name="userLoginId"]')):
        print("  Logging in ...")
        page.goto("https://www.netflix.com/login", wait_until="domcontentloaded")
        page.fill('input[name="userLoginId"]', email)
        page.fill('input[name="password"]', pw)
        page.click('button[type="submit"], button[data-uia="login-submit-button"]')
        time.sleep(3)

        warned = False
        deadline = time.time() + 1800  # up to 30 min for a manual code
        while time.time() < deadline:
            st = _auth_state(page)
            if st in ("profiles", "app"):
                print(f"  Verified / logged in (state: {st}).")
                break
            if st in ("verify", "login") and not headed and not warned:
                warned = True
                print("\n  " + "=" * 64)
                print("  Login / device verification is required, but the browser")
                print("  is running headless (no visible window). Re-run once with")
                print("  --headed to complete it, e.g.:")
                print("    python3 netflix_title_identifier.py --headed \\")
                print("      --type series --title \"The Sandman\" --seasons 1,2")
                print("  " + "=" * 64 + "\n")
                break
            if st == "verify" and not warned:
                warned = True
                print("\n  " + "=" * 64)
                print("  ONE-TIME device verification required (first run only).")
                print("  In the Firefox window that just opened:")
                print("    1) choose 'Email a code'  (goes to the account email)")
                print("    2) enter the code Netflix sends")
                print("    3) if a 'Who's watching?' screen appears, pick a profile")
                print("  I'll wait here and continue automatically once you're in.")
                print("  " + "=" * 64 + "\n")
            time.sleep(3)
        else:
            print("  ! Timed out waiting for verification/login.")

    if _auth_state(page) == "profiles":
        _pick_profile(page, profile)
        time.sleep(2)


def _pick_profile(page, profile):
    print(f"  Selecting profile: {profile}")
    try:
        loc = page.get_by_role("link", name=profile, exact=True)
        if loc.count() > 0:
            loc.first.click(); time.sleep(3); return
    except Exception:
        pass
    for sel in (f'a[aria-label="{profile}"]', f'a:has-text("{profile}")'):
        el = page.query_selector(sel)
        if el:
            el.click(); time.sleep(3); return
    print(f"  ! Could not find profile '{profile}'; continuing with current.")


def search_title(page, title):
    """Return the Netflix title id for the best-matching search result."""
    print(f"  Searching Netflix for: {title!r}")
    page.goto("https://www.netflix.com/search?q=" + title.replace(" ", "%20"),
              wait_until="domcontentloaded")
    # wait for result boxart anchors (href carries jbv=<id>)
    for _ in range(20):
        time.sleep(0.75)
        if page.query_selector('a[href*="jbv="]'):
            break
    results = page.eval_on_selector_all(
        'a[href*="jbv="]',
        """els => els.map(a => ({
              label: a.getAttribute('aria-label') || '',
              href:  a.getAttribute('href') || ''
           }))""")
    seen, cand = set(), []
    for r in results:
        m = re.search(r"jbv=(\d+)", r["href"])
        if not m:
            continue
        tid = m.group(1)
        if tid in seen:
            continue
        seen.add(tid)
        cand.append((r["label"], tid))
    if not cand:
        return None, None
    want = tokens(title)
    exact = [c for c in cand if tokens(c[0]) == want]
    chosen = exact[0] if exact else cand[0]
    print(f"  -> matched '{chosen[0]}'  (title id {chosen[1]})"
          + ("" if exact else "  [closest match]"))
    return chosen[1], chosen[0]


_SCRAPE_JS = """
() => {
  const cards = Array.from(document.querySelectorAll(
      '.episode-item[data-uia="titleCard--container"]'));
  return cards.map(el => {
    const idx = el.querySelector('.titleCard-title_index');
    const p = el.querySelector('.ptrack-content[data-ui-tracking-context]');
    let vid = null;
    if (p) {
      try {
        const c = JSON.parse(decodeURIComponent(
            p.getAttribute('data-ui-tracking-context')));
        vid = c.video_id || ((c.unifiedEntityId||'').split(':')[1]) || null;
      } catch (e) {}
    }
    return { index: idx ? idx.textContent.trim() : '',
             title: el.getAttribute('aria-label') || '',
             video_id: vid ? String(vid) : null };
  }).filter(e => e.video_id);
}
"""


def open_title(page, title_id):
    page.goto(f"https://www.netflix.com/title/{title_id}",
              wait_until="domcontentloaded")
    # wait for the episode selector (series) or a play button (movie)
    for _ in range(24):
        time.sleep(0.75)
        if page.query_selector('[data-uia="episode-selector"]') or \
           page.query_selector('a[data-uia="play-button"]'):
            break
    show = re.sub(r"\s*-\s*Netflix\s*$", "", page.title()).strip()
    return show


_TOGGLE = '[data-uia="episode-selector"] [data-uia="dropdown-toggle"]'
_MENU_ITEM = 'li[data-uia="dropdown-menu-item"]'


def _open_dropdown(page):
    """Open the season dropdown (locator-based, retries). Returns True if open."""
    toggle = page.locator(_TOGGLE)
    if toggle.count() == 0:
        return False
    for _ in range(3):
        try:
            toggle.first.click(timeout=5000)
            page.wait_for_selector(_MENU_ITEM, timeout=5000)
            return True
        except PWTimeout:
            time.sleep(0.5)
        except Exception:
            time.sleep(0.5)
    return False


def list_seasons(page):
    """Return [(season_number, menu_text)]; [] means a single-season show."""
    # the episode area lazy-loads after the billboard/play button; wait for it
    for _ in range(24):
        if page.query_selector('[data-uia="episode-selector"]'):
            break
        time.sleep(0.75)
    time.sleep(1.0)  # let the season dropdown (if any) mount inside it
    if page.locator(_TOGGLE).count() == 0:
        return []
    if not _open_dropdown(page):
        return []
    items = page.locator(_MENU_ITEM)
    seasons = []
    for i in range(items.count()):
        t = (items.nth(i).inner_text() or "").strip()
        m = re.match(r"season\s+(\d+)", t, re.I)
        if m:
            seasons.append((int(m.group(1)), re.sub(r"\s+", " ", t)))
    # close the menu by clicking the toggle again — pressing Escape here would
    # dismiss the whole detail-page overlay and bounce us back to /browse.
    try:
        page.locator(_TOGGLE).first.click()
    except Exception:
        pass
    time.sleep(0.4)
    return seasons


def select_season(page, snum):
    """Open the dropdown and click the 'Season <snum>' item (locator-based)."""
    if page.locator(_TOGGLE).count() == 0:
        return
    # already on this season? toggle text will say so
    try:
        if re.match(rf"season\s+{snum}\b",
                    (page.locator(_TOGGLE).first.inner_text() or "").strip(), re.I):
            return
    except Exception:
        pass
    if not _open_dropdown(page):
        return
    items = page.locator(_MENU_ITEM)
    for i in range(items.count()):
        t = (items.nth(i).inner_text() or "").strip()
        if re.match(rf"season\s+{snum}\b", t, re.I):
            items.nth(i).click()
            break
    time.sleep(1.8)  # let the episode list re-render


_CARD_SEL = '.episode-item[data-uia="titleCard--container"]'


def scrape_episodes(page):
    """Wait for episode cards, scroll to load the full (lazy) list, then read."""
    eps = []
    for _ in range(30):  # wait up to ~30s for the first card to render
        try:
            page.eval_on_selector(
                '[data-uia="episode-selector"]',
                "el => el.scrollIntoView({block:'center'})")
        except Exception:
            pass
        eps = page.evaluate(_SCRAPE_JS)
        if eps:
            break
        time.sleep(1.0)
    if not eps:
        return eps
    # episodes lazy-load as the last card scrolls into view; keep nudging
    # until the count stops growing.
    last, stable = len(eps), 0
    for _ in range(40):
        try:
            page.eval_on_selector_all(
                _CARD_SEL,
                "els => { if (els.length) "
                "els[els.length-1].scrollIntoView({block:'center'}); }")
        except Exception:
            pass
        time.sleep(0.6)
        eps = page.evaluate(_SCRAPE_JS)
        if len(eps) == last:
            stable += 1
            if stable >= 3:
                break
        else:
            last, stable = len(eps), 0
    return eps


# ── reusable resolver (imported by title_lookup.py) ───────────────────────────
def resolve(title=None, title_id=None, kind="series", seasons=None,
            profile="Jenna", browser="firefox", headed=False):
    """Resolve a Netflix title to episode watch ids.

    Returns (show_name, rows) where each row is a dict:
        {season, episode, title, identifier, watch_url}
    (season/episode/title are "" for a movie).  `seasons` is a set or None (all).
    Raises RuntimeError if credentials are missing.
    """
    env = load_env()
    email = env.get("NETFLIX_EMAIL")
    pw = env.get("NETFLIX_PASSWORD")
    if not email or not pw:
        raise RuntimeError("Missing NETFLIX_EMAIL / NETFLIX_PASSWORD in .env.local.")

    profile_dir = PROFILE_DIR + ("" if browser == "chromium" else "_" + browser)
    os.makedirs(profile_dir, exist_ok=True)

    def mkrow(season, ep, eptitle, vid):
        return {"season": season, "episode": ep, "title": eptitle,
                "identifier": f"watch/{vid}",
                "watch_url": f"https://www.netflix.com/watch/{vid}"}

    show = title or title_id or ""
    rows = []
    with sync_playwright() as pw_ctx:
        launcher = getattr(pw_ctx, browser)
        kwargs = dict(headless=not headed,
                      viewport={"width": 1360, "height": 1000}, user_agent=UA)
        if browser == "chromium":
            kwargs["args"] = ["--disable-blink-features=AutomationControlled"]
        ctx = launcher.launch_persistent_context(profile_dir, **kwargs)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            ensure_login(page, email, pw, profile, headed=headed)
            tid, matched = (title_id, title)
            if not tid:
                tid, matched = search_title(page, title)
            if not tid:
                return (show, [])
            show = open_title(page, tid) or (matched or title or tid)

            if kind == "movie":
                href = page.eval_on_selector(
                    'a[data-uia="play-button"]',
                    "a => a.getAttribute('href')") if page.query_selector(
                    'a[data-uia="play-button"]') else None
                vid = re.search(r"/watch/(\d+)", href).group(1) if href else None
                if vid:
                    rows.append(mkrow("", "", "", vid))
            else:
                avail = list_seasons(page)
                if not avail:  # single-season show: scrape what's shown
                    for e in scrape_episodes(page):
                        rows.append(mkrow("1", e["index"], e["title"], e["video_id"]))
                else:
                    for snum, _ in avail:
                        if seasons is not None and snum not in seasons:
                            continue
                        print(f"  Season {snum}: reading episodes ...")
                        select_season(page, snum)
                        eps = scrape_episodes(page)
                        for e in eps:
                            rows.append(mkrow(str(snum), e["index"],
                                              e["title"], e["video_id"]))
                        print(f"    {len(eps)} episodes.")
        finally:
            ctx.close()
    return (show, rows)


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Netflix title -> episode watch ids")
    ap.add_argument("--type", choices=["movie", "series"])
    ap.add_argument("--title")
    ap.add_argument("--seasons", help="e.g. 1  |  1,3  |  1-4  |  all")
    ap.add_argument("--id", help="Netflix title id (skip search)")
    ap.add_argument("--profile", default="Jenna")
    ap.add_argument("--browser", default="firefox",
                    choices=["firefox", "chromium", "webkit"],
                    help="firefox avoids the macOS Chromium single-instance clash")
    ap.add_argument("--headed", action="store_true",
                    help="show the browser window (needed only for first-time "
                         "login / device verification)")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    kind = args.type
    title = args.title
    seasons = parse_seasons(args.seasons) if args.seasons is not None else None

    # interactive fallback
    if not kind:
        a = ask("Movie or Series?  [m/s]: ").lower()
        kind = "movie" if a.startswith("m") else "series"
    if not title:
        title = ask("What title?: ")
    if kind == "series" and args.seasons is None:
        seasons = parse_seasons(ask("Which season(s)?  1 | 1,3 | 1-4 | all: "))
    if not title and not args.id:
        print("Need a title (or --id)."); return 1

    try:
        show, rows = resolve(title=title, title_id=args.id, kind=kind,
                             seasons=seasons, profile=args.profile,
                             browser=args.browser, headed=args.headed)
    except RuntimeError as e:
        print(f"  ! {e}"); return 1
    if not rows:
        print(f"  ! No Netflix result for {title!r}."); return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or args.id or "title").lower()).strip("-")
    out = os.path.join(args.outdir, f"netflix_{kind}_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "SEASON", "EPISODE", "EPISODE_TITLE",
                    "WATCH_ID", "WATCH_URL", "PLATFORM"])
        for r in rows:
            w.writerow([show, r["season"], r["episode"], r["title"],
                        r["identifier"], r["watch_url"], "Netflix"])

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
