#!/usr/bin/env python3
"""
stores.py — per-store search + URL parsing for GameTool.

Reliability tiers (see STORES registry at the bottom):
  • "api"     — live title search over a public HTTP/JSON endpoint (no login):
                Steam, GOG, Apple App Store, Google Play, Nintendo eShop.
  • "catalog" — a small built-in map (Battle.net's fixed Blizzard line-up).
  • "headless"— a headless Chromium drives the real search page (for stores whose
                search is bot-walled to plain HTTP): PlayStation, Xbox, Epic.
  • "paste"   — no clean anonymous search; we parse a pasted product URL into a
                clean id (and the runner offers a paste-URL prompt). These are
                the bot-walled marketplaces/retailers (Amazon Luna, Eneba,
                Loaded, G2A, Green Man Gaming, Amazon, Best Buy, GameStop,
                Walmart, Target).
"""
import html
import re

from common import (best_matches, hit, http_get, http_json, q, qplus,
                    similarity, tokens)
from epic_store import search as epic_search
from playstation_store import search as playstation_search
from xbox_store import search as xbox_search


# ── Steam (public storesearch JSON) ───────────────────────────────────────────
def steam_search(title, limit=4):
    d = http_json(f"https://store.steampowered.com/api/storesearch/?term={q(title)}"
                  f"&cc=us&l=en") or {}
    out = []
    for it in d.get("items", []):
        appid = it.get("id")
        if not appid:
            continue
        out.append(hit(it.get("name", ""),
                       f"https://store.steampowered.com/app/{appid}/",
                       appid, "Digital (PC)"))
    return best_matches(title, out, key=lambda h: h["title"], limit=limit)


# ── Apple App Store (iTunes Search API) ───────────────────────────────────────
def apple_search(title, limit=3):
    d = http_json(f"https://itunes.apple.com/search?term={q(title)}"
                  f"&entity=software&country=us&limit=12") or {}
    out = []
    for r in d.get("results", []):
        tid = r.get("trackId")
        url = r.get("trackViewUrl", "")
        if not (tid and url):
            continue
        # Games only. iTunes "software" returns every app category, so a
        # same-named non-game app (e.g. a 2014 "Big Hops" Entertainment app)
        # would otherwise exact-title-match and masquerade as the game. 6014 is
        # the App Store "Games" genre id.
        gids = {str(g) for g in (r.get("genreIds") or [])}
        if "6014" not in gids and r.get("primaryGenreName") != "Games":
            continue
        out.append(hit(r.get("trackName", ""), url.split("?")[0], tid,
                       "App (iOS)", r.get("artistName", "")))
    return best_matches(title, out, key=lambda h: h["title"], limit=limit)


# ── Nintendo eShop (public Algolia index the US store search itself uses) ──────
_NINTENDO_APP = "U3B6GR4UA3"
_NINTENDO_KEY = "c4da8be7fd29f0f5bfa42920b0a99dc7"
_NINTENDO_IDX = "ncom_game_en_us"


def nintendo_search(title, limit=4):
    d = http_json(
        f"https://{_NINTENDO_APP.lower()}-dsn.algolia.net/1/indexes/"
        f"{_NINTENDO_IDX}/query",
        headers={"X-Algolia-Application-Id": _NINTENDO_APP,
                 "X-Algolia-API-Key": _NINTENDO_KEY},
        data={"params": f"query={q(title)}&hitsPerPage=12"}) or {}
    out = []
    for h in d.get("hits", []):
        m = re.search(r"/games/detail/([a-z0-9-]+)", h.get("url") or "")
        if not m:
            continue
        slug = m.group(1)
        pubs = h.get("publishers")
        pub = pubs[0] if isinstance(pubs, list) and pubs else ""
        out.append(hit(h.get("title", ""),
                       f"https://www.nintendo.com/us/store/products/{slug}/",
                       slug, "Digital", pub))
    return best_matches(title, out, key=lambda h: h["title"], limit=limit)


