#!/usr/bin/env python3
"""
playstation_store.py — hands-off PlayStation Store resolver for GameTool.

PlayStation renders search + editions client-side and hydrates them from an
internal GraphQL API whose query hashes rotate, so plain HTTP can't see them.
Instead we drive a headless Chromium (the StreamScout pattern): load the real
search page, let it call its own API, and harvest the `getSearchResults`
payload. Each product carries a `storeDisplayClassification` — we keep the
game/edition classes (FULL_GAME, BUNDLE, PREMIUM_GAME, DEMO, …) and drop
costume/character/pass add-ons, so a title like "Marvel vs Capcom" fans out to
every buyable edition/region SKU automatically — no URL pasting, no expertise.

Falls back gracefully to [] if Playwright/Chromium isn't available, in which
case GameTool offers the paste-a-URL path instead.
"""
import re
import urllib.parse

from common import hit, similarity, tokens

try:
    from playwright.sync_api import sync_playwright
    _HAVE_PW = True
except Exception:                       # noqa: BLE001
    _HAVE_PW = False

STORE = "PlayStation Store"
BASE = "https://store.playstation.com/en-us"
# product id = <region>-<titleId>_00-<label>. titleId is CUSA##### (PS4),
# PPSA##### (PS5), or future 4-letter prefixes — match them all, not just CUSA.
_PRODID = re.compile(r"^[A-Z]{2}[0-9]{4}-[A-Z]{4}[0-9]{5}_00-[A-Z0-9]{16}$")

# classifications that mean "a game someone bought or played" (not a costume,
# character, season pass, currency, or other add-on)
_GAME_CLASSES = {"FULL_GAME", "BUNDLE", "GAME", "GAME_BUNDLE", "PREMIUM_GAME",
                 "PS5_GAME", "PSVR_GAME", "PSVR2_GAME", "DEMO"}

# non-game items that occasionally carry a game classification
_NOT_A_GAME = {"comic", "comics", "soundtrack", "ost", "theme", "themes",
               "avatar", "avatars", "wallpaper", "wallpapers"}


def _collect(title, nav_timeout=45000, sel_timeout=30000):
    """Drive headless Chromium to the search page; return getSearchResults JSON."""
    payloads = []

    def on_response(resp):
        if "graphql" in resp.url and "getSearchResults" in resp.url:
            try:
                payloads.append(resp.json())
            except Exception:            # noqa: BLE001
                pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        # skip images/media/fonts — we only need the JSON XHR, and this is faster
        page.route("**/*", lambda r: r.abort()
                   if r.request.resource_type in ("image", "media", "font")
                   else r.continue_())
        page.on("response", on_response)
        url = f"{BASE}/search/" + urllib.parse.quote(title)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout)
            page.wait_for_selector("a[href*='/product/']", timeout=sel_timeout)
            page.wait_for_timeout(1500)   # let late getSearchResults settle
        except Exception:                 # noqa: BLE001
            pass
        browser.close()
    return payloads


def _products(payloads):
    """Walk the payloads and collect unique {CUSA id -> product object}."""
    found = {}

    def walk(o):
        if isinstance(o, dict):
            pid = o.get("id")
            if (isinstance(pid, str) and _PRODID.match(pid) and "name" in o
                    and "storeDisplayClassification" in o):
                found[pid] = o
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for j in payloads:
        walk(j)
    return found


# tiny words that don't decide identity — an edition may add/drop these freely
_STOP = {"the", "a", "an", "of", "and", "or", "to", "for", "vs", "x",
         "de", "la", "el", "le"}


def _relevant(title, name):
    """Keep only editions of the SAME game. PlayStation's search returns anything
    that shares a single word, so "8 Ball Pool" drags in every generic *pool*
    game. But edition/region SKUs only ADD words to the base title (e.g.
    "…Infinite - Deluxe Edition", "…Arcade Classics"), so we require the
    candidate to contain EVERY distinctive query word. That drops different games
    that merely share a common noun ("pool", "ball") without hurting real
    multi-edition enumeration."""
    q = [t for t in tokens(title) if t not in _STOP]
    if not q:                                    # query was all stop-words
        q = tokens(title)
    if not set(q).issubset(set(tokens(name))):
        return False
    return similarity(title, name) >= 0.2


def search(title, limit=25):
    """Return one hit per buyable game edition/region SKU for `title`."""
    if not _HAVE_PW:
        return []
    prods = _products(_collect(title))
    scored = []
    for pid, o in prods.items():
        if (o.get("storeDisplayClassification") or "").upper() not in _GAME_CLASSES:
            continue
        name = o.get("name", "")
        if set(tokens(name)) & _NOT_A_GAME:      # comic/soundtrack/theme/avatar
            continue
        if not _relevant(title, name):
            continue
        scored.append((similarity(title, name),
                       hit(name, f"{BASE}/product/{pid}", pid, "Digital", "")))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [h for _, h in scored][:limit]
