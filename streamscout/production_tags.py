#!/usr/bin/env python3
"""
production_tags.py  —  title -> primary production-house tag (all platforms)
============================================================================
One shared source of the PRODUCTION column for every platform in
`title_lookup.py`. Resolution order for a given show/movie name:

    1. MANUAL OVERRIDE  — `OVERRIDES` below (authoritative; pin whatever you want)
    2. CACHE            — a confirmed name -> code, on disk (looked up once)
    3. WIKIDATA         — the FIRST "production company" (property P272) of the
                          matching film/series, run through `ALIASES` for your
                          short code.

Wikidata is open public-domain data (CC0): NO API key, NO signup, NO usage terms
to accept. If a title has no P272 (or the lookup fails), the tag is left blank —
pin it in `OVERRIDES` if you want it filled. Everything is cached to disk so each
title is only looked up once.
"""

import json
import os
import re
import urllib.parse
import urllib.request

CACHE = os.path.expanduser("~/Desktop/production_tag_cache.json")
_TIMEOUT = 15
# Wikidata asks for a descriptive User-Agent identifying the tool.
_UA = "behavioralgraph-title-tool/1.0 (https://crosswalknyc.com)"
_WD = "https://www.wikidata.org/w/api.php"


def tokens(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


# ── 1. manual overrides (authoritative). Keys match on tokens (subset ok). ─────
# This is the same hand-curated list the tool grew before TMDB — keep pinning
# titles here whenever you want a specific house code regardless of TMDB.
OVERRIDES = {
    "the bear": "FXP",
    "brilliant minds": "WBD",
    "the sandman": "WBD",
    "only murders in the building": "20TV",
    "the kardashians": "FX",
    "ted lasso": "WBD",
    "landman": "Paramount TV Studios",
    "house of the dragon": "WBD",
    "percy jackson and the olympians": "20th Television",
    "power": "CBS Studios",
}

# ── 2. canonical TMDB company name (lowercased) -> your preferred short code ────
# TMDB returns full legal names; normalise the common ones to house style. Any
# name not listed here passes through unchanged, so this is purely optional
# polish — extend it whenever you want a tidier/abbreviated code.
ALIASES = {
    "fx productions": "FXP",
    "fx": "FX",
    "20th television": "20th Television",
    "20th television animation": "20th Television",
    "20th century studios": "20th Century Studios",
    "20th century fox television": "20th Television",
    "walt disney pictures": "Walt Disney Pictures",
    "walt disney animation studios": "Walt Disney Animation",
    "pixar": "Pixar",
    "pixar animation studios": "Pixar",
    "lucasfilm": "Lucasfilm",
    "lucasfilm ltd.": "Lucasfilm",
    "marvel studios": "Marvel Studios",
    "hbo": "HBO",
    "home box office": "HBO",
    "home box office (hbo)": "HBO",
    "warner bros. television": "WBD",
    "warner bros. television studios": "WBD",
    "netflix": "Netflix",
    "paramount television studios": "Paramount TV Studios",
    "paramount television": "Paramount TV Studios",
    "cbs studios": "CBS Studios",
    "nbcuniversal": "NBCU",
    "universal television": "Universal TV",
    "universal pictures": "Universal",
}


def _override(show):
    want = tokens(show)
    if not want:
        return None
    # exact token match wins outright (e.g. "power" -> only "Power")
    for name, code in OVERRIDES.items():
        if tokens(name) == want:
            return code
    # otherwise a MULTI-token override may match as a subset ("the bear" inside
    # "the bear fx"); single-token keys never subset-match, so a short title like
    # "Power" can't leak its code onto "Power Book II Ghost".
    for name, code in OVERRIDES.items():
        nt = tokens(name)
        if len(nt) >= 2 and set(nt) <= set(want):
            return code
    return None


# ── cache ─────────────────────────────────────────────────────────────────────
def _cache_key(show, kind):
    return f"{'movie' if kind == 'movie' else 'series'}:" + " ".join(tokens(show))


def cache_get(show, kind, path=CACHE):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f).get(_cache_key(show, kind))
    except Exception:
        return None


def cache_put(show, kind, code, path=CACHE):
    try:
        data = {}
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
        data[_cache_key(show, kind)] = code
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# ── Wikidata (open, no key) ───────────────────────────────────────────────────
def _get_json(url):
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _wikidata_first_company(show, kind):
    """First 'production company' (P272) of the matching film/series, or None."""
    # 1) search candidate entities by title (ranked by relevance)
    s = (f"{_WD}?action=wbsearchentities&search={urllib.parse.quote(show)}"
         f"&language=en&format=json&type=item&limit=8")
    try:
        hits = _get_json(s).get("search", [])
    except Exception:
        return None
    ids = [h["id"] for h in hits]
    if not ids:
        return None
    # 2) pull claims + descriptions for all candidates in one call
    g = (f"{_WD}?action=wbgetentities&ids={'|'.join(ids)}"
         f"&props=claims|descriptions&languages=en&format=json")
    try:
        ents = _get_json(g).get("entities", {})
    except Exception:
        return None
    want_movie = (kind == "movie")
    for qid in ids:  # search-rank order
        e = ents.get(qid, {})
        claims = e.get("claims", {})
        p272 = claims.get("P272")
        if not p272:
            continue
        desc = (e.get("descriptions", {}).get("en", {}) or {}).get("value", "").lower()
        is_film = "film" in desc
        is_series = any(w in desc for w in ("series", "television", "sitcom",
                                            "miniseries", "anime"))
        if want_movie and is_series and not is_film:
            continue
        if not want_movie and is_film and not is_series:
            continue
        try:
            comp_qid = p272[0]["mainsnak"]["datavalue"]["value"]["id"]
        except Exception:
            continue
        try:
            cg = (f"{_WD}?action=wbgetentities&ids={comp_qid}"
                  f"&props=labels&languages=en&format=json")
            return _get_json(cg)["entities"][comp_qid]["labels"]["en"]["value"]
        except Exception:
            continue
    return None


# ── public entry point (used by title_lookup.py for ALL platforms) ────────────
def production_for(show, kind="series"):
    """Primary production-house code for a title (blank if truly unknown)."""
    if not show:
        return ""
    # 1) manual override wins
    o = _override(show)
    if o is not None:
        return o
    # 2) cache (a prior Wikidata result, including a cached blank miss)
    c = cache_get(show, kind)
    if c is not None:
        return c
    # 3) Wikidata (open, no key). Cache hits AND misses so we query each once.
    name = _wikidata_first_company(show, kind)
    code = ALIASES.get(name.lower().strip(), name) if name else ""
    cache_put(show, kind, code)
    return code


if __name__ == "__main__":
    import sys
    k = "movie" if (len(sys.argv) > 2 and sys.argv[2].startswith("m")) else "series"
    print(production_for(sys.argv[1] if len(sys.argv) > 1 else "The Bear", k))