# ── Google Play (search HTML -> package ids -> details og:title) ───────────────
def googleplay_search(title, limit=3):
    page = http_get(f"https://play.google.com/store/search?q={qplus(title)}&c=apps")
    seen, pkgs = set(), []
    for m in re.finditer(r"store/apps/details\?id=([A-Za-z0-9._]+)", page):
        p = m.group(1)
        if p not in seen:
            seen.add(p)
            pkgs.append(p)
        if len(pkgs) >= limit * 2:
            break
    out = []
    for pkg in pkgs:
        d = http_get(f"https://play.google.com/store/apps/details?id={pkg}")
        # Games only. Play search mixes in tools/utilities that share a game's
        # name (e.g. a "Hades PC" TOOLS app), so require a GAME_* category —
        # the same games-genre guard used for the Apple App Store.
        if not re.search(r"/store/apps/category/GAME", d):
            continue
        m = re.search(r'<meta property="og:title" content="([^"]+)"', d)
        # decode HTML entities so "Hades&#39; Star" tokenizes as "Hades ' Star",
        # not a bogus "39" token that would fake a prefix match
        name = html.unescape(m.group(1) if m else "").split(" - ")[0].strip()
        if not name:
            continue
        dev = re.search(r'href="/store/apps/dev(?:eloper)?\?id=[^"]*"[^>]*>'
                        r'<[^>]*>([^<]+)<', d)
        out.append(hit(name, f"https://play.google.com/store/apps/details?id={pkg}",
                       pkg, "App (Android)",
                       html.unescape(dev.group(1)) if dev else ""))
    return best_matches(title, out, key=lambda h: h["title"], limit=limit)


# ── GOG (catalog v1 JSON) ─────────────────────────────────────────────────────
def gog_search(title, limit=4):
    d = http_json(f"https://catalog.gog.com/v1/catalog?query={q(title)}&limit=12"
                  f"&locale=en-US&currencyCode=USD&countryCode=US") or {}
    out = []
    for p in d.get("products", []):
        link = p.get("storeLink") or ""
        if not link:
            continue
        pub = (p.get("publishers") or [""])[0]
        out.append(hit(p.get("title", ""), link.split("?")[0],
                       p.get("id", ""), "Digital (PC)", pub))
    return best_matches(title, out, key=lambda h: h["title"], limit=limit)


# ── Battle.net (fixed Blizzard catalog) ───────────────────────────────────────
_BNET = [
    ("World of Warcraft", "world-of-warcraft"),
    ("Diablo IV", "diablo-iv"),
    ("Diablo III", "diablo-iii"),
    ("Diablo II: Resurrected", "diablo-ii-resurrected"),
    ("Diablo Immortal", "diablo-immortal"),
    ("Overwatch 2", "overwatch-2"),
    ("StarCraft II", "starcraft-ii"),
    ("StarCraft: Remastered", "starcraft-remastered"),
    ("Warcraft III: Reforged", "warcraft-iii-reforged"),
    ("Warcraft Rumble", "warcraft-rumble"),
    ("Hearthstone", "hearthstone"),
    ("Heroes of the Storm", "heroes-of-the-storm"),
]


def battlenet_search(title, limit=6):
    out = []
    for name, slug in _BNET:
        if similarity(title, name) >= 0.45 or set(tokens(title)) & set(tokens(name)):
            out.append(hit(name,
                           f"https://us.shop.battle.net/en-us/product/{slug}",
                           slug, "Digital (PC)", "Blizzard"))
    return best_matches(title, out, key=lambda h: h["title"], limit=limit) \
        if out else []


