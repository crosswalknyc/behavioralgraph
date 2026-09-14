#!/usr/bin/env python3
"""
siriusxm_episode_identifier.py  —  SiriusXM podcast show -> every episode URL
=============================================================================
A SiriusXM podcast is a "show-podcast" entity (a UUID); each episode is an
"episode-podcast" entity (also a UUID). We enumerate every episode and emit ONE
row per episode holding the full siriusxm.com player link:

    SHOW                        URL                                              PLAT
    BABY, THIS IS KEKE PALMER   https://www.siriusxm.com/player/episode-podcast/  SiriusXM
    10|                                entity/<episodeId>
    ... (one row per episode) ...

How (SiriusXM's own web player API — anonymous, no account)
-----------------------------------------------------------
SiriusXM's catalog lives behind an "edge-gateway" that only answers to a Bearer
`accessToken`. That token is minted by a JS device-registration handshake (an
EdDSA device-attestation flow), NOT by a plain HTTP call — so, like the Netflix
resolver, we spin up a REAL headless Chromium just long enough to let the web
player register an ANONYMOUS session (roles:["anonymous"] — no login, no SiriusXM
account). Then, inside that authenticated browser context, we call SiriusXM's own
gateway exactly as the player does:

  1. page/v1/page/show-podcast/{showId}  -> the show's containers; the "aod"
     (audio-on-demand) container is the full episode list.
  2. relationship/v1/set/aod?…           -> paged 30-at-a-time by `offset` until
     `pagination.hits` are exhausted. Each item is an episode-podcast entity id.

  Episode URL = https://www.siriusxm.com/player/episode-podcast/entity/<id>.

  • URL hint: paste any siriusxm.com/player/{show-podcast,episode-podcast}/entity
    /<id> link to skip discovery. A show link -> every episode; an episode link +
    --type movie -> just that one.
  • Title search uses the player's own search box (the search API itself is
    WAF-gated), reading the matching show-podcast card.

Requirements
------------
Needs Playwright + Chromium (same as the Netflix / Amazon Podcasts resolvers). If
Playwright isn't installed, this resolver raises a clear message; every other
StreamScout platform still works. Everything is anonymous — no login.

Usage
-----
    python3 siriusxm_episode_identifier.py --title "Baby, this is Keke Palmer"
    python3 siriusxm_episode_identifier.py --url https://www.siriusxm.com/player/show-podcast/entity/38835851-9d39-3e8f-b492-a909960502f7
    python3 siriusxm_episode_identifier.py                    # interactive
"""

import argparse
import csv
import difflib
import os
import re
import sys
from datetime import datetime

BASE = "https://www.siriusxm.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_SHOW_IN_URL = re.compile(r"show-podcast/entity/(" + _UUID + r")")
_EP_IN_URL = re.compile(r"episode-podcast/entity/(" + _UUID + r")")


# ── fuzzy title matching (case-insensitive) ───────────────────────────────────
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
            "SiriusXM needs Playwright + Chromium (like Netflix). Install once "
            "with:  pip install playwright && playwright install chromium") from exc
    return sync_playwright


def _new_page(pw, headless=True):
    browser = pw.chromium.launch(headless=headless)
    ctx = browser.new_context(user_agent=UA,
                              viewport={"width": 1360, "height": 1000},
                              locale="en-US")
    ctx.set_default_timeout(45000)
    return browser, ctx, ctx.new_page()


