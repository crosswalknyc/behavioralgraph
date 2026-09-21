#!/usr/bin/env python3
"""
amazon_podcasts_identifier.py  —  Amazon Music podcast show -> every episode URL
================================================================================
An Amazon Music "podcast" is a show identified by a UUID; each episode is also a
UUID. We enumerate every episode and emit ONE row per episode holding the full
music.amazon.com watch link:

    SHOW                        URL                                              PLAT
    BABY, THIS IS KEKE PALMER   https://music.amazon.com/podcasts/<showId>/      Amazon
    10|                                episodes/<episodeId>/<show-slug>-<ep-slug>       Podcasts
    ... (one row per episode) ...

How (a real anonymous browser — no login, no account)
-----------------------------------------------------
Unlike the other podcast platforms, Amazon Music has NO usable anonymous HTTP
catalog API — the episode list is served only through a token-gated Coral
service that the web player calls with a JS-minted csrf/device session. So, like
the Netflix resolver, we drive a REAL headless Chromium (via Playwright) to do
exactly what a listener would:

  1. open  music.amazon.com/search/<title>   -> the show's card exposes its
     `primary-href="/podcasts/<showId>/<slug>"`  (fuzzy-match the title).
  2. open  music.amazon.com/podcasts/<showId>  and SCROLL the (virtualized)
     episode list to the bottom, harvesting every `/episodes/<episodeId>/<slug>`
     anchor Amazon renders. Each anchor is Amazon's OWN canonical path, so we
     never guess a slug. Full URL = music.amazon.com + href.

  • URL hint: paste any music.amazon.com/podcasts/<showId>[/episodes/<epId>/…]
    link (or a `/podcasts/<showId>` share link) to skip the search.

Requirements
------------
Needs Playwright + Chromium (same as the Netflix resolver). If Playwright isn't
installed, this resolver raises a clear message; every other StreamScout platform
still works. Everything here is anonymous — no login, no Amazon account.

Usage
-----
    python3 amazon_podcasts_identifier.py --title "Baby, this is Keke Palmer"
    python3 amazon_podcasts_identifier.py --url https://music.amazon.com/podcasts/479d20e6-2d8c-451a-b662-5bd4fa0178ab
    python3 amazon_podcasts_identifier.py                     # interactive
"""

import argparse
import csv
import difflib
import os
import re
import sys
from datetime import datetime

BASE = "https://music.amazon.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_SHOW_HREF = re.compile(r"/podcasts/(" + _UUID + r")(?:/(?!episodes/)[^/?\s]*)?/?$")
_EP_IN_URL = re.compile(r"/podcasts/(" + _UUID + r")/episodes/(" + _UUID + r")")
_SHOW_IN_URL = re.compile(r"/podcasts/(" + _UUID + r")")


# ── fuzzy title matching (case-insensitive) ───────────────────────────────────
try:
    from match_gate import is_relevant           # shared over-match relevance floor
except ImportError:                              # keep the sibling importable
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from match_gate import is_relevant


def tokens(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def similarity(a, b):
    a = (a or "").lower().strip()
    b = (b or "").lower().strip()
    if not a or not b:
        return 0.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = tokens(a), tokens(b)
    jacc = (len(ta & tb) / len(ta | tb)) if (ta or tb) else 0.0
    contain = 1.0 if (ta and tb and (ta <= tb or tb <= ta)) else 0.0
    return max(ratio, jacc, contain * 0.95)


# ── Playwright plumbing ───────────────────────────────────────────────────────
def _sync_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Amazon Podcasts needs Playwright + Chromium (like Netflix). "
            "Install once with:  pip install playwright && playwright install "
            "chromium") from exc
    return sync_playwright


def _new_page(pw, headless=True):
    browser = pw.chromium.launch(headless=headless)
    ctx = browser.new_context(user_agent=UA,
                              viewport={"width": 1360, "height": 1000},
                              locale="en-US")
    ctx.set_default_timeout(45000)
    return browser, ctx, ctx.new_page()