# ── URL parsing (works for every store, powers the paste-URL fallback) ─────────
# label -> (regex capturing the id, canonical-url builder from that id or None)
_URL_RULES = {
    "Steam": (r"store\.steampowered\.com/app/(\d+)",
              lambda i: f"https://store.steampowered.com/app/{i}/"),
    "Apple App Store": (r"apps\.apple\.com/[^/]+/app/[^/]+/id(\d+)", None),
    "Google Play": (r"play\.google\.com/store/apps/details\?id=([A-Za-z0-9._]+)",
                    lambda i: f"https://play.google.com/store/apps/details?id={i}"),
    "GOG": (r"gog\.com/(?:\w+/)?game/([A-Za-z0-9_]+)", None),
    "Green Man Gaming": (r"greenmangaming\.com/games/([a-z0-9-]+)",
                         lambda i: f"https://www.greenmangaming.com/games/{i}/"),
    "Nintendo eShop": (r"nintendo\.com/[^\s]*?/store/products/([A-Za-z0-9-]+)", None),
    "PlayStation Store": (
        r"store\.playstation\.com/[^/]+/(?:product|concept)/([A-Za-z0-9_-]+)", None),
    "Xbox": (r"xbox\.com/[^\s]*?/store/[^/]+/([A-Za-z0-9]{12})", None),
    "Epic Games Store": (r"store\.epicgames\.com/[^/]+/p/([A-Za-z0-9-]+)", None),
    "Amazon Luna": (r"luna\.amazon\.[a-z.]+/game/(?:[a-z0-9-]+/)?(B0[0-9A-Z]{8})",
                    None),
    # marketplaces: Eneba is /<region>/<slug>; Loaded is a flat root slug
    # (loaded.com/hogwarts-legacy-pc-steam). Require a hyphen so category
    # landings (/pc, /playstation/games) don't parse as products.
    "Eneba": (r"eneba\.com/(?:[a-z]{2}/)?([a-z0-9]+(?:-[a-z0-9]+)+)", None),
    "Loaded": (r"loaded\.com/([a-z0-9]+(?:-[a-z0-9]+)+)", None),
    # G2A: slug carries the stable numeric product id as a "-i<digits>" suffix
    "G2A": (r"g2a\.com/[a-z0-9-]+-i(\d+)", None),
    "Amazon": (r"amazon\.[a-z.]+/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10})",
               lambda i: f"https://www.amazon.com/dp/{i}"),
    "Best Buy": (r"bestbuy\.com/.*?(?:/sku/(\d+)|/(\d{7}))", None),
    "GameStop": (r"gamestop\.com/.*?/products/[^/]+/(\d+)\.html", None),
    "Walmart": (r"walmart\.com/ip/(?:[A-Za-z0-9-]+/)?(\d+)", None),
    "Target": (r"target\.com/p/[^/]*/-/A-(\d+)", None),
}


# stores whose product URLs we can parse — used for the paste-URL fallback even
# when a live/headless search happens to return nothing
URL_PARSE_LABELS = set(_URL_RULES)


def parse_url(url):
    """Return (label, store_id, canonical_url) for a pasted product URL, or None."""
    for label, (rx, canon) in _URL_RULES.items():
        m = re.search(rx, url or "", re.I)
        if m:
            sid = next((g for g in m.groups() if g), "")
            return label, sid, (canon(sid) if canon else url.split("?")[0])
    return None


# ── content-map search-term builder ───────────────────────────────────────────
# Turn a product URL into the normalized "content map" term. This is the twin of
# the hostmap builder in gametool_hostmap, with ONE deliberate difference: the
# content map KEEPS the real URL punctuation (slug hyphens / underscores / dots)
# instead of flattening every non-alphanumeric to a space. That's the whole
# reason this lives in StreamScout separately:
#   hostmap  → "marvel vs capcom fighting collection arcade classics/9nwfm3hdjc94"
#   content  → "marvel-vs-capcom-fighting-collection-arcade-classics/9nwfm3hdjc94"
# We still keep only the operative path segment(s) (locale prefixes + tracking
# suffixes sit outside these captures), keep at most ONE "/" on the meaningful
# boundary, and anchor on the store's stable id (or prefix/slug at franchise
# grain). Captures already contain just slug/id characters, so we emit them
# verbatim — no space-flattening.
def _t_steam(u):
    m = re.search(r"/app/(\d+)", u)
    return f"app/{m.group(1)}" if m else ""


def _t_epic(u):
    m = re.search(r"/p/([A-Za-z0-9-]+)", u)
    return f"p/{m.group(1)}" if m else ""


def _t_gog(u):
    m = re.search(r"/game/([A-Za-z0-9_]+)", u)
    return f"game/{m.group(1)}" if m else ""


def _t_battlenet(u):
    m = re.search(r"/product/([A-Za-z0-9-]+)", u)
    return f"product/{m.group(1)}" if m else ""


def _t_nintendo(u):
    m = re.search(r"/products/([A-Za-z0-9-]+)", u)
    return f"products/{m.group(1)}" if m else ""


def _t_playstation(u):
    m = re.search(r"/(?:product|concept)/([A-Za-z0-9_-]+)", u)
    return f"product/{m.group(1)}" if m else ""


def _t_xbox(u):
    m = re.search(r"/store/([A-Za-z0-9-]+)/([A-Za-z0-9]{12})", u)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def _t_luna(u):
    m = re.search(r"/game/([A-Za-z0-9-]+)/(B0[0-9A-Z]{8})", u)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m = re.search(r"/game/(?:[a-z0-9-]+/)?(B0[0-9A-Z]{8})", u)
    return f"game/{m.group(1)}" if m else ""


def _t_apple(u):
    m = re.search(r"/app/([A-Za-z0-9-]+)/id(\d+)", u)
    return f"{m.group(1)}/id{m.group(2)}" if m else ""