# JS run inside the authenticated player context: read the anonymous accessToken
# from the AUTH_TOKEN cookie, then walk show page -> aod container -> paged set.
_ENUM_JS = r"""
async (showId) => {
  const readToken = () => {
    for (const c of document.cookie.split(';')) {
      const i = c.indexOf('='); const k = c.slice(0, i).trim();
      if (k === 'AUTH_TOKEN') {
        try { return JSON.parse(decodeURIComponent(c.slice(i + 1)))?.session?.accessToken; }
        catch (e) { return null; }
      }
    }
    return null;
  };
  const G = 'https://api.edge-gateway.siriusxm.com/';
  const t = readToken();
  if (!t) return JSON.stringify({ error: 'no-token' });
  const H = { 'Authorization': 'Bearer ' + t, 'Accept': 'application/json' };
  const getJson = async (u) => {
    const r = await fetch(u, { headers: H });
    if (!r.ok) return { __status: r.status };
    return r.json();
  };
  // find a set object carrying items + pagination (to learn the set url)
  const findSetPag = (o) => {
    if (!o || typeof o !== 'object') return null;
    if (Array.isArray(o.items) && o.pagination) return o;
    if (Array.isArray(o)) { for (const v of o) { const r = findSetPag(v); if (r) return r; } return null; }
    for (const k in o) { const r = findSetPag(o[k]); if (r) return r; }
    return null;
  };
  // find the largest items[] holding episode-podcast entities (per page)
  const findItems = (o, best) => {
    best = best || { items: [] };
    if (!o || typeof o !== 'object') return best;
    if (Array.isArray(o.items) && o.items.length > best.items.length
        && o.items.some(it => (it.entity || {}).type === 'episode-podcast')) best = o;
    if (Array.isArray(o)) { for (const v of o) best = findItems(v, best); return best; }
    for (const k in o) best = findItems(o[k], best);
    return best;
  };
  // 1) show page -> containers
  const pj = await getJson(G + 'page/v1/page/show-podcast/' + showId);
  if (pj.__status) return JSON.stringify({ error: 'page-' + pj.__status });
  const page = pj.page || {};
  const showName = (((page.entity || {}).texts || {}).title || {}).default || '';
  const containers = page.containers || [];
  let aod = containers.find(c => /\/container\/aod\?/.test(c.url || ''))
        || containers.find(c => /aod/i.test(c.id || ''));
  if (!aod) return JSON.stringify({ showName, episodes: [] });
  // 2) fetch the aod container once just to learn its set pagination url
  const cj = await getJson(G + aod.url);
  const s0 = findSetPag(cj);
  if (!s0) return JSON.stringify({ showName, episodes: [] });
  // strip any offset/size the template carried so we page from 0 ourselves
  let setBase = G + s0.pagination.url.replace(/&?(offset|size|maxResponses)=\d+/g, '');
  setBase += (setBase.indexOf('?') >= 0 ? '' : '?');
  const seen = {}, out = [];
  const take = (setObj) => {
    for (const it of (setObj.items || [])) {
      const e = it.entity || {};
      if (e.type === 'episode-podcast' && e.id && !seen[e.id]) {
        seen[e.id] = 1;
        out.push({ id: e.id,
                   title: ((e.texts || {}).title || {}).default || '',
                   date: (it.decorations || {}).originalAirDate || '' });
      }
    }
  };
  // page the SET from offset 0 until a page adds nothing new (proven pattern)
  let offset = 0, guard = 0;
  while (guard < 300) {
    guard++;
    const j = await getJson(setBase + '&offset=' + offset + '&size=30');
    if (j.__status) break;
    const s = findItems(j);
    const items = s.items || [];
    if (!items.length) break;
    const before = out.length;
    take(s);
    offset += items.length;
    if (out.length === before) break;   // no new -> done
  }
  return JSON.stringify({ showName, episodes: out });
}
"""


def _mint_and_enumerate(page, show_id):
    """Load the player (mints anon token) then enumerate every episode."""
    # Landing on a show entity page both mints the anonymous AUTH_TOKEN and
    # gives the gateway a warm session; any /player/* entity page works.
    page.goto(BASE + "/player/show-podcast/entity/" + show_id,
              wait_until="domcontentloaded")
    # wait until the anonymous AUTH_TOKEN cookie exists
    import json as _json
    import time as _time
    deadline = _time.time() + 30
    have = False
    while _time.time() < deadline:
        page.wait_for_timeout(500)
        for c in page.context.cookies():
            if c.get("name") == "AUTH_TOKEN" and c.get("value"):
                have = True
                break
        if have:
            break
    raw = page.evaluate(_ENUM_JS, show_id)
    data = _json.loads(raw)
    return data