# ── discovery + enumeration ───────────────────────────────────────────────────
def discover_show(page, title):
    """(show_id, show_name) for the best fuzzy match on Amazon Music search."""
    page.goto(BASE + "/search/" + re.sub(r"\s+", "%20", title.strip()),
              wait_until="domcontentloaded")
    _read_cards = """() => {
        const out = [];
        document.querySelectorAll('[primary-href]').forEach(el => {
            const h = el.getAttribute('primary-href') || '';
            const m = h.match(/\\/podcasts\\/([0-9a-f-]{36})(?:\\/(?!episodes\\/)|$)/);
            if (m) out.push([m[1], el.getAttribute('primary-text') || '']);
        });
        return out;
    }"""
    best = None
    for _ in range(20):                      # let the SPA paint the results
        page.wait_for_timeout(500)
        cards = page.evaluate(_read_cards)
        if cards:
            uniq = {}
            for sid, name in cards:
                uniq.setdefault(sid, name)   # first (top) name wins
            best = max(uniq.items(), key=lambda kv: similarity(title, kv[1]))
            if similarity(title, best[1]) >= 0.45:
                break
    if not best or not is_relevant(title, best[1]):   # relevance floor (no dump)
        return None, None
    return best[0], best[1]


def _scrape_episodes(page, show_id):
    """(show_name, [(ep_id, full_url, ep_title)]) — scroll the whole list."""
    page.goto(BASE + "/podcasts/" + show_id, wait_until="domcontentloaded")
    # wait until at least one episode anchor exists (or give up)
    for _ in range(30):
        page.wait_for_timeout(400)
        if page.evaluate(
                "document.querySelectorAll('a[href*=\"/episodes/\"],"
                "[primary-href*=\"/episodes/\"]').length > 0"):
            break

    page.evaluate("window.__eps = new Map()")
    _grab = """() => {
        const add = (h, t) => {
            if (!h) return;
            const m = h.match(/episodes\\/([0-9a-f-]{36})/);
            if (m && !window.__eps.has(m[1]))
                window.__eps.set(m[1], {href: h, title: (t || '').trim()});
        };
        document.querySelectorAll('a[href*="/episodes/"]').forEach(
            a => add(a.getAttribute('href'), a.textContent));
        document.querySelectorAll('[primary-href*="/episodes/"]').forEach(
            e => add(e.getAttribute('primary-href'), e.getAttribute('primary-text')));
        return window.__eps.size;
    }"""
    _scroll = """() => {
        window.scrollTo(0, document.body.scrollHeight);
        document.querySelectorAll('*').forEach(e => {
            if (e.scrollHeight > e.clientHeight + 400 && e.clientHeight > 200)
                e.scrollTop = e.scrollHeight;
        });
        return document.body.scrollHeight;
    }"""
    last, last_h, stable = -1, -1, 0
    for _ in range(600):
        cnt = page.evaluate(_grab)
        h = page.evaluate(_scroll)
        page.wait_for_timeout(350)
        if cnt == last and h == last_h:
            stable += 1
        else:
            stable = 0
        last, last_h = cnt, h
        if stable >= 12:
            break
    page.evaluate(_grab)                      # final sweep after last scroll

    name = page.evaluate(
        "(document.querySelector('h1') && document.querySelector('h1')"
        ".textContent || document.title || '').trim()")
    data = page.evaluate(
        "[...window.__eps.entries()].map(([id, v]) => [id, v.href, v.title])")
    rows = []
    for ep_id, href, ep_title in data:
        url = href if href.startswith("http") else BASE + href
        rows.append((ep_id, url, ep_title))
    # Amazon renders oldest-first in the DOM; emit newest-first to match sheets.
    rows.reverse()
    return name, rows


def _clean_name(s):
    """Trim Amazon's page-title decoration to a bare show name."""
    s = (s or "").strip()
    s = re.sub(r"\s*\|\s*Listen on Amazon Music\s*$", "", s, flags=re.I)
    s = re.sub(r"\s+Podcast\s*$", "", s, flags=re.I)
    return s.strip()