def _t_googleplay(u):
    m = re.search(r"[?&]id=([A-Za-z0-9._]+)", u)
    return m.group(1) if m else ""


def _t_gmg(u):
    m = re.search(r"/games/([A-Za-z0-9-]+)", u)
    return f"games/{m.group(1)}" if m else ""


def _t_eneba(u):
    m = re.search(r"eneba\.com/(?:[a-z]{2}/)?([A-Za-z0-9-]+)", u)
    return f"eneba.com/{m.group(1)}" if m else ""


def _t_loaded(u):
    m = re.search(r"loaded\.com/([A-Za-z0-9-]+)", u)
    return f"loaded.com/{m.group(1)}" if m else ""


def _t_g2a(u):
    m = re.search(r"g2a\.com/([A-Za-z0-9-]+)", u)
    return f"g2a.com/{m.group(1)}" if m else ""


def _t_amazon(u):
    m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", u)
    return f"dp/{m.group(1)}" if m else ""


def _t_bestbuy(u):
    m = re.search(r"/site/([A-Za-z0-9-]+)/(\d+)\.p", u)
    return f"{m.group(1)}/{m.group(2)}.p" if m else ""


def _t_gamestop(u):
    m = re.search(r"/products/([A-Za-z0-9.-]+)/(\d+)\.html", u)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def _t_walmart(u):
    m = re.search(r"/ip/([A-Za-z0-9-]+)/(\d+)", u)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def _t_target(u):
    m = re.search(r"/A-(\d+)", u)
    return f"A-{m.group(1)}" if m else ""


_TERM_BUILDERS = {
    "Steam": _t_steam,
    "Epic Games Store": _t_epic,
    "GOG": _t_gog,
    "Battle.net": _t_battlenet,
    "Nintendo eShop": _t_nintendo,
    "PlayStation Store": _t_playstation,
    "Xbox": _t_xbox,
    "Amazon Luna": _t_luna,
    "Apple App Store": _t_apple,
    "Google Play": _t_googleplay,
    "Green Man Gaming": _t_gmg,
    "Eneba": _t_eneba,
    "Loaded": _t_loaded,
    "G2A": _t_g2a,
    "Amazon": _t_amazon,
    "Best Buy": _t_bestbuy,
    "GameStop": _t_gamestop,
    "Walmart": _t_walmart,
    "Target": _t_target,
}


def to_term(label, url):
    """Normalized content-map search term for a store URL ('' if unbuildable) —
    the operative path segment(s) with real punctuation preserved."""
    fn = _TERM_BUILDERS.get(label)
    return fn(url or "") if fn else ""


# ── registry ──────────────────────────────────────────────────────────────────
# key: (label, format, tier, search-callable-or-None)
STORES = [
    ("steam",       ("Steam",             "Digital (PC)",   "api",     steam_search)),
    ("epic",        ("Epic Games Store",  "Digital (PC)",   "headless", epic_search)),
    ("gog",         ("GOG",               "Digital (PC)",   "api",     gog_search)),
    ("battlenet",   ("Battle.net",        "Digital (PC)",   "catalog", battlenet_search)),
    ("nintendo",    ("Nintendo eShop",    "Digital",        "api",     nintendo_search)),
    ("playstation", ("PlayStation Store", "Digital",        "headless", playstation_search)),
    ("xbox",        ("Xbox",              "Digital",        "headless", xbox_search)),
    ("luna",        ("Amazon Luna",       "Cloud",          "paste",   None)),
    ("apple",       ("Apple App Store",   "App (iOS)",      "api",     apple_search)),
    ("googleplay",  ("Google Play",       "App (Android)",  "api",     googleplay_search)),
    ("gmg",         ("Green Man Gaming",  "Key (PC)",       "paste",   None)),
    ("eneba",       ("Eneba",             "Key (market)",   "paste",   None)),
    ("loaded",      ("Loaded",            "Key (market)",   "paste",   None)),
    ("g2a",         ("G2A",               "Key (market)",   "paste",   None)),
    ("amazon",      ("Amazon",            "Physical/Digital", "paste", None)),
    ("bestbuy",     ("Best Buy",          "Physical/Digital", "paste", None)),
    ("gamestop",    ("GameStop",          "Physical",       "paste",   None)),
    ("walmart",     ("Walmart",           "Physical/Digital", "paste", None)),
    ("target",      ("Target",            "Physical/Digital", "paste", None)),
]
STORE_MAP = {k: v for k, v in STORES}