def _discover_show_id(page, title):
    """Use the player's own search box to find the best show-podcast id."""
    page.goto(BASE + "/player/discover", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    # dismiss cookie banner if it's covering the UI
    for sel in ("button:has-text('Deny Non-Essential')",
                "button:has-text('Accept')", "button[aria-label='Close']"):
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(timeout=2000)
                break
        except Exception:  # noqa: BLE001
            pass
    # open search + type the title
    for sel in ("a[href*='/search']", "text=Search", "[aria-label='Search']"):
        try:
            page.click(sel, timeout=3000)
            break
        except Exception:  # noqa: BLE001
            pass
    box = None
    for sel in ("input[placeholder*='Search' i]", "input[type='search']",
                "input[type='text']"):
        try:
            box = page.wait_for_selector(sel, timeout=5000)
            if box:
                break
        except Exception:  # noqa: BLE001
            pass
    if not box:
        return None
    box.click()
    box.fill(title)
    # wait for show-podcast result cards to render
    try:
        page.wait_for_selector("a[href*='show-podcast/entity/']", timeout=12000)
    except Exception:  # noqa: BLE001
        return None
    cands = page.eval_on_selector_all(
        "a[href*='show-podcast/entity/']",
        "els => els.map(e => [e.getAttribute('href'), (e.textContent||'').trim()])")
    best, best_score = None, -1.0
    for href, text in cands:
        m = _SHOW_IN_URL.search(href or "")
        if not m:
            continue
        sc = similarity(title, text) if text else 0.5
        if sc > best_score:
            best, best_score = m.group(1), sc
    return best


def _row(ep_title, ep_id):
    url = BASE + "/player/episode-podcast/entity/" + ep_id
    return {"season": "", "episode": "", "title": ep_title or "",
            "identifier": url, "watch_url": url}


# ── public entry point ────────────────────────────────────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None, headless=True):
    """Resolve a SiriusXM podcast to every episode's siriusxm.com URL.

    Returns (show_name, rows); each row is
        {season:"", episode:"", title:<episode name>,
         identifier:<https://www.siriusxm.com/player/episode-podcast/entity/<id>>,
         watch_url:<same>}
    `seasons` is accepted for interface parity but ignored (flat episode list).
    Anonymous — no login, no SiriusXM account.
    """
    if os.environ.get("STREAMSCOUT_HEADED"):
        headless = False

    show_id_hint, ep_id_hint = None, None
    if url:
        m = _EP_IN_URL.search(url)
        ep_id_hint = m.group(1) if m else None
        m = _SHOW_IN_URL.search(url)
        show_id_hint = m.group(1) if m else None

    # pasted single-episode link + movie kind -> just that episode (no browser)
    if ep_id_hint and not show_id_hint and kind == "movie":
        return (title or "", [_row("", ep_id_hint)])

    sp = _sync_playwright()
    with sp() as pw:
        browser, ctx, page = _new_page(pw, headless=headless)
        try:
            show_id = show_id_hint
            if not show_id and title:
                show_id = _discover_show_id(page, title)
            if not show_id:
                return (title or "", [])

            data = _mint_and_enumerate(page, show_id)
            eps = data.get("episodes", []) if isinstance(data, dict) else []
            show_name = (data.get("showName") if isinstance(data, dict) else "") \
                or title or ""

            if kind == "movie" and title:      # single best-matching episode
                if not eps:
                    return (show_name, [])
                best = max(eps, key=lambda e: similarity(title, e.get("title", "")))
                return (show_name, [_row(best.get("title", ""), best["id"])])

            rows = [_row(e.get("title", ""), e["id"]) for e in eps]
            return (show_name, rows)
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
        description="SiriusXM podcast -> every episode URL (anonymous browser).")
    ap.add_argument("--type", choices=["movie", "series"], default="series")
    ap.add_argument("--title")
    ap.add_argument("--url", help="a siriusxm.com/player entity URL")
    ap.add_argument("--seasons", help="(accepted but ignored; flat episode list)")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    kind, title = args.type, args.title
    if not args.url and not title:
        title = ask("What SiriusXM podcast?: ")
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
        print("  ! Not found on SiriusXM. Try --url with the show link.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "siriusxm").lower()).strip("-")
    path = os.path.join(args.outdir, f"lookup_siriusxm_{kind}_{safe}_{stamp}.csv")
    try:
        from production_tags import production_for  # noqa: WPS433
        prod = production_for(show or title or "") or ""
    except Exception:  # noqa: BLE001
        prod = ""
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["SHOW", "URL", "PRODUCTION", "PLATFORM", "SEASON"])
        for r in rows:
            w.writerow([show, r["identifier"], prod, "SiriusXM", ""])
    print(f"  ok  {len(rows)} episode(s) -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