def parse_url(url):
    """(show_id, episode_id) from any music.amazon.com podcast/episode link."""
    m = _EP_IN_URL.search(url or "")
    if m:
        return m.group(1), m.group(2)
    m = _SHOW_IN_URL.search(url or "")
    if m:
        return m.group(1), None
    return None, None


def _row(ep_title, url):
    return {"season": "", "episode": "", "title": ep_title or "",
            "identifier": url, "watch_url": url}


# ── public entry point ────────────────────────────────────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None, headless=True):
    """Resolve an Amazon Music podcast to every episode's music.amazon.com URL.

    Returns (show_name, rows); each row is
        {season:"", episode:"", title:<episode name>,
         identifier:<full music.amazon.com/podcasts/.../episodes/... URL>,
         watch_url:<same>}
    `seasons` is accepted for interface parity but ignored (podcasts are a flat
    episode list). Anonymous — no login, no Amazon account.
    """
    if os.environ.get("STREAMSCOUT_HEADED"):
        headless = False

    show_id_hint, ep_id_hint = (None, None)
    if url:
        show_id_hint, ep_id_hint = parse_url(url)

    # A pasted single-episode link + movie kind -> just that episode (no browser).
    if ep_id_hint and kind == "movie":
        return (title or "", [_row("", url)])

    sp = _sync_playwright()
    with sp() as pw:
        browser, ctx, page = _new_page(pw, headless=headless)
        try:
            show_id, show_name = show_id_hint, (title or "")

            if not show_id and title:
                show_id, show_name = discover_show(page, title)
                if not show_id:
                    return (title or "", [])

            name, eps = _scrape_episodes(page, show_id)
            # Prefer the clean card/name we already matched; the show page has no
            # <h1>, so its <title> carries "… Podcast | Listen on Amazon Music".
            show_name = (show_name or "").strip() or _clean_name(name) or title or ""

            if kind == "movie" and title:      # single best-matching episode
                if not eps:
                    return (show_name, [])
                best = max(eps, key=lambda e: similarity(title, e[2]))
                return (show_name, [_row(best[2], best[1])])

            return (show_name, [_row(t, u) for (_i, u, t) in eps])
        finally:
            try:
                ctx.close(); browser.close()
            except Exception:  # noqa: BLE001
                pass


# ── main (standalone CLI) ─────────────────────────────────────────────────────
def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def main():
    ap = argparse.ArgumentParser(
        description="Amazon Music podcast -> every episode URL (anonymous browser).")
    ap.add_argument("--type", choices=["movie", "series"], default="series")
    ap.add_argument("--title")
    ap.add_argument("--url", help="a music.amazon.com podcast/episode URL")
    ap.add_argument("--seasons", help="(accepted but ignored; flat episode list)")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    kind, title = args.type, args.title
    if not args.url and not title:
        title = ask("What Amazon Music podcast?: ")
    if not args.url and not title:
        print("Need a --title or --url.")
        return 1

    try:
        show, rows = resolve(title=title, url=args.url, kind=kind,
                             headless=not args.headed)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! Search/fetch failed: {exc!r}")
        return 2
    if not rows:
        print("  ! Not found on Amazon Music. Try --url with the podcast link.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "amazon").lower()).strip("-")
    path = os.path.join(args.outdir,
                        f"lookup_amazonpodcasts_{kind}_{safe}_{stamp}.csv")
    try:
        from production_tags import production_for  # noqa: WPS433
        prod = production_for(show or title or "") or ""
    except Exception:  # noqa: BLE001
        prod = ""
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["SHOW", "URL", "PRODUCTION", "PLATFORM", "SEASON"])
        for r in rows:
            w.writerow([show, r["identifier"], prod, "Amazon Podcasts", ""])
    print(f"  ok  {len(rows)} episode(s) -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
